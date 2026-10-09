#!/usr/bin/env python3
"""
Nikola -- Dynamic Risk-Reward trading app (separate from the live margin bot).

Usage:
    python nikola.py --paper --once          # single paper scan + state machine demo
    python nikola.py --paper                 # paper daemon (15s light / 300s scan)
    python nikola.py --paper --cycles N      # N daemon ticks then exit
    python nikola.py --status                # show state without trading
    python nikola.py --depth BTC/USD         # depth chart for a pair
    python nikola.py --godcode               # print a God Code quote
    python nikola.py --live                  # REAL Kraken orders (user-approved)

Paper mode defaults to $300 clean slate (Nikola.md).  ETH/SOL/XBT are FOREIGN --
blocked from paper trading (user-managed on the live bot, never touched).

The live bot (PID 2045) runs independently -- Nikola uses its own CCXT session
and/or its own PaperExchange simulator.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Engine
from engine import config as cfg
from engine import indicators as ind
from engine import risk as risk_mod
from engine import strategy as strat
from engine import signals as sig
from engine.exchange import (
    ExchangeManager, PaperExchange, fetch_order_book, depth_chart_text,
    place_entry, place_sl, place_tp,
)
from engine.state import PaperState, now_iso
from agents.god_code import GodCodeAgent

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
PAPER_STATE_PATH = DATA_DIR / "paper_state.json"

# The user actively trades these on the live bot -- NEVER paper-trade them.
FOREIGN_PAIRS = {"BTC/USD", "ETH/USD", "SOL/USD"}

log = logging.getLogger("Nikola")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        stream=sys.stdout,
    )


# ---------------------------------------------------------------------------
# Live-mode credentials loader (PREPARED, NOT ACTIVATED — await user go-live)
# ---------------------------------------------------------------------------
def load_kraken_env(env_path: str) -> tuple:
    """Parse kraken.env for API_KEY + API_SECRET (KEY=VALUE format)."""
    key, secret = "", ""
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("API_KEY=") or line.startswith("KRAKEN_API_KEY="):
                key = line.split("=", 1)[1].strip()
            elif line.startswith("API_SECRET=") or line.startswith("KRAKEN_API_SECRET="):
                secret = line.split("=", 1)[1].strip()
    return key, secret


# ---------------------------------------------------------------------------
# Mock candle generation for paper mode (no exchange needed)
# ---------------------------------------------------------------------------
_MOCK_SEED_TS = 1789905600000  # 2026-09-20 12:00 UTC
_MOCK: dict = {}  # pair -> {"index": int, "1m_prices": [float, ...]}


def seed_mock_candles() -> None:
    """Generate synthetic trending OHLCV for all 28 pairs x 6 TFs.

    Each pair gets a deterministic trend so MTF signals produce meaningful
    LONG/SHORT votes. 1m closes are tracked separately for price ticks.
    """
    random.seed(42)
    _MOCK.clear()
    for pair in cfg.SYMBOLS:
        base = 20.0 + (hash(pair) % 200)
        trend = 0.0008 if hash(pair) % 3 == 0 else -0.0006
        for tf in cfg.MTF_TIMEFRAMES:
            mult = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}[tf]
            n, price, ts = 200, base, _MOCK_SEED_TS
            for _ in range(n):
                noise = random.gauss(0, base * 0.004)
                o = price
                c = price * (1 + trend * mult * 0.02 + noise / base * 0.3)
                h = max(o, c) + abs(noise) * 0.4
                l = min(o, c) - abs(noise) * 0.4
                v = random.uniform(50, 500)
                candle = [int(ts), round(o, 5), round(h, 5), round(l, 5),
                          round(c, 5), round(v, 2)]
                key = (pair, tf)
                existing = sig._CANDLES.get(key, [])
                if len(existing) < n:
                    sig.set_candles(pair, tf, existing + [candle])
                else:
                    sig.set_candles(pair, tf, existing[1:] + [candle])
                price, ts = c, ts + mult * 60000
        prices_1m = sig.load_candles(pair, "1m")
        c1m = [c[4] for c in prices_1m[-200:]] if prices_1m else []
        _MOCK[pair] = {"index": 0, "1m_prices": c1m}
    log.info(f"Seeded mock candles: {len(cfg.SYMBOLS)} pairs x "
             f"{len(cfg.MTF_TIMEFRAMES)} TFs")


def advance_mock_price(pair: str) -> float:
    """Walk one 1m candle forward, return the new close price."""
    entry = _MOCK.get(pair)
    if not entry or not entry["1m_prices"]:
        return 0.0
    prices = entry["1m_prices"]
    entry["index"] = (entry["index"] + 1) % len(prices)
    return prices[entry["index"]]


def current_mock_price(pair: str) -> float:
    entry = _MOCK.get(pair)
    if not entry or not entry["1m_prices"]:
        return 0.0
    return entry["1m_prices"][entry["index"]]


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------
class Daemon:
    """Nikola's autonomous trading daemon.

    Light cycle (15s): advance mock/live prices, step open positions through
    the DynamicRiskReward state machine, process TP/SL/exit actions.

    Full scan (300s = 20 light ticks): refresh MTF candles, compute signals,
    rank candidates, deploy the top signal (subject to foreign-pair blocking
    + budget gate).
    """

    TICKS_PER_SCAN = cfg.FULL_SCAN_INTERVAL // cfg.LIGHT_LOOP_INTERVAL  # 20

    def __init__(self, paper: bool = True,
                 cash: float = cfg.PAPER_BUDGET_USD,
                 cycles: int = None, verbose: bool = False,
                 live_key: str = "", live_secret: str = "",
                 faith_tradition: str = "all"):
        self.paper = paper
        self.cycles = cycles
        self.verbose = verbose
        self.state = PaperState.load(PAPER_STATE_PATH)
        if self.state.cash_usd <= 0:
            self.state = PaperState(cash=cash)  # fresh clean slate
        self.cash = self.state.cash_usd
        self.engine = strat.DynamicRiskReward()
        self.god_code = GodCodeAgent(faith_tradition=faith_tradition)
        self.mtf = sig.MTFEngine(signal_weights=sig.load_signal_weights())
        self.ex = ExchangeManager(paper=paper, cash_usd=self.cash,
                                  api_key=live_key, api_secret=live_secret)
        self.active: dict[str, strat.Position] = {}          # pair -> Position
        self.cooldowns: dict[str, float] = {}
        self.tick = 0
        if paper:
            seed_mock_candles()

    # -- display --
    def _depth_display(self, pair: str = "BTC/USD") -> str:
        book = self.ex.fetch_order_book(pair, limit=5)
        return depth_chart_text(book)

    def _gamification_display(self) -> str:
        g = self.state.gamification
        bar = g.progress_bar()
        ach = ", ".join(g.achievements) if g.achievements else "none yet"
        return (f"  [Lvl {g.level}] XP {g.xp}  {bar}  |  "
                f"Trades: {g.trades_won}W-{g.trades_lost}L  "
                f"Streak: {g.streak} (best {g.longest_streak})  "
                f"|  Achievements: {ach}")

    def _positions_display(self) -> str:
        if not self.active:
            return "  (no active positions)"
        lines = []
        for pair, pos in self.active.items():
            price = current_mock_price(pair) if self.paper else \
                self.ex.fetch_ticker(pair).get("last", 0)
            r = self.engine.r_multiple(pos, price) if price else 0
            tp = pos.tp
            lines.append(
                f"  {pair} {'LONG' if pos.direction == 'LONG' else 'SHORT'} "
                f"@ {pos.entry_price:.5f}  R={r:+.2f}  [{pos.state.label}]  "
                f"TP1:{'v' if tp.tp1_filled else '-'} "
                f"TP2:{'v' if tp.tp2_filled else '-'}"
            )
        return "\n".join(lines)

    def _equity(self) -> float:
        if self.paper:
            total = self.cash
        else:
            # Live: equity = exchange total USD balance
            bal = self.ex.exchange.fetch_balance()
            total = float(bal.get("USD", {}).get("total",
                      bal.get("USD", {}).get("free", 0)))
        for pair, pos in self.active.items():
            price = current_mock_price(pair) if self.paper else \
                self.ex.fetch_ticker(pair).get("last", pos.entry_price)
            if pos.direction == "LONG":
                total += price * pos.base_amount
            else:
                total += (2 * pos.entry_price - price) * pos.base_amount
        return total

    def display(self, cycle_type: str = "SCAN") -> None:
        q, src = self.god_code.current_quote
        mode_str = "LIVE" if not self.paper else "PAPER"
        print(f"\n{'='*68}")
        print(f" NIKOLA :: Dynamic RR  |  {mode_str}  |  CYCLE {self.state.cycle}  "
              f"[{cycle_type}]")
        print(f"{'='*68}")
        print(f"  [God Code x {src}]")
        print(f'  >> "{q}"')
        if self.paper:
            cash_str = f"${self.cash:.2f}"
        else:
            cash_str = f"${self.ex.free_margin():.2f}"
        print(f"  Cash: {cash_str}  Equity: ${self._equity():.2f}  "
              f"Active: {len(self.active)}")
        print(f"\n  Depth Chart -- BTC/USD:")
        print(self._depth_display("BTC/USD"))
        print(f"\n  Active Positions:")
        print(self._positions_display())
        print(f"\n  {self._gamification_display()}")
        print(f"{'='*68}")

    # -- light cycle: step the state machine --
    def light_cycle(self) -> None:
        """Advance prices + step state machine for open positions."""
        for pair in list(self.active.keys()):
            price = advance_mock_price(pair) if self.paper else \
                self.ex.fetch_ticker(pair).get("last", 0)
            pos = self.active[pair]
            pos.current_price = price
            votes = sig.compute_votes(pair)
            candles_1h = sig.load_candles(pair, "1h")
            atr = ind.get_atr(candles_1h, 14) if candles_1h else None
            actions = self.engine.step(pos, price, votes=votes, atr=atr)
            if actions and self.verbose:
                for a in actions:
                    pl = " " + json.dumps(a.payload, default=str) \
                        if a.payload else ""
                    print(f"    -> {a.action}({a.pair} @ {a.price:.5f}){pl}")
            self._exec_actions(pair, pos, actions, price, votes, atr)
            # Verify SL2/TP orders survived on the exchange (Kraken cancelation guard)
            if not self.paper:
                self._verify_protection_orders(pair, pos)

    def _exec_actions(self, pair: str, pos: strat.Position,
                      actions: list, price: float, votes, atr) -> None:
        for a in actions:
            if a.action in ("SL2_FULL_EXIT", "SL2", "SL2_EXIT"):
                self._close_position(pair, a.price or price, reason=a.action)
            elif a.action == "CASCADE_SCALEOUT":
                # 15% reduce-only close on BTC cascade (both LONG + SHORT safe)
                scale_base = a.payload.get("scale_base", 0.0)
                if not self.paper and scale_base > 0:
                    self._place_live_exit(pair, pos, scale_base,
                                          a.price, "cascade_scaleout")
                if scale_base > 0 and pos.trade_id:
                    entry = pos.entry_price
                    if pos.direction == "LONG":
                        leg_pnl = (a.price - entry) * scale_base
                    else:
                        leg_pnl = (entry - a.price) * scale_base
                    leg_pnl *= (1 - cfg.FEE_TERM_PCT)
                    leg_pnl_pct = (leg_pnl / (entry * scale_base)) * 100 \
                        if entry and scale_base > 0 else 0.0
                    self.state.record_paper_leg(pos.trade_id, a.price, scale_base,
                                                leg_pnl, leg_pnl_pct, "cascade_scaleout")
                if self.verbose:
                    print(f"    -> CASCADE SCALEOUT on {pair} @ {a.price:.5f} "
                          f"(BTC {a.payload['btc_momentum']*100:.2f}% 1m, "
                          f"closed {scale_base:.4f}, "
                          f"{a.payload.get('remaining_base', 0):.4f} remaining)")
            elif a.action == "PARTIAL_EXIT_50PCT":
                # Record the 50% partial as a leg BEFORE halving base_amount
                exited_base = pos.base_amount * 0.5
                if not self.paper and exited_base > 0:
                    self._place_live_exit(pair, pos, exited_base,
                                          price, "expanded_partial_50pct")
                if pos.trade_id:
                    entry = pos.entry_price
                    if pos.direction == "LONG":
                        leg_pnl = (price - entry) * exited_base
                    else:
                        leg_pnl = (entry - price) * exited_base
                    leg_pnl *= (1 - cfg.FEE_TERM_PCT)
                    leg_pnl_pct = (leg_pnl / (entry * exited_base)) * 100 \
                        if entry and exited_base > 0 else 0.0
                    self.state.record_paper_leg(pos.trade_id, price, exited_base,
                                                leg_pnl, leg_pnl_pct, "expanded_partial_50pct")
                pos.expanded_exited_half = True
                pos.base_amount *= 0.5
                if self.verbose:
                    print(f"    -> partial exit 50% on {pair} @ {a.price:.5f}")
            elif a.action == "BE_RATCHET_ARM":
                pos.be_floor = a.price
            elif a.action == "SL1_HIT":
                # Partial close at SL1 (-2.1%): close 40% of base,
                # retain TP ladder, arm SL2 as the new floor.
                close_base = pos.base_amount * a.payload.get("partial", 0.4)
                if not self.paper and close_base > 0:
                    self._place_live_exit(pair, pos, close_base,
                                          price, "sl1_partial")
                if close_base > 0 and pos.trade_id:
                    entry = pos.entry_price
                    if pos.direction == "LONG":
                        leg_pnl = (price - entry) * close_base
                    else:
                        leg_pnl = (entry - price) * close_base
                    leg_pnl *= (1 - cfg.FEE_TERM_PCT)
                    leg_pnl_pct = (leg_pnl / (entry * close_base)) * 100 \
                        if entry and close_base > 0 else 0.0
                    self.state.record_paper_leg(pos.trade_id, price,
                                                close_base, leg_pnl, leg_pnl_pct,
                                                "sl1_partial")
                pos.base_amount -= close_base
                pos.be_floor = a.payload.get("sl2_price", pos.sl2_price)
                if self.verbose:
                    print(f"    -> SL1 hit on {pair} @ {price:.5f}: "
                          f"partial close {close_base:.4f} base "
                          f"({a.payload.get('partial', 0.4)*100:.0f}%), "
                          f"SL2 armed @ {pos.be_floor:.5f}")
                print(f"    >> God Code adversity: "
                      f"\"{self.god_code.quote_for_adversity(pair)}\"")
            elif a.action == "TP1_FILLED":
                # Close 40% of base at TP1 (+1.4%), record realized leg.
                close_base = pos.base_amount * a.payload.get("partial", 0.4)
                if not self.paper and close_base > 0:
                    self._place_live_exit(pair, pos, close_base,
                                          a.price, "tp1_fill")
                if close_base > 0 and pos.trade_id:
                    entry = pos.entry_price
                    if pos.direction == "LONG":
                        leg_pnl = (a.price - entry) * close_base
                    else:
                        leg_pnl = (entry - a.price) * close_base
                    leg_pnl *= (1 - cfg.FEE_TERM_PCT)
                    leg_pnl_pct = (leg_pnl / (entry * close_base)) * 100 \
                        if entry and close_base > 0 else 0.0
                    self.state.record_paper_leg(pos.trade_id, a.price,
                                                close_base, leg_pnl, leg_pnl_pct,
                                                "tp1_fill")
                pos.base_amount -= close_base
                if self.verbose:
                    print(f"    -> TP1 filled on {pair} @ {a.price:.5f}: "
                          f"close {close_base:.4f} base")
            elif a.action == "TP2_FILLED":
                # Close 40% of base at TP2 (+2.8%), record realized leg.
                close_base = pos.base_amount * a.payload.get("partial", 0.4)
                if not self.paper and close_base > 0:
                    self._place_live_exit(pair, pos, close_base,
                                          a.price, "tp2_fill")
                if close_base > 0 and pos.trade_id:
                    entry = pos.entry_price
                    if pos.direction == "LONG":
                        leg_pnl = (a.price - entry) * close_base
                    else:
                        leg_pnl = (entry - a.price) * close_base
                    leg_pnl *= (1 - cfg.FEE_TERM_PCT)
                    leg_pnl_pct = (leg_pnl / (entry * close_base)) * 100 \
                        if entry and close_base > 0 else 0.0
                    self.state.record_paper_leg(pos.trade_id, a.price,
                                                close_base, leg_pnl, leg_pnl_pct,
                                                "tp2_fill")
                pos.base_amount -= close_base
                if self.verbose:
                    print(f"    -> TP2 filled on {pair} @ {a.price:.5f}: "
                          f"close {close_base:.4f} base")
            elif a.action in ("SL1", "EXIT"):
                self._close_position(pair, a.price or price, reason=a.action)
        # Runner trailing stop check
        if pos.state == strat.PositionState.EXPANDED and pos.trailing_sl > 0:
            if pos.direction == "LONG" and price <= pos.trailing_sl:
                self._close_position(pair, price, reason="TRAIL_EXIT")
            elif pos.direction == "SHORT" and price >= pos.trailing_sl:
                self._close_position(pair, price, reason="TRAIL_EXIT")

    def _close_position(self, pair: str, exit_price: float,
                        reason: str) -> None:
        pos = self.active.pop(pair, None)
        if not pos:
            return
        # --- LIVE MODE: place reduce-only exit order on exchange ---
        if not self.paper:
            remaining = pos.base_amount
            if remaining > 0:
                self._place_live_exit(pair, pos, remaining, exit_price, reason)
            # Live cash/equity tracked by exchange; still record for audit
            self.state.trade_history.append({
                "pair": pair, "direction": pos.direction,
                "entry_price": pos.entry_price, "exit_price": exit_price,
                "pnl_usd": 0.0, "r_multiple": 0.0,
                "signal_type": pos.signal_type, "confidence": round(pos.confidence, 4),
                "reason": reason, "time": now_iso(),
            })
            if pos.trade_id:
                self.state.close_paper_trade(pos.trade_id, exit_price, reason)
            return
        # --- PAPER MODE (existing logic) ---
        # P&L
        if pos.direction == "LONG":
            gross = (exit_price - pos.entry_price) * pos.base_amount
        else:
            gross = (pos.entry_price - exit_price) * pos.base_amount
        gross *= (1 - cfg.FEE_TERM_PCT)  # round-trip fee
        self.cash += exit_price * pos.base_amount + gross
        risk_cap = pos.entry_price * pos.base_amount * cfg.SL1_PCT
        r = gross / risk_cap if risk_cap > 0 else 0
        # Gamification
        won = gross > 0
        msgs = self.state.gamification.on_trade_close(won, abs(r))
        for m in msgs:
            print(f"\n  * {m}")
        self.state.trade_history.append({
            "pair": pair, "direction": pos.direction,
            "entry_price": pos.entry_price, "exit_price": exit_price,
            "pnl_usd": round(gross, 4), "r_multiple": round(r, 2),
            "signal_type": pos.signal_type, "confidence": round(pos.confidence, 4),
            "reason": reason, "time": now_iso(),
        })
        self.state.activity_log.append({
            "cycle": self.state.cycle, "time": now_iso(),
            "event": f"CLOSE {pair} {reason} PnL=${gross:.4f} R={r:+.2f}",
        })
        # Self-learning -- adjust signal weight
        self.mtf.signal_weights = sig.load_signal_weights()
        sig.adjust_weight(pos.signal_type, won, self.mtf.signal_weights)
        sig.save_signal_weights(self.mtf.signal_weights)
        # God Code quote for the trade outcome
        q = self.god_code.quote_for_trade(pair, pos.direction, r)
        outcome = "victory" if won else "lesson learned"
        print(f'  >> God Code: "{q}"')
        # Close paper trade ledger record (links to paper_trades.json audit)
        if pos.trade_id:
            self.state.close_paper_trade(pos.trade_id, exit_price, reason)

    # -- live order placement (GO-LIVE ACTIVE) --
    def _deploy_live_orders(self, pair: str, pos: strat.Position,
                            size: risk_mod.SizeResult, entry_price: float) -> bool:
        """Place real Kraken BTNL entry + SL + TP orders.

        Only called when ``--live``. Entry is a limit order;
        SL is a stop-loss-limit at SL2; TP1/TP2 are reduce-only limits.
        All BTNL symbols use the ``:BTNL`` suffix.

        Returns True only if entry + SL2 both succeed. If SL2 fails after
        a successful entry, the position is immediately closed via a
        reduce-only market order so capital is never left unprotected.
        """
        from engine.exchange import place_entry, place_sl, place_tp
        mx = self.ex.exchange  # CCXT Kraken instance
        btnl = f"{pair}:BTNL"
        side = "buy" if pos.direction == "LONG" else "sell"
        # Entry
        entry_order = place_entry(mx, btnl, side, size.base_amount,
                                  entry_price, size.leverage)
        if not entry_order:
            log.error(f"LIVE entry failed for {pair} — position NOT opened")
            self.active.pop(pair, None)
            return False
        pos.entry_confirmed = True  # entry order placed on exchange
        pos.entry_order_id = entry_order.get("id", "") if entry_order else ""
        # Poll entry order until filled (fixes reduce-only race on BTNL margin)
        entry_id = pos.entry_order_id
        for attempt in range(10):
            if not entry_id:
                break
            try:
                eo = mx.fetch_order(entry_id, btnl)
                if eo.get("status") == "closed":
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            log.warning(f"Entry for {pair} not filled after 5s — proceeding with protection placement")
        # SL2 (full exit stop-loss-limit) — CRITICAL: must succeed
        close_side = "sell" if pos.direction == "LONG" else "buy"
        sl_order = place_sl(mx, btnl, side, size.base_amount,
                            pos.sl2_price, size.leverage)
        if not sl_order:
            log.error(f"SL2 FAILED for {pair} — emergency close of entry position")
            # Emergency reduce-only market order to close the full position
            try:
                mx.create_order(
                    symbol=btnl, type="market", side=close_side,
                    amount=size.base_amount,
                    params={"leverage": size.leverage, "reduceOnly": True},
                )
                log.info(f"EMERGENCY CLOSE: {close_side} {size.base_amount} {btnl}")
            except Exception as e:
                log.critical(f"EMERGENCY CLOSE FAILED: {e} — position open, no SL!")
            self.active.pop(pair, None)
            return False
        pos.sl2_set = True  # mark SL2 as confirmed on exchange
        pos.sl2_order_id = sl_order.get("id", "") if sl_order else ""
        # TP1 / TP2 (reduce-only limits) — non-fatal if they fail, with retry
        tp1_base = size.base_amount * pos.tp.tp1_pct
        tp2_base = size.base_amount * pos.tp.tp2_pct
        tp1_order = None
        for attempt in range(3):
            tp1_order = place_tp(mx, btnl, side, tp1_base,
                                 pos.tp.tp1_price, size.leverage)
            if tp1_order:
                pos.tp1_order_id = tp1_order.get("id", "")
                break
            time.sleep(1.0)
        if not tp1_order:
            log.warning(f"TP1 placement failed for {pair} after 3 retries — SL2 still active")
        tp2_order = None
        for attempt in range(3):
            tp2_order = place_tp(mx, btnl, side, tp2_base,
                                 pos.tp.tp2_price, size.leverage)
            if tp2_order:
                pos.tp2_order_id = tp2_order.get("id", "")
                break
            time.sleep(1.0)
        if not tp2_order:
            log.warning(f"TP2 placement failed for {pair} after 3 retries — SL2 still active")
        log.info(f"LIVE orders placed: {btnl} entry + SL2 + TP1/TP2")

    def _place_live_exit(self, pair: str, pos: strat.Position,
                         amount: float, price: float, reason: str) -> None:
        """Place a reduce-only exit order on the live exchange."""
        from engine.exchange import place_tp
        mx = self.ex.exchange
        btnl = f"{pair}:BTNL"
        side = "buy" if pos.direction == "LONG" else "sell"
        order = place_tp(mx, btnl, side, amount, price, pos.leverage)
        if self.verbose:
            print(f"    -> LIVE_EXIT {pair} {reason} {amount:.4f} @ {price:.5f}")

    def _verify_protection_orders(self, pair: str, pos: strat.Position) -> None:
        """Check SL2/TP1/TP2 are still on the exchange; re-place if missing.
        Guards against Kraken cancelling reduce-only orders post-placement."""
        if not pos.filled or not getattr(pos, 'entry_confirmed', False):
            return
        btnl = f"{pair}:BTNL"
        mx = self.ex.exchange
        side = "buy" if pos.direction == "LONG" else "sell"
        from engine.exchange import place_sl, place_tp

        def _order_open(oid):
            """Verify an order is still 'open' on the exchange by ID."""
            if not oid:
                return False
            try:
                o = mx.fetch_order(oid, btnl)
                return o.get("status") == "open"
            except Exception:
                return False

        try:
            # --- Remediation: if SL2 was never placed, place it now ---
            if not getattr(pos, 'sl2_set', False):
                log.warning(f"SL2 not set for {pair} — placing protection orders")
                sl_order = place_sl(mx, btnl, side, pos.base_amount,
                                    pos.sl2_price, pos.leverage)
                if sl_order:
                    pos.sl2_set = True
                    pos.sl2_order_id = sl_order.get("id", "")
                    log.info(f"SL2 placed for {pair} — {pos.sl2_order_id}")
                else:
                    log.critical(f"SL2 placement FAILED for {pair} — emergency close")
                    self._close_position(pair, pos.current_price,
                                         reason="EMERGENCY_NO_SL2")
                    return
                if pos.tp:
                    tp1_o = place_tp(mx, btnl, side,
                                     pos.base_amount * pos.tp.tp1_pct,
                                     pos.tp.tp1_price, pos.leverage)
                    if tp1_o:
                        pos.tp1_order_id = tp1_o.get("id", "")
                    tp2_o = place_tp(mx, btnl, side,
                                     pos.base_amount * pos.tp.tp2_pct,
                                     pos.tp.tp2_price, pos.leverage)
                    if tp2_o:
                        pos.tp2_order_id = tp2_o.get("id", "")
                return  # just placed — skip stale-order checks

            # --- SL2 check (verify by order ID, not fetch_open_orders) ---
            sl2_id = getattr(pos, 'sl2_order_id', '')
            if sl2_id and not _order_open(sl2_id):
                log.warning(f"SL2 missing for {pair} — re-placing with correct side")
                sl_order = place_sl(mx, btnl, side, pos.base_amount,
                                    pos.sl2_price, pos.leverage)
                if sl_order:
                    pos.sl2_order_id = sl_order.get("id", "")
                    log.info(f"SL2 re-placed for {pair} — {pos.sl2_order_id}")
                else:
                    log.critical(f"SL2 re-place FAILED for {pair} — emergency close")
                    self._close_position(pair, pos.current_price,
                                         reason="EMERGENCY_NO_SL2")
                    return

            # --- TP1 check ---
            if pos.tp and not pos.tp.tp1_filled:
                tp1_id = getattr(pos, 'tp1_order_id', '')
                if tp1_id and not _order_open(tp1_id):
                    log.info(f"TP1 missing for {pair} — re-placing with correct side")
                    tp1_order = place_tp(mx, btnl, side,
                                         pos.base_amount * pos.tp.tp1_pct,
                                         pos.tp.tp1_price, pos.leverage)
                    if tp1_order:
                        pos.tp1_order_id = tp1_order.get("id", "")

            # --- TP2 check ---
            if pos.tp and not pos.tp.tp2_filled:
                tp2_id = getattr(pos, 'tp2_order_id', '')
                if tp2_id and not _order_open(tp2_id):
                    log.info(f"TP2 missing for {pair} — re-placing with correct side")
                    tp2_order = place_tp(mx, btnl, side,
                                         pos.base_amount * pos.tp.tp2_pct,
                                         pos.tp.tp2_price, pos.leverage)
                    if tp2_order:
                        pos.tp2_order_id = tp2_order.get("id", "")
        except Exception as e:
            log.error(f"Order verification failed for {pair}: {e}")

    # -- dynamic equity scaling + daily snapshot --
    def _update_equity_scale(self) -> float:
        """Dynamic equity scaling (live bot lines 88-92, 1173-1178).

        Scale = equity / PAPER_BUDGET_USD, clamped [MIN, MAX].
        Updates cfg.EQUITY_SCALE so size_position() uses the live scale.
        """
        equity = self._equity()
        baseline = cfg.PAPER_BUDGET_USD
        if baseline > 0 and equity > 0:
            scale = max(cfg.EQUITY_SCALE_MIN,
                       min(cfg.EQUITY_SCALE_MAX, equity / baseline))
        else:
            scale = 1.0
        cfg.EQUITY_SCALE = scale
        if self.verbose and self.tick % 20 == 0:
            print(f"    -> equity_scale={scale:.3f}x (equity=${equity:.2f})")
        return scale

    def _daily_snapshot(self) -> None:
        """Once-per-day equity snapshot (live bot lines 1182-1191).

        Bot-attributable realized PnL vs total paper equity.
        Skips if same day as last snapshot.
        """
        today = now_iso()[:10]
        if self.state.last_snapshot_day == today:
            return
        equity = self._equity()
        realized = self.state.realized_pnl()
        scale = cfg.EQUITY_SCALE
        self.state.daily_snapshot(equity, realized, len(self.active), scale)
        print(f"\n  [DAILY SNAPSHOT {today}] equity=${equity:.2f} "
              f"realized=${realized:+.2f} open={len(self.active)} "
              f"scale={scale:.3f}x")

    # -- budget: paper cash vs live exchange margin --
    def _deployable_budget(self) -> float:
        """Return the deployable budget for position sizing.

        Paper mode:  self.cash (paper budget).
        Live mode:   free margin from the Kraken exchange (minus 10% reserve).
        """
        if self.paper:
            return self.cash * 0.9
        return max(self.ex.free_margin() * 0.9, 0.0)

    # -- full scan --
    def full_scan(self) -> None:
        """Refresh MTF signals, rank candidates, deploy top signal."""
        if self.paper:
            seed_mock_candles()  # re-seed so trends evolve per scan
        else:
            self.ex.refresh_mtf_candles()
        self.mtf = sig.MTFEngine(signal_weights=sig.load_signal_weights())
        signals = self.mtf.compute_all_signals()
        ranked = self.mtf.rank_candidates(signals)
        # In paper mode, block FOREIGN_PAIRS (owned by live bot);
        # in live mode, trade all pairs — foreign blocking not needed.
        held = set(self.active.keys())
        if self.paper:
            held |= FOREIGN_PAIRS
        else:
            # Live: SOL and ETH are user-owned (leave as-is); TRX, PAXG, and rest are NIKOLA's
            held |= {"SOL/USD", "ETH/USD"}
        candidates = self.mtf.find_best_signals(
            ranked, n=3, held=held, cooldowns=self.cooldowns,
        )
        # Persist signal weights every full scan (live bot lines 3301-3314)
        sig.save_signal_weights(self.mtf.signal_weights)
        if not candidates:
            if self.verbose:
                print("  (no deployable candidates this scan)")
            return
        self._deploy(candidates[0])

    def _deploy(self, signal: dict) -> None:
        pair = signal["pair"]
        direction = "LONG" if signal["composite_signal"] == "BUY" else "SHORT"
        price = signal["current_price"]
        if price <= 0:
            return
        confidence = signal["confidence"]
        signal_type = signal["signal_type"]
        candles_1h = sig.load_candles(pair, "1h")
        atr_val = ind.get_atr(candles_1h, 14) if candles_1h else 0.0
        budget = self._deployable_budget()
        size = risk_mod.size_position(
            pair, price, atr_val or 1.0, confidence, direction,
            budget=budget,
        )
        if not size or size.margin_cost > max(budget * 0.88, 5.0):
            if self.verbose:
                cost = size.margin_cost if size else 0
                print(f"  (skip {pair}: margin ${cost:.2f} > available)")
            self.cooldowns[pair] = time.time()
            return
        # Ethical evaluation -- God Code veto check
        ev = self.god_code.evaluate_action("OPEN", profit_usd=0, harm_kind="")
        if ev.get("veto"):
            print(f"  [VETO] God Code: {ev['reason']}")
            self.cooldowns[pair] = time.time() + 3600
            return
        if self.paper:
            self.cash -= size.margin_cost
        price = current_mock_price(pair) if self.paper else price
        pos = self.engine.position_from_signal(
            pair, direction, price, size.base_amount, size.leverage,
            confidence, signal_type,
        )
        self.active[pair] = pos
        pos.sl2_set = False  # will be True after _deploy_live_orders confirms SL2
        pos.entry_confirmed = False  # will be True after _deploy_live_orders confirms entry on exchange
        pos.trade_id = self.state.open_paper_trade(
            pair, direction, price, size.base_amount, size.leverage,
            signal_type, confidence, size.margin_cost,
        )
        self.cooldowns[pair] = time.time()
        # XP for opening a trade
        g = self.state.gamification
        g.xp += g.XP_PER_TRADE
        print(f"\n  >> DEPLOY {pair} {direction} @ {price:.5f}")
        print(f"    base={size.base_amount:.4f}  lev={size.leverage}x  "
              f"margin=${size.margin_cost:.2f}  conf={confidence:.2f}")
        print(f"    SL1=${pos.sl1_price:.5f}  TP1=${pos.tp.tp1_price:.5f}  "
              f"TP2=${pos.tp.tp2_price:.5f}  SL2=${pos.sl2_price:.5f}")
        q = self.god_code.quote_for_trade(pair, direction, 0)
        print(f'  >> God Code: "{q}"')
        # --- LIVE MODE: place real Kraken BTNL orders ---
        if not self.paper:
            self._deploy_live_orders(pair, pos, size, price)

    # -- main loop --
    def run(self) -> None:
        mode_str = "LIVE" if not self.paper else "PAPER"
        print(f"\n{'='*68}")
        print(f" NIKOLA daemon -- {mode_str} MODE  (live bot PID 2045, untouched)")
        budget_display = f"${self.cash:.2f}" if self.paper else f"${self.ex.free_margin():.2f}"
        print(f" Budget: {budget_display}  Cycles: {self.cycles or 'infinity'}  "
              f"|  Light: {cfg.LIGHT_LOOP_INTERVAL}s  Scan: "
              f"{cfg.FULL_SCAN_INTERVAL}s")
        if self.paper:
            print(f" Foreign (never trade): {', '.join(sorted(FOREIGN_PAIRS))}")
        print(f"{'='*68}")
        try:
            while True:
                try:
                    is_full = (self.tick % self.TICKS_PER_SCAN == 0)
                    if is_full:
                        self.full_scan()
                        q = self.god_code.tick()
                        if q:
                            src = self.god_code.current_quote[1]
                            print(f"\n  >> God Code cycle -- {src}: \"{q}\"")
                    else:
                        self.light_cycle()
                    self.state.cycle += 1
                    if self.paper:
                        self.state.cash_usd = self.cash
                    self._update_equity_scale()
                    if self.paper:
                        self._daily_snapshot()
                        self.state.save(PAPER_STATE_PATH)
                    if is_full or self.tick % 5 == 0:
                        self.display("SCAN" if is_full else "LIGHT")
                except Exception as e:
                    if 'nonce' in str(e).lower():
                        import engine.exchange as _ex
                        import os as _osmod
                        import json as _jsonmod
                        import time as _timemod
                        _ex._nonce_state = int(_timemod.time() * 1000) + 60000
                        with open(_ex._NONCE_FILE, 'w') as _f:
                            _jsonmod.dump({'nonce': _ex._nonce_state}, _f)
                            _osmod.fsync(_f.fileno())
                        log.info(f"InvalidNonce at tick {self.tick} — nonce jumped to "
                                 f"{_ex._nonce_state}, retrying next cycle")
                    else:
                        log.error(f"Cycle error (tick {self.tick}): {e}", exc_info=True)
                    # In live mode, verify no half-open positions lack SL protection
                    if not self.paper and self.active:
                        for pair in list(self.active.keys()):
                            pos = self.active[pair]
                            if (not getattr(pos, 'sl2_set', False)
                                    and getattr(pos, 'entry_confirmed', False)):
                                log.critical(f"{pair}: position open with no SL2 — "
                                            f"triggering emergency close")
                                btnl = f"{pair}:BTNL"
                                close_side = "sell" if pos.direction == "LONG" else "buy"
                                try:
                                    self.ex.exchange.create_order(
                                        symbol=btnl, type="market", side=close_side,
                                        amount=pos.base_amount,
                                        params={"leverage": getattr(pos, 'leverage', 3),
                                                "reduceOnly": True},
                                    )
                                    log.info(f"EMERGENCY CLOSE (error handler): {close_side} {pos.base_amount} {btnl}")
                                except Exception as e2:
                                    log.critical(f"EMERGENCY CLOSE FAILED: {e2} — position open, no SL!")
                                self.active.pop(pair, None)
                    # Skip sleep on error to retry immediately next tick
                self.tick += 1
                if self.cycles and self.tick >= self.cycles:
                    break
                time.sleep(cfg.LIGHT_LOOP_INTERVAL)
        except KeyboardInterrupt:
            print("\n  STOPPED. Saving state...")
            if self.paper:
                self.state.save(PAPER_STATE_PATH)

    def run_once(self) -> None:
        """Single cycle: scan -> deploy -> advance price -> step machine."""
        if self.paper:
            seed_mock_candles()
        self.state.cycle += 1
        self.full_scan()
        if self.active:
            for _ in range(10):
                self.light_cycle()
                time.sleep(0.05)
        self.state.cash_usd = self.cash
        self._update_equity_scale()
        self._daily_snapshot()
        if self.paper:
            self.state.save(PAPER_STATE_PATH)
        self.display("ONCE")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Nikola -- Dynamic Risk-Reward trading app "
                    "(separate from live bot)")
    ap.add_argument("--paper", action="store_true", help="Paper mode (default)")
    ap.add_argument("--live", action="store_true",
                    help="Live Kraken orders (user-approved)")
    ap.add_argument("--once", action="store_true",
                    help="Single cycle then exit")
    ap.add_argument("--cycles", type=int, default=None, help="Max daemon ticks")
    ap.add_argument("--status", action="store_true",
                    help="Show state without trading")
    ap.add_argument("--depth", metavar="PAIR", help="Show depth chart for a pair")
    ap.add_argument("--godcode", action="store_true",
                    help="Print a God Code quote")
    ap.add_argument("--faith-tradition", default="all",
                    choices=["all", "christian", "universal", "secular"],
                    help="God Code wisdom well to draw from (default: all)")
    ap.add_argument("--verbose", action="store_true",
                    help="Verbose state-machine actions")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    setup_logging(args.log_level)

    if args.live and args.paper:
        print("ERROR: --live and --paper are mutually exclusive")
        sys.exit(1)

    paper = not args.live

    # --- LIVE MODE: credentials loading -- GO-LIVE ACTIVE --
    # Credentials loaded from kraken.env. Live trading active via --live + DRY_RUN=False.
    live_key, live_secret = "", ""
    if args.live:
        env_path = os.environ.get(
            "NIKOLA_KRAKEN_ENV",
            str(BASE_DIR.parent / "kraken_bot" / "kraken.env"),
        )
        try:
            live_key, live_secret = load_kraken_env(env_path)
        except FileNotFoundError:
            print(f"ERROR: kraken.env not found at {env_path}")
            print("  Copy your Kraken API credentials there before going live.")
            sys.exit(1)
        if not live_key or not live_secret:
            print("ERROR: API_KEY/API_SECRET missing from kraken.env")
            sys.exit(1)
        print("=" * 68)
        print("  LIVE MODE ACTIVE — placing real BTNL orders on Kraken.")
        print(f"  Credentials loaded from {env_path}")
        print("=" * 68)

    if args.godcode and not args.status and not args.once and not args.depth:
        g = GodCodeAgent(faith_tradition=args.faith_tradition)
        q, src = g.current_quote
        print(f"\n  >> God Code -- {src}")
        print(f'  "{q}"')
        print()
        return

    if args.status:
        st = PaperState.load(PAPER_STATE_PATH)
        print(f"\n  NIKOLA {st.mode} -- cycle {st.cycle}")
        print(f"  Cash: ${st.cash_usd:.2f}  Equity: ${st.equity():.2f}  "
              f"Trades: {len(st.trade_history)}")
        g = st.gamification
        print(f"  [Lvl {g.level}] XP {g.xp}  Streak {g.streak}  "
              f"Achievements: {', '.join(g.achievements) or 'none'}")
        return

    if args.depth:
        seed_mock_candles()
        ex = PaperExchange(cash_usd=300)
        book = ex.fetch_order_book(args.depth, limit=5)
        print(f"\n  Depth Chart -- {args.depth}")
        print(depth_chart_text(book))
        return

    d = Daemon(paper=paper, cash=cfg.PAPER_BUDGET_USD, cycles=args.cycles,
               verbose=args.verbose, live_key=live_key, live_secret=live_secret,
               faith_tradition=args.faith_tradition)
    if args.once:
        d.run_once()
    else:
        d.run()


if __name__ == "__main__":
    main()

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
        # Quiet background operation: only re-print the status banner on a real
        # state change (position opens/closes, trade closes) or a ~20-minute
        # heartbeat (4 full scans) -- so the daemon can run unattended for weeks.
        self._last_pos_count = None
        self._last_trade_count = None
        self._full_scans_since_status = 0
        # Historical-sim mode: real engine on real candles (no synthetic mock).
        self.sim_mode = False
        self._sim_raw = {}
        self._sim_idx = 0
        self._sim_base_ms = 0
        if paper and not self.sim_mode:
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
            # Unambiguous TP state: hit(TP tripped) / arm(TP configured & live, not yet hit) / -(no TP ladder yet).
            # Fixes the old 'v'/'-' board which could not distinguish "armed, waiting" from "not placed".
            _tp1 = ("hit" if getattr(tp, "tp1_filled", False)
                    else "arm" if (tp and getattr(tp, "tp1_price", 0) > 0) else "-")
            _tp2 = ("hit" if getattr(tp, "tp2_filled", False)
                    else "arm" if (tp and getattr(tp, "tp2_price", 0) > 0) else "-")
            lines.append(
                f"  {pair} {'LONG' if pos.direction == 'LONG' else 'SHORT'} "
                f"@ {pos.entry_price:.8g}  R={r:+.2f}  [{pos.state.label}]  "
                f"TP1:{_tp1} TP2:{_tp2}"
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
        print(f" [GUARDIAN ANGEL ACTIVE] -- watching over your process, assisting Nikola")
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
        # Position sync: remove ghost positions (closed on exchange but still in memory)
        if not self.paper:
            self._sync_position_state()
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

    def _sync_position_state(self) -> None:
        """Detect positions closed on the exchange and purge from self.active."""
        from engine.config import normalize_btln_symbol
        try:
            ex_positions = self.ex.exchange.fetch_positions()
        except Exception:
            return
        live_pairs: set[str] = set()
        for p in ex_positions:
            if not p or not p.get("info"):
                continue
            sym = p.get("symbol", "")
            pair = normalize_btln_symbol(sym)
            if pair.endswith(":BTNL"):
                pair = pair[:-5]
            contracts = float(p.get("contracts", 0) or 0)
            margin = float(p.get("initialMargin", 0) or 0)
            if contracts > 0 and margin > 0.01:
                live_pairs.add(pair)
        for pair in list(self.active.keys()):
            pos = self.active.get(pair)
            if pos is None:
                continue
            if pair in live_pairs:
                continue
            # Pending entry (limit not yet filled): give light_cycle a bounded
            # grace to detect the fill before killing the entry. Pump-safe —
            # no monitoring blackout, never kills a live order; deploys are
            # 150s apart so at most one pending entry exists at a time.
            if (not getattr(pos, "filled", True)
                    and getattr(pos, "entry_order_id", "")):
                try:
                    _st = self.ex.exchange.fetch_order(pos.entry_order_id, f"{pair}:BTNL")
                    _ostatus = _st.get("status", "")
                except Exception:
                    _ostatus = ""   # can't verify -> keep waiting (safe default)
                if _ostatus == "open" and getattr(pos, "entry_order_ts", 0) \
                        and (time.time() - pos.entry_order_ts) < cfg.ENTRY_FILL_GRACE_SEC:
                    continue        # still attempting a fill — keep, light_cycle detects
            # Position is gone from exchange — remove from active + cancel orphans
            if getattr(pos, "filled", True):
                log.info(f"Position {pair} closed on exchange — removing from active")
            else:
                log.info(f"Position {pair} entry not filled — removing from active")
            btnl = f"{pair}:BTNL"
            for oid_attr in ("sl2_order_id", "tp1_order_id", "tp2_order_id",
                             "entry_order_id"):
                oid = getattr(pos, oid_attr, "")
                if oid:
                    try:
                        self.ex.exchange.cancel_order(oid, btnl)
                    except Exception:
                        pass
            # Also cancel any other orphaned orders for this pair
            try:
                orphans = self.ex.exchange.fetch_open_orders(btnl)
                for o in orphans:
                    try:
                        self.ex.exchange.cancel_order(o["id"], btnl)
                    except Exception:
                        pass
            except Exception:
                pass
            self.active.pop(pair, None)

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
            elif a.action == "BE_PRIME":
                # Micro-breakeven tracking armed at +0.1R (before TP1 fills)
                if pos.tp:
                    pos.tp.be_ratchet_primed = True
                if self.verbose:
                    print(f"    -> BE_PRIME armed on {pair} "
                          f"@ R={a.payload.get('r', 0):.2f}")
            elif a.action == "BE_RATCHET_ARM":
                pos.be_floor = a.price
                # Ratchet live SL2 up to BE floor — protects accrued profit
                if (not self.paper and getattr(pos, 'sl2_set', False)
                        and pos.be_floor > pos.sl2_price + 1e-9):
                    self._ratchet_live_sl2(pair, pos, pos.be_floor,
                                           "BE_RATCHET_ARM")
            elif a.action == "ARM_RUNNER_TRAIL":
                # Wide trailing (0.60%) armed on the remaining runner
                # after TP2 fill + peak ≥ 1.19R
                if pos.tp:
                    pos.tp.runner_trailing = True
                pos.trail_pct = a.payload.get("trail_pct", 0.006)
                pos.trailing_sl = a.price  # seed at peak; updated each light cycle
                if (not self.paper and getattr(pos, 'sl2_set', False)
                        and pos.be_floor > pos.sl2_price + 1e-9):
                    self._ratchet_live_sl2(pair, pos, pos.be_floor,
                                           "ARM_RUNNER_TRAIL")
                if self.verbose:
                    print(f"    -> Runner trail armed on {pair} @ {a.price:.5f} "
                          f"(trail={pos.trail_pct*100:.2f}%)")
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
        # ACTIVE-state runner trailing (armed via ARM_RUNNER_TRAIL)
        # Trails 0.60% behind peak, floored at BE (+2.0%)
        if (pos.tp and pos.tp.runner_trailing
                and pos.state == strat.PositionState.ACTIVE
                and getattr(pos, 'be_floor', 0) > 0):
            trail_pct = getattr(pos, 'trail_pct', 0.006)
            if pos.direction == "LONG":
                pos.trailing_sl = max(pos.peak_price * (1 - trail_pct),
                                      pos.be_floor)
                if pos.trailing_sl > 0 and price <= pos.trailing_sl:
                    self._close_position(pair, price, reason="RUNNER_TRAIL_EXIT")
            else:
                pos.trailing_sl = min(pos.peak_price * (1 + trail_pct),
                                      pos.be_floor)
                if pos.trailing_sl > 0 and price >= pos.trailing_sl:
                    self._close_position(pair, price, reason="RUNNER_TRAIL_EXIT")

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
                            size: risk_mod.SizeResult, entry_price: float,
                            order_type: str = "limit") -> bool:
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
                                  entry_price, size.leverage, order_type)
        if not entry_order:
            log.error(f"LIVE entry failed for {pair} — position NOT opened")
            self.active.pop(pair, None)
            return False
        pos.entry_order_id = entry_order.get("id", "") if entry_order else ""
        pos.entry_order_ts = time.time()   # for _sync grace window (entry-fill patience)
        # Poll entry order until filled (fixes reduce-only race on BTNL margin)
        entry_id = pos.entry_order_id
        entry_filled = False
        for attempt in range(10):
            if not entry_id:
                break
            try:
                eo = mx.fetch_order(entry_id, btnl)
                if eo.get("status") == "closed":
                    entry_filled = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            log.warning(f"Entry for {pair} not filled after 5s — proceeding with protection placement")
        if entry_filled:
            pos.entry_confirmed = True
            pos.filled = True
        else:
            pos.entry_confirmed = False
            pos.filled = False
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
        # TP1 / TP2 (reduce-only limits) — only place when entry is confirmed
        if not entry_filled:
            log.warning(
                f"TP1/TP2 deferred for {pair} — entry not filled; "
                f"light_cycle will place on fill detection"
            )
            return
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

    def _ratchet_live_sl2(self, pair: str, pos: strat.Position,
                          new_price: float, reason: str) -> None:
        """Cancel existing SL2 on exchange and replace at new_price (ratchet up).

        Falls back to original SL2 price if the ratchet placement fails —
        capital is never left unprotected.
        """
        mx = self.ex.exchange
        btnl = f"{pair}:BTNL"
        close_side = "buy" if pos.direction == "LONG" else "sell"
        # Cancel old SL2
        old_oid = getattr(pos, 'sl2_order_id', '')
        if old_oid:
            try:
                mx.cancel_order(old_oid, btnl)
            except Exception as e:
                log.warning(f"  SL2 cancel failed for {pair}: {e}")
        # Place new SL2 at ratcheted price
        sl_order = place_sl(mx, btnl, close_side, pos.base_amount,
                            new_price, pos.leverage)
        if sl_order:
            pos.sl2_order_id = sl_order.get("id", "")
            pos.sl2_price = new_price
            log.info(f"  SL2 ratcheted {pair} → {new_price:.5f} ({reason})")
        else:
            log.warning(f"  SL2 ratchet FAILED for {pair} ({reason}) "
                        "— re-placing at last known SL2 price")
            fallback = place_sl(mx, btnl, close_side, pos.base_amount,
                                pos.sl2_price, pos.leverage)
            if fallback:
                pos.sl2_order_id = fallback.get("id", "")

    def _verify_protection_orders(self, pair: str, pos: strat.Position) -> None:
        """Check SL2/TP1/TP2 are still on the exchange; re-place if missing.
        Guards against Kraken cancelling reduce-only orders post-placement."""
        # Check if a pending entry order has filled since deploy
        if not getattr(pos, 'filled', True) and getattr(pos, 'entry_order_id', ''):
            try:
                eo = self.ex.exchange.fetch_order(pos.entry_order_id, f"{pair}:BTNL")
                if eo.get("status") == "closed":
                    pos.filled = True
                    pos.entry_confirmed = True
                    log.info(f"Entry for {pair} filled on light cycle — activating protection checks")
            except Exception:
                pass
        if not pos.filled or not getattr(pos, 'entry_confirmed', False):
            return
        btnl = f"{pair}:BTNL"
        mx = self.ex.exchange
        side = "buy" if pos.direction == "LONG" else "sell"
        from engine.exchange import place_sl, place_tp, _jump_nonce

        def _order_open(oid):
            """Verify an order is still 'open' on the exchange by ID.
            Retries on transient CCXT errors (nonce collision, timeout)."""
            if not oid:
                return False
            for _attempt in range(3):
                try:
                    o = mx.fetch_order(oid, btnl)
                    return o.get("status") == "open"
                except Exception as e:
                    err = str(e).lower()
                    if 'nonce' in err:
                        # Jump nonce past Kraken's seen value (thread-safe)
                        _jump_nonce()
                    time.sleep(0.5)
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
                    fail_count = getattr(pos, '_sl2_fail_count', 0) + 1
                    pos._sl2_fail_count = fail_count
                    if fail_count >= 3:
                        log.critical(f"SL2 placement FAILED for {pair} 3 cycles — emergency close")
                        self._close_position(pair, pos.current_price,
                                             reason="EMERGENCY_NO_SL2")
                        return
                    else:
                        log.warning(f"SL2 placement FAILED for {pair} — "
                                    f"count {fail_count}/3, will retry next cycle")
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
            if not sl2_id or not _order_open(sl2_id):
                log.warning(f"SL2 missing for {pair} — re-placing with correct side")
                sl_order = place_sl(mx, btnl, side, pos.base_amount,
                                    pos.sl2_price, pos.leverage)
                if sl_order:
                    pos.sl2_order_id = sl_order.get("id", "")
                    pos._sl2_fail_count = 0
                    log.info(f"SL2 re-placed for {pair} — {pos.sl2_order_id}")
                elif sl2_id and _order_open(sl2_id):
                    # Re-placement failed but original SL2 is still active — transient error
                    pos._sl2_fail_count = 0
                    log.warning(f"SL2 re-place FAILED for {pair} — original still active, will retry next cycle")
                elif sl2_id:
                    # Can't verify SL2 state — retry before emergency close
                    fail_count = getattr(pos, '_sl2_fail_count', 0) + 1
                    pos._sl2_fail_count = fail_count
                    if fail_count >= 3:
                        log.critical(f"SL2 re-place FAILED for {pair} 3 cycles — emergency close")
                        self._close_position(pair, pos.current_price,
                                             reason="EMERGENCY_NO_SL2")
                        return
                    else:
                        log.warning(f"SL2 re-place FAILED for {pair} — "
                                    f"count {fail_count}/3, will retry next cycle")
                else:
                    log.critical(f"SL2 re-place FAILED for {pair} — emergency close")
                    self._close_position(pair, pos.current_price,
                                         reason="EMERGENCY_NO_SL2")
                    return

            # --- TP1 check ---
            if pos.tp and not pos.tp.tp1_filled and pos.tp.tp1_price > 0:
                tp1_id = getattr(pos, 'tp1_order_id', '')
                if not tp1_id or not _order_open(tp1_id):
                    log.info(f"TP1 missing for {pair} — re-placing with correct side")
                    tp1_order = place_tp(mx, btnl, side,
                                         pos.base_amount * pos.tp.tp1_pct,
                                         pos.tp.tp1_price, pos.leverage)
                    if tp1_order:
                        pos.tp1_order_id = tp1_order.get("id", "")

            # --- TP2 check ---
            if pos.tp and not pos.tp.tp2_filled and pos.tp.tp2_price > 0:
                tp2_id = getattr(pos, 'tp2_order_id', '')
                if not tp2_id or not _order_open(tp2_id):
                    log.info(f"TP2 missing for {pair} — re-placing with correct side")
                    tp2_order = place_tp(mx, btnl, side,
                                         pos.base_amount * pos.tp.tp2_pct,
                                         pos.tp.tp2_price, pos.leverage)
                    if tp2_order:
                        pos.tp2_order_id = tp2_order.get("id", "")

            # --- BE floor sync: ratchet exchange SL2 up to be_floor ---
            if (getattr(pos, 'be_floor', 0) > pos.sl2_price
                    and not getattr(pos, 'sl1_hit', False)):
                log.info(f"  BE_RATCHET_SYNC: {pair} SL2 {pos.sl2_price:.5f} "
                         f"→ {pos.be_floor:.5f}")
                self._ratchet_live_sl2(pair, pos, pos.be_floor, "BE_VERIFY_SYNC")
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

    def _sync_state_positions(self) -> None:
        """Mirror Daemon.active -> PaperState.positions so --export-html/--status
        render mid-trade position geometry. Reporting-only: the live cycle uses
        Daemon._equity() (not state.equity()), so trading behavior is unchanged.
        Called before state.save(); rebuilds state.positions from Daemon.active."""
        from engine.state import PaperPosition
        self.state.positions = []
        for _, pos in self.active.items():
            tp = getattr(pos, "tp", None)
            tp_ladder = []
            if tp is not None:
                tp_ladder = [
                    {"level": 1, "price": float(tp.tp1_price),
                     "size": float(tp.tp1_pct), "filled": bool(tp.tp1_filled)},
                    {"level": 2, "price": float(tp.tp2_price),
                     "size": float(tp.tp2_pct), "filled": bool(tp.tp2_filled)},
                ]
                if getattr(tp, "runner_pct", 0):
                    tp_ladder.append({"level": 3, "size": float(tp.runner_pct),
                                      "trailing": True})
            cur = getattr(pos, "current_price", 0.0)
            current = float(cur) if cur else (float(pos.peak_price) or float(pos.entry_price))
            # R-multiple (profit / risk) for the --export-html geometry column
            risk = float(getattr(pos, "risk_distance", 0.0) or 0.0)
            if risk > 0:
                r = (current - pos.entry_price) / risk if pos.direction == "LONG" \
                    else (pos.entry_price - current) / risk
            else:
                r = 0.0
            self.state.positions.append(PaperPosition(
                pair=pos.pair, direction=pos.direction,
                entry_price=float(pos.entry_price), base_amount=float(pos.base_amount),
                leverage=int(pos.leverage), sl1_price=float(pos.sl1_price),
                sl2_price=float(pos.sl2_price), tp_ladder=tp_ladder,
                entry_time=pos.open_time or now_iso(),
                signal_type=pos.signal_type or "UNKNOWN",
                confidence=float(pos.confidence or 0.0),
                state="active",
                tp1_price=float(tp.tp1_price) if tp is not None else 0.0,
                tp2_price=float(tp.tp2_price) if tp is not None else 0.0,
                current_price=current,
                r_multiple=float(r),
            ))

    def _recover_live_positions(self) -> None:
        """Load existing Kraken open positions into self.active (LIVE only).

        Rebuilds Position objects from exchange positions + their open SL2/TP1/TP2
        orders so the light cycle can manage them (trail runner, verify exits).
        Without this the bot is blind: it thinks Active=0 and fires doomed
        duplicate deploys that Kraken rejects with 'Insufficient initial margin'.
        """
        if self.paper:
            return  # paper mode has its own state
        try:
            ex_positions = self.ex.fetch_positions()
        except Exception as e:
            log.warning(f"position recovery: fetch_positions failed: {e}")
            return
        try:
            orders = self.ex.fetch_open_orders()
        except Exception:
            orders = []
        from engine.config import normalize_btln_symbol, TP_RUNG_PCTS, SL2_PCT
        from engine.strategy import Position, TPRungs
        # index orders by pair for SL2/TP1/TP2 re-attachment
        by_pair: dict[str, list] = {}
        for o in orders:
            psym = o.get("symbol", "")
            # Normalize Kraken-native symbol (PAXGUSD:BTNL) -> CCXT format (PAXG/USD:BTNL)
            # so it matches the position symbol from fetch_positions()
            psym = normalize_btln_symbol(psym)
            by_pair.setdefault(psym, []).append(o)
        user_owned = {"SOL/USD", "ETH/USD"}  # never touch — user-owned pairs
        recovered = 0
        skipped = 0
        for p in ex_positions:
            if float(p.get("contracts", 0) or 0) == 0:
                continue
            # Skip dust positions (zero margin — e.g., leftover 0.2 PEPE tokens)
            if float(p.get("initialMargin", 0) or 0) <= 0.0:
                skipped += 1
                continue
            sym = p.get("symbol", "")
            pair = normalize_btln_symbol(sym)  # -> "BTC/USD:BTNL" etc
            if pair.endswith(":BTNL"):
                pair = pair[:-5]  # strip suffix for SYMBOLS lookup
            if pair in user_owned or pair not in cfg.SYMBOLS:
                continue
            direction = "LONG" if str(p.get("side", "")).lower() == "long" else "SHORT"
            info = p.get("info", {})
            entry_val = float(info.get("cost", 0) or 0)
            vol = float(info.get("vol", 0) or 0)
            entry = (entry_val / vol) if vol > 0 else 0.0
            contracts = float(p.get("contracts", 0))
            lev = cfg.PAIR_LEVERAGE.get(pair, cfg.PREFERRED_LEVERAGE)
            pos = Position(
                pair=pair, direction=direction, entry_price=entry or 1e-9,
                base_amount=abs(contracts), leverage=lev,
                signal_type="RECOVERED", filled=True,
            )
            # mark recovered positions as protected (set dynamically, not in __init__)
            pos.entry_confirmed = True
            pos.sl2_set = True
            # re-attach TP/SL prices + order IDs from open orders if present
            tp_list = []  # (price, order_id) for reduce-only sells
            sl2_price = None
            sl2_oid = ""
            for o in by_pair.get(normalize_btln_symbol(sym), []):
                op = float(o.get("average") or o.get("price") or 0.0)
                if op <= 0.0:
                    continue
                oinfo = o.get("info", {})
                ofee = str(oinfo)
                # classify by order type: stop-loss-limit -> SL2, limit -> TP
                if "stop" in ofee.lower() and "loss" in ofee.lower():
                    sl2_price = op
                    sl2_oid = o.get("id", "")
                elif o.get("reduceOnly"):
                    tp_list.append((op, o.get("id", "")))
            if sl2_price is not None:
                pos.sl2_price = sl2_price
                pos.sl2_order_id = sl2_oid
            if tp_list:
                # LONG: lower sell = TP1, higher = TP2; SHORT: reverse
                tp_list.sort(key=lambda x: x[0], reverse=(direction == "SHORT"))
                pos.tp = TPRungs(tp1_price=0.0, tp2_price=0.0)
                pos.tp.tp1_price = tp_list[0][0]
                pos.tp1_order_id = tp_list[0][1]
                if len(tp_list) >= 2:
                    pos.tp.tp2_price = tp_list[1][0]
                    pos.tp2_order_id = tp_list[1][1]
            else:
                # No TP orders found — create default ladder from entry price
                pos.tp = TPRungs(
                    tp1_price=entry * (1 + TP_RUNG_PCTS[0][2]) if direction == "LONG"
                    else entry * (1 - TP_RUNG_PCTS[0][2]),
                    tp2_price=entry * (1 + TP_RUNG_PCTS[-1][2]) if direction == "LONG"
                    else entry * (1 - TP_RUNG_PCTS[-1][2]),
                )
            if pos.sl2_price == 0.0:
                pos.sl2_price = entry * (1 - SL2_PCT) if direction == "LONG" else entry * (1 + SL2_PCT)
            # Default any TP prices not found live on the exchange from entry, so
            # light_cycle can place the missing rung. When only TP1 was live
            # (tp_list len 1) tp2_price stayed 0.0 -> gate at light_cycle 848
            # (tp2_price > 0) never armed TP2 -> "TP2:-" forever.
            if pos.tp.tp1_price == 0.0:
                pos.tp.tp1_price = entry * (1 + TP_RUNG_PCTS[0][2]) if direction == "LONG" else entry * (1 - TP_RUNG_PCTS[0][2])
            if pos.tp.tp2_price == 0.0:
                pos.tp.tp2_price = entry * (1 + TP_RUNG_PCTS[-1][2]) if direction == "LONG" else entry * (1 - TP_RUNG_PCTS[-1][2])
            if pos.peak_price == 0.0:
                pos.peak_price = entry or pos.entry_price
            self.active[pair] = pos
            self.cooldowns[pair] = time.time()  # 1h cooldown so bot doesn't ADD to pair
            recovered += 1
        log.info(f"position recovery: loaded {recovered} live positions from Kraken "
                 f"(skipped {skipped} dust/orphan)")
        # -- housekeeping: cancel EXACT-DUPLICATE protection orders left by the
        # old nonce-cascade run. Guards: only cancels a copy at the SAME price
        # as an order we are CONFIRMED tracking (tracked ID must be non-empty);
        # never a distinct TP rung, never the tracked order itself, never an
        # entry/buy order, never an order whose tracked ID is empty. Protection
        # is never reduced. Idempotent — a clean recovery cancels nothing.
        try:
            for pair, pos in self.active.items():
                btnl = pair + ":BTNL"
                sl2_id = pos.sl2_order_id or ""
                tp1_id = pos.tp1_order_id or ""
                tp2_id = pos.tp2_order_id or ""
                sl2_price = pos.sl2_price or 0.0
                tp1_price = pos.tp.tp1_price or 0.0
                tp2_price = pos.tp.tp2_price or 0.0
                for o in by_pair.get(btnl, []):
                    oid = o.get("id", "")
                    if not oid or oid in {sl2_id, tp1_id, tp2_id}:
                        continue  # keep every tracked copy
                    oinfo = str(o.get("info", {})).lower()
                    price = float(o.get("average") or o.get("price") or 0.0)
                    if price <= 0:
                        continue
                    is_sl2 = ("stop" in oinfo and "loss" in oinfo)
                    is_tp = bool(o.get("reduceOnly"))
                    is_dup = (
                        (is_sl2 and sl2_id and sl2_price and abs(price - sl2_price) < 1e-9)
                        or (is_tp and tp1_id and tp1_price and abs(price - tp1_price) < 1e-9)
                        or (is_tp and tp2_id and tp2_price and abs(price - tp2_price) < 1e-9)
                    )
                    if not is_dup:
                        continue
                    try:
                        self.ex.exchange.cancel_order(oid, btnl)
                        log.info(f"  dedup: cancelled duplicate {pair} order {oid} @ {price}")
                    except Exception as ce:
                        log.warning(f"  dedup: cancel failed {pair} {oid}: {ce}")
        except Exception as de:
            log.warning(f"dedup housekeeping skipped: {de}")

    # -- budget: paper cash vs live exchange margin --
    def _deployable_budget(self) -> float:
        """Return the deployable budget for position sizing.

        Paper mode:  self.cash (paper budget).
        Live mode:   all free margin from the Kraken exchange (10% hold removed;
        MARGIN_RESERVE_PCT 5% provides the margin-call buffer).
        """
        if self.paper:
            return self.cash
        return max(self.ex.free_margin(), 0.0)

    # -- full scan --
    def full_scan(self) -> None:
        """Refresh MTF signals, rank candidates, deploy top signal."""
        if self.sim_mode:
            self._feed_sim_bars(self._sim_idx)   # real candles truncated to current bar (no lookahead)
        elif self.paper:
            seed_mock_candles()  # re-seed so trends evolve per scan
        else:
            self.ex.refresh_mtf_candles()
        self.mtf = sig.MTFEngine(signal_weights=sig.load_signal_weights(),
                                 paper_sim=self.paper)
        signals = self.mtf.compute_all_signals()
        ranked = self.mtf.rank_candidates(signals)
        # BTC-regime guard: don't open NEW alt positions when BTC is dumping
        # (protect rising equity; the cascade already scales out existing BTC).
        # Inactive in calm/rising regimes, so it never disrupts normal deploys.
        _btc_15m = sig.load_candles("BTC/USD", "15m") or []
        if len(_btc_15m) >= 2 and _btc_15m[-1][4] > 0 and _btc_15m[0][4] > 0:
            _btc_mom = (_btc_15m[-1][4] / _btc_15m[0][4]) - 1.0
            if _btc_mom < cfg.BTC_DUMP_GUARD_PCT:
                log.warning(
                    f"BTC 15m momentum {_btc_mom*100:.2f}% < "
                    f"{cfg.BTC_DUMP_GUARD_PCT*100:.2f}% -- holding cash, "
                    f"skipping alt deploy"
                )
                sig.save_signal_weights(self.mtf.signal_weights)
                return
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

    def _deploy(self, signal: dict, order_type: str = "limit") -> None:
        pair = signal["pair"]
        direction = "LONG" if signal["composite_signal"] == "BUY" else "SHORT"
        price = signal["current_price"]        # prior 1h close (signals.py:434)
        # Anchored + buffered entry -- keeps all SL/TP geometry but stops entry
        # churn. A plain 1h-close buy-LIMIT sits below spot in uptrends and never
        # fills (observed 3 deploys/6min abandoned; DOGE/WWL churned in chop).
        # Anchor the LIMIT to the live 1m close and add a thin slip buffer so it
        # catches the ask and fills in chop/uptrends (calm-window friendly:
        # bounded 0.3% slip; RR rebases onto the filled price, distances
        # unchanged). In a real downtrend keep the 1h-close floor (don't chase).
        _c1m = sig.load_candles(pair, "1m")
        spot = float(_c1m[-1][4]) if _c1m else 0.0
        if not spot:
            spot = self.ex.fetch_ticker(pair).get("last", 0.0) or 0.0
        _slp = cfg.ENTRY_SLIPPAGE_PCT
        # Micro-cap tick fix: sub-dollar tickers (PEPE/SHIB/PENGU/TRX @ 0.0000x)
        # lose the 0.3% buffer below tick precision -> a buy-LIMIT rounds to the
        # stale 1h close and enters underwater (the 0.00000 artifact / churn).
        # Market-fill them at the live 1m spot instead: guaranteed fill, deep
        # mcap liquidity absorbs it, SL2/TP1/TP2 still gate the trade. Majors
        # ($1+) keep the buffered LIMIT.
        if price > 0 and price < 1.0:
            order_type = "market"
            price = spot or price
        elif direction == "LONG" and price > 0 and 0 < spot >= price * (1.0 - _slp):
            price = spot * (1.0 + _slp)       # buffered limit lifts above spot -> fills in chop/uptrend
        elif direction == "SHORT" and price > 0 and 0 < spot <= price * (1.0 + _slp):
            price = spot * (1.0 - _slp)
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
        print(f"\n  >> DEPLOY {pair} {direction} @ {price:.8g}")
        print(f"    base={size.base_amount:.4f}  lev={size.leverage}x  "
              f"margin=${size.margin_cost:.2f}  conf={confidence:.2f}")
        print(f"    SL1=${pos.sl1_price:.8g}  TP1=${pos.tp.tp1_price:.8g}  "
              f"TP2=${pos.tp.tp2_price:.8g}  SL2=${pos.sl2_price:.8g}")
        q = self.god_code.quote_for_trade(pair, direction, 0)
        print(f'  >> God Code: "{q}"')
        # --- LIVE MODE: place real Kraken BTNL orders ---
        if not self.paper:
            self._deploy_live_orders(pair, pos, size, price, order_type)

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
        if not self.paper:
            self._recover_live_positions()          # hydrate self.active from Kraken
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
                        self._sync_state_positions()
                        self.state.save(PAPER_STATE_PATH)
                    # Quiet background operation: banner only on a real state
                    # change (position open/close, trade close) or a ~20-minute
                    # heartbeat (4 full scans). God Code cycle line still prints
                    # every full scan (~5 min) as intended encouragement.
                    pos_now = len(self.active)
                    trd_now = len(self.state.trade_history)
                    if is_full:
                        self._full_scans_since_status += 1
                    _state_changed = (pos_now != self._last_pos_count
                                      or trd_now != self._last_trade_count)
                    if _state_changed or self._full_scans_since_status >= 4:
                        self.display("SCAN" if is_full else "LIGHT")
                        self._last_pos_count = pos_now
                        self._last_trade_count = trd_now
                        self._full_scans_since_status = 0
                except Exception as e:
                    if 'nonce' in str(e).lower():
                        # Forward-only recovery: reuse _jump_nonce so we can never
                        # step backward into a nonce Kraken has already accepted
                        # (the old inline `now + 60000` could overwrite a higher
                        # in-flight value and re-invent the collision).
                        from engine.exchange import _jump_nonce
                        _new = _jump_nonce()
                        log.info(f"InvalidNonce at tick {self.tick} — nonce jumped to "
                                 f"{_new}, retrying next cycle")
                    else:
                        log.error(f"Cycle error (tick {self.tick}): {e}", exc_info=True)
                    # In live mode, verify no half-open positions lack SL protection
                    if not self.paper and self.active:
                        for pair in list(self.active.keys()):
                            pos = self.active[pair]
                            if (not getattr(pos, 'sl2_set', False)
                                    and getattr(pos, 'entry_confirmed', False)):
                                log.critical(f"{pair}: position open with no SL2 — "
                                            f"[GUARDIAN ANGEL] enforcing protection: "
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
                                    log.info(f"[GUARDIAN ANGEL] emergency close: {close_side} {pos.base_amount} {btnl}")
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
                self._sync_state_positions()
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

    # === Historical simulation: REAL live engine on REAL Kraken candles (fair test) ===
    def _load_historical(self, days: float) -> None:
        """Fetch real Kraken OHLCV for the window into the candle cache + price walk."""
        import ccxt
        ex = ccxt.kraken({"enableRateLimit": True, "rateLimit": 400, "timeout": 30000})
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - int(days * 86400000)
        self._sim_raw = {}
        for pair in cfg.SYMBOLS:
            self._sim_raw[pair] = {}
            for tf in cfg.MTF_TIMEFRAMES:
                ohlcv = ex.fetch_ohlcv(pair, timeframe=tf, since=start_ms, limit=720)
                # ohlcv: [ts,open,high,low,close,volume] -> ints/floats
                self._sim_raw[pair][tf] = [[int(c[0])] + [float(x) for x in c[1:6]] for c in ohlcv]
            closes = [float(c[4]) for c in self._sim_raw[pair].get("15m", [])]
            _MOCK[pair] = {"index": 0, "1m_prices": closes}
        self._sim_base_ms = self._sim_raw[cfg.SYMBOLS[0]]["15m"][0][0]
        # fresh clean $300 slate (Nikola.md)
        self.state = PaperState(cash=cfg.PAPER_BUDGET_USD)
        self.cash = cfg.PAPER_BUDGET_USD
        self.active = {}
        self.cooldowns = {}
        self.mtf = sig.MTFEngine(signal_weights=sig.load_signal_weights())
        self.cycles = None
        print(f"[SIM] fetch done | {len(cfg.SYMBOLS)} pairs x {len(cfg.MTF_TIMEFRAMES)} TFs | end "
              f"{datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).isoformat()}")
        print(f"[SIM] fresh $300 paper slate | paper-blocks {sorted(FOREIGN_PAIRS)} (live rule, not a cancel)")

    def _feed_sim_bars(self, idx: int) -> None:
        """Truncate real candles to bars ending at the current sim time (no lookahead)."""
        cutoff = self._sim_base_ms + idx * 15 * 60000
        for pair in cfg.SYMBOLS:
            for tf, raw in self._sim_raw.get(pair, {}).items():
                keep = [c for c in raw if c[0] <= cutoff][-800:]
                if keep:
                    sig.set_candles(pair, tf, keep)

    def _baselines(self) -> dict:
        """Naive HODL vs Panic-bail over the same window (per-pair $20 margin @ listed lev)."""
        hodl = panic = 0.0
        pref = getattr(cfg, "PREFERRED_LEVERAGE", 3)
        plev = getattr(cfg, "PAIR_LEVERAGE", {})
        for pair in cfg.SYMBOLS:
            bars = self._sim_raw[pair].get("15m", [])
            if len(bars) < 2:
                continue
            entry = bars[0][4]
            exit_p = bars[-1][4]
            lev = plev.get(pair, pref)
            notional = 20.0  # per-pair margin anchor for the naive comparison
            hodl += (exit_p - entry) / entry * lev * notional
            # Panic: bail the moment a 15m bar's LOW drops >=2.1% below entry
            bail = entry * (1 - 0.021)
            px = exit_p
            for c in bars[1:]:
                if c[3] <= bail:
                    px = c[4]
                    break
            panic += (px - entry) / entry * lev * notional
        return {"hodl": hodl, "panic": panic}

    def run_sim(self, days: float) -> None:
        """Replay the REAL live loop bar-by-bar over historical candles (no mock)."""
        n = min((len(_MOCK[p]["1m_prices"]) for p in _MOCK if _MOCK[p]["1m_prices"]), default=0)
        SCAN_EVERY = 20  # bars between full scans (~live cadence on 15m data)
        print(f"[SIM] replaying {n} x 15m bars ({days}d) with the REAL engine rules\n")
        for i in range(n):
            self._sim_idx = i
            self.state.cycle += 1
            self.tick += 1
            # advance master price walker to current bar (real 15m closes)
            for p in _MOCK:
                if _MOCK[p]["1m_prices"]:
                    _MOCK[p]["index"] = min(i, len(_MOCK[p]["1m_prices"]) - 1)
            # full scan every SCAN_EVERY bars (truncated real candles via _feed_sim_bars)
            if i >= 60 and i % SCAN_EVERY == 0:
                self.full_scan()            # sim_mode guard -> _feed_sim_bars (no mock seed)
                self._update_equity_scale()
            # step open positions every bar (real BE-ratchet / trailing / SL1-SL2 / cascade / exits)
            if self.active:
                for pair in list(self.active.keys()):
                    price = current_mock_price(pair)
                    pos = self.active[pair]
                    pos.current_price = price
                    votes = sig.compute_votes(pair)
                    candles_1h = sig.load_candles(pair, "1h")
                    atr = ind.get_atr(candles_1h, 14) if candles_1h else None
                    actions = self.engine.step(pos, price, votes=votes, atr=atr)
                    self._exec_actions(pair, pos, actions, price, votes, atr)
            if i % 96 == 0:                 # daily snapshot
                self._daily_snapshot()
                self.state.save(PAPER_STATE_PATH)
            if self.verbose and i % 48 == 0:
                print(f"  sim bar {i}/{n} equity=${self._equity():.2f} active={len(self.active)} "
                      f"scale={cfg.EQUITY_SCALE:.3f}x")
        self.state.cash_usd = self._equity()
        self.state.save(PAPER_STATE_PATH)
        b = self._baselines()
        eq = self._equity()
        hist = self.state.trade_history
        wins = sum(1 for t in hist if t.get("pnl_usd", 0) > 0)
        if hist:
            avg_r = sum(t.get("r_multiple", 0) for t in hist) / len(hist)
            print(f"\n[SIM] END | equity=${eq:.2f} from $300 | trades={len(hist)} "
                  f"(W{wins}/L{len(hist)-wins}) avgR={avg_r:+.2f} active={len(self.active)}")
        else:
            print(f"\n[SIM] END | equity=${eq:.2f} from $300 | trades=0 active=0")
        print(f"[SIM] vs naive (per-pair $20 @ listed lev): HODL={b['hodl']:+.2f}  "
              f"Panic={b['panic']:+.2f}  Nikola={eq-300:+.2f}  "
              f"Edge_vs_Panic={eq-300-b['panic']:+.2f}")
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Nikola -- Dynamic Risk-Reward trading app "
                    "(separate from live bot)",
        epilog="Tithe / Offering / Donation (100% free, optional): "
               "1) Click https://www.paypal.me/LucasMurr369  "
               "2) Scan the PayPal QR embedded in `--export-html` reports  "
               "3) Crypto: `python3 donation_monitor.py info`. See INSTRUCTIONS.md.\n\n"
               "A Guardian Angel watches over your process and assists Nikola "
               "(single foreground PID via nikola.pid; clean Ctrl+C shutdown; "
               "auto-enforced protective stops). See INSTRUCTIONS.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    ap.add_argument("--simulate-window", default=None,
                    help="HISTORICAL fair test: run the REAL live engine over past "
                         "N days of real Kraken OHLCV on a clean $300 paper slate "
                         "(e.g. 5d). No mock candles. HODL/Panic baselines included.")
    ap.add_argument("--export-html", metavar="OUTPUT", nargs="?", const="nikola_report.html",
                    default=None,
                    help="Generate a single-file HTML performance report from the last "
                         "data/paper_state.json and exit (no trading). Optional OUTPUT "
                         "path; defaults to nikola_report.html.")
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

    if args.simulate_window:
        _days = float(args.simulate_window[:-1]) if args.simulate_window[-1] in "dDwWhHmM" else float(args.simulate_window)
        d = Daemon(paper=True, cash=cfg.PAPER_BUDGET_USD, cycles=None,
                   verbose=args.verbose, live_key=live_key, live_secret=live_secret,
                   faith_tradition=args.faith_tradition)
        d.sim_mode = True
        d._load_historical(_days)
        d.run_sim(_days)
        return

    if args.export_html:
        from html_report import generate_html_report
        out_path = generate_html_report(state_path=PAPER_STATE_PATH,
                                        output_path=args.export_html)
        print(f"  HTML report written: {out_path}")
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

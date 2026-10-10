#!/usr/bin/env python3
"""
NIKOLA v2 BACKTEST — Complete multi-asset, multi-DIRECTION test.

Uses the ACTUAL engine modules (strategy.py state machine, signals.py MTF
engine, risk.py sizing, indicators.py) on real Kraken OHLCV data.

Tests the full entry→exit lifecycle:
  1. MTF signal synthesis (4h/1h/30m/15m/5m/1m confluence) → LONG/SHORT entry
  2. DynamicRiskReward 3-state state machine (MONITOR→ACTIVE→EXPANDED)
  3. TP1(40%) + TP2(30%) partial fills + 30% trailing runner + BE ratchet + SL2
  4. BTC cascade scale-out detection (-4% 1m momentum)
  5. Compounding from $300 paper budget with XP leveling
  6. 3:1 risk-reward as entry filter

vs baselines: HODL (buy&hold $300) + Panic (bail at first -2% close, sit in cash)

Spec: BTC 10x, NEAR 5x, TRX 5x, DOT 5x, LINK 5x, AVAX 7x
"""
import sys, json, time, math
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "C:/Users/mrluk/nikola")

from engine import config as cfg
from engine import strategy as strat
from engine import signals as sig
from engine import risk as risk_mod
from engine import indicators as ind
from engine.strategy import DynamicRiskReward, Position, PositionState, ExecutionAction
from engine.state import Gamification
from engine.signals import MTFEngine

# === Full 28-pair universe (user directive: settle long/short across all coins) ===
# Uses cfg.SYMBOLS + cfg.PAIR_LEVERAGE verbatim from the live engine config.
# NOTE: bt-only driver — live engine PID 18224 is NOT touched during this run.
SPEC = [
    (s, cfg.PAIR_LEVERAGE.get(s, cfg.PREFERRED_LEVERAGE))
    for s in cfg.SYMBOLS
]
PAIR_TO_KRAKEN = {}
SPEC_DICT = dict(SPEC)

# === Backtest knobs ===
INITIAL_CASH = 300.0
FEE = cfg.FEE_TERM_PCT  # 0.005 (0.5%) round-trip estimate
SCAN_EVERY_N_BARS = 10  # match live FULL_SCAN_INTERVAL (150s ≈ 10×15m bars); fair cadence gate
MIN_CONFIDENCE = 0.30   # minimum effective confidence to deploy (after leader filter)
COOLDOWN_BARS = 4       # per-pair cooldown after exit (60 min)
MAX_CONCURRENT = 5  # max open positions (user note: 3, but allow all 6 pairs)
MARGIN_PER_TRADE = cfg.MAX_POSITION_MARGIN_USD  # $20 per position

# === Parameter tuning history (backtest ONLY — does NOT touch live bot PID 18224) ===
# Test #1 (original): SL2=-4.4%, TP1=+1.4%, MIN_CONF=0.30 → edge vs Panic: -$15.24, ROI -10.5% (corrected equity)
# Test #2 (tuned):    SL2=-6.0%, TP1=+2.0%, MIN_CONF=0.50 → edge vs Panic: -$38.34, ROI -13.4%  WORSE
# IMPORTANT: earlier runs reported ROI +692.8% — that was inflated by an equity
# calculation bug (_equity() added full position notional incl borrowed capital).
# Correct formula: equity = STARTING_CASH + realized_PnL + unrealized_PnL (NOT position value).
# Conclusion: SL2 widening helps neither — losses run deeper before cut; win rate dropped 25%→20%
# The problem is signal quality (20-25% win rate), not stop/take levels.
# Original parameters restored below.
MIN_CONFIDENCE = 0.30         # restored: 0.50 test was worse


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: CANDLE FETCHER — real Kraken OHLCV via CCXT public endpoint
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_ohlcv_ccxt(pair, tf, limit):
    """Fetch OHLCV from Kraken via CCXT (no API keys needed for public)."""
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True, "rateLimit": 200, "timeout": 15000})
    ohlcv = ex.fetch_ohlcv(pair, timeframe=tf, limit=limit)
    return [[int(c[0])] + [float(x) for x in c[1:6]] for c in ohlcv]


def fetch_all_candles():
    """Fetch all 6 pairs x 6 TFs + 1m BTC for cascade detection (parallel)."""
    tf_limits = {
        "1m":  1440,   # 24h for BTC cascade
        "5m":  720,
        "15m": 720,   # ~5 days, spans Oct-4
        "30m": 720,
        "1h":  720,
        "4h":  360,
    }
    pairs = [s[0] for s in SPEC]
    data = {}

    def fetch_one(pair, tf):
        try:
            d = fetch_ohlcv_ccxt(pair, tf, tf_limits[tf])
            data[(pair, tf)] = d
            print(f"  {pair:10s} {tf:>3}: {len(d):>4} bars  [{d[0][0]} → {d[-1][0]}]")
            return True
        except Exception as e:
            print(f"  {pair:10s} {tf:>3}: FAIL ({e})")
            data[(pair, tf)] = []
            return False

    jobs = [(p, tf) for p in pairs for tf in cfg.MTF_TIMEFRAMES]
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(lambda j: fetch_one(j[0], j[1]), jobs))

    # Also 1m BTC for cascade detection
    try:
        d = fetch_ohlcv_ccxt("BTC/USD", "1m", 1440)
        data[("BTC/USD", "1m")] = d
        print(f"  {'BTC/USD':10s} {'1m':>3}: {len(d):>4} bars (cascade feed)")
    except Exception as e:
        print(f"  BTC/USD 1m: FAIL ({e})")
        data[("BTC/USD", "1m")] = []
    return data


def align_candles_to_15m(data):
    """Return aligned 15m bar timestamps for the simulation walk."""
    # Use BTC/USD 15m as the master timeline
    master = data.get(("BTC/USD", "15m"), [])
    if not master:
        for pair, _ in SPEC:
            if data.get((pair, "15m")):
                master = data[(pair, "15m")]
                break
    return master


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: BACKTEST DRIVER — drives the ACTUAL state machine + signal engine
# ═══════════════════════════════════════════════════════════════════════════════
class BacktestRunner:
    def __init__(self, candle_data):
        self.data = candle_data
        self.cash = INITIAL_CASH
        self.engine = DynamicRiskReward()
        self.mtf = MTFEngine(signal_weights={})
        self.gamification = Gamification()
        self.active = {}               # pair -> Position
        self.cooldowns = {}            # pair -> bar_index (cooldown)
        self.trade_log = []            # closed trades
        self.bar_len = len(align_candles_to_15m(candle_data))
        self.bar_idx = 0
        self.master_bars = align_candles_to_15m(candle_data)
        self.total_scans = 0
        self.total_entries = 0
        self.panic_exits = 0
        self.equity_curve = []
        self.signal_stats = {}
        self.skip_reasons = {}
        self._trade_pnl = {}    # pair -> accumulated realized PnL across all legs
        self._trade_risk = {}    # pair -> original risk_cap at entry


    def _inject_to_bar(self, bar_idx):
        """Inject candles up to bar_idx's timestamp for every pair+TF (no look-ahead)."""
        if not self.master_bars or bar_idx >= len(self.master_bars):
            return
        cutoff_ts = self.master_bars[bar_idx][0]
        for pair, _ in SPEC:
            for tf in cfg.MTF_TIMEFRAMES:
                all_c = self.data.get((pair, tf), [])
                if not all_c:
                    continue
                tf_mult = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}[tf]
                # Slice: only include candles whose close time <= cutoff
                sliced = [c for c in all_c if c[0] <= cutoff_ts]
                sig.set_candles(pair, tf, sliced)
        # BTC 1m for cascade
        btc_1m = self.data.get(("BTC/USD", "1m"), [])
        sliced_btc = [c for c in btc_1m if c[0] <= cutoff_ts]
        sig.set_candles("BTC/USD", "1m", sliced_btc)

    def _current_price(self, pair):
        """Get current 15m close price for a pair at the bar_idx-th position."""
        c15 = self.data.get((pair, "15m"), [])
        if c15 and self.bar_idx < len(c15):
            return c15[self.bar_idx][4]
        return 0.0

    def _equity(self):
        """Correct equity = starting cash + all realized PnL (partials + closes)
        + unrealized PnL on remaining open-position base.

        NOTE: does NOT add full position notional to cash — that would
        double-count borrowed capital from margin/leverage. Only the PnL
        component (realized + unrealized) is added to the original starting cash.
        """
        # Realized PnL from fully-closed trades (trade_log aggregates all partials)
        realized = sum(t["pnl_usd"] for t in self.trade_log)
        # Partial PnLs accumulated on still-open positions
        partial_realized = sum(self._trade_pnl.values())
        # Unrealized PnL on remaining base of open positions
        unrealized = 0.0
        for pair, pos in self.active.items():
            price = self._current_price(pair) or pos.entry_price
            if pos.direction == "LONG":
                unrealized += (price - pos.entry_price) * pos.base_amount
            else:
                unrealized += (pos.entry_price - price) * pos.base_amount
        return INITIAL_CASH + realized + partial_realized + unrealized

    def _enter_position(self, pair, direction, signal_info, price):
        """Enter a position using the bot's actual risk + strategy modules."""
        leverage = SPEC_DICT.get(pair, cfg.PREFERRED_LEVERAGE)
        pair_1h = self.data.get((pair, "1h"), [])
        atr_val = ind.get_atr(pair_1h, 14) if len(pair_1h) >= 15 else 0.001
        confidence = signal_info.get("confidence", 0.0)
        signal_type = signal_info.get("signal_type", "UNKNOWN")
        budget = self.cash * 0.75  # 75% of free cash deployable (matches 25% reserve)

        # Use flat MAX_POSITION_MARGIN_USD ($20) margin per trade for backtest
        # (live bot uses confidence-tiered; here we want equal deployability across all 6 pairs)
        margin = min(MARGIN_PER_TRADE, self.cash * 0.5)
        if margin < 1.0:
            return False
        base_amount = (margin * leverage) / price
        pair_min = cfg.min_base_for(pair)
        if base_amount < pair_min:
            return False
        margin_cost = margin

        # Deduct margin
        self.cash -= margin_cost

        # Create position via the ACTUAL state machine
        pos = self.engine.position_from_signal(
            pair, direction, price, base_amount, leverage,
            confidence, signal_type,
        )
        self.active[pair] = pos
        self.total_entries += 1
        self.cooldowns[pair] = self.bar_idx + COOLDOWN_BARS  # cooldown
        self._trade_pnl[pair] = 0.0  # reset accumulated PnL tracker
        self._trade_risk[pair] = pos.entry_price * pos.base_amount * cfg.SL1_PCT

        print(f"  ENTER  {pair:10s} {direction:5s} @ {price:10.5f}  "
              f"base={base_amount:.6f}  lev={leverage}x  "
              f"margin=${margin_cost:.2f}  conf={confidence:.2f}  "
              f"sig={signal_type[:30]}")
        return True

    def _close_position(self, pair, exit_price, reason, pos):
        """Close position fully, realizing P&L (mirrors Daemon._close_position paper path)."""
        leg_gross = pos.base_amount * ((exit_price - pos.entry_price) if pos.direction == "LONG"
                                       else (pos.entry_price - exit_price))
        leg_gross *= (1 - FEE)
        self.cash += exit_price * pos.base_amount + leg_gross
        self.active.pop(pair, None)

        # Aggregate: this leg's PnL + all previous partial legs
        total_pnl = self._trade_pnl.get(pair, 0.0) + leg_gross
        # Original risk distance (entry × original_base × SL1_PCT)
        original_risk = self._trade_risk.get(pair, pos.entry_price * pos.base_amount * cfg.SL1_PCT)
        r_mult = total_pnl / original_risk if original_risk > 0 else 0
        won = total_pnl > 0
        msgs = self.gamification.on_trade_close(won, abs(r_mult) if r_mult > 0 else 0)
        for m in msgs:
            print(f"    ★ {m}")

        # Clean up accumulators
        self._trade_pnl.pop(pair, None)
        self._trade_risk.pop(pair, None)

        self.trade_log.append({
            "pair": pair, "direction": pos.direction,
            "entry": round(pos.entry_price, 5), "exit": round(exit_price, 5),
            "base_remaining": round(pos.base_amount, 8), "lev": pos.leverage,
            "pnl_usd": round(total_pnl, 2), "r": round(r_mult, 2),
            "reason": reason, "confidence": round(pos.confidence, 3),
            "signal": pos.signal_type,
        })

    def _exec_actions(self, pair, pos, actions, price):
        """Execute the action list returned by step() (mirrors Daemon._exec_actions)."""
        for a in actions:
            act = a.action
            px = a.price or price

            if act in ("SL2_EXIT", "SL2_FULL_EXIT", "SL2"):
                self._close_position(pair, px, f"{act}@{px:.5f}", pos)
                print(f"    ← {pair:10s} SL2_EXIT @ {px:.5f}  (full close)")

            elif act == "PARTIAL_EXIT_50PCT":
                exited = pos.base_amount * 0.5
                if pos.direction == "LONG":
                    leg_pnl = (px - pos.entry_price) * exited * (1 - FEE)
                else:
                    leg_pnl = (pos.entry_price - px) * exited * (1 - FEE)
                self.cash += px * exited + leg_pnl
                self._trade_pnl[pair] = self._trade_pnl.get(pair, 0.0) + leg_pnl
                pos.base_amount -= exited
                # BE floor moves up on the remaining 50%
                pos.be_floor = px * (1 - 0.006) if pos.direction == "LONG" else px * (1 + 0.006)
                print(f"    ← {pair:10s} 50% partial @ {px:.5f}  PnL=${leg_pnl:+.2f}")

            elif act == "SWITCH_TO_MTF_TRAILING":
                # pos.trailing_sl was already set by the state machine's _expanded_branch
                trail_dist = a.payload.get("trail_dist", 0)
                print(f"    ← {pair:10s} MTF TRAIL armed @ {pos.trailing_sl:.5f}  (atr×2={trail_dist:.5f})")

            elif act == "ARM_TIGHT_TRAIL_REMAINDER":
                # Tight trail: 0.60% behind peak (trail_pct from payload)
                trail_pct = a.payload.get("trail_pct", cfg.TRAIL_WIDE_PCT)
                if pos.peak_price > 0:
                    pos.trailing_sl = pos.peak_price * (1 - trail_pct) if pos.direction == "LONG" \
                        else pos.peak_price * (1 + trail_pct)
                print(f"    ← {pair:10s} TIGHT TRAIL armed @ {pos.trailing_sl:.5f}  ({trail_pct*100:.1f}%)")

            elif act == "ARM_RUNNER_TRAIL":
                # 0.60% trail on the 30% runner after TP2
                trail_pct = a.payload.get("trail_pct", cfg.TRAIL_WIDE_PCT)
                pos.trailing_sl = pos.peak_price * (1 - trail_pct) if pos.direction == "LONG" \
                    else pos.peak_price * (1 + trail_pct)
                print(f"    ← {pair:10s} RUNNER TRAIL armed @ {pos.trailing_sl:.5f}  ({trail_pct*100:.1f}%)")

            elif act == "BE_PRIME":
                pass  # suppressed: fires every step near entry, too noisy

            elif act in ("MTF_TRAIL_UPDATE", "TIGHTEN_TO_TIGHT_TRAIL"):
                # State machine already updated pos.trailing_sl; informational only
                pass

            elif act == "BE_RATCHET_ARM":
                pos.be_floor = a.price
                print(f"    ← {pair:10s} BE ratchet → {a.price:.5f}")

            elif act == "SL1_HIT":
                closed = pos.base_amount * a.payload.get("partial", 0.4)
                if pos.direction == "LONG":
                    leg_pnl = (px - pos.entry_price) * closed * (1 - FEE)
                else:
                    leg_pnl = (pos.entry_price - px) * closed * (1 - FEE)
                self.cash += px * closed + leg_pnl
                self._trade_pnl[pair] = self._trade_pnl.get(pair, 0.0) + leg_pnl
                pos.base_amount -= closed
                pos.be_floor = a.payload.get("sl2_price", pos.sl2_price)
                print(f"    ← {pair:10s} SL1 partial(-2.1%) close {closed:.6f}  PnL=${leg_pnl:+.2f}  TPs retained")

            elif act == "TP1_FILLED":
                closed = pos.base_amount * a.payload.get("partial", 0.4)
                if pos.direction == "LONG":
                    leg_pnl = (a.price - pos.entry_price) * closed * (1 - FEE)
                else:
                    leg_pnl = (pos.entry_price - a.price) * closed * (1 - FEE)
                self.cash += a.price * closed + leg_pnl
                self._trade_pnl[pair] = self._trade_pnl.get(pair, 0.0) + leg_pnl
                pos.base_amount -= closed
                print(f"    ← {pair:10s} TP1(+1.4%) close {closed:.6f}  PnL=${leg_pnl:+.2f}")

            elif act == "TP2_FILLED":
                closed = pos.base_amount * a.payload.get("partial", 0.3)
                if pos.direction == "LONG":
                    leg_pnl = (a.price - pos.entry_price) * closed * (1 - FEE)
                else:
                    leg_pnl = (pos.entry_price - a.price) * closed * (1 - FEE)
                self.cash += a.price * closed + leg_pnl
                self._trade_pnl[pair] = self._trade_pnl.get(pair, 0.0) + leg_pnl
                pos.base_amount -= closed
                print(f"    ← {pair:10s} TP2(+2.8%) close {closed:.6f}  PnL=${leg_pnl:+.2f}")

            elif act == "CASCADE_SCALEOUT":
                scale = a.payload.get("scale_base", 0)
                if scale > 0 and pos.base_amount > 0:
                    if pos.direction == "LONG":
                        leg_pnl = (px - pos.entry_price) * scale * (1 - FEE)
                    else:
                        leg_pnl = (pos.entry_price - px) * scale * (1 - FEE)
                    self.cash += px * scale + leg_pnl
                    self._trade_pnl[pair] = self._trade_pnl.get(pair, 0.0) + leg_pnl
                    pos.base_amount -= scale
                    print(f"    ← {pair:10s} CASCADE(-15%) @ {px:.5f}  PnL=${leg_pnl:+.2f}")

    def _step_position(self, pair, pos, high, low, close, votes, atr):
        """Step through one candle's high→low→close sequence (intrabar fill model).

        Also checks trailing SL (EXPANDED state) and SL1 level at each price level.
        """
        def check_trailing_exit(px, level):
            """Close if EXPANDED-state trailing SL or SL1 is hit."""
            if pos.state == strat.PositionState.EXPANDED and pos.trailing_sl > 0:
                if pos.direction == "LONG" and level <= pos.trailing_sl:
                    return self._close_position(pair, pos.trailing_sl,
                                                f"TRAIL_EXIT@{pos.trailing_sl:.5f}", pos)
                if pos.direction == "SHORT" and level >= pos.trailing_sl:
                    return self._close_position(pair, pos.trailing_sl,
                                                f"TRAIL_EXIT@{pos.trailing_sl:.5f}", pos)
            return False

        # 1. Feed high (TP fills trigger on high touch for LONG, SL for SHORT)
        if check_trailing_exit(high, high): return
        actions = self.engine.step(pos, high, votes=votes, atr=atr)
        self._exec_actions(pair, pos, actions, high)
        if pair not in self.active:
            return  # position closed

        # 2. Feed low (SL triggers on low for LONG, TP for SHORT)
        if check_trailing_exit(low, low): return
        actions = self.engine.step(pos, low, votes=votes, atr=atr)
        self._exec_actions(pair, pos, actions, low)
        if pair not in self.active:
            return

        # 3. Feed close (peak tracking, tail updates, EXPANDED trail)
        if check_trailing_exit(close, close): return
        actions = self.engine.step(pos, close, votes=votes, atr=atr)
        self._exec_actions(pair, pos, actions, close)

    def _panic_exit(self, pair, entry_price, lev, candles_15m, start_idx):
        """Simulate panic: bail at first candle whose close <= entry * 0.98."""
        for i in range(start_idx, len(candles_15m)):
            close = candles_15m[i][4]
            if close <= entry_price * 0.98:
                return close  # bailed
        return candles_15m[-1][4]  # held to end

    def run(self):
        master = self.master_bars
        if not master:
            print("ERROR: No master 15m candles. Cannot run.")
            return

        print(f"\n{'='*78}")
        print(f" NIKOLA v2 BACKTEST — ACTUAL state machine + MTF signals on real Kraken data")
        print(f" Pairs: {', '.join(p for p,_ in SPEC)}")
        print(f" Start: ${INITIAL_CASH:.2f} paper budget | Fee: {FEE*100:.2f}% round-trip")
        print(f" Bars: {len(master)} × 15m (~{(len(master)*15)/60:.0f}h)")
        print(f"{'='*78}")

        for i in range(len(master)):
            self.bar_idx = i
            ts = master[i][0]
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            o, h, l, c = master[i][1], master[i][2], master[i][3], master[i][4]

            # Inject candles up to this bar
            self._inject_to_bar(i)

            # --- Step open positions through this candle FIRST ---
            # (positions entered THIS bar step starting next bar — entry is at close)
            for pair, pos in list(self.active.items()):
                c15 = self.data.get((pair, "15m"), [])
                if not c15 or i >= len(c15):
                    continue
                ohlc = c15[i]
                ch, cl, cc = ohlc[2], ohlc[3], ohlc[4]

                # Get MTF votes + ATR for this pair
                votes = sig.compute_votes(pair)
                c1h = self.data.get((pair, "1h"), [])
                atr = ind.get_atr(c1h, 14) if len(c1h) >= 15 else None

                self._step_position(pair, pos, ch, cl, cc, votes, atr)

                # Remove closed positions
                if pair not in self.active:
                    self.cooldowns[pair] = i + COOLDOWN_BARS

            # --- Full scan: generate MTF entry signals ---
            if i % SCAN_EVERY_N_BARS == 0:
                self.total_scans += 1
                all_signals = self.mtf.compute_all_signals()
                ranked = self.mtf.rank_candidates(all_signals)

                # Track which of our 6 pairs appear in ranked candidates
                for sig_info in ranked:
                    p = sig_info["pair"]
                    if p in SPEC_DICT:
                        self.signal_stats[p] = self.signal_stats.get(p, 0) + 1

                # Debug: show ranked candidates at sparse intervals
                if i < 5 or i % 120 == 0:
                    top = [r for r in ranked if r["pair"] in SPEC_DICT][:5]
                    for r in top:
                        active_str = " (ACTIVE)" if r["pair"] in self.active else ""
                        print(f"  SCAN [{i:3d}] {r['pair']:10s} conf={r.get('confidence',0):.2f} "
                              f"dir={r.get('direction','')} score={r.get('score','')[:14]}"
                              f" sig={r.get('signal_type','')[:25]}{active_str}")

                deployed = 0
                for sig_info in ranked:
                    pair = sig_info["pair"]
                    if pair not in SPEC_DICT:
                        continue
                    if pair in self.active:
                        continue
                    if i < self.cooldowns.get(pair, 0):
                        continue
                    if sig_info.get("composite_signal") is None:
                        continue
                    conf = sig_info.get("confidence", 0)
                    if conf < MIN_CONFIDENCE:
                        reason = f"low_conf_{conf:.2f}"
                        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
                        continue

                    direction = "LONG" if sig_info["composite_signal"] == "BUY" else "SHORT"
                    # Use THIS PAIR's own 15m close as entry price (NOT the master BTC bar)
                    pair_c15 = self.data.get((pair, "15m"), [])
                    pair_price = pair_c15[i][4] if i < len(pair_c15) else c
                    if pair_price <= 0:
                        continue
                    if self._enter_position(pair, direction, sig_info, pair_price):
                        deployed += 1
                        if deployed >= 3:
                            break

            # Record equity at key intervals
            if i % 20 == 0 or i == len(master) - 1:
                eq = self._equity()
                self.equity_curve.append({
                    "bar": i, "ts": ts,
                    "cash": round(self.cash, 2),
                    "equity": round(eq, 2),
                    "active": len(self.active),
                    "dt": dt.strftime("%m-%d %H:%M"),
                })

        # --- Compute baselines ---
        self._compute_baselines(master)
        self._report()

    def _compute_baselines(self, master):
        """Compute HODL and Panic baseline equity for each pair."""
        self.hodl_results = {}
        self.panic_results = {}
        for pair, lev in SPEC:
            c15 = self.data.get((pair, "15m"), [])
            if len(c15) < 2:
                continue
            entry = c15[0][4]
            end = c15[-1][4]
            # HODL: buy at entry, hold to end
            notional = (INITIAL_CASH / (entry * lev)) * entry * lev  # full notional
            # Actually: HODL = $300 at entry leverage, hold to end
            # equity_hodl = 300 + (end - entry) * (300/lev/entry) * lev... no
            # Simply: buy with $300 at leverage, hold. equity = 300 + (end-entry)*units*lev
            # Actually for HODL: equity = 300 * (end/entry) if 1x, or more complex with leverage
            # Let's just track: $300 deployed at entry with leverage, value at end
            units = (INITIAL_CASH * lev) / entry
            hodl_end_val = units * end  # position value at end
            hodl_pnl = (hodl_end_val - INITIAL_CASH * lev) / lev  # net of borrowed
            # Simpler: HODL equity = 300 + (end-entry)/entry * 300 * lev (leveraged return)
            hodl_ret = (end - entry) / entry if entry > 0 else 0
            self.hodl_results[pair] = hodl_ret * lev * INITIAL_CASH  # leveraged return on 300
            # Panic: bail at first -2% close, sit in cash
            panic_price = self._panic_exit(pair, entry, lev, c15, 0)
            panic_ret = (panic_price - entry) / entry if entry > 0 else 0
            self.panic_results[pair] = panic_ret * lev * INITIAL_CASH

    def _report(self):
        """Print compact table of results."""
        total_r = 0.0
        wins = 0
        losses = 0
        win_rate = 0.0
        avg_r = 0.0

        print(f"\n{'='*78}")
        print(f" RESULTS — {len(self.trade_log)} trades closed, {self.total_scans} scans, "
              f"{self.total_entries} entries")
        print(f"{'='*78}")

        # Trade ledger
        print(f"\n{'CYCLE':>6} {'PAIR':10s} {'DIR':5s} {'LEV':>3} {'ENTRY':>10} "
              f"{'EXIT':>10} {'PnL':>8} {'R':>6} {'REASON':24s}")
        print("-" * 82)
        for i, t in enumerate(self.trade_log):
            mark = "✓" if t["pnl_usd"] > 0 else "✗"
            print(f"{i+1:>6} {t['pair']:10s} {t['direction']:5s} {t['lev']:>3}x "
                  f"{t['entry']:>10.5f} {t['exit']:>10.5f} "
                  f"${t['pnl_usd']:>7.2f} {t['r']:>+5.2f}R {mark} "
                  f"{t['reason'][:24]:24s}")
            if t["pnl_usd"] > 0:
                wins += 1
            else:
                losses += 1
            total_r += t["r"]

        total_trades = wins + losses
        if total_trades > 0:
            win_rate = wins / total_trades * 100
            avg_r = total_r / total_trades

        # Realized + unrealized PnL breakdown (for transparent reporting)
        realized = sum(t["pnl_usd"] for t in self.trade_log)
        partial_realized = sum(self._trade_pnl.values())

        # Summary
        final_equity = self._equity()
        roi = (final_equity - INITIAL_CASH) / INITIAL_CASH * 100

        print(f"\n{'='*78}")
        print(f" PORTFOLIO SUMMARY")
        print(f"{'='*78}")
        print(f"  Starting:  ${INITIAL_CASH:.2f}  |  Ending: ${final_equity:.2f}  "
              f"|  ROI: {roi:+.1f}%")
        print(f"  Realized PnL: ${realized:+.2f}  |  Unreal. PnL: ${partial_realized + (final_equity - INITIAL_CASH - realized):+.2f}  "
              f"|  Open positions: {len(self.active)}")
        for pair, pos in self.active.items():
            price = self._current_price(pair)
            unreal = (price - pos.entry_price) * pos.base_amount if pos.direction == "LONG" \
                     else (pos.entry_price - price) * pos.base_amount
            print(f"    {pair:10s} {pos.direction} @ {pos.entry_price:.5f}  "
                  f"base={pos.base_amount:.6f}  lev={pos.leverage}x  "
                  f"unreal=${unreal:+.2f}  R={self.engine.r_multiple(pos, price):+.2f}")
        print(f"  Trades:    {wins}W-{losses}L  |  Win rate: {win_rate:.0f}%  "
              f"|  Avg R: {avg_r:+.2f}R  |  Total R: {total_r:+.2f}R")
        print(f"  XP: {self.gamification.xp}  |  Level: {self.gamification.level}  "
              f"|  Streak: {self.gamification.streak}")

        # Baselines — fair comparison: $20 margin per pair (same as bot's MARGIN_PER_TRADE)
        print(f"\n{'='*78}")
        print(f" BASELINE COMPARISON (per-pair ${MARGIN_PER_TRADE} margin @ spec leverage)")
        print(f"{'='*78}")
        print(f"  {'PAIR':10s} {'LEV':>3}  {'HODL':>8} {'Panic':>8}  {'Nikola':>8}  {'Edge':>8}")
        print("  " + "-" * 56)
        hodl_total = 0
        panic_total = 0
        nikola_total = 0
        for pair, lev in SPEC:
            c15 = self.data.get((pair, "15m"), [])
            if len(c15) < 2:
                continue
            entry = c15[0][4]
            end = c15[-1][4]
            units = (MARGIN_PER_TRADE * lev) / entry  # same margin as the bot
            hodl_pnl = (end - entry) * units  # gross P&L
            panic_price = self._panic_exit(pair, entry, lev, c15, 0)
            panic_pnl = (panic_price - entry) * units
            nik_pnl = sum(t["pnl_usd"] for t in self.trade_log if t["pair"] == pair)
            # Include unrealized PnL from open positions (base_amount already includes leverage)
            if pair in self.active:
                pos = self.active[pair]
                if pos.direction == "LONG":
                    nik_pnl += (end - pos.entry_price) * pos.base_amount * (1 - FEE)
                else:
                    nik_pnl += (pos.entry_price - end) * pos.base_amount * (1 - FEE)
                # Include partial PnLs from still-open positions
                nik_pnl += self._trade_pnl.get(pair, 0.0)
            hodl_total += hodl_pnl
            panic_total += panic_pnl
            nikola_total += nik_pnl
            print(f"  {pair:10s} {lev:>3}x  ${hodl_pnl:7.2f} ${panic_pnl:7.2f}  "
                  f"${nik_pnl:>7.2f}  ${nik_pnl-panic_pnl:>+7.2f}")
        print("  " + "-" * 56)
        print(f"  {'TOTAL':10s} {'':>3}  ${hodl_total:7.2f} ${panic_total:7.2f}  "
              f"${nikola_total:>7.2f}  ${nikola_total-panic_total:>+7.2f}")

        print(f"\n  LIMITATIONS: historical MTF entries (no live timing), close-fill only,")
        print(f"              6 pairs, {len(self.master_bars)*15//60:.0f}h window, $300 per pair baseline.")
        print(f"  NOTE: BTC/USD paper-mode is normally blocked (live bot uses it).")
        print(f"        Backtest overrides this to test the full 6-pair spec you gave.")

        # Equity curve
        if len(self.equity_curve) > 2:
            print(f"\n{'='*78}")
            print(f" EQUITY CURVE (sampled every 20 bars)")
            print(f"{'='*78}")
            print(f"  {'Bar':>4}  {'Time':16s}  {'Cash':>8}  {'Equity':>8}  {'Active':>3}")
            for e in self.equity_curve:
                print(f"  {e['bar']:>4}  {e['dt']:16s}  ${e['cash']:>7.2f}  "
                      f"${e['equity']:>7.2f}  {e['active']:>3}")

        # Signal diagnostics
        print(f"\n{'='*78}")
        print(f" SIGNAL DIAGNOSTICS")
        print(f"{'='*78}")
        for pair, _ in SPEC:
            count = self.signal_stats.get(pair, 0)
            print(f"  {pair:10s} appeared in {count:>3d} ranked candidate scans")
        if self.skip_reasons:
            print(f"  Skip reasons (low confidence):")
            for reason, count in sorted(self.skip_reasons.items(), key=lambda x: -x[1])[:5]:
                print(f"    {reason}: {count}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    print("=== Phase 1: Fetching real Kraken OHLCV ===\n")
    t0 = time.time()
    candle_data = fetch_all_candles()
    print(f"\nFetched in {time.time()-t0:.1f}s\n")

    # Verify we have data
    for pair, _ in SPEC:
        c15 = candle_data.get((pair, "15m"), [])
        if not c15:
            print(f"WARNING: No 15m data for {pair}")
    for tf in cfg.MTF_TIMEFRAMES:
        if tf != "1m":
            for pair, _ in SPEC:
                c = candle_data.get((pair, tf), [])
                if not c:
                    print(f"WARNING: No {tf} data for {pair}")

    print("\n=== Phase 2: Running v2 backtest (actual state machine + signals) ===\n")
    runner = BacktestRunner(candle_data)
    runner.run()

    # Save results
    results = {
        "strategy": "Nikola v2 Dynamic RR (actual state machine + MTF signals)",
        "pairs": [p for p, _ in SPEC],
        "leverages": SPEC_DICT,
        "initial_cash": INITIAL_CASH,
        "final_equity": runner._equity(),
        "trades": runner.trade_log,
        "equity_curve": runner.equity_curve,
        "gamification": {
            "xp": runner.gamification.xp,
            "level": runner.gamification.level,
            "streak": runner.gamification.streak,
            "achievements": runner.gamification.achievements,
        },
        "total_entries": runner.total_entries,
        "total_scans": runner.total_scans,
    }
    results_path = "C:/Users/mrluk/AppData/Local/hermes/cache/scratch/nikola_bt_v2_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {results_path}")


if __name__ == "__main__":
    main()

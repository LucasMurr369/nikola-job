"""
Nikola engine configuration.

Faithful port of the proven parameter set from the live margin bot
(kraken_bot/live_margin_bot.py, lines 41-258). These are the *exact* constants
that survived the TP/SL reconfigure and are currently live on Kraken — ported
verbatim so Nikola and the bot share an apples-to-apples risk profile.

The live bot is the source of truth; this module mirrors it so Nikola can be
validated against the bot's real P&L without touching its running process.
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
PAPER_STATE_FILE = DATA_DIR / "paper_state.json"

# ---------------------------------------------------------------------------
# Pair universe — 28-pair Kraken BTNL margin universe (user directive)
# ---------------------------------------------------------------------------
SYMBOLS = [
    "AAVE/USD", "ADA/USD", "ALGO/USD", "AVAX/USD", "BCH/USD", "BTC/USD",
    "CRV/USD", "DOGE/USD", "DOT/USD", "ETH/USD", "HBAR/USD", "HYPE/USD",
    "LINK/USD", "LTC/USD", "NEAR/USD", "PAXG/USD", "PENGU/USD", "PEPE/USD",
    "RENDER/USD", "SHIB/USD", "SOL/USD", "SUI/USD", "TRX/USD", "UNI/USD",
    "WLD/USD", "XLM/USD", "XRP/USD", "ZEC/USD",
]

# Per-pair Kraken maximum leverage (exceeding → "EOrder:Invalid leverage").
# NOTE: XDG→DOGE mapped in normalize_btln_symbol(); the bot marks non-Nikola
# pairs as FOREIGN and never touches them.
PAIR_LEVERAGE = {
    "AAVE/USD": 5,  "ADA/USD": 10,  "ALGO/USD": 5,  "AVAX/USD": 10, "BCH/USD": 5,
    "BTC/USD": 20,  "CRV/USD": 5,   "DOGE/USD": 10, "DOT/USD": 5,   "ETH/USD": 20,
    "HBAR/USD": 5,  "HYPE/USD": 5,  "LINK/USD": 10, "LTC/USD": 10,  "NEAR/USD": 5,
    "PAXG/USD": 5,  "PENGU/USD": 3, "PEPE/USD": 5,  "RENDER/USD": 5, "SHIB/USD": 5,
    "SOL/USD": 10,  "SUI/USD": 10,  "TRX/USD": 5,   "UNI/USD": 5,   "WLD/USD": 3,
    "XLM/USD": 5,   "XRP/USD": 10,  "ZEC/USD": 5,
}

# ---------------------------------------------------------------------------
# Multi-timeframe live refresh (user directive 2026-10-05, 1m added 2026-10-07)
# ---------------------------------------------------------------------------
MTF_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h"]
TF_ORDER = ["4h", "1h", "30m", "15m", "5m", "1m"]  # trend → entry
MTF_CANDLE_LIMIT = 200
MIN_TF_AGREE_DEPLOY = 2  # >=2 of 6 TFs must agree at deploy time

# ---------------------------------------------------------------------------
# Money management — $100 live budget, $300 paper clean-slate (Nikola.md)
# ---------------------------------------------------------------------------
PREFERRED_LEVERAGE = 5
LEVERAGE_FLOOR = 3
LEVERAGE_CAP = 10
LEV_UNLOCK_EQUITY_USD = 150.0       # BTC/ETH uncap to 10x after this equity
HIGH_LEV_PAIRS = ("BTC/USD", "ETH/USD")

MAX_POSITION_MARGIN_USD = 20.0
TOTAL_MARGIN_BUDGET_USD = 1_000_000.0
PAPER_BUDGET_USD = 300.0            # Nikola.md clean-state restart
MARGIN_RESERVE_PCT = 0.25           # keep 25% of budget un-deployed
REENTRY_COOLDOWN_SEC = 3600         # 1h per-pair cooldown
MAX_POSITION_LOSS_USD = 10.0        # paper-mode fixed $ loss per trade
FEE_TERM_PCT = 0.005                # round-trip fee estimate

# Conviction-tiered leverage + margin (mirrors each other)
LEV_TIERS = [(0.60, 10), (0.50, 7), (0.40, 5), (0.0, 3)]
MARGIN_TIERS = [(0.60, 20.0), (0.50, 15.0), (0.40, 10.0), (0.0, 5.0)]

# ---------------------------------------------------------------------------
# Two-stage stop-loss + TP ladder (user directive — current live config)
# ---------------------------------------------------------------------------
SL1_PCT = 0.0210          # -2.10% — partial close (40% of base), TPs retained
SL2_PCT = 0.0440          # -4.40% — full exit of remaining (2x SL1 distance)
STOP_LOSS_PCT = SL1_PCT   # backward-compat alias
TRAIL_STOP_PCT = 0.0030   # stage-1 trail 0.30%
TRAIL_WIDE_PCT = 0.0060   # stage-2 trail 0.60% @ +2.5%
TRAIL_WIDE_ARM_PCT = 0.025  # switch to wide trail once peak >= +2.5%
TRAIL_LOCK_RATIO = 0.5    # retained for legacy adjust_trailing_tp
# --- BTC cascade scale-out (live bot lines 2618-2661) ---
BTC_CASCADE_TRIGGER_PCT = 0.04    # BTC -4% 1m momentum triggers cascade
CASCADE_SCALEOUT_PCT = 0.15       # close 15% of base reduce-only before SL fires
TRAIL_TRIGGER_RATIO = 0.0  # trail kicks in once any profit exists
SL_LIMIT_BAND_PCT = 0.015  # stop-loss-limit slippage allowance
BREAKEVEN_BUFFER_PCT = 0.02  # BE floor = entry + 2.0% (ratchets after TP1+TP2)

# TP1 +1.4%/40% + TP2 +2.8%/30% (2× TP1 distance — user 2026-10-07)
# Final 30% rides the trailing stop.
TP_RUNG_PCTS = [
    ("TP1", 0.40, 0.014),   # 40% of base @ +1.4%
    ("TP2", 0.30, 0.028),   # 30% of base @ +2.8% = 2× TP1 distance (user 2026-10-07)
]                           # Final 30% rides the trailing stop.

# Bigger slower coins get closer targets (+1.0%/+2.0% books faster)
MAJOR_TP_PCTS = {"BTC/USD": (0.010, 0.020), "ETH/USD": (0.010, 0.020)}

# ---------------------------------------------------------------------------
# Balance caps — sweep protection (user 2026-10-06)
# ---------------------------------------------------------------------------
MEME_GROUP = {"DOGE/USD", "SHIB/USD", "PEPE/USD", "PENGU/USD", "WLD/USD"}
MAJOR_GROUP = {"BTC/USD", "ETH/USD", "PAXG/USD", "LTC/USD", "BCH/USD",
               "XRP/USD", "TRX/USD", "XLM/USD", "ADA/USD", "HBAR/USD"}
# MAX_*_POSITIONS = 0 → budget is the only gate (user 2026-10-07)
MAX_TOTAL_POSITIONS = 0
MAX_MEME_POSITIONS = 0
MAX_HIGH_BETA_POSITIONS = 0

# Three-leader model
COIN_GROUPS = {
    "BTC": ["BTC", "BCH", "LTC", "HBAR", "PAXG", "TRX", "XLM", "ALGO", "CRV", "ZEC"],
    "ETH": ["ETH", "ADA", "SOL", "AVAX", "DOT", "LINK", "UNI", "NEAR", "AAVE",
            "WLD", "RENDER", "SUI", "HYPE", "PENGU"],
    "XRP": ["XRP"],
}
LEADERS = ["BTC", "ETH", "XRP"]
MEME_COINS = ["DOGE", "SHIB", "PEPE"]
LEADER_DIR_THRESHOLD = 0.01  # 1h leader direction threshold

# ---------------------------------------------------------------------------
# Per-pair exchange minimums (Kraken public AssetPairs 'ordermin')
# ---------------------------------------------------------------------------
MIN_BASE_AMOUNT = 8.0  # fallback (DOGE not listed publicly)
PAIR_MIN_BASE = {
    "AAVE/USD": 0.05,     "ADA/USD": 20.0,      "ALGO/USD": 41.0,
    "AVAX/USD": 0.5,      "BCH/USD": 0.01,      "BTC/USD": 0.00005,
    "CRV/USD": 20.0,      "DOGE/USD": 8.0,      "DOT/USD": 3.9,
    "ETH/USD": 0.001,     "HBAR/USD": 55.0,     "HYPE/USD": 0.1,
    "LINK/USD": 0.55,     "LTC/USD": 0.1,       "NEAR/USD": 4.0,
    "PAXG/USD": 0.001,    "PENGU/USD": 700.0,   "PEPE/USD": 1500000.0,
    "RENDER/USD": 2.5,    "SHIB/USD": 770000.0, "SOL/USD": 0.06,
    "SUI/USD": 5.0,       "TRX/USD": 16.0,      "UNI/USD": 1.5,
    "WLD/USD": 11.0,      "XLM/USD": 30.0,      "XRP/USD": 1.65,
    "ZEC/USD": 0.01,
}

# Per-pair amount precision (decimal fix for all coins)
PAIR_AMT_PRECISION = {
    "AAVE/USD": 8, "ADA/USD": 8, "ALGO/USD": 8, "AVAX/USD": 8,
    "BCH/USD": 8, "BTC/USD": 8, "CRV/USD": 8, "DOGE/USD": 8,
    "DOT/USD": 8, "ETH/USD": 8, "HBAR/USD": 8, "HYPE/USD": 8,
    "LINK/USD": 8, "LTC/USD": 8, "NEAR/USD": 8, "PAXG/USD": 8,
    "PENGU/USD": 2, "PEPE/USD": 0, "RENDER/USD": 8, "SHIB/USD": 0,
    "SOL/USD": 8, "SUI/USD": 8, "TRX/USD": 8, "UNI/USD": 8,
    "WLD/USD": 8, "XLM/USD": 8, "XRP/USD": 8, "ZEC/USD": 8,
}

# ---------------------------------------------------------------------------
# Timing / loop cadence
# ---------------------------------------------------------------------------
LIGHT_LOOP_INTERVAL = 15     # seconds — light check (stop/TP monitoring)
FULL_SCAN_INTERVAL = 150     # seconds — full scan (MTF refresh + signal scan)
SWEEP_CHECK_TF = "15m"
STALE_TICKER_MAX_AGE_SEC = 900

# ---------------------------------------------------------------------------
# Self-learning (signal_weights.json ±0.05, clamped 0.5-1.5)
# ---------------------------------------------------------------------------
WEIGHT_MIN = 0.5
WEIGHT_MAX = 1.5
WEIGHT_ADJUST = 0.05

# ---------------------------------------------------------------------------
# Paper mode
# ---------------------------------------------------------------------------
PAPER_AUTO_EXECUTE = True
PAPER_TRADE_MAX_AGE_HOURS = 24

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DRY_RUN = True
FREE_MARGIN_MIN = 1.0
MIN_FREE_MARGIN_USD = 2.0
EQUITY_SCALE_MIN = 0.5
EQUITY_SCALE_MAX = 1.35
EQUITY_BASELINE_USD = 100.0
EQUITY_SCALE = 1.0


def leverage_for(confidence: float, pair: str | None = None, equity: float = 0.0) -> int:
    """Conviction-tiered leverage (3-10x), capped by Kraken's per-pair maximum.

    BTC/ETH capped at 7x until equity >= LEV_UNLOCK_EQUITY_USD.
    """
    lev = LEVERAGE_FLOOR
    tiers = list(LEV_TIERS)
    for floor, l in tiers:
        if confidence >= floor:
            lev = min(l, LEVERAGE_CAP)
            break
    if pair is not None:
        pair_cap = PAIR_LEVERAGE.get(pair, LEVERAGE_CAP)
        if pair in HIGH_LEV_PAIRS and equity < LEV_UNLOCK_EQUITY_USD:
            pair_cap = min(pair_cap, 7)
        lev = min(lev, pair_cap)
    return max(lev, 1)


def deployable_margin(budget: float = TOTAL_MARGIN_BUDGET_USD) -> float:
    """Deployable margin = budget × equity_scale × (1 − reserve_pct)."""
    return budget * EQUITY_SCALE * (1.0 - MARGIN_RESERVE_PCT)


def margin_tier_for(confidence: float) -> float:
    """Conviction-weighted margin: <$5/$10/$15/$20, capped at MAX_POSITION_MARGIN_USD."""
    for floor, amt in MARGIN_TIERS:
        if confidence >= floor:
            return min(amt * EQUITY_SCALE, MAX_POSITION_MARGIN_USD * EQUITY_SCALE)
    return MARGIN_TIERS[-1][1] * EQUITY_SCALE


def tp_rungs_for(pair: str | None = None) -> list[tuple[str, float, float]]:
    """Per-pair TP geometry: majors get closer targets, alts use the default ladder."""
    if pair and pair in MAJOR_TP_PCTS:
        t1, t2 = MAJOR_TP_PCTS[pair]
        return [("TP1", TP_RUNG_PCTS[0][1], t1),
                ("TP2", TP_RUNG_PCTS[1][1], t2)]
    return list(TP_RUNG_PCTS)


def min_base_for(pair: str) -> float:
    return PAIR_MIN_BASE.get(pair, MIN_BASE_AMOUNT)


def round_amount(pair: str, amount: float) -> float:
    """Round order amount to the correct per-coin decimal precision."""
    return round(amount, PAIR_AMT_PRECISION.get(pair, 8))


def group_of(pair: str) -> str:
    if pair in MEME_GROUP:
        return "MEME"
    if pair in MAJOR_GROUP:
        return "MAJOR"
    return "HIGH_BETA"


def normalize_btln_symbol(sym: str) -> str:
    """Convert Kraken-native BTNL symbol (NEARUSD:BTNL) to CCXT format (NEAR/USD:BTNL)."""
    if "/" in sym:
        return sym
    if ":BTNL" not in sym:
        return sym
    prefix = sym.replace(":BTNL", "")
    # XBT→BTC, XDG→DOGE mapping (FOREIGN-pair blocking depends on it)
    if prefix.startswith("XBT"):
        prefix = "BTC" + prefix[3:]
    if prefix.startswith("XDG"):
        prefix = "DOGE" + prefix[3:]
    for pair in SYMBOLS:
        if pair.replace("/", "") == prefix:
            return f"{pair}:BTNL"
    return sym

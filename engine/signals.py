"""
Nikola engine — Multi-TimeFrame signal engine.

Faithful port of the live margin bot's MTF signal layer (live_margin_bot.py
lines 290-745). The bot's approach:

  4h  → trend gate  (MACD histogram + RSI)
  1h  → momentum   (RSI > 50 & MACD > 0 → LONG)
  30m → = 1h vote
  15m → entry window (volume imbalance + RSI)   [soft +5% confidence boost]
  5m  → = 15m vote
  1m  → = 5m vote

``synthesize_signal`` computes a **directional_count / total_count** agreement
score (e.g. "6/6 STRONG") and a confidence 0.30–0.85+ weighted by learned
``signal_weights.json``. ``filter_by_leader`` applies the three-leader-model
filter (BTC/ETH/XRP leader direction + BTC 1m cascade guard).

A module-level candle cache (``_CANDLES``) lets the engine run from in-memory
data (tests / mock) or from on-disk JSON files (production, populated by the
exchange refresh layer).
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional, Dict, List, Any, Tuple

from . import config as cfg
from . import indicators as ind
from .config import (
    SYMBOLS, MTF_TIMEFRAMES, TF_ORDER, MTF_CANDLE_LIMIT,
    LEADER_DIR_THRESHOLD, MEME_COINS, COIN_GROUPS, LEADERS,
    WEIGHT_MIN, WEIGHT_MAX,
)


# ---------------------------------------------------------------------------
# Candle store — in-memory cache that falls back to on-disk JSON
# ---------------------------------------------------------------------------
_CANDLES: Dict[Tuple[str, str], List[List]] = {}


def load_candles(pair: str, tf: str) -> List[List]:
    """Load [ts, o, h, l, c, v] candles for a pair/timeframe.

    Checks the in-memory cache first (populated by ``set_candles`` or the
    exchange refresh layer), then falls back to ``data/{pair_safe}_{tf}.json``.
    """
    key = (pair, tf)
    if key in _CANDLES:
        return _CANDLES[key]
    safe = pair.replace("/", "_")
    fn = cfg.DATA_DIR / f"{safe}_{tf}.json"
    if not fn.exists():
        return []
    try:
        with open(fn) as f:
            data = json.load(f)
        if isinstance(data, list):
            _CANDLES[key] = data
            return data
        return []
    except Exception:
        return []


def set_candles(pair: str, tf: str, candles: List[List]) -> None:
    """Inject candles for testing / mock operation."""
    _CANDLES[(pair, tf)] = candles


def clear_cache() -> None:
    """Clear the in-memory candle cache (forces file reload)."""
    _CANDLES.clear()


# ---------------------------------------------------------------------------
# Leader + meme helpers
# ---------------------------------------------------------------------------
def get_leader_direction(pair: str) -> Optional[str]:
    """Return leader direction (UP/DOWN/SIDEWAYS) from the leader's 1h candles."""
    base = pair.split("/")[0]
    leader = None
    for grp, coins in COIN_GROUPS.items():
        if base in coins:
            leader = grp
            break
    if leader is None:
        return None
    leader_pair = f"{leader}/USD"
    candles = load_candles(leader_pair, "1h")
    if len(candles) < 13:
        return None
    recent = float(candles[-1][4])
    prior = float(candles[-13][4])
    change = (recent - prior) / prior if prior != 0 else 0
    if change > LEADER_DIR_THRESHOLD:
        return "UP"
    elif change < -LEADER_DIR_THRESHOLD:
        return "DOWN"
    return "SIDEWAYS"


def get_meme_momentum(pair: str) -> float:
    """25-hour momentum of the meme pair (1h candles)."""
    candles = load_candles(pair, "1h")
    if len(candles) < 25:
        return 0.0
    recent = float(candles[-1][4])
    prior = float(candles[-25][4])
    return (recent - prior) / prior if prior != 0 else 0.0


def btc_1m_momentum(lookback: int = 15) -> float:
    """BTC ~15-min momentum from the 1m feed — the cascade detector.

    An active BTC cascade (< -0.6%) drags alts faster than their own signals
    flip. Returns % move over the last ``lookback`` 1m candles (default 15 = 15m).
    """
    candles = load_candles("BTC/USD", "1m")
    if len(candles) <= lookback:
        return 0.0
    recent = float(candles[-1][4])
    prior = float(candles[-1 - lookback][4])
    return (recent - prior) / prior if prior != 0 else 0.0


# ---------------------------------------------------------------------------
# Per-TF vote functions
# ---------------------------------------------------------------------------
def vote_4h(candles: List[List]) -> str:
    if len(candles) < 52:
        return "NEUTRAL"
    macd, signal, hist = ind.get_macd(candles)
    rsi = ind.get_rsi(candles)
    if macd is None or rsi is None:
        return "NEUTRAL"
    bullish = (hist > 0 or rsi > 45)
    bearish = (hist < 0 or rsi < 55)
    if bullish and not bearish:
        return "LONG"
    elif bearish and not bullish:
        return "SHORT"
    return "NEUTRAL"


def vote_1h(candles: List[List]) -> str:
    if len(candles) < 52:
        return "NEUTRAL"
    macd, signal, hist = ind.get_macd(candles)
    rsi = ind.get_rsi(candles)
    if macd is None or rsi is None:
        return "NEUTRAL"
    if rsi > 50 and macd > 0:
        return "LONG"
    elif rsi < 50 and macd < 0:
        return "SHORT"
    return "NEUTRAL"


def vote_15m(candles: List[List]) -> str:
    if len(candles) < 20:
        return "NEUTRAL"
    vi = ind.get_volume_imbalance(candles)
    rsi = ind.get_rsi(candles)
    if vi is None or rsi is None:
        return "NEUTRAL"
    if vi > 0.1 and rsi < 70:
        return "LONG"
    elif vi < -0.1 and rsi > 30:
        return "SHORT"
    return "NEUTRAL"


def vote_30m(candles: List[List]) -> str:
    return vote_1h(candles)

def vote_5m(candles: List[List]) -> str:
    return vote_15m(candles)

def vote_1m(candles: List[List]) -> str:
    # 2026-10-07: delegate to 5m logic (same MACD+RSI confluence, faster 1m data)
    return vote_5m(candles)

def vote_1d(candles: List[List]) -> str:
    return vote_4h(candles)


TF_VOTE_FUNCS: Dict[str, Any] = {
    "4h": vote_4h,
    "1h": vote_1h,
    "30m": vote_30m,
    "15m": vote_15m,
    "5m": vote_5m,
    "1m": vote_1m,
    "1d": vote_1d,
}


def compute_votes(pair: str) -> Dict[str, str]:
    """Vote every TF for a pair. Returns {tf: LONG|SHORT|NEUTRAL}."""
    votes: Dict[str, str] = {}
    for tf in TF_ORDER:
        candles = load_candles(pair, tf)
        if not candles:
            votes[tf] = "NEUTRAL"
            continue
        func = TF_VOTE_FUNCS[tf]
        votes[tf] = func(candles)
    return votes


# ---------------------------------------------------------------------------
# Signal synthesis — confluence scoring
# ---------------------------------------------------------------------------
def synthesize_signal(pair: str, votes: Dict[str, str],
                      signal_weights: Dict[str, Dict]) -> Dict[str, Any]:
    """Score a pair's MTF confluence → confidence 0.30–0.85+.

    Confidence tiers:
      6/6 all agree   → base 0.70 + 0.15 * (6/6) = 0.85  → STRONG
      2-5 agree       → base 0.50 + 0.20 * (n/6)        → MODERATE/WEAK
      0-1 agree       → base 0.30 + 0.20 * (n/6)        → WEAK
    Then × learned weight (signal_weights.json, clamped 0.5-1.5).
    15m entry-window agreement gives a soft +5% bump (no hard gate).
    """
    directional = {k: v for k, v in votes.items() if v != "NEUTRAL"}
    n_dir = len(directional)
    n_total = len(votes)

    longs = [tf for tf, v in directional.items() if v == "LONG"]
    shorts = [tf for tf, v in directional.items() if v == "SHORT"]

    if longs and shorts:
        return {"pair": pair, "composite_signal": None, "confidence": 0.0,
                "score": "0/0 CONFLICT", "votes": votes,
                "reasoning": f"CONFLICT: {len(longs)} LONG vs {len(shorts)} SHORT",
                "signal_type": "CONFLICT", "direction": None,
                "directional_count": 0, "total_count": n_total}
    if n_dir == 0:
        return {"pair": pair, "composite_signal": None, "confidence": 0.0,
                "score": "0/0 NO SIGNAL", "votes": votes,
                "reasoning": "All timeframes NEUTRAL",
                "signal_type": "NO_SIGNAL", "direction": None,
                "directional_count": 0, "total_count": n_total}
    if len(longs) > len(shorts):
        direction = "LONG"
    elif len(shorts) > len(longs):
        direction = "SHORT"
    else:
        return {"pair": pair, "composite_signal": None, "confidence": 0.0,
                "score": "0/0 CONFLICT", "votes": votes,
                "reasoning": "Tie between LONG and SHORT",
                "signal_type": "CONFLICT", "direction": None,
                "directional_count": 0, "total_count": n_total}

    if n_dir == n_total:
        base_conf = 0.70 + (0.15 * (n_dir / n_total))
    elif n_dir >= 2:
        base_conf = 0.50 + (0.20 * (n_dir / n_total))
    else:
        base_conf = 0.30 + (0.20 * (n_dir / n_total))

    signal_type = "-".join(f"{tf}-{votes[tf]}" for tf in TF_ORDER if votes[tf] != "NEUTRAL")
    if not signal_type:
        signal_type = "NO_SIGNAL"
    weight_entry = signal_weights.get(signal_type, {"weight": 1.0})
    weight = weight_entry.get("weight", 1.0)
    effective_conf = base_conf * weight
    # Soft 15m bump: if the 15m entry-window vote agrees with the composite direction
    if votes.get("15m") == direction:
        effective_conf *= 1.05

    score_label = (f"{n_dir}/{n_total} STRONG" if n_dir == n_total else
                   f"{n_dir}/{n_total} MODERATE" if n_dir >= 2 else
                   f"{n_dir}/{n_total} WEAK")
    reasoning_parts = []
    for tf in TF_ORDER:
        v = votes.get(tf, "NEUTRAL")
        if v != "NEUTRAL":
            label = {"4h": "(trend gate)", "1h": "(momentum)", "15m": "(entry window)",
                     "1d": "(long-term)"}.get(tf, "")
            reasoning_parts.append(f"{tf} {v} {label}".strip())
    reasoning = ", ".join(reasoning_parts) if reasoning_parts else "No directional votes"

    composite = "BUY" if direction == "LONG" else "SELL"
    return {"pair": pair, "composite_signal": composite,
            "confidence": round(effective_conf, 4),
            "base_confidence": round(base_conf, 4), "weight": weight,
            "score": score_label, "votes": votes, "reasoning": reasoning,
            "signal_type": signal_type, "direction": direction,
            "directional_count": n_dir, "total_count": n_total}


# ---------------------------------------------------------------------------
# Leader filter (three-leader model + BTC cascade guard)
# ---------------------------------------------------------------------------
def filter_by_leader(signal_info: Dict[str, Any], pair: str) -> Dict[str, Any]:
    """Apply three-leader-model + BTC short-term cascade guard (deploy-side only)."""
    base = pair.split("/")[0]

    # BTC cascade guard: < -0.6% over 1m → deprioritize alts (soft 0.25x, never block)
    btc_short = btc_1m_momentum()
    if btc_short < -0.006:
        signal_info["confidence"] = float(signal_info.get("confidence", 0.0) or 0.0) * 0.25
        signal_info["reasoning"] = signal_info.get("reasoning", "") + \
            f" [BTC CASCADE {btc_short*100:.2f}% 1m — alt deprioritized]"

    if base in MEME_COINS:
        btc_dir = get_leader_direction(pair)
        mem_momentum = get_meme_momentum(pair)
        if btc_dir == "DOWN" and mem_momentum <= 0.01:
            signal_info["composite_signal"] = None
            signal_info["confidence"] = 0.0
            signal_info["reasoning"] = signal_info.get("reasoning", "") + \
                " [FILTERED: BTC DOWN + no MEME momentum]"
    elif base in COIN_GROUPS.get("BTC", []) or \
         base in COIN_GROUPS.get("ETH", []) or \
         base in COIN_GROUPS.get("XRP", []):
        leader = None
        if base in COIN_GROUPS.get("BTC", []):
            leader = "BTC"
        elif base in COIN_GROUPS.get("ETH", []):
            leader = "ETH"
        elif base in COIN_GROUPS.get("XRP", []):
            leader = "XRP"
        if leader:
            leader_dir = get_leader_direction(pair)
            if leader_dir == "UP" and signal_info.get("direction") == "SHORT":
                signal_info["composite_signal"] = None
                signal_info["confidence"] = 0.0
                signal_info["reasoning"] = signal_info.get("reasoning", "") + \
                    f" [FILTERED: {leader} UP, can't short]"
            elif leader_dir == "DOWN" and signal_info.get("direction") == "LONG" and leader != base:
                signal_info["composite_signal"] = None
                signal_info["confidence"] = 0.0
                signal_info["reasoning"] = signal_info.get("reasoning", "") + \
                    f" [FILTERED: {leader} DOWN, can't buy]"
    return signal_info


# ---------------------------------------------------------------------------
# Entry timing — 15m confirmation
# ---------------------------------------------------------------------------
def check_entry_window(pair: str, direction: str) -> bool:
    """15m volume-imbalance + RSI entry-window confirmation."""
    candles = load_candles(pair, "15m")
    if len(candles) < 20:
        return False
    vi = ind.get_volume_imbalance(candles)
    rsi = ind.get_rsi(candles)
    if vi is None or rsi is None:
        return False
    if direction == "LONG":
        return vi > 0.1 and rsi < 70
    else:
        return vi < -0.1 and rsi > 30


# ---------------------------------------------------------------------------
# Signal weights I/O (self-learning: ±WEIGHT_ADJUST, clamped WEIGHT_MIN..WEIGHT_MAX)
# ---------------------------------------------------------------------------
WEIGHTS_FILE = cfg.DATA_DIR / "signal_weights.json"
WEIGHT_ADJUST = 0.05


def load_signal_weights() -> Dict[str, Dict]:
    if WEIGHTS_FILE.exists():
        try:
            with open(WEIGHTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_signal_weights(weights: Dict[str, Dict]) -> None:
    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WEIGHTS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(weights, f, indent=2)
    os.replace(tmp, WEIGHTS_FILE)


def adjust_weight(signal_type: str, won: bool,
                  weights: Dict[str, Dict]) -> Dict[str, Dict]:
    if signal_type not in weights:
        weights[signal_type] = {
            "trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "avg_pnl": 0.0, "avg_pnl_pct": 0.0,
            "weight": 1.0,
        }
    entry = weights[signal_type]
    entry["trades"] += 1
    if won:
        entry["wins"] += 1
        entry["weight"] = min(entry["weight"] + WEIGHT_ADJUST, WEIGHT_MAX)
    else:
        entry["losses"] += 1
        entry["weight"] = max(entry["weight"] - WEIGHT_ADJUST, WEIGHT_MIN)
    entry["win_rate"] = entry["wins"] / entry["trades"] if entry["trades"] > 0 else 0.0
    return weights


# ---------------------------------------------------------------------------
# Engine class — ties it all together
# ---------------------------------------------------------------------------
class MTFEngine:
    """Compute, score, and rank MTF signals for the 28-pair universe.

    Usage:
        eng = MTFEngine(signal_weights={})
        sigs = eng.compute_all_signals()
        ranked = eng.rank_candidates(sigs)
        top = eng.find_best_signals(sigs, n=5, held=set(), cooldowns={})
    """

    def __init__(self, signal_weights: Optional[Dict[str, Dict]] = None):
        self.signal_weights = signal_weights if signal_weights is not None else load_signal_weights()

    def compute_all_signals(self) -> List[Dict[str, Any]]:
        """Score every pair: votes → synthesize → filter_by_leader."""
        all_signals: List[Dict] = []
        for pair in SYMBOLS:
            votes = compute_votes(pair)
            sig = synthesize_signal(pair, votes, self.signal_weights)
            sig = filter_by_leader(sig, pair)
            if sig.get("composite_signal") is not None:
                candles_1h = load_candles(pair, "1h")
                if candles_1h:
                    sig["current_price"] = float(candles_1h[-1][4])
                else:
                    sig["current_price"] = 0.0
            all_signals.append(sig)
        return all_signals

    def rank_candidates(self, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rank actionable signals by confidence × weight + small XP boost.

        Soft preference only: new/unproven STRONG setups still qualify, they
        just start lower until they prove themselves (>=5 trades, win>50%).
        """
        cands: List[Dict] = []
        for s in signals:
            if s.get("composite_signal") is None or s.get("confidence", 0) <= 0:
                continue
            if not ("STRONG" in s.get("score", "") or "MODERATE" in s.get("score", "")):
                continue
            st = s.get("signal_type", "UNKNOWN")
            w = self.signal_weights.get(st, {}) or {}
            trades = int(w.get("trades", 0) or 0)
            win_rate = float(w.get("win_rate", 0) or 0)
            weight = float(w.get("weight", 1.0) or 1.0)
            boost = 0.02 if (trades >= 5 and win_rate > 0.5 and weight >= 1.0) else 0.0
            c = dict(s)
            c["rank_score"] = round(float(s.get("confidence", 0)) + boost, 4)
            c["xp_boost"] = boost
            cands.append(c)
        cands.sort(key=lambda c: c["rank_score"], reverse=True)
        return cands

    def find_best_signals(self, signals: Optional[List[Dict]] = None,
                           n: int = 5, held: Optional[set] = None,
                           cooldowns: Optional[Dict[str, float]] = None) -> List[Dict]:
        """Top-N deployable signals (excludes held pairs + cooldown)."""
        if signals is None:
            signals = self.compute_all_signals()
        ranked = self.rank_candidates(signals)
        held = held or set()
        cooldowns = cooldowns or {}
        now = time.time()
        result: List[Dict] = []
        for c in ranked:
            pair = c["pair"]
            if pair in held:
                continue
            if now - cooldowns.get(pair, 0) < cfg.REENTRY_COOLDOWN_SEC:
                continue
            result.append(c)
            if len(result) >= n:
                break
        return result

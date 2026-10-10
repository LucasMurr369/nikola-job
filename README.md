# Nikola Utility Engine — Lightweight Local Risk Engine for Kraken Pro

**Zero-overhead, open-source Python trading engine** that replaces $30–$100+/month
SaaS bots (3Commas, Cryptohopper, Kryll) with a single local script. Built to the
**exact, battle-tested risk parameters** of the live Kraken Pro margin bot — no cloud
fees, no UI lock-in, fully auditable.

> **Keywords / SEO:** Kraken API · crypto trading bot · risk management ·
> algorithmic trading · take-profit ladder · open source · CCXT · Python 3 ·
> margin trading · multi-timeframe · trailing stop · 3:1 risk reward ·
> break-even ratchet · stop loss · TP SL

---

## 🚨 1. READ THIS FIRST — The Human Hook

### The Mission

**Nikola is built to protect small capital and teach safe, disciplined margin
math.** It is not a "get rich quick" script. It is a precision tool that treats
every trade as a 3:1 (minimum) risk-reward coin flip, letting compounding do the
heavy lifting over hundreds of small, controlled cycles.

### The Real Story

Nikola was born on **$300**. During the great BTC pullback of 2026, the account
dipped to **under $100**. Instead of panic-scaling or chasing bounces, the engine
kept positions tiny, took small stop-losses, and let the TP ladder bank 80% of
every winner. By October, it had clawed back to green — **proving that small math
done over time beats over-trading.**

| Milestone | Account | Key Lesson |
|-----------|---------|------------|
| Start | $300 | Size small, risk smaller |
| BTC crash | <$100 | Stop-losses are protection, not failure |
| Recovery | $300+ | 80% banked via TP ladder before runner trails |

### The Golden Rule

> **Taking small stop-losses is not a loss — it is the protection that keeps your
> float alive for the next trade.**

Nikola's conviction-aware SL1 + SL2 and 40/40/20 take-profit ladder are designed
around this principle. Every trade risks 1 unit (R) to make at least 3
(3:1 minimum). When a stop fires, the loss is small and the capital survives to
fight another day.

### Capital Protection Rules

| Rule | What it means |
|------|---------------|
| **3:1 Minimum R:R** | No trade enters unless the TP ladder offers ≥ 3× the SL distance |
| **SL1 Partial at -2.1%** | If a 1m/5m candle **closes** adverse by ≥ 1.5%, close 40% — TPs retained |
| **SL2 Full Exit at -4.4%** | If the remaining position hits -4.4%, the rest exits fully |
| **80% Banked Before Runner** | TP1 (40%) + TP2 (40%) = 80% banked before the 20% runner trails |
| **Break-Even Ratchet** | After TP1 + TP2 fill, SL2 snaps to entry + 2.0% — runner is free |

### Portfolio Balance

Nikola does **not** spam 100 random positions. It holds **defensive anchors**
alongside **high-beta runners**:

| Role | Pairs | Purpose |
|------|-------|---------|
| **Defensive Anchors** | `PAXG/USD`, `TRX/USD` | Stable-store of value + steady yield; absorbs volatility drag |
| **Beta Runners** | `DOT/USD`, `AVAX/USD`, `PEPE/USD` | High-momentum alts that amplify 1m/5m confluence signals |
| **Leader Filters** | `BTC/USD`, `ETH/USD`, `XRP/USD` | Three-leader model gates alt deployment; BTC cascade guard |

**ETH is user-owned** — Nikola never touches it. **SOL is user-owned** — never touched.

---

## 🚀 2. Where Do You Want to Go Next?

### Option A: "I Want to Get Started Right Now"

```bash
cd C:\Users\mrluk\nikola
python -m pip install -r requirements.txt
python nikola.py --once --paper    # single scan, paper mode, no orders sent
python nikola.py --paper          # paper daemon (15s light / 300s full scan)
python nikola.py --export-html    # snapshot to report (equity curve, TP/SL, God Code)
python nikola.py --live          # REAL orders (user-approved only)
```

**Paper mode** starts at a clean slate: **$300 cash, 0 positions, cycle 0**
(see `Nikola.md` for the reset procedure). No API keys required for paper mode.

### Option B: "I Want to See How Nikola Is Built (The Tech & Math)"

Nikola's core innovation is the **6-timeframe confluence engine** + **adaptive
leverage/sizing**:

- **6 Timeframes**: `1m, 5m, 15m, 30m, 1h, 4h` — trend (4h→1h) → entry (15m→5m→1m)
- **Confidence 0.30–0.85+** weighted by learned `data/signal_weights.json`
- **Dynamic Leverage**: 3–20× tiered by conviction (BTC/ETH capped at 7× until
  $150 equity, then up to 20×)
- **Dynamic Sizing**: margin = `confidence_pct × deployable_budget` (no static
  $5–$20 caps; scales with account equity)

| Confidence | Leverage | Margin % of Deployable |
|------------|----------|----------------------|
| ≥ 0.60 | up to 20× | 8.0% |
| ≥ 0.50 | up to 10× | 5.0% |
| ≥ 0.40 | up to 7×  | 3.0% |
| < 0.40 | 3× (floor) | 1.0% |

### Option C: "I Want to Understand the Risk & Margin Math"

#### The 40/40/20 Exit Ladder

```
Entry → TP1 (+1.4%, 40% closed) → TP2 (+2.8%, 40% closed) → 20% runner trails
     ↘ SL1 (-2.1%, 40% closed on candle-close ≥ 1.5% adverse) → SL2 (-4.4%, full exit)
```

- **TP1** at +1.4% exits 40% of base position
- **TP2** at +2.8% exits another 40% (2× TP1 distance — "mirrors TP1")
- **20% runner** trails 0.30% → 0.60% wide at +2.5% peak
- **SL1** at -2.1%: partial close (40%) fires when 1m/5m candle closes ≥ 1.5% adverse
  (conviction gate — no whipsaw on price touch)
- **SL2** at -4.4%: full exit of remaining position (2× SL1 distance)
- **BTC/ETH majors**: tighter targets (+1.0%/+2.0%) for faster fills

#### Why Two Stop-Losses?

SL1 is an **intelligent, conviction-aware scale-out**: it doesn't fire on every
wick through -2.1%. It waits for a confirmed 1m/5m candle close at ≥ 1.5% adverse,
confirming real directional conviction. This de-risks the position (40% closed)
while keeping the TP ladder active for a potential reversal bounce.

SL2 is the **hard floor** at -4.4% — if the conviction signal was wrong, the
remaining 60% exits cleanly before losses compound.

#### Break-Even Ratchet (After TP1 + TP2)

Once 80% is banked, SL2 snaps to **entry + 2.0%** (BE floor). The 20% runner is
now risk-free. Beyond +2.5% peak, the wide 0.60% trail arms — letting strong
trends run to 5:1, 8:1+ while profit is already secured.

---

## The 3-State Dynamic Risk-Reward Model

```
┌──────────┐ entry filled      ┌──────────┐ +3R reached    ┌──────────────┐
│ MONITOR  │ ────────────────→ │ ACTIVE   │ ──────────────→ │ EXPANDED     │
│ (entry   │                   │ (in      │                 │ (dynamic     │
│  sent)   │                   │  pos)    │                 │  exit)       │
└──────────┘                   └──────────┘                 └──────────────┘
```

- **State 1 — MONITOR**: Entry sent, SL1 armed (candle-close conviction at 1.5%).
  TP1 (+1.4%/40%) + TP2 (+2.8%/40%) + 20% runner pending. Waiting for fill.
- **State 2 — ACTIVE**: Position live. At +0.1R the BE ratchet primes; once TP1
  *and* TP2 both fill, SL snaps to breakeven (+2.0% floor). Beyond +2.5% the 20%
  runner trails 0.30% → 0.60% wide.
- **State 3 — EXPANDED** (the innovation): When profit hits **3× risk** (+3R ≈ 6.3%
  for alts / 4.2% for majors), the state machine branches on MTF confluence:
  - **MTF strong** (≥3/6 timeframes confirm trend) → 50% partial exit at +3R,
    remaining 50% switches to a **2×ATR trailing stop** (mega-trend extension).
  - **MTF weak** → 50% partial exit, remaining 50% trails at 0.60% (tighten & bank).

This re-anchors every trade at a **3:1 minimum** while letting strong trends
extend far beyond it — the proven bot's runner logic formalised into an auditable
state machine.

---

## Architecture

```
nikola/
├── nikola.py                  # CLI launcher (--paper / --live / --once / --cycles / --export-html)
├── html_report.py             # --export-html report generator (stdlib-only)
├── donation_monitor.py        # Tithe / Offering / Donation addresses (incl. PayPal)
├── ACTIVE_BOT.txt             # Anchor file: live PID + log path (instant startup lookup)
├── archive/                   # Retired test scripts + fixtures (out of the working path)
├── engine/
│   ├── __init__.py
│   ├── config.py              # Pair/leverage/TP/SL/MTF params (ported from bot)
│   ├── indicators.py          # ATR, RSI, EMA, MACD, volume imbalance (ported)
│   ├── risk.py                # SL/TP ladder + dynamic position sizing
│   ├── signals.py             # Per-TF votes → synthesize_signal confluence scorer
│   ├── strategy.py            # 3-state Dynamic RR state machine (core innovation)
│   ├── exchange.py            # CCXT Kraken wrapper — paper + live order primitives
│   └── state.py               # Paper state ($300, cycle 0), position tracking, health
├── agents/
│   └── cycle_agent.py         # Per-cycle autonomous subagent (Hermes delegate_task)
├── config/
│   └── settings.yaml          # User-tunable params (leverage caps, risk thresholds)
├── data/                      # Candle cache, positions, paper ledger
├── requirements.txt
└── README.md
```

### Relationship to the live bot

| Aspect | Live bot (`kraken_bot/`) | Nikola (`nikola/`) |
|--------|--------------------------|---------------------|
| **Status** | **Running** (PID 712, Python312, live trades) | Standalone — paper mode by default, `--live` for real |
| **Code** | Monolithic `live_margin_bot.py` (3467 lines) | Modular package: `engine/{signals, risk, strategy, exchange}` |
| **R:R** | Fixed TP1/TP2 + trailing runner | 3-state machine: Monitor → Active → **Expanded** |
| **SL1** | Static -2.1% price touch | Conviction-aware: 1m/5m candle close ≥ 1.5% adverse |
| **Sizing** | Static $5–$20 MARGIN_TIERS + $20 cap | Dynamic equity-based: 1–8% of deployable budget |
| **Leverage** | Max 10× | Max 20× (BTC/ETH after $150 equity) |
| **Data** | `kraken_bot/data/` | `nikola/data/` (independently seeded) |
| **Execution** | Real Kraken BTNL margin orders | Paper first ($300 clean slate), `--live` for real |

**The live bot is never touched by Nikola.** Nikola reads the same Kraken API keys
but operates in paper mode (simulated fills) or, with explicit `--live` approval,
routes real orders through its own CCXT session. Nikola's `ACTIVE_BOT.txt` anchor
file points to PID 712 so new sessions read the live bot's status instantly —
**no workspace scanning required.**

---

## Start

```bash
cd C:\Users\mrluk\nikola
python -m pip install -r requirements.txt
python nikola.py --once --paper    # single scan, paper mode, no orders sent
python nikola.py --paper          # paper daemon (15s light / 300s full scan; quiet — status on state change + 20 min)
python nikola.py --export-html    # snapshot to nikola_report.html (equity curve, TP/SL, God Code, PayPal Tithe QR)
python nikola.py --live           # REAL orders (user-approved only)
```

`--export-html` writes a single self-contained `nikola_report.html` (no trading).
Optional Tithe / Offering / Donation via PayPal (paypal.me/LucasMurr369) — see
`INSTRUCTIONS.md` and `donation_monitor.py`.

---

## Free-model usage (Hermes)

| Model | Role |
|-------|------|
| `poolside/laguna-s-2.1:free` | Primary reasoning (this session) |
| `hermes/claude-3-5-sonnet` | Code-gen via `delegate_task` subagents |
| `hermes/gpt-4o-mini` | Fast signal-scoring iterations |

*Restart point: see `Nikola.md` (clean slate — $300, 0 positions, cycle 0).*

---

## 📜 License

Open source. See `LICENSE` file.

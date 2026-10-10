# Nikola Utility Engine — Lightweight Local Risk Engine for Kraken Pro

**Zero-overhead, open-source Python trading engine** that replaces $30–$100+/month
SaaS bots (3Commas, Cryptohopper, Kryll) with a single local script. Built to the
**exact, battle-tested risk parameters** of the live Kraken Pro margin bot — no cloud
fees, no UI lock-in, fully auditable.

> **Keywords / SEO:** Kraken API · crypto trading bot · risk management ·
> algorithmic trading · take-profit ladder · open source · CCXT · Python 3 ·
> margin trading · multi-timeframe · trailing stop · 3:1 risk reward ·
> break-even ratchet · stop loss · TP SL

## Problem → Solution

| | |
|---|---|
| **Problem** | 3Commas, Cryptohopper and similar SaaS bots charge **$30–$100+ per month**, lock you into their cloud, and hide the risk math behind an opaque UI you cannot audit. |
| **Solution** | **Nikola** is a single local Python script (CCXT + stdlib) that runs on your own machine, costs **nothing to run**, and exposes every risk rule — stop-loss, take-profit ladder, break-even ratchet, trailing runner — as transparent, unit-testable code. |

## Core Mechanics

Nikola ports the *live* Kraken Pro BTNL margin parameters verbatim from
`live_margin_bot.py` into `engine/config.py` (the source of truth).

- **40 / 40 / 20 take-profit ladder** — TP1 exits **40%** at **+1.4%**; TP2 exits
  **40%** at **+2.8%** (2× TP1 distance); the remaining **20%** rides a trailing stop.
  *(BTC/ETH majors target +1.0%/+2.0%.)*
- **Two-stage static stop-loss** — **SL1 at 2.10%** partially closes 40% of the base
  (take-profits retained); **SL2 at 4.40%** exits the rest. The `SL_LIMIT_BAND_PCT =
  1.5%` is the stop-limit **slippage band**, not the loss guard.
- **Break-even ratchet** — once TP1 *and* TP2 both fill, SL snaps to the breakeven
  floor (entry + 2.0%) so the runner is protected.
- **Float protection** — 80% of every position is banked via the TP ladder before the
  20% runner trails (0.30% → 0.60% wide at +2.5%), letting strong trends run while
  profit is already off the table.

> Verified directly against `engine/config.py` + `config/settings.yaml`. See
> *The 3-State Model* below for the dynamic +3R extension.

---

## Relationship to the live bot

| Aspect | Live bot (`kraken_bot/`) | Nikola (`nikola/`) |
|--------|--------------------------|---------------------|
| Status | **Running** (PID 712, Python312, live trades) | Standalone — paper mode by default, separate state |
| Code | Monolithic `live_margin_bot.py` (3467 lines) | Modular package: `engine/{signals, risk, strategy, exchange}` |
| R:R | Fixed TP1/TP2 + trailing runner | 3-state machine: Monitor → Active → **Expanded** |
| Data | `kraken_bot/data/` | `nikola/data/` (independently seeded) |
| Execution | Real Kraken BTNL margin orders | Paper first ($300 clean slate), `--live` for real |

**The live bot is never touched by Nikola.** Nikola reads the same Kraken API keys but
operates in paper mode (simulated fills) or, with explicit `--live` approval, routes real
orders through its own CCXT session.

## The 3-State Dynamic Risk-Reward Model

```
┌──────────┐ entry filled      ┌──────────┐ +3R reached    ┌──────────────┐
│ MONITOR  │ ────────────────→ │ ACTIVE   │ ──────────────→ │ EXPANDED     │
│ (entry   │                   │ (in      │                 │ (dynamic     │
│  sent)   │                   │  pos)    │                 │  exit)       │
└──────────┘                   └──────────┘                 └──────────────┘
```

- **State 1 — MONITOR**: Entry order sent, SL1 placed (-2.1% partial / TPs retained),
  TP1 (+1.4%/40%) + TP2 (+2.8%/40%) + 20% runner pending. Waiting for fill.

- **State 2 — ACTIVE**: Position live. At +0.1R the BE ratchet primes; once TP1 *and* TP2
  both fill, SL snaps to breakeven (+2.0% floor). Beyond +2.5% the 20% runner trails
  0.30% → 0.60% wide.

- **State 3 — EXPANDED** (the innovation): When profit hits **3× risk** (+3R ≈ 6.3%
  for alts / 4.2% for majors), the state machine branches on MTF confluence:
  - **MTF strong** (≥3/6 timeframes confirm trend) → 50% partial exit at +3R, remaining
    50% switches to a **2×ATR trailing stop** that lets mega-trends run to 5:1, 8:1+.
  - **MTF weak** → 50% partial exit, remaining 50% trails at 0.60% (tighten & bank).

This re-anchors every trade at a **3:1 minimum** while letting strong trends extend far
beyond it — the proven bot's runner logic formalised into an auditable state machine.

## Architecture

```
nikola/
├── nikola.py              # CLI launcher (--paper / --live / --once / --cycles / --export-html)
├── html_report.py         # --export-html report generator (stdlib-only)
├── donation_monitor.py    # Tithe / Offering / Donation addresses (incl. PayPal)
├── ACTIVE_BOT.txt         # Anchor file: live PID + log path (instant startup lookup)
├── archive/               # Retired test scripts + fixtures (out of the working path)
├── engine/
│   ├── __init__.py
│   ├── config.py          # Proven pair/leverage/TP/SL/MTF params (ported from bot)
│   ├── indicators.py      # ATR, RSI, EMA, MACD, volume imbalance (ported)
│   ├── signals.py         # Per-TF votes → synthesize_signal confluence scorer
│   ├── risk.py            # SL/TP ladder + position sizing (ported)
│   ├── strategy.py        # 3-state Dynamic RR state machine (core innovation)
│   ├── exchange.py        # CCXT Kraken wrapper — paper + live order primitives
│   └── state.py           # Paper state ($300, cycle 0), position tracking, health
├── agents/
│   └── cycle_agent.py     # Per-cycle autonomous subagent (Hermes delegate_task)
├── config/
│   └── settings.yaml      # User-tunable params
├── data/                  # Candle cache, positions, paper ledger
├── requirements.txt
└── README.md
```

## Start

```bash
cd C:\Users\mrluk\nikola
python -m pip install -r requirements.txt
python nikola.py --once --paper    # single scan, paper mode, no orders sent
python nikola.py --paper          # paper daemon (15s light / 300s full scan; quiet - status on state change + 20 min)
python nikola.py --export-html    # snapshot to nikola_report.html (equity curve, TP/SL, God Code, PayPal Tithe QR)
python nikola.py --live          # REAL orders (user-approved only)

`--export-html` writes a single self-contained `nikola_report.html` (no trading). Optional Tithe / Offering / Donation via PayPal (paypal.me/LucasMurr369) - see INSTRUCTIONS.md and donation_monitor.py.
```

## Free-model usage (Hermes)

| Model | Role |
|-------|------|
| `poolside/laguna-s-2.1:free` | Primary reasoning (this session) |
| `hermes/claude-3-5-sonnet` | Code-gen via `delegate_task` subagents |
| `hermes/gpt-4o-mini` | Fast signal-scoring iterations |

*Restart point: see `Nikola.md` (clean slate — $300, 0 positions, cycle 0).*

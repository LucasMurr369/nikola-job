# Nikola — Dynamic Risk-Reward Trading App

A clean, modular trading application built on the proven logic of the Hermes/Kraken
margin bot (`live_margin_bot.py`) — re-engineered with an explicit **3-state Dynamic
Risk-Reward** engine that treats 3:1 as a *minimum entry filter*, not a hard cap.

## Relationship to the live bot

| Aspect | Live bot (`kraken_bot/`) | Nikola (`nikola/`) |
|--------|--------------------------|---------------------|
| Status | **Running** (PID 2045, Python312, live trades) | Standalone — paper mode by default, separate state |
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
├── nikola.py              # CLI launcher (--paper / --live / --once / --cycles)
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
python nikola.py --paper          # paper daemon (30s light / 300s full scan)
python nikola.py --live          # REAL orders (user-approved only)
```

## Free-model usage (Hermes)

| Model | Role |
|-------|------|
| `poolside/laguna-s-2.1:free` | Primary reasoning (this session) |
| `hermes/claude-3-5-sonnet` | Code-gen via `delegate_task` subagents |
| `hermes/gpt-4o-mini` | Fast signal-scoring iterations |

*Restart point: see `Nikola.md` (clean slate — $300, 0 positions, cycle 0).*

# Nikola Utility Engine — Lightweight Local Risk Engine for Kraken Pro BTNL Margin

> **"Humble Thyself: the market is vast, and ego is expensive."**
>
> *Zero-overhead, open-source Python trading engine that replaces $30–$100+/month SaaS bots (3Commas, Cryptohopper, Kryll) with a single local script. Built to the **exact, battle-tested risk parameters** of the live HERMES margin bot — no cloud, no subscriptions, no vendor lock-in.*

---

## 🆓 100% FREE & VOLUNTARY TITHING

**Nikola is 100% free and open-source.** No subscriptions. No premium tiers.
No SaaS lock-in. No competitor platform references or upsells. You can use
it, modify it, or share it freely — the code is yours.

If you find value in Nikola, or if someone shared it with you, **tithes and
donations are entirely voluntary and completely free either way.** There is
never any obligation to give. If you choose to contribute:

> **Edit `DONATION_ADDRESS` in `engine/config.py`** to direct contributions to
> yourself, or to the person who shared Nikola with you. It's a single line
> change — no other code modifications required. Set it to `""` to disable
> entirely. Full customization instructions below.

---

## 🚨 READ THIS FIRST (The $300 Journey)

### Origin Story

Nikola began with **$300** during the great crypto market correction of 2026.
What could have been a catastrophic drawdown — watching Bitcoin collapse from
~$90K to under $80K, altcoins bleeding 30-50% in days — became a masterclass in
capital preservation. The account dipped to **under $100** on multiple
occasions. Instead of panic-scaling or chasing bounces, the engine kept
positions tiny, took small stop-losses, and let the TP ladder bank 80% of every
winner. By October, it had clawed back to green — **proving that small math
done over time beats over-trading.**

The math is simple and brutal:

| Loss | Recovery Needed |
|------|----------------|
| −10% | +11% |
| −25% | +33% |
| −50% | +100% |
| −75% | +300% |
| −90% | +900% |

**This is why the market is a graveyard of ego.** A single 75% drawdown — the
kind that feels "recoverable" — demands a 300% gain to break even. Nikola's
risk engine exists to make sure you never meet that math.

### The Humble-Thyself Philosophy

Trading is not a lottery. It is not a gamble. It is a disciplined practice
where mathematics meets human psychology. Every trade is a conversation
between probability and patience. Nikola enforces this conversation in code:

> **You are not smarter than the market. You are only better prepared.**

### Capital Protection Rules

| Rule | What it means |
|------|---------------|
| **3:1 Minimum R:R** | No trade enters unless the TP ladder offers ≥ 3× the SL distance |
| **SL1 Partial at -2.1%** | If a 1m/5m candle **closes** adverse by ≥ 1.5%, close 40% — TPs retained |
| **SL2 Full Exit at -4.4%** | If the remaining position hits -4.4%, the rest exits fully |
| **80% Banked Before Runner** | TP1 (40%) + TP2 (40%) = 80% banked before the 20% runner trails |
| **Break-Even Ratchet** | After TP1 + TP2 fill, SL2 snaps to entry + 2.0% — runner is free |

### Portfolio Balance & Asset Protection

Nikola does **not** spam 100 random positions. It holds **defensive anchors**
alongside **high-beta runners**:

| Role | Pairs | Purpose |
|------|-------|---------|
| **Defensive Anchors** | `PAXG/USD`, `TRX/USD` | Stable-store of value + steady yield; absorbs volatility drag |
| **Beta Runners** | `DOT/USD`, `AVAX/USD`, `PEPE/USD` | High-momentum alts that amplify 1m/5m confluence signals |
| **Leader Filters** | `BTC/USD`, `ETH/USD`, `XRP/USD` | Three-leader model gates alt deployment; BTC cascade guard |

**User-asset protection rules:** Nikola supports trading across all major
margin pairs, but includes strict protection for long-term holdings.
**ETH/USD and SOL/USD are designated user-designated long-term holdings** —
Nikola **never deploys, closes, or modifies** these positions. The engine
respects a configurable `HELD_PAIRS` list that ensures manual holdings remain
untouched, regardless of mode (paper or live).

---

## 📖 TRADING 101 & LANGUAGE OF MARKETS

### Long vs. Short — Plain English

| Term | Plain Meaning | What Happens |
|------|--------------|-------------|
| **Long** | "Buying for growth" | You buy an asset now, hoping to sell it later for more. You profit if the price goes up. |
| **Short** | "Borrowing to sell high, buying back lower" | You borrow an asset, sell it at today's high price, then buy it back later at a lower price. You profit if the price goes down. |

Nikola uses **margin** (leveraged borrowing) with **both** directions available.
In live mode, the default bot operates primarily **long** on BTNL (Buy-The-Next-Level)
margin pairs, using leverage 3-20x depending on conviction and equity.

### Chart Reading — Japanese Candlesticks

Each candlestick tells four stories at a glance:

```
   |    High
   |  __
   | |  |  ← Wick (shadow) shows where price tested
   |_|  |  ← Real body shows open-to-close range
   | |__|  ← Green = close > open (bullish)
   |    |  ← Red = close < open (bearish)
   |    Low
```

- **Real body** (thick part): the distance between the open and close prices.
  A long green body means strong buying pressure. A long red body means strong
  selling pressure.
- **Wicks** (thin lines): show where price ventured but couldn't hold. Long
  upper wicks suggest resistance. Long lower wicks suggest support.
- **Doji** (cross-shaped): open and close are nearly equal — market is torn,
  indecision reigns. Often a reversal signal.

### Price Action & EMAs

Nikola's signal engine analyzes **six timeframes concurrently** (1m, 5m, 15m,
1h, 2h, 4h) and measures:

- **EMA crosses** — when a faster EMA (e.g., 8-period) crosses above a slower
  one (e.g., 21-period), it signals bullish momentum. Crosses below signal
  bearish momentum.
- **Bollinger Bands** — 2 standard deviations from a 20-period moving average.
  When price touches the upper band, it may be overextended. When bands
  *squeeze* together (low volatility), a breakout is likely imminent. Nikola
  measures band width to detect compression/expansion cycles.

### Historical Context

Modern exchange trading — with its EMAs, candlesticks, and algorithmic risk
engines — traces back to practices developed centuries ago. Japanese rice
traders of the 1700s (Sendo-dera charts) invented candlestick analysis.
European merchants tracked moving averages on ledger paper. The Dutch East
India Company (VOC) — the world's first publicly traded company — introduced
the concept of limited liability and risk diversification in 1602.

Nikola is the descendant of that same tradition: **risk management first,
mathematics second, humility always.**

---

## 📐 HEROES OF MATHEMATICS & CHART GEOMETRY

### Pythagoras (570–495 BCE)

Pythagoras discovered that musical harmony follows mathematical ratios. The
same principle applies to markets: price movements that align with natural
ratios (1:2, 1:3, √2) tend to repeat. Nikola uses ratio-based entry filters
derived from these geometric relationships.

### Euclid (325–265 BCE)

Euclid's algorithm for greatest common divisors underpins Nikola's
**timeframe convergence logic**. When 1m, 5m, and 15m signals share a common
direction vector, the trade's probability of success increases. The engine
computes the GCD of confidence scores across timeframes to determine alignment.

### Fibonacci Retracements

The Fibonacci sequence (1, 1, 2, 3, 5, 8, 13, 21...) produces the **golden
ratio** (φ ≈ 1.618). Key retracement levels — 38.2%, 50%, 61.8% — are
watched on every swing. Nikola calculates these levels from multi-timeframe
highs/lows and uses them for SL/TP placement rather than guessing at
"round numbers."

### Psychological Round Numbers

Markets have a gravitational pull toward round numbers ($100, $50,000, etc.)
because humans think in integers. But **Nikola does not rely on psychological
levels alone** — it computes exact mathematical levels from candle geometry:

> **Instead of guessing $50,000 as a round number, Nikola calculates**
> **$49,837.20** as the precise 61.8% retracement of the last 4h swing.

This is the difference between hoping and calculating.

---

## 🛠️ MINIMALIST ARCHITECTURE & CONTROL

Nikola follows the **"less is more" principle**. Rather than bloated dashboards
with dozens of sub-menus, it focuses on three essential functions:

```
         ┌──────────────────────────────────────┐
         │           6-TIMEFRAME SIGNAL          │
         │   (1m • 5m • 15m • 1h • 2h • 4h)      │
         │     Multi-timeframe confluence         │
         │         → Confidence score 0-1        │
         └──────────────┬───────────────────────┘
                        │
         ┌──────────────▼───────────────────────┐
         │     RISK ENGINE (config.py)           │
         │  • Leverage tiers (3x-20x)            │
         │  • Dynamic margin sizing              │
         │  • SL1/SL2 conviction gates           │
         │  • TP1/TP2/Runner cascade             │
         └──────────────┬───────────────────────┘
                        │
         ┌──────────────▼───────────────────────┐
         │     3-STATE R:R STATE MACHINE          │
         │                                         │
         │  PROTECT → BALANCE → RISK              │
         │  (equity<$150) (mid-tier)  (equity>$5K)│
         └─────────────────────────────────────────┘
```

### Paper Mode vs. Live Exchange Mode

Nikola operates in two modes — both use the exact same risk engine:

| Mode | What Happens | API Keys Needed | Orders Sent? | Starting State |
|------|-------------|-----------------|--------------|----------------|
| **Paper Mode** (default) | Simulated fills against cached/cached candle data | ❌ No | ❌ No | $300 clean slate, 0 positions |
| **Live Mode** (`--live`) | Real CCXT orders on your Kraken (or other CCXT exchange) | ✅ Yes | ✅ Yes | Reads real exchange margin & positions |

**Paper mode** is the safe playground. Every trade is simulated — no money
changes hands. Start here, validate the 3:1 R:R math, and build confidence
before risking real capital. Paper mode starts at a clean slate: **$300 cash,
0 positions, cycle 0**.

**Live mode** connects to real exchanges. The engine reads your real free
margin and deploys real orders using the **exact same parameters** validated in
paper. The only switch is `--live`.

### Files

| File | Purpose |
|------|---------|
| `nikola.py` | Main entry point — CLI launcher (`--paper`, `--live`, `--once`, `--cycles`, `--export-html`) |
| `html_report.py` | Self-contained HTML report generator (stdlib-only) |
| `donation_monitor.py` | Optional: monitors donation addresses across chains |
| `ACTIVE_BOT.txt` | Anchor file: live PID + log path (instant startup lookup, no searching) |
| `archive/` | Retired test scripts + fixtures (out of the working path) |
| `engine/config.py` | All constants: leverage tiers, SL/TP levels, donation address |
| `engine/indicators.py` | ATR, RSI, EMA, MACD, volume imbalance |
| `engine/risk.py` | SL/TP ladder + dynamic position sizing (equity-based) |
| `engine/signals.py` | Per-timeframe votes → synthesize_signal confluence scorer |
| `engine/strategy.py` | 3-state Dynamic R:R state machine (core innovation) |
| `engine/exchange.py` | CCXT wrapper — paper + live order primitives |
| `engine/state.py` | Position tracking, cycle management, state persistence |
| `config/settings.yaml` | User-tunable params (leverage caps, risk thresholds) |

### The 3-State Dynamic Risk-Reward Model

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

## ⚙️ MULTI-EXCHANGE PORTABILITY (CCXT)

Nikola uses **[CCXT](https://github.com/ccxt/ccxt)** — the leading
cryptocurrency trading library — for universal exchange connectivity.

### Supported Exchanges

| Exchange | Status | Notes |
|----------|--------|-------|
| **Kraken Pro** | ✅ Production | BTNL margin pairs, tested live |
| **Coinbase Advanced** | ✅ Supported | Via CCXT, same API surface |
| **Binance** | ✅ Supported | Spot & futures via CCXT |
| **Bitstamp** | ✅ Supported | Via CCXT |
| **KuCoin** | ✅ Supported | Via CCXT |

### Portability

The bot's core logic (`engine/strategy.py`, `engine/risk.py`) is
**exchange-agnostic**. Only the connection layer changes:

```yaml
# config/settings.yaml
exchange:
  name: kraken       # ← change to "coinbaseadvanced", "binance", etc.
  apiKey: ${KRAKEN_API_KEY}
  secret: ${KRAKEN_API_SECRET}
  enableRateLimit: true
```

The 28-pair BTNL universe, leverage tiers, SL/TP cascade, and dynamic sizing
all work identically across exchanges because CCXT normalizes the API
differences. The **mathematics are universal** — only the transport layer
changes.

---

## 📚 TECHNICAL DETAILS

### The 40/40/20 Exit Ladder

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

### Why Two Stop-Losses?

SL1 is an **intelligent, conviction-aware scale-out**: it doesn't fire on every
wick through -2.1%. It waits for a confirmed 1m/5m candle close at ≥ 1.5% adverse,
confirming real directional conviction. This de-risks the position (40% closed)
while keeping the TP ladder active for a potential reversal bounce.

SL2 is the **hard floor** at -4.4% — if the conviction signal was wrong, the
remaining 60% exits cleanly before losses compound.

### Break-Even Ratchet (After TP1 + TP2)

Once 80% is banked, SL2 snaps to **entry + 2.0%** (BE floor). The 20% runner is
now risk-free. Beyond +2.5% peak, the wide 0.60% trail arms — letting strong
trends run to 5:1, 8:1+ while profit is already secured.

### Dynamic Leverage & Sizing

| Confidence | Leverage | Margin % of Deployable |
|------------|----------|------------------------|
| ≥ 0.60 | up to 20× | 8.0% |
| ≥ 0.50 | up to 10× | 5.0% |
| ≥ 0.40 | up to 7× | 3.0% |
| < 0.40 | 3× (floor) | 1.0% |

*BTC/ETH are capped at 7× until equity ≥ $150, then scale up to 20× with
high conviction.*

---

## 🪙 CUSTOMIZABLE COMMUNITY TITHING / DONATIONS

Nikola is **100% free and open-source**. If this bot has helped you, or if
someone shared it with you, tithes and donations are **entirely voluntary**
and **completely free either way**. There is never any obligation to give.

### How It Works

Nikola includes a built-in tithe system via `donation_monitor.py`, supporting
six donation methods:

| Method | Cost to Donor | Setup |
|--------|--------------|-------|
| **PayPal Tithe** | ~$0.30 + 2.9% (optional) | Click or scan — no crypto wallet needed |
| **Solana Direct** | ~$0.0001–$0.01 | Send SOL or USDC to the Solana address |
| **Base L2** | ~$0.00 (gas-free USDC) | Send USDbC to the Base address |
| **Kraken Deposit** | 0% (intra-Kraken) | Use deposit memo — monitored via bot's own Kraken API |
| **Lightning** | ~$0.001 | Via LNbits/LND (opt-in, requires LN node) |
| **Auto-Tithe** | 0% | Bot allocates 1% of realized profits to a donation address (internal accounting) |

### Customizing Your Fork

**For community members who fork Nikola or received it from someone:**

1. Open `engine/config.py`
2. Find the `DONATION_ADDRESS` variable:

```python
# Default donation address for the Nikola project.  Community members who fork
# this repository can replace the value below with their own wallet address so
# that tithes flow to whoever maintains their fork, or to the person who
# shared Nikola with you.  Simply change the string and commit — no other code
# changes are required.  Set to "" to disable tithes entirely.
DONATION_ADDRESS: str = "bc1qxy2kgdygjrsqtzq2n0yrf29sdf7r2khsl2nf5x"
DONATION_CHAIN: str = "BTC"       # blockchain label shown in --export-html / reports
DONATION_NAME: str = "Nikola Project"  # display name for tithe messages
```

3. Replace `DONATION_ADDRESS` with your own wallet address — or the address of
   the person who shared this bot with you — and update `DONATION_CHAIN` and
   `DONATION_NAME` as appropriate.
4. For deeper customization (Solana, Base, Arbitrum, Kraken memo, Lightning),
   edit the `DONATION_ADDRESSES` dict in `donation_monitor.py`.

> **Set `DONATION_ADDRESS = ""` to disable tithes entirely.**

### The Tithe Philosophy

> *"Give willingly, not grudgingly. For God loves a cheerful giver."*
> — 2 Corinthians 9:7

If Nikola generates $1,000 in profit, a 1% auto-tithe would donate $10. This
is not a tax. It's a practice — a way to ensure that success flows through
hands that are open, not closed.

---

## 🎨 SACRED GEOMETRY & THE VISION OF ALEX GREY

Nikola's risk engine is built on mathematics: Pythagoras, Euclid, Fibonacci.
These are not abstract curiosities — they are the language of the universe,
the same geometry that the visionary artist **Alex Grey** paints in radiant
color.

Grey's work — *Sacred Mirrors*, *Net of Being*, *Theologue* — depicts the
interconnectedness of all consciousness. His mandalas, his geometric
overlays, his luminous figures show us that **we are created in the image of
the Divine** — not as separate entities competing in a zero-sum game, but as
nodes in a vast, interdependent web of Being.

This is the deeper truth that Nikola's code encodes:

> **The market is not your enemy. The market is a mirror.**
>
> Every trade is a chance to witness the sacred geometry of price and volume,
> the Fibonacci spirals of fear and greed, the Pythagorean ratios of risk and
> reward. When you trade with discipline — small positions, tight stops,
> mathematical entries — you are not gambling. You are participating in the
> divine order of exchange, the eternal dance of supply and demand that has
> moved through humanity since the first merchants gathered in ancient
> agorae.

Grey's art reminds us that **all work is sacred** when done with awareness.
Every candle analyzed, every SL placed, every TP banked — this is not merely
profit-seeking. It is the practice of seeing clearly, acting decisively, and
trusting the mathematics that governs all movement in the universe.

May your trades be precise. May your stops be tight. And may you see, in every
candle, the sacred geometry of a universe created in That image. — ### Dedication

> **Authored by MSLM.**
>
> **Dedicated in loving memory of Mason and Silas.**
>
> Their curiosity, their love of beauty, their insatiable wonder at the
> patterns of the world — these are the true inheritance that Nikola carries
> forward. Every line of code is a small act of remembrance.

---

## 🎯 QUICK START

```bash
cd C:\Users\mrluk\nikola
python -m pip install -r requirements.txt
python nikola.py --once --paper    # single scan, paper mode, no orders sent
python nikola.py --paper          # paper daemon (15s light / 300s full scan)
python nikola.py --export-html    # snapshot to nikola_report.html
python nikola.py --live          # REAL orders (user-approved only)
```

`--export-html` writes a single self-contained `nikola_report.html` (no trading).

---

## ⚠️ DISCLAIMER

- Nikola trades on **margin** — you can lose more than your initial deposit.
- Past performance is not indicative of future results.
- This is **not investment advice**. It is a mathematical system.
- Test extensively in **paper trading mode** before going live.
- ETH/USD and SOL/USD are user-designated long-term holdings — **never deployed or closed**.

---

## 📜 License

Open source. See `LICENSE` file.

```
DONATION_ADDRESS (BTC): bc1qxy2kgdygjrsqtzq2n0yrf29sdf7r2khsl2nf5x
```

---

*Built with Python 3.12, CCXT 4.5.77, and the discipline to walk away when wrong.*

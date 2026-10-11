[POST — COPY EVERYTHING BELOW THIS LINE]

## 🚨 READ THIS FIRST: The $300 Journey

Nikola didn't start with a trust fund. It started with **$300** during the great crypto correction of 2026 — watching Bitcoin crash from ~$90K to under $80K, altcoins bleeding 30-50% in days. The account dipped under $100 on multiple occasions.

> "Humble Thyself: the market is vast, and ego is expensive."

Instead of panic-scaling or chasing bounces, the engine kept positions tiny, took small stop-losses, and let the TP ladder bank 80% of every winner. By October, it had clawed back to green — **proving that small math done over time beats over-trading.**

The loss/recovery math is brutal:

| Loss | Recovery Needed |
|------|----------------|
| −10% | +11% |
| −25% | +33% |
| −50% | +100% |
| −75% | +300% |
| −90% | +900% |

**This is why humility is the trader's greatest asset.**

---

## What Nikola Does Differently

### 1. 100% Free, Zero Cloud, No Subscription Trap
Nikola replaces $30–$100+/month SaaS subscription bots with a single local Python script. No cloud. No subscriptions. No vendor lock-in. You can fork, modify, or share it freely.

**Edit `DONATION_ADDRESS` in `engine/config.py`** to direct any voluntary tithe to yourself or whoever shared it with you — it is a one-line change.

### 2. The 40/40/20 Exit Ladder (With Ratcheting Stops)

```
Entry → TP1 (+1.4%, 40% closed) → TP2 (+2.8%, 40% closed) → 20% runner trails
     ↘ SL1 (-2.1%, 40% closed on candle-close ≥ 1.5% adverse) → SL2 (-4.4%, full exit)

POST-TP1 RATCHET: Cancel SL1(-2.1%) → Hard SL2 at Entry+Fees+0.2% → SL1 below TP1 (trail)
POST-TP2 RATCHET: SL2 → TP1 level (lock +1.4%) → SL1 trails 20% runner at 0.60% wide
```

**The three-stage stop ratchet eliminates trade washouts:**
- **Upon TP1 fill:** Cancel the initial -2.1% SL1. Place a new hard SL2 at Entry + Fees + 0.2% Profit Buffer. Move SL1 to just below TP1 to trail the remaining position.
- **Upon TP2 fill (80% banked):** Ratchet SL2 UP to the TP1 price level. The runner can never lose money.
- **Runner trail:** SL1 attaches to the 20% runner as an immediate 0.60% wide trailing stop.

> **Result: once TP1 fills, no trade can wash out to zero. Once TP2 fills, the runner can never lose money.**

### 3. SL1 — The Conviction Gate (No Whipsaw Protection)
SL1 is an **intelligent, conviction-aware scale-out**: it does not fire on every wick through -2.1%. It waits for a confirmed 1m/5m candle close at ≥ 1.5% adverse — confirming real directional conviction before closing 40% of the position.

### 4. Only 28 Vetted Pairs — Institutional Depth, Not Meme Chaos
Nikola trades strictly **28 established, high-liquidity pairs** — never random meme coins, never low-cap pumps, never illiquid ghosts that trap retail traders.

For traditional investors: think of this like a **value investing screen** — Benjamin Graham would not have bought shares of a company with no historical earnings, no auditor, and no institutional ownership. The same bar applies to every crypto asset.

| Filter | Standard |
|--------|----------|
| Daily volume | ≥ $50M 30-day average |
| Price history | ≥ 2 years on major exchanges |
| Institutional custody | Supported by major custodians |
| Margin eligibility | Available on BTNL margin pairs |

### 5. ETH/USD and SOL/USD Are Off-Limits
**ETH/USD and SOL/USD are user-designated long-term holdings.** Nikola **never deploys, closes, or modifies** these positions — regardless of mode.

---

## Architecture (Minimalist)

```
         ┌──────────────────────────────────────┐
         │           6-TIMEFRAME SIGNAL          │
         │   (1m • 5m • 15m • 1h • 2h • 4h)      │
         │     Multi-timeframe Confluence         │
         │         → Confidence Score 0-1        │
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
         │  PROTECT → BALANCE → EXPANDED         │
         └─────────────────────────────────────────┘
```

- **MONITOR** → Entry sent, SL1 armed, waiting for fill
- **ACTIVE** → Position live, TP ladder active, stops ratcheting
- **EXPANDED** → At 3× risk, branches on timeframe confluence

### Paper Mode vs. Live Mode
Both use the **exact same risk engine**:
- **Paper Mode** (default): Simulated fills, no API keys. Starts at $300 clean slate.
- **Live Mode** (`--live`): Real CCXT orders on your Kraken. Reads real free margin.

---

## Trading 101: Long vs. Short (No Jargon)

**Long = buying low, selling high (you own the asset).**
*A vintage guitar shop during a recession. You buy a rare 1959 Les Paul for 30% below value, wait for the music scene to recover, sell for 40% above. You held an asset the world eventually valued higher.*

**Short = borrowing high, selling now, buying back cheaper (you owe the asset).**
*A college town housing shortage in August. You rent at inflated $2K/month, immediately sublet for $2K, then in May find a new tenant for $1.2K — netting $800/month profit on the declining cost of borrowing.*

---

## Multi-Exchange (CCXT)

- **Kraken Pro** ✅ Production-tested (live)
- **Coinbase Advanced** ✅ Supported
- **Binance** ✅ Supported
- **Bitstamp** ✅ Supported
- **KuCoin** ✅ Supported

---

## Quick Start

```bash
git clone https://github.com/LucasMurr369/nikola-job.git
cd nikola-job
python -m pip install -r requirements.txt
python nikola.py --once --paper    # single scan, no orders
python nikola.py --paper          # paper daemon
python nikola.py --live           # REAL orders (user-approved only)
```

**GitHub:** https://github.com/LucasMurr369/nikola-job

---

## FAQ

**Q: Do I have to run Paper Mode first?**
A: **Yes.** Keep Nikola in Paper Mode until you can explain in your own words why he places TP1 at +1.4% and why his stop loss ratchets to Entry + Fees + 0.2% after TP1 fills. Move to live trading only when you understand his risk math.

**Q: Why does the stop loss ratchet?**
A: Once TP1 banked 40%, the remaining 60% is protected by a hard stop just above break-even. After TP2 (80% banked), that stop ratchets to the TP1 price. The runner can never lose money.

---

> "Humble Thyself: the market is vast, and ego is expensive."
>
> **Authored by MSLM.**
> **Inspired by and dedicated to Mason and Silas.**

[/POST]

# NIKOLA CAPITAL BLIND-SPOT FIX — Session Directions

> **Status:** Diagnosed. Approved to implement. Bot is LIVE (PID 12420) — do NOT hot-patch the running process; apply code, then clean-restart via `launch_nikola.py`.
> **Root cause:** Nikola starts with `self.active = {}` and has **no on-startup position recovery in LIVE mode**. It is blind to 13 existing open LONG positions on Kraken. It keeps firing deploy attempts into pairs it already owns → Kraken rejects every one with `EOrder:Insufficient initial margin`.

---

## 0. ANCHOR & VERIFY (read-only, no bot changes)

Every new session starts here — **never scan the workspace** (that's the startup-delay problem from Oct 10):

```bash
# 1. Read the anchor (2s)
cat /c/Users/mrluk/nikola/ACTIVE_BOT.txt
# BOT_NAME: Nikola
# PID: 12420
# LOG_FILE: nikola_live.log
# REPO: /c/Users/mrluk/nikola

# 2. Confirm the process is ALIVE (do not restart/stop unless directions say so)
tasklist /fi "pid eq 12420" | grep -i python

# 3. Confirm LIVE mode + log is growing
head -5 /c/Users/mrluk/nikola/nikola_live.log   # "LIVE MODE ACTIVE — placing real BTNL orders"
tail -1 /c/Users/mrluk/nikola/nikola_live.log   # timestamp advances
```

**Confirmed live state (as of diagnosis):**
| Field | Value | Source |
|---|---|---|
| PID | 12420 (python.exe, ~94 MB) | `nikola.pid`, `tasklist` |
| Mode | **LIVE** (real BTNL orders) | log header |
| Scan / Light | 150s / 15s | log + config.py:212-213 |
| Cash (free margin) | ~$29.67 | log banner |
| Equity | ~$138.84 | log banner |
| Active (bot's view) | **0** | log banner |
| Active (Kraken reality) | **13 LONG positions** | `fetch_positions()` snapshot below |

### Root-cause proof (read-only Kraken GET — does NOT touch the bot)

```python
import ccxt
# credentials from C:\Users\mrluk\kraken_bot\kraken.env only
ex = ccxt.kraken({'apiKey': KR, 'secret': KS, 'enableRateLimit': True})
pos = [p for p in ex.fetch_positions() if float(p.get('contracts',0)) != 0]
bal = ex.fetch_balance()
print('free USD:', bal['free']['USD'])               # ~138.77 (raw)
print('open positions:', len(pos))                    # 13
```

**Result: 13 open LONG positions** — BTC, ETH, BCH, SUI, DOT, LTC, LINK,
AVAX, PEPE, AAVE, PAXG, SHIB, TRX. Bot tracks 0 of them. This is the gap
(`self.active` empty → `$109` margin locked in untracked positions → every
new deploy rejected).

---

## 1. FIX A — Position recovery at startup (CRITICAL)

**Why:** `nikola.py:_sync_state_positions` (line 774) is explicitly
"reporting-only" and is gated by `if self.paper:` at lines 946 & 1007 — it
**never runs in LIVE mode**. `self.active` starts empty (line 174) and is
never hydrated from the exchange.

**Where:** `nikola.py` — add the method, then call it once in `run()`
**before** the `while True:` loop (around line 920, after the banner print).

**The method (skeleton — attach existing protection orders so the bot can
take over managing the recovered positions, not just avoid re-deploying):**

```python
# ---- add near _sync_state_positions, e.g. after line 809 ----
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
        # BTNL symbols come back as "XBTUSD:BTNL", "ETHUSD:BTNL", etc.
        orders = self.ex.fetch_open_orders()
    except Exception:
        orders = []
    # index orders by pair for SL2/TP1/TP2 re-attachment
    by_pair: dict[str, list] = {}
    for o in orders:
        psym = o.get("symbol", "")
        by_pair.setdefault(psym, []).append(o)

    from engine.strategy import Position, TPRungs
    from engine.config import normalize_btln_symbol  # XDG->DOGE, XBT->BTC
    user_owned = {"SOL/USD", "ETH/USD"}  # never touch — user's pairs

    for p in ex_positions:
        if float(p.get("contracts", 0) or 0) == 0:
            continue
        sym = p.get("symbol", "")
        pair = normalize_btln_symbol(sym)  # -> BTC/USD etc, or unchanged
        if pair in user_owned or pair not in cfg.SYMBOLS:
            continue
        direction = "LONG" if str(p.get("side", "")).lower() == "long" else "SHORT"
        info = p.get("info", {})
        entry = float(info.get("entryPrice") or info.get("cost") or 0.0)
        contracts = float(p.get("contracts", 0))
        # infer leverage: not always in CCXT; default from config tier
        lev = cfg.PAIR_LEVERAGE.get(pair, cfg.PREFERRED_LEVERAGE)
        pos = Position(
            pair=pair, direction=direction, entry_price=entry or current_mock_price(pair),
            base_amount=abs(contracts), leverage=lev,
            signal_type="RECOVERED", entry_confirmed=True, sl2_set=True,
        )
        # re-attach TP/SL prices from open orders if present
        for o in by_pair.get(sym, []):
            op = float(o.get("average") or o.get("price") or 0.0)
            # classify by order type: stop-loss-limit -> SL2, limit -> TP1/TP2
            oinfo = o.get("info", {})
            ofee = str(oinfo)
            if "stop" in ofee.lower() and "loss" in ofee.lower():
                pos.sl2_price = op
            elif "reduceOnly" in ofee or o.get("reduceOnly"):
                # assign to TP1/TP2 by price level (TP1 lower, TP2 higher for LONG)
                if not pos.tp:
                    pos.tp = TPRungs(tp1_price=0.0, tp2_price=0.0)
                if pos.tp.tp1_price == 0.0 or (direction == "LONG" and op < pos.tp.tp1_price):
                    pos.tp.tp1_price = op
                else:
                    pos.tp.tp2_price = op
        if pos.peak_price == 0.0:
            pos.peak_price = entry or pos.entry_price
        self.active[pair] = pos
        # 1h cooldown so the bot doesn't immediately try to ADD to the pair
        self.cooldowns[pair] = time.time()
    log.info(f"position recovery: loaded {len(self.active)} live positions from Kraken")
```

**Call it in `run()`** (after the banner, before `while True:`, ~line 920):

```python
        print(f"{'='*68}")
        if not self.paper:
            self._recover_live_positions()          # <-- ADD THIS LINE
        try:
            while True:
```

> **Test first:** run `python nikola.py --paper --cycles 3` to confirm the recovery
> path doesn't crash (it early-returns in paper mode). Then `--live --cycles 3` in
> a quiet window and confirm `Active:` banner shows the recovered count, not 0.

---

## 2. FIX B — Deploy gate respects self-learning

**Why:** `rank_candidates` (signals.py:439) admits any MODERATE-STRONG signal. But
`signal_weights.json` shows partial-confluence LONGs (`15m-LONG-5m-LONG-1m-LONG`,
weight 0.80, 0/12 wins) lose, while full-confluence (`1h-LONG-...-1m-LONG`, weight
1.05, 1/1 win) is the only proven LONG type. The bot should stop deploying on
signals its own learning has flagged as losers.

**Where:** `engine/signals.py` `rank_candidates`, inside the `for s in signals:`
  loop — after `weight`, `trades`, `win_rate` are computed (after line 456).

**The gate (LIVE mode only — paper keeps exploring):**

```python
            weight = float(w.get("weight", 1.0) or 1.0)
            # LIVE only: do not deploy on signal types proven to lose
            # (weight down-weighted AND a real sample showing no wins).
            if not self.paper_sim and trades >= 3 and weight < 1.0 and win_rate <= 0.0:
                continue
```

> Note: `MTFEngine` doesn't currently know `paper` vs `live`. Set a flag.
> Simplest: pass `paper_sim` into `MTFEngine.__init__` (signals.py:420) and
> store `self.paper_sim = paper_sim`; pass it from `full_scan` via
> `sig.MTFEngine(signal_weights=..., paper_sim=self.paper)` (nikola.py:838).

**Also cap live deploy leverage** so a high-confidence signal doesn't blow up
on a tiny recovered position: the existing `leverage_for()` (config.py:242)
already caps BTC/ETH at 7x until equity ≥ $150; leave as-is.

---

## 3. FIX C — Reconcile config drift

`config/settings.yaml` (the user-tunable overlay) says `mode: paper`,
`live_budget_usd: 100.0`, `full_scan_interval: 300`. The runtime bot runs
`--live` with `config.py` saying `TOTAL_MARGIN_BUDGET_USD=1_000_000`,
`FULL_SCAN_INTERVAL=150`, `DRY_RUN=True`. The drift is cosmetic (CLI flag wins)
but a footgun on restart.

```bash
# Edit config/settings.yaml:
#   mode: paper            ->  mode: live
#   live_budget_usd: 100.0 ->  live_budget_usd: 1_000_000.0   (match config.py:84)
#   full_scan_interval: 300 -> full_scan_interval: 150        (match config.py:213)
#   max_position_margin_usd: 0.0 is correct (dynamic sizing)
```

Then verify the YAML overlay actually binds in config.py (`_apply_settings_yaml`,
line 104) — confirm `mode`/`live_budget` reach the live path you expect.

---

## 4. RESTART & VERIFY

```bash
# STOP the current blind bot
taskkill /F /PID 12420
sleep 3
tasklist /fi "pid eq 12420"   # confirm gone

# START (detached — survives session)
cd /c/Users/mrluk/nikola
python launch_nikola.py        # prints "NIKOLA launched: PID=..."

# VERIFY
NEWPID=$(cat /c/Users/mrluk/nikola/nikola.pid)
tasklist /fi "pid eq $NEWPID"
tail -20 /c/Users/mrluk/nikola/nikola_live.log
```

**Pass criteria after restart:**
1. Banner shows **Active: 13** (or ≥9 NIKOLA pairs; SOL/ETH excluded) — not 0.
2. `position recovery: loaded N live positions` in the log.
3. No more `Insufficient initial margin` spam on pairs already owned.
4. New deploys fire **only** on full-confluence LONGs (1h-inclusive, weight ≥ 1.0),
   i.e. conf ≥ 0.85 STRONG or the learned-winning signal types.
5. BTC/ETH excluded from deploys (user-owned); TRX/PAXG included.

---

## 5. WHAT "SLIGHTLY BULLISH" MEANS NOW (context for this fix)

With the blind-spot fixed, the bullish read resolves cleanly: the bot is
finding LONG confluence across BTC, LTC, BCH, LINK, SUI, DOGE (conf 0.63-0.89,
MODERATE tier). That matches the tape — BTC holding the $82.5K support after the
$87K→$80.4K dump, "neutral to cautiously bullish" per the major analysts.
"Slightly" is correct: confluences are 3-6/6 (not 6/6 strong), leverage stays
5-10x (not 20x), and the bot's own 1W-9L drawdown keeps it from going full-tilt.

Once positions are recovered, the bot will express this bullish read by
**managing its existing 13 longs** (trailing runners, ratcheting BE) rather than
firing doomed new deploys. That's the real fix: turn "slightly bullish but
blind" into "slightly bullish and aware."

---

## FILES TOUCHED
| File | Change | Lines (approx) |
|---|---|---|
| `nikola.py` | add `_recover_live_positions()` + call in `run()` | +~70 lines near 774, +1 call ~920 |
| `nikola.py` | pass `paper_sim` flag into MTFEngine in `full_scan` | line 838 |
| `engine/signals.py` | `MTFEngine.__init__` accepts + stores `paper_sim`; gate in `rank_candidates` | lines 420, 456 |
| `config/settings.yaml` | `mode: live`, budget/scan reconciled | 4 lines |

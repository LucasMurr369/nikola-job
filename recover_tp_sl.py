#!/usr/bin/env python3
"""Recover TP/SL orders for NIKOLA's 3 deployed positions (SHIB, DOT, PAXG).

The side-conversion bug caused ALL exit orders to be placed with side='buy' on
LONG positions, so Kraken cancelled them. This script places correct SELL-side
SL2 + TP1 + TP2 using the FIXED place_sl/place_tp functions (which compute
close_side correctly from the entry side).

Usage:  python3 recover_tp_sl.py
"""
import os, sys, time
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.exchange import make_exchange, place_sl, place_tp

# ----------------------------------------------------------------------------
# Load credentials from kraken.env
# ----------------------------------------------------------------------------
ENV_PATH = r"C:\Users\mrluk\kraken_bot\kraken.env"

def load_kraken_env(path):
    key, secret = "", ""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(("API_KEY=", "KRAKEN_API_KEY=")):
                key = line.split("=", 1)[1].strip()
            elif line.startswith(("API_SECRET=", "KRAKEN_API_SECRET=")):
                secret = line.split("=", 1)[1].strip()
    return key, secret

# ----------------------------------------------------------------------------
# Position recovery data — from NIKOLA deploy log (entry side = "buy" = LONG)
# ----------------------------------------------------------------------------
# Config-derived constants from engine/config.py
SL1_PCT = 0.0210
SL2_PCT = 0.0440
TP1_PCT = 0.014
TP2_PCT = 0.028
SL_LIMIT_BAND_PCT = 0.015
TP1_BASE_PCT = 0.40  # 40 % of position (mirrors TP1)
TP2_BASE_PCT = 0.30  # 30 % of position

POSITIONS = [
    {
        "pair":   "SHIB/USD",
        "btnl":   "SHIB/USD:BTNL",
        "lev":    5,
        "amount": 5208290.58,         # integer-lot pair
        "entry":  5.388e-06,
        "entry_px_str": "5.388e-06",
    },
    {
        "pair":   "DOT/USD",
        "btnl":   "DOT/USD:BTNL",
        "lev":    5,
        "amount": 20.2120488407375,
        "entry":  1.1788,
        "entry_px_str": "1.1788",
    },
    {
        "pair":   "PAXG/USD",
        "btnl":   "PAXG/USD:BTNL",
        "lev":    5,
        "amount": 0.0105686333033087,
        "entry":  4183.8,
        "entry_px_str": "4183.8",
    },
]

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    api_key, api_secret = load_kraken_env(ENV_PATH)
    print(f"Loaded credentials for Kraken BTNL margin")
    mx = make_exchange(api_key, api_secret)
    print(f"Exchange markets loaded\n")

    for p in POSITIONS:
        pair    = p["pair"]
        btnl    = p["btnl"]
        entry   = p["entry"]
        amount  = p["amount"]
        lev     = p["lev"]
        side    = "buy"       # entry side for LONG — functions derive close_side

        sl2_trigger = entry * (1 - SL2_PCT)
        tp1_price   = entry * (1 + TP1_PCT)
        tp2_price   = entry * (1 + TP2_PCT)
        tp1_amount  = amount * TP1_BASE_PCT
        tp2_amount  = amount * TP2_BASE_PCT

        print(f"=== {pair}  (entry {p['entry_px_str']}  LVL {lev}) ===")
        print(f"  base={amount}")
        print(f"  SL2 trigger={sl2_trigger:.10f}  (stop-loss-limit, SELL)")
        print(f"  TP1={tp1_amount:.6f} @ {tp1_price:.10f}  (limit, SELL reduce-only)")
        print(f"  TP2={tp2_amount:.6f} @ {tp2_price:.10f}  (limit, SELL reduce-only)")

        # --- Cancel any leftover (wrong-side) orders ---
        try:
            existing = mx.fetch_open_orders(btnl)
            for o in existing:
                oid = o.get("id", "")
                os_ = o.get("side", "?")
                print(f"  [cancel] {oid} side={os_}")
                try:
                    mx.cancel_order(oid, btnl)
                    print(f"    -> cancelled")
                except Exception as e:
                    print(f"    -> cancel error: {e}")
        except Exception as e:
            print(f"  [warn] fetch_open_orders: {e}")

        time.sleep(0.5)  # rate limit

        # --- Place SL2 (stop-loss-limit, close_side=SELL derived from side=buy) ---
        sl = place_sl(mx, btnl, side, amount, sl2_trigger, lev)
        if sl:
            print(f"  ✓ SL2  order placed: {sl.get('id')}")
        else:
            print(f"  ✗ SL2  FAILED")

        time.sleep(0.5)

        # --- Place TP1 (limit, reduce-only, close_side=SELL derived from side=buy) ---
        tp1 = place_tp(mx, btnl, side, tp1_amount, tp1_price, lev)
        if tp1:
            print(f"  ✓ TP1  order placed: {tp1.get('id')}")
        else:
            print(f"  ✗ TP1  FAILED")

        time.sleep(0.5)

        # --- Place TP2 ---
        tp2 = place_tp(mx, btnl, side, tp2_amount, tp2_price, lev)
        if tp2:
            print(f"  ✓ TP2  order placed: {tp2.get('id')}")
        else:
            print(f"  ✗ TP2  FAILED")

        time.sleep(0.5)
        print()

    # --- Verify ---
    print("=== Verification: open orders on BTNL pairs ===")
    for p in POSITIONS:
        btnl = p["btnl"]
        try:
            orders = mx.fetch_open_orders(btnl)
            for o in orders:
                print(f"  {p['pair']}:  id={o.get('id','')}  side={o.get('side','?')}  "
                      f"type={o.get('type','?')}  price={o.get('price','?')}  "
                      f"amount={o.get('amount','?')}")
            if not orders:
                print(f"  {p['pair']}:  [NO OPEN ORDERS!]")
        except Exception as e:
            print(f"  {p['pair']}:  fetch error: {e}")

    print("\n=== Recovery complete ===")

if __name__ == "__main__":
    main()

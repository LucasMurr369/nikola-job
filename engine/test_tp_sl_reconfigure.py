"""Unit test: verify TP/SL reconfigure logic ported from live bot.

Tests the two-stage SL (SL1 partial close → SL2 full exit) and TP1/TP2 partial
fills that were ported from kraken_bot/live_margin_bot.py into Nikola's state
machine + execution layer.
"""
import sys
sys.path.insert(0, ".")

from engine.strategy import DynamicRiskReward, Position, PositionState, ExecutionAction
from engine.config import SL1_PCT, SL2_PCT, TP_RUNG_PCTS

engine = DynamicRiskReward()

# ---- Test 1: ACTIVE state SL1 hit → partial close, TP retained ----
print("=== Test 1: SL1 hit in ACTIVE state ===")
entry = 100.0
pos = engine.position_from_signal("TEST/USD", "LONG", entry,
                                  base_amount=1000.0, leverage=5,
                                  confidence=0.8, signal_type="MTF")
# SL1 is at -2.1% = 97.90, SL2 at -4.4% = 95.60
assert pos.sl1_hit == False, "sl1_hit should start False"
assert abs(pos.sl1_price - 97.90) < 0.01, f"SL1 price {pos.sl1_price}"
assert abs(pos.sl2_price - 95.60) < 0.01, f"SL2 price {pos.sl2_price}"

# Simulate price dropping to SL1 level (direct price comparison)
price_at_sl1 = pos.sl1_price  # 97.90
actions = engine.step(pos, price_at_sl1)
sl1_actions = [a for a in actions if a.action == "SL1_HIT"]
assert len(sl1_actions) == 1, f"Expected 1 SL1_HIT action, got {len(sl1_actions)}"
print(f"  SL1_HIT emitted at price={price_at_sl1} (entry={entry}, R={engine.r_multiple(pos, price_at_sl1):.2f})")
print(f"  Payload: {sl1_actions[0].payload}")
assert pos.sl1_hit == True, "sl1_hit should be True after SL1 hit"

# Simulate SL1 partial close in execution layer (mimics _exec_actions)
a = sl1_actions[0]
close_base = pos.base_amount * a.payload.get("partial", 0.4)
pos.base_amount -= close_base
pos.be_floor = a.payload.get("sl2_price", pos.sl2_price)
assert pos.base_amount == 600.0, f"After SL1 partial: base={pos.base_amount}, expected 600"

# Verify TP ladder still active (not delisted)
assert pos.tp.tp1_filled == False, "TP1 should still be active after SL1 hit"
assert pos.tp.tp2_filled == False, "TP2 should still be active after SL1 hit"
print(f"  After SL1: base={pos.base_amount:.1f}, TP1={pos.tp.tp1_price:.2f}, TP2={pos.tp.tp2_price:.2f} (both retained)")
print("  PASSED")

# ---- Test 2: SL2 hit after SL1 → full exit ----
print("\n=== Test 2: SL2 hit after SL1 in ACTIVE state ===")
price_at_sl2 = pos.sl2_price  # 95.60 → R ≈ -2.095
actions = engine.step(pos, price_at_sl2)
sl2_actions = [a for a in actions if a.action == "SL2_EXIT"]
assert len(sl2_actions) == 1, f"Expected 1 SL2_EXIT action, got {len(sl2_actions)}"
print(f"  SL2_EXIT emitted at price={price_at_sl2} (R={engine.r_multiple(pos, price_at_sl2):.2f})")
print(f"  Payload: {sl2_actions[0].payload}")
print("  PASSED")

# ---- Test 3: SL1 hit again — should NOT re-trigger ----
print("\n=== Test 3: SL1 hit does NOT re-trigger ===")
actions = engine.step(pos, price_at_sl1)
sl1_actions = [a for a in actions if a.action == "SL1_HIT"]
assert len(sl1_actions) == 0, f"SL1_HIT should not re-trigger, got {len(sl1_actions)}"
print("  PASSED (no duplicate SL1_HIT)")

# ---- Test 4: TP1 fill → partial close 40% in ACTIVE state ----
print("\n=== Test 4: TP1 fill in ACTIVE state ===")
entry2 = 100.0
pos2 = engine.position_from_signal("TEST2/USD", "LONG", entry2,
                                   base_amount=1000.0, leverage=5,
                                   confidence=0.8, signal_type="MTF")
tp1_price = pos2.tp.tp1_price  # 101.40
actions = engine.step(pos2, tp1_price)
tp1_actions = [a for a in actions if a.action == "TP1_FILLED"]
assert len(tp1_actions) == 1, f"Expected 1 TP1_FILLED, got {len(tp1_actions)}"
print(f"  TP1_FILLED emitted at price={tp1_price} (R={engine.r_multiple(pos2, tp1_price):.2f})")
# Simulate execution: close 40% of base
a = tp1_actions[0]
close_base = pos2.base_amount * a.payload.get("partial", 0.4)
pos2.base_amount -= close_base
assert pos2.base_amount == 600.0, f"After TP1: base={pos2.base_amount}, expected 600"
print(f"  After TP1: base={pos2.base_amount:.1f} (40% closed)")
print("  PASSED")

# ---- Test 5: TP2 fill after TP1 → close 40% of remaining ----
print("\n=== Test 5: TP2 fill after TP1 in ACTIVE state ===")
tp2_price = pos2.tp.tp2_price  # 102.80
actions = engine.step(pos2, tp2_price)
tp2_actions = [a for a in actions if a.action == "TP2_FILLED"]
assert len(tp2_actions) == 1, f"Expected 1 TP2_FILLED, got {len(tp2_actions)}"
print(f"  TP2_FILLED emitted at price={tp2_price} (R={engine.r_multiple(pos2, tp2_price):.2f})")
a = tp2_actions[0]
close_base = pos2.base_amount * a.payload.get("partial", 0.4)
pos2.base_amount -= close_base
assert pos2.base_amount == 360.0, f"After TP2: base={pos2.base_amount}, expected 360"
print(f"  After TP2: base={pos2.base_amount:.1f} (40% of remaining 600 closed)")
# Verify BE_RATCHET_ARM also emitted
be_actions = [a for a in actions if a.action == "BE_RATCHET_ARM"]
assert len(be_actions) == 1, f"Expected BE_RATCHET_ARM, got {len(be_actions)}"
print(f"  BE_RATCHET_ARM emitted: floor={be_actions[0].price:.5f}")
print("  PASSED")

# ---- Test 6: SHORT-side SL1 hit ----
print("\n=== Test 6: SHORT-side SL1 hit ===")
entry3 = 100.0
pos3 = engine.position_from_signal("TEST3/USD", "SHORT", entry3,
                                   base_amount=1000.0, leverage=5,
                                   confidence=0.8, signal_type="MTF")
# For SHORT: SL1 is at entry + SL1_PCT = 102.10, SL2 at 104.40
print(f"  SHORT entry={entry3}, SL1={pos3.sl1_price:.2f}, SL2={pos3.sl2_price:.2f}")
actions = engine.step(pos3, pos3.sl1_price)
sl1_actions = [a for a in actions if a.action == "SL1_HIT"]
assert len(sl1_actions) == 1, f"Expected 1 SL1_HIT for SHORT, got {len(sl1_actions)}"
print(f"  SL1_HIT emitted for SHORT at price={pos3.sl1_price}")
print("  PASSED")

# ---- Test 7: EXPANDED state SL2_FULL_EXIT at -1.0R ----
print("\n=== Test 7: EXPANDED state SL2_FULL_EXIT at -1.0R ===")
entry4 = 100.0
pos4 = engine.position_from_signal("TEST4/USD", "LONG", entry4,
                                   base_amount=1000.0, leverage=5,
                                   confidence=0.8, signal_type="MTF")
# Go to +3R first → EXPANDED
price_high = entry4 + entry4 * SL1_PCT * 3.5  # slightly above 3R to avoid FP issue
actions = engine.step(pos4, price_high)
state_actions = [a for a in actions if a.action == "STATE_TRANSITION"]
assert pos4.state == PositionState.EXPANDED, f"Expected EXPANDED, got {pos4.state}"
print(f"  Transitioned to EXPANDED at R={engine.r_multiple(pos4, price_high):.2f}")
# Now price drops to -1.0R (SL1 level)
price_drop = entry4 - entry4 * SL1_PCT  # 97.90
actions = engine.step(pos4, price_drop)
sl2_actions = [a for a in actions if a.action == "SL2_FULL_EXIT"]
assert len(sl2_actions) == 1, f"Expected 1 SL2_FULL_EXIT in EXPANDED, got {len(sl2_actions)}"
print(f"  SL2_FULL_EXIT emitted at R={engine.r_multiple(pos4, price_drop):.2f}")
print("  PASSED")

print("\n=== ALL TESTS PASSED ===")

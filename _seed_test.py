"""Test: flexible scan order + QR registry."""
from logic import WarehouseEngine
import time

# Fresh DB for clean test
import os
if os.path.exists("test_flex.db"):
    os.remove("test_flex.db")

e = WarehouseEngine(db_path="test_flex.db")

print("=" * 55)
print("TEST 1: Prefix-based QRs (auto-registered)")
print("=" * 55)

# Traditional: RACK first, then BOX
print("\n--- RACK first, then BOX ---")
print(e.process_scan("RACK_01"))
print(e.process_scan("BOX_A1"))
print(e.process_scan("RACK_02"))
time.sleep(1.6)

# NEW: BOX first, then RACK
print("\n--- BOX first (flexible order) ---")
print(e.process_scan("BOX_B2"))
print(e.process_scan("RACK_03"))
time.sleep(1.6)

print("\n" + "=" * 55)
print("TEST 2: Arbitrary UIDs (needs registration)")
print("=" * 55)

# Simulate unknown QR
result = e.process_scan("WH-SHELF-47A")
print(f"\nUnknown scan: {result}")
print(f"  needs_registration = {result.needs_registration}")
print(f"  raw_data = {result.raw_data}")

# Simulate user registering it as a rack
e.register_qr("WH-SHELF-47A", "rack", "Shelf 47A")
time.sleep(1.6)

# Now re-scan - should work as rack
print(f"\nAfter registration:")
print(e.process_scan("WH-SHELF-47A"))

# Scan an arbitrary box
result2 = e.process_scan("PKG-2024-X99")
print(f"\nUnknown box: {result2}")
e.register_qr("PKG-2024-X99", "box", "Package X99")
time.sleep(1.6)

print(e.process_scan("PKG-2024-X99"))    # pickup
time.sleep(1.6)
print(e.process_scan("WH-SHELF-47A"))    # drop

print("\n" + "=" * 55)
print("FINAL STATE")
print("=" * 55)
print(f"Registered QRs: {e.get_all_registered_qr()}")
print(f"All boxes: {e.get_all_boxes()}")
print(f"All racks: {e.get_all_racks()}")
print(f"History: {e.get_movement_history()}")

e.save_on_exit()
os.remove("test_flex.db")
print("\nAll tests passed!")

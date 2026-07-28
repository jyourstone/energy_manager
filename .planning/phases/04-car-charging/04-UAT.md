---
status: diagnosed
phase: 04-car-charging
source: [04-01-SUMMARY.md, 04-02-SUMMARY.md, 04-03-SUMMARY.md]
started: 2026-02-23T13:00:00Z
updated: 2026-02-23T13:10:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Car entities created per subentry
expected: After adding a car via the EV module subentry flow, four entities appear in HA: car_schedule sensor, departure_time time entity, car_target_soc number, car_max_charge_power number
result: issue
reported: "Two issues: 1. It's not auto detecting my Skoda car. 2. It's asking for my 'Home and plugged in sensor' but we should be able to automatically calculate which car is home and plugged in by checking the Easee status sensor (not disconnected) together with the car binary sensor that shows if the charger is connected or not, along with the location of the vehicle (home or not)."
severity: major

### 2. Car Schedule Sensor state and attributes
expected: The car schedule sensor shows current action as its state (idle/charge/solar_charge). Attributes include schedule list (up to 48 slots), energy_needed_kwh, hours_needed, current_soc, target_soc, and is_preliminary flag
result: skipped
reason: Blocked by Test 1 - car subentry setup issues prevent entity creation

### 3. Departure Time entity defaults and persistence
expected: Departure time entity shows 07:00 as default. Can be changed via the time picker. Value persists after HA restart (uses RestoreEntity)
result: skipped
reason: Blocked by Test 1 - car subentry setup issues prevent entity creation

### 4. Target SOC entity range and behavior
expected: Target SOC number entity has range 10-100%, step 1, default 80%. Slider works and value persists across HA restarts
result: skipped
reason: Blocked by Test 1 - car subentry setup issues prevent entity creation

### 5. Max Charge Power entity range and behavior
expected: Max charge power number entity has range 1.4-22.0 kW, step 0.1. Value persists across HA restarts
result: skipped
reason: Blocked by Test 1 - car subentry setup issues prevent entity creation

### 6. Settings change triggers schedule recalculation
expected: Changing departure time, target SOC, or max charge power triggers a coordinator refresh. The car schedule sensor updates shortly after with a recalculated schedule reflecting the new parameters
result: skipped
reason: Blocked by Test 1 - car subentry setup issues prevent entity creation

### 7. Car device grouping in HA
expected: All four car entities are grouped under a single car device in HA (Devices page). The device shows the car name and links back to the Energy Manager hub device
result: skipped
reason: Blocked by Test 1 - car subentry setup issues prevent entity creation

### 8. Entity translations / friendly names
expected: Entities display translated friendly names in the UI (e.g., "Car Schedule", "Departure Time", "Target SOC", "Max Charge Power") rather than raw entity IDs
result: skipped
reason: Blocked by Test 1 - car subentry setup issues prevent entity creation

### 9. Fallback mode when SOC data is stale
expected: If the charger SOC data hasn't updated for a long time (stale), the scheduler falls back to selecting the cheapest half of available slots instead of calculating exact hours needed. The schedule sensor's is_preliminary attribute or schedule reflects this fallback behavior
result: skipped
reason: Blocked by Test 1 - car subentry setup issues prevent entity creation

### 10. Unit tests pass
expected: Running pytest on the car charging test suite passes all 23 tests: `pytest tests/test_car_charging_scheduler.py`
result: pass

## Summary

total: 10
passed: 1
issues: 1
pending: 0
skipped: 8

## Gaps

- truth: "Car subentry auto-detects available cars and doesn't require manual 'Home and plugged in' sensor selection"
  status: failed
  reason: "User reported: 1. Not auto detecting Skoda car. 2. Asking for 'Home and plugged in sensor' but should auto-calculate from Easee status sensor (not disconnected) + car binary sensor (charger connected) + vehicle location (home or not)"
  severity: major
  test: 1
  root_cause: "Two causes: (A) find_car_integrations() entity matching too narrow - only checks 'battery_level' and 'state_of_charge' patterns, missing mySkoda's 'battery_percentage'. Domain matches via substring but entity scan silently fails. (B) home_plugged_entity has no auto-detection/derivation - raw EntitySelector with no pre-fill. CarChargingCoordinator stores it but never uses it. Should be auto-derived from Easee status + car charger_connected binary sensor + location."
  artifacts:
    - path: "custom_components/energy_manager/auto_detect.py"
      issue: "find_car_integrations() entity patterns too narrow; no home_plugged detection; no logging on silent match failure"
    - path: "custom_components/energy_manager/config_flow.py"
      issue: "CarSubentryFlowHandler never pre-fills home_plugged_entity; no auto-derivation logic"
    - path: "custom_components/energy_manager/coordinator.py"
      issue: "home_plugged_entity stored but unused in _async_update_data"
  missing:
    - "Add 'battery_percentage' and 'soc' to entity matching patterns in find_car_integrations()"
    - "Add 'myskoda' explicitly to platform_patterns"
    - "Add debug logging when domain matches but no battery entity found"
    - "Remove home_plugged_entity from config form"
    - "Auto-detect binary_sensor.*_charger_connected per car device in find_car_integrations()"
    - "Derive 'home and plugged in' state in CarChargingCoordinator from Easee status + car binary sensor + location"
  debug_session: ".planning/debug/car-subentry-autodetect.md"

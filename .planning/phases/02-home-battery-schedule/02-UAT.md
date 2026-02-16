---
status: diagnosed
phase: 02-home-battery-schedule
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md, 02-03-SUMMARY.md, 02-04-SUMMARY.md]
started: 2026-02-16T20:00:00Z
updated: 2026-02-16T20:15:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Schedule Slot Price Precision
expected: In HA, open Battery Schedule sensor attributes. Each slot's "price" value should have at most 4 decimal places (e.g. 0.6412), no long IEEE 754 float artifacts like 0.6411699999999999.
result: pass

### 2. Next Charging Slot Availability
expected: With no charging slots scheduled, the "Next Charging Slot" sensor should show as available (green icon) with state "Unknown" — NOT unavailable (red icon). The entity should be green/active, just with no value.
result: skipped
reason: Icon color distinction not clearly visible in user's HA theme. Icons appeared normal/blue (not red).

### 3. Next Discharging Slot Availability
expected: With no discharging slots scheduled, the "Next Discharging Slot" sensor should show as available (green icon) with state "Unknown" — NOT unavailable (red icon). The entity should be green/active, just with no value.
result: skipped
reason: Same as test 2 — icon color distinction not clearly visible.

### 4. Charge Price Threshold Default
expected: Delete and re-add the integration (or check a fresh install). The "Charge Price Threshold" number entity should default to 1.0 SEK/kWh (not 0.50).
result: pass

### 5. Discharge Price Threshold Default
expected: On a fresh install, the "Discharge Price Threshold" number entity should default to 0.50 SEK/kWh (not 1.50).
result: pass

### 6. Max Charge Power in kW
expected: The "Max Charge Power" number entity should display in kW (not W). It should default to 5.0, have a step of 0.1, and a maximum of 15.0. The unit label should show "kW".
result: pass

### 7. Battery Schedule Sensor State
expected: The Battery Schedule sensor should show a current state (one of: idle, grid_charging, discharging, solar_charging) with schedule slots in attributes. Each slot should have start, end, action, and price fields.
result: issue
reported: "All 48 visible slots show action: idle even though Discharging slots count is 13 and Next Discharging Slot shows Feb 17 07:45. The 48-slot cap shows only the beginning of the schedule window, cutting off before the discharge slots."
severity: major

## Summary

total: 7
passed: 4
issues: 1
pending: 0
skipped: 2

## Gaps

- truth: "Schedule attributes should show charge/discharge slots, not just idle slots"
  status: failed
  reason: "User reported: All 48 visible slots show action: idle even though Discharging slots count is 13 and Next Discharging Slot shows Feb 17 07:45. The 48-slot cap shows only the beginning of the schedule window, cutting off before the discharge slots."
  severity: major
  test: 7
  root_cause: "BatteryScheduleSensor.extra_state_attributes in sensor.py line 163 slices data.schedule[:48] from index 0. With today+tomorrow prices, the first 48 slots cover overnight/morning idle periods. Discharge slots at afternoon/next-day peaks fall beyond index 48 and are invisible."
  artifacts:
    - path: "custom_components/energy_manager/sensor.py"
      issue: "extra_state_attributes uses data.schedule[:48] from index 0, cutting off late-schedule charge/discharge slots"
  missing:
    - "Filter schedule to show non-idle slots (or start from now) so charge/discharge actions are always visible within the 48-slot cap"
  debug_session: ""

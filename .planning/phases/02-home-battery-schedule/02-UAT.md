---
status: complete
phase: 02-home-battery-schedule
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md, 02-03-SUMMARY.md, 02-04-SUMMARY.md, 02-05-SUMMARY.md]
started: 2026-02-16T20:00:00Z
updated: 2026-02-17T00:00:00Z
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
result: pass (re-verified after 02-05 fix)

## Summary

total: 7
passed: 5
issues: 0
pending: 0
skipped: 2

## Gaps

- truth: "Schedule attributes should show charge/discharge slots, not just idle slots"
  status: resolved
  reason: "Fixed by 02-05: filter past slots before 48-slot cap. Re-verified 2026-02-17."
  severity: major
  test: 7
  root_cause: "BatteryScheduleSensor.extra_state_attributes sliced data.schedule[:48] from index 0"
  fix: "02-05-PLAN.md — filter by slot.end > now before cap"

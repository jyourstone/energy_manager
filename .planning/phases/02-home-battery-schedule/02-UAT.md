---
status: complete
phase: 02-home-battery-schedule
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md, 02-03-SUMMARY.md]
started: 2026-02-16T10:00:00Z
updated: 2026-02-16T10:20:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Battery Schedule Sensor Exists
expected: In HA, navigate to Settings > Devices > Energy Manager. A "Battery Schedule" sensor should appear showing a current state value (one of: idle, grid_charging, discharging, solar_charging). The sensor attributes should include a schedule list with charge/discharge slots (capped at 48 entries).
result: issue
reported: "Two things: The price attribute contains a lot of decimals, seems unnecessary? For example: price: 0.6411699999999999. All slots are idle, no planned charging or discharging."
severity: cosmetic

### 2. Next Charging Slot Sensor
expected: A "Next Charging Slot" sensor should appear under the Energy Manager device showing a datetime value for the next scheduled charging slot. If no charging is scheduled, it should show "unknown".
result: issue
reported: "When no charging slots are scheduled, sensor shows 'Unknown' which implies something went wrong. Should display something like 'None' to communicate 'no slots scheduled' vs an error state."
severity: minor

### 3. Next Discharging Slot Sensor
expected: A "Next Discharging Slot" sensor should appear under the Energy Manager device showing a datetime value for the next scheduled discharging slot. If no discharging is scheduled, it should show "unknown".
result: pass

### 4. Charge Price Threshold Number Entity
expected: A "Charge Price Threshold" number entity should appear under the Energy Manager device, defaulting to 0.50 SEK/kWh. It should use a direct input box (not a slider) and be classified as a configuration entity.
result: pass
note: User requests default changed from 0.50 to 1.0 SEK/kWh

### 5. Discharge Price Threshold Number Entity
expected: A "Discharge Price Threshold" number entity should appear under the Energy Manager device, defaulting to 1.50 SEK/kWh. It should use a direct input box (not a slider) and be classified as a configuration entity.
result: pass
note: User requests default changed from 1.50 to 0.50 SEK/kWh

### 6. Max Charge Power Number Entity
expected: A "Max Charge Power" number entity should appear under the Energy Manager device, defaulting to 5000 W. It should use a direct input box and be classified as a configuration entity.
result: pass
note: User requests unit changed from W to kW with 1 decimal (e.g. 5.0 kW)

### 7. Config Flow Battery Step
expected: When adding the Energy Manager integration, after the Nordpool and modules steps, the battery configuration step should show a Battery Capacity (kWh) number field and a Forecast Solar Entity selector. If Forecast.Solar is installed, the entity should be auto-detected and pre-filled.
result: pass

### 8. Schedule Recalculates on Threshold Change
expected: After changing the Charge Price Threshold or Discharge Price Threshold number entity to a new value, the Battery Schedule sensor should update its state and schedule attributes within a few seconds (automatic recalculation).
result: pass

## Summary

total: 8
passed: 6
issues: 2
pending: 0
skipped: 0

## Gaps

- truth: "Schedule slot prices should display with reasonable precision, and schedule should show charge/discharge slots when prices fall outside thresholds"
  status: failed
  reason: "User reported: The price attribute contains a lot of decimals (e.g. 0.6411699999999999). All slots are idle, no planned charging or discharging."
  severity: cosmetic
  test: 1
  root_cause: ""
  artifacts: []
  missing: []
  debug_session: ""

- truth: "Next Charging Slot sensor should clearly communicate 'no slots scheduled' vs an error state"
  status: failed
  reason: "User reported: When no charging slots are scheduled, sensor shows 'Unknown' which implies something went wrong. Should display something like 'None' to communicate no slots scheduled vs an error."
  severity: minor
  test: 2
  root_cause: ""
  artifacts: []
  missing: []
  debug_session: ""

- truth: "Charge Price Threshold default should be 1.0 SEK/kWh instead of 0.50"
  status: failed
  reason: "User reported: Default value should be changed from 0.50 to 1.0 SEK/kWh"
  severity: minor
  test: 4
  root_cause: ""
  artifacts: []
  missing: []
  debug_session: ""

- truth: "Discharge Price Threshold default should be 0.50 SEK/kWh instead of 1.50"
  status: failed
  reason: "User reported: Default value should be changed from 1.50 to 0.50 SEK/kWh"
  severity: minor
  test: 5
  root_cause: ""
  artifacts: []
  missing: []
  debug_session: ""

- truth: "Max Charge Power should display in kW with 1 decimal instead of W"
  status: failed
  reason: "User reported: Should display in kW with 1 decimal (e.g. 5.0 kW) instead of 5000 W"
  severity: minor
  test: 6
  root_cause: ""
  artifacts: []
  missing: []
  debug_session: ""

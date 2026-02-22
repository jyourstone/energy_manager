---
status: complete
phase: 03-ems-controller
source: 03-01-SUMMARY.md, 03-02-SUMMARY.md, 03-03-SUMMARY.md
started: 2026-02-22T10:00:00Z
updated: 2026-02-22T10:15:00Z
---

## Current Test

[testing complete]

## Tests

### 1. EMS Config Flow Step
expected: When configuring the integration, after the battery step there is a dedicated EMS step with fuse rating field (10-63A) and SigenStor control entity fields. Auto-detected values pre-filled.
result: issue
reported: "EMS mode select entity: Works. Max charging limit entity: Cannot be found. The correct Sigen sensor is sensor.sigen_battery_ess_rated_charging_power. Max discharging limit entity: Cannot be found. The correct Sigen sensor is sensor.sigen_plant_ess_rated_discharging_power. L-current sensor: not found, but has 3 phase power sensors in kW (sigen_plant_grid_phase_a/b/c_active_power) that could be converted to A. PV power sensor: not found, has sensor.sigen_inverter_pv_power and sensor.sigen_plant_pv_power (both show same value)."
severity: major

### 2. EMS Status Sensor Exists
expected: After setup, an EMS status sensor appears (sensor.energy_manager_ems_status or similar) showing the current EMS mode as its state (e.g., "command_charging", "max_self_consumption", "standby").
result: pass

### 3. EMS Sensor Attributes
expected: The EMS status sensor has attributes including: target_mode, charge_limit_kw, fuse_headroom_amps, override_reason, command_verified, car_override_active, and pv_charging_active.
result: pass

### 4. EMS Mode Follows Battery Schedule
expected: The EMS mode changes automatically based on the battery schedule -- switching to command_charging during cheap charging slots, max_self_consumption or standby during discharge/idle periods. No manual intervention needed.
result: skipped
reason: Can't verify yet

### 5. Fuse Headroom Calculated
expected: The fuse_headroom_amps attribute shows a numeric value representing available fuse capacity (fuse rating minus current phase load). The charge limit adjusts dynamically if load approaches the fuse rating.
result: issue
reported: "fuse_headroom_amps stays at 18 A all the time, no matter the current power/phase usage."
severity: major

### 6. Car Priority Override
expected: When a car is scheduled to charge and the Easee charger shows a plugged-in status, the EMS overrides battery charging (pauses it) to free fuse capacity. The car_override_active attribute shows True and override_reason explains why.
result: skipped
reason: Will test later

### 7. Command Verification
expected: After the EMS sends a mode-change command, the command_verified attribute reflects whether the device confirmed the new mode. If verification fails within 60 seconds, a warning is logged.
result: skipped
reason: Will test later

### 8. Unit Tests Pass
expected: Running the test suite (pytest tests/) shows all 40 tests passing with zero failures. The 27 EMS controller tests cover mode selection, fuse protection, car priority, PV hysteresis, and safety guards.
result: pass

## Summary

total: 8
passed: 3
issues: 2
pending: 0
skipped: 3

## Gaps

- truth: "EMS config flow auto-detects and pre-fills all SigenStor control entities"
  status: failed
  reason: "User reported: EMS mode select entity: Works. Max charging limit entity: Cannot be found. The correct Sigen sensor is sensor.sigen_battery_ess_rated_charging_power. Max discharging limit entity: Cannot be found. The correct Sigen sensor is sensor.sigen_plant_ess_rated_discharging_power. L-current sensor: not found, but has 3 phase power sensors in kW (sigen_plant_grid_phase_a/b/c_active_power) that could be converted to A. PV power sensor: not found, has sensor.sigen_inverter_pv_power and sensor.sigen_plant_pv_power (both show same value)."
  severity: major
  test: 1
  artifacts: []
  missing: []

- truth: "fuse_headroom_amps dynamically reflects available fuse capacity based on current phase load"
  status: failed
  reason: "User reported: fuse_headroom_amps stays at 18 A all the time, no matter the current power/phase usage."
  severity: major
  test: 5
  artifacts: []
  missing: []

---
status: diagnosed
phase: 03-ems-controller
source: 03-01-SUMMARY.md, 03-02-SUMMARY.md, 03-03-SUMMARY.md, 03-04-SUMMARY.md
started: 2026-02-23T10:00:00Z
updated: 2026-02-23T10:15:00Z
---

## Current Test

[testing complete]

## Tests

### 1. EMS Config Flow Step
expected: When configuring the integration, after the battery step there is a dedicated EMS step with fuse rating (10-63A), EMS mode select, charge/discharge limits, grid power/L-current entity, and PV power entity. Auto-detected values pre-filled.
result: issue
reported: "All values exist and are auto-detected, but fuse protection uses total grid power (sigen_plant_grid_active_power) instead of per-phase power. One phase could be at 30A while total looks fine. Need to use individual phase sensors: sigen_plant_grid_phase_a/b/c_active_power"
severity: major

### 2. EMS Status Sensor Exists
expected: After setup, an EMS status sensor appears showing the current EMS mode as its state (e.g., "command_charging", "max_self_consumption", "standby").
result: skipped
reason: Will verify later

### 3. EMS Sensor Attributes
expected: The EMS status sensor has attributes including: target_mode, charge_limit_kw, fuse_headroom_amps, override_reason, command_verified, car_override_active, and pv_charging_active.
result: pass

### 4. Fuse Headroom Reflects Actual Load
expected: The fuse_headroom_amps attribute shows a dynamic value based on actual grid load (not static 18A). The value changes as household load changes. Calculated as: fuse_rating - current_load_amps - safety_buffer.
result: pass
note: Works but uses total grid power — per-phase issue from Test 1 applies

### 5. EMS Mode Follows Battery Schedule
expected: The EMS mode changes automatically based on the battery schedule -- switching to command_charging during cheap charging slots, max_self_consumption or standby during discharge/idle periods.
result: skipped
reason: Will verify later

### 6. Car Priority Override
expected: When a car is scheduled to charge and the Easee charger shows a plugged-in status, the EMS overrides battery charging to free fuse capacity. car_override_active shows True and override_reason explains why.
result: skipped
reason: Will verify later

### 7. Command Verification
expected: After the EMS sends a mode-change command, the command_verified attribute reflects whether the device confirmed the new mode. If verification fails within 60 seconds, a warning is logged.
result: skipped
reason: Will verify later

### 8. Unit Tests Pass
expected: Running pytest tests/ shows all 50 tests passing with zero failures.
result: pass

## Summary

total: 8
passed: 3
issues: 1
pending: 0
skipped: 4

## Gaps

- truth: "Fuse protection calculates headroom per-phase to catch imbalanced loads"
  status: failed
  reason: "User reported: fuse protection uses total grid power (sigen_plant_grid_active_power) instead of per-phase. One phase could be at 30A while total looks fine. Need individual phase sensors: sigen_plant_grid_phase_a/b/c_active_power"
  severity: major
  test: 1
  root_cause: |
    Single-phase architecture throughout the entire stack. auto_detect.py actively excludes per-phase sensors
    ("phase_" not in entity_id_lower). coordinator.py _read_grid_current_amps() divides total power by 3*230V
    assuming balanced phases. On imbalanced loads (e.g., phase A at 30A, B/C at 5A), the system sees 13.3A average
    and allows charging when phase A is 10A over the fuse rating.
  artifacts:
    - path: "custom_components/energy_manager/const.py"
      issue: "Only one config key CONF_GRID_POWER_ENTITY, no per-phase keys"
    - path: "custom_components/energy_manager/auto_detect.py"
      issue: "Lines 219/259 actively filter out per-phase sensors with 'phase_' exclusion"
    - path: "custom_components/energy_manager/coordinator.py"
      issue: "_read_grid_current_amps() divides total by 3*230V, balanced-load assumption"
    - path: "custom_components/energy_manager/config_flow.py"
      issue: "Only one EntitySelector for grid power in EMS step"
    - path: "custom_components/energy_manager/strings.json"
      issue: "UI text says 'total grid active power'"
    - path: "custom_components/energy_manager/translations/en.json"
      issue: "Same UI text"
  missing:
    - "Add three per-phase config keys (grid_phase_a/b/c_entity)"
    - "Update auto-detect to find per-phase sensors instead of excluding them"
    - "Add three entity selectors to EMS config step"
    - "Replace _read_grid_current_amps() to read all three phases, convert each to amps (abs(P)/230), use max()"
    - "Keep single total-power path as fallback for single-phase installations"
    - "ems_controller.py needs no changes -- already expects highest phase current"
  debug_session: ".planning/debug/per-phase-fuse-protection.md"

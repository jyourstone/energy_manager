---
phase: 03-ems-controller
verified: 2026-02-23T12:00:00Z
status: passed
score: 16/16 must-haves verified
re_verification:
  previous_status: passed
  previous_score: 11/11
  gaps_closed:
    - "Fuse protection uses the highest loaded phase (not balanced average) for headroom calculation"
    - "User can configure three per-phase grid power sensors in the EMS config step"
    - "Auto-detection finds per-phase sensors (sigen_plant_grid_phase_a/b/c_active_power)"
    - "Single total-power sensor remains as fallback for single-phase installations"
    - "ems_controller.py is untouched -- it already expects highest-phase current"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Re-run EMS Config Flow auto-detection with real SigenStor hardware"
    expected: "Per-phase entities (phase_a/b/c_active_power) are pre-filled, max charging/discharging limit entities pre-filled, PV power pre-filled -- all from real firmware entity names"
    why_human: "Requires real SigenStor device with actual HA entity registry entries to confirm patterns match production firmware"
  - test: "Verify fuse_headroom_amps uses worst-case phase after per-phase sensors configured"
    expected: "On an unbalanced 3-phase load (e.g. phase A at 15A, B/C at 3A), fuse_headroom_amps reflects the 15A worst case (not 7A average). Headroom = 20 - 15 - 2 = 3A, not 20 - 7 - 2 = 11A."
    why_human: "Requires live 3-phase electrical load and a running HA instance with real per-phase sensor state updates"
---

# Phase 3: EMS Controller Re-Verification Report

**Phase Goal:** The integration actively controls the battery EMS mode in real time based on the schedule, with fuse protection ensuring safe operation across all connected devices

**Verified:** 2026-02-23T12:00:00Z
**Status:** passed
**Re-verification:** Yes -- after plan 03-05 (per-phase fuse protection gap closure)

## Context

Previous verification (2026-02-22) passed 11/11 automated truths. UAT then identified a critical safety issue:

**Per-phase fuse protection missing**: The fuse headroom calculation divided total grid power by 3 (balanced-load assumption). On unbalanced 3-phase loads, phase A at 30A with B/C at 5A would yield a 13.3A average, allowing battery charging when phase A was already 10A over a 20A fuse. Plan 03-05 was executed (commits `a2c374c` and `3ad8cd6`) to fix this.

This re-verification confirms 03-05 changes are correct, complete, and no regressions were introduced against the prior 11 truths.

## Goal Achievement

### Observable Truths (Original -- plans 01-04)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Battery EMS mode changes automatically based on the current schedule slot | VERIFIED | `_async_update_data()` in coordinator.py unchanged -- schedule-driven mode dispatch intact; 27 EMS controller tests pass |
| 2 | User can see an EMS status sensor showing the current mode and available fuse headroom in amps | VERIFIED | `EMSStatusSensor` at sensor.py line 293 unchanged; `fuse_headroom_amps` attribute still returned from `EMSData` |
| 3 | Fuse protection dynamically limits battery charging power when phase load approaches the configured fuse rating, calculated amp values always clamped to safe range | VERIFIED | `compute_ems_state()` in ems_controller.py unchanged; `clamp_amps()` and `max(0.0, ...)` intact; coordinator now feeds worst-case phase current instead of balanced average |
| 4 | When a car is scheduled to charge and plugged in, battery charging pauses automatically to free fuse capacity | VERIFIED | Car priority logic in ems_controller.py unchanged; `test_car_priority_pauses_battery_charging` passes |
| 5 | After sending a mode-change command, the integration reads back actual device state to verify the command took effect | VERIFIED | `_schedule_verification()` and `_check_verification()` in coordinator.py unchanged |
| 6 | Auto-detection finds SigenStor charge limit entity (sensor domain, ess_rated_charging pattern) | VERIFIED | auto_detect.py lines 172-190: `domain in ("number", "sensor")`, `ess_rated_charging` pattern; `TestChargeLimit::test_detects_charge_limit_sensor_domain` PASSES |
| 7 | Auto-detection finds SigenStor discharge limit entity (sensor domain, ess_rated_discharging pattern) | VERIFIED | auto_detect.py lines 192-211: `domain in ("number", "sensor")`, `ess_rated_discharging`/`rated_discharging_power` patterns; `TestDischargeLimit::test_detects_discharge_limit_sensor_domain` PASSES |
| 8 | Auto-detection finds L-current entity via phase_a_active_power / grid_phase fallback patterns | VERIFIED | Now tracked as grid-power auto-detection; auto_detect.py lines 230-271: per-phase detection in sigen scan; lines 294-316: global fallback for all three phases |
| 9 | Auto-detection finds PV power entity via global fallback scan with plant-over-inverter preference | VERIFIED | auto_detect.py lines 338-371: global PV fallback, prefers sigen-prefixed, prefers plant over inverter; `TestPVPlantPreference` PASSES |
| 10 | Config flow EMS step accepts sensor domain for charge/discharge limit entity selectors | VERIFIED | config_flow.py lines 345-350: `EntitySelectorConfig(domain=["sensor", "number"])` for both |
| 11 | EMSCoordinator logs a warning at startup when no grid power entity is configured | VERIFIED | coordinator.py lines 621-624: `_LOGGER.warning(...)` in else branch when neither per-phase nor total grid power is configured |

### Observable Truths (Plan 03-05 -- per-phase fuse protection)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 12 | Fuse protection uses the highest loaded phase (not balanced average) for headroom calculation | VERIFIED | coordinator.py lines 780-797: `_read_grid_current_amps()` uses `max(phase_amps)` when all three per-phase entities configured |
| 13 | User can configure three per-phase grid power sensors in the EMS config step | VERIFIED | config_flow.py lines 354-362: three `vol.Optional(CONF_GRID_PHASE_[ABC]_ENTITY)` EntitySelector fields; stored at lines 310-318; written to options at lines 446-448 |
| 14 | Auto-detection finds per-phase sensors (sigen_plant_grid_phase_a/b/c_active_power) | VERIFIED | auto_detect.py lines 230-271: per-phase detection in sigen scan (phase_a/b/c_active_power patterns); lines 294-316: global fallback scan for all three; `TestPerPhaseGridPower` class (4 tests) all PASS |
| 15 | Single total-power sensor remains as fallback for single-phase installations | VERIFIED | coordinator.py lines 799-808: fallback `abs(power) / (3.0 * 230.0)` when not all three per-phase entities configured; auto_detect.py lines 213-228: total grid power detection preserved; `TestGridPower::test_skips_per_phase_grid_power` PASSES |
| 16 | ems_controller.py is untouched -- it already expects highest-phase current | VERIFIED | `git log -- custom_components/energy_manager/ems_controller.py` shows last commit is `5a373bb` (plan 03-01), not touched by 03-04 or 03-05 |

**Score:** 16/16 truths verified

## Required Artifacts

### Plan 03-05 Artifacts (New)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `custom_components/energy_manager/const.py` | Three per-phase config keys | VERIFIED | Lines 91-93: `CONF_GRID_PHASE_A_ENTITY`, `CONF_GRID_PHASE_B_ENTITY`, `CONF_GRID_PHASE_C_ENTITY` |
| `custom_components/energy_manager/auto_detect.py` | Per-phase sensor detection | VERIFIED | Lines 25-27: imports all three; lines 230-271: sigen scan detection; lines 294-316: global fallback |
| `custom_components/energy_manager/config_flow.py` | Three per-phase EntitySelector fields in EMS step | VERIFIED | Lines 310-318: user_input storage; lines 354-362: schema; lines 446-448: `_create_entry()` options |
| `custom_components/energy_manager/coordinator.py` | Per-phase current calculation using max() | VERIFIED | Lines 558-566: `__init__` stores per-phase entities; lines 598-612: phase-aware listener registration; lines 780-797: `max(phase_amps)` calculation |
| `custom_components/energy_manager/strings.json` | Per-phase labels and descriptions | VERIFIED | Lines 51-53: labels; lines 62-64: descriptions; line 61: grid_power_entity updated to clarify fallback role |
| `custom_components/energy_manager/translations/en.json` | Per-phase labels and descriptions (mirror of strings.json) | VERIFIED | All three `grid_phase_[abc]_entity` keys present in both `data` and `data_description` sections |
| `tests/test_auto_detect_ems.py` | Per-phase detection tests (TestPerPhaseGridPower class) | VERIFIED | Lines 306-393: `TestPerPhaseGridPower` with 4 tests: single phase A, all three, coexistence with total, fallback scan |

### Original Phase Artifacts (Regression Check)

| Artifact | Status | Evidence |
|----------|--------|----------|
| `custom_components/energy_manager/ems_controller.py` | VERIFIED | Last modified commit `5a373bb` (plan 03-01); not touched in 03-04 or 03-05; all 27 EMS controller tests pass |
| `tests/test_ems_controller.py` | VERIFIED | 27 tests, all pass |
| `custom_components/energy_manager/sensor.py` | VERIFIED | EMSStatusSensor class unchanged; not modified in 03-05 |

## Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `auto_detect.py find_sigenstor_ems_entities()` | `config_flow.py async_step_ems()` | `CONF_GRID_PHASE_[ABC]_ENTITY` keys in returned dict, pre-filled via `_add_suggested_values` | WIRED | auto_detect.py returns per-phase keys; config_flow.py line 328 calls `find_sigenstor_ems_entities`; line 371 applies suggestions |
| `config_flow.py _create_entry()` | `coordinator.py EMSCoordinator.__init__()` | `entry.options.get(CONF_GRID_PHASE_[ABC]_ENTITY)` | WIRED | config_flow.py lines 446-448 write per-phase keys to options; coordinator.py lines 558-566 read them in `__init__` |
| `coordinator.py _read_grid_current_amps()` | `ems_controller.py compute_ems_state()` | `max(phase_amps)` passed as `current_l_amps` | WIRED | coordinator.py line 669: `l_current = self._read_grid_current_amps()`; line 685: `current_l_amps=l_current` passed to `compute_ems_state()` |
| `coordinator.py _async_setup()` | `_handle_fuse_update` | `async_track_state_change_event` for per-phase entities | WIRED | coordinator.py lines 598-612: all three per-phase entities registered for state-change callback when configured |

## Test Results

| Test File | Tests | Status |
|-----------|-------|--------|
| `tests/test_ems_controller.py` | 27 | All PASS |
| `tests/test_auto_detect_ems.py` | 15 | All PASS (7 original + 4 new per-phase + regressions) |
| `tests/test_battery_scheduler.py` | 12 | All PASS |
| **Total** | **54** | **54 passed, 0 failed** |

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `config_flow.py` | 157 | `return {}` in `get_supported_subentry_types` | Info | Correct behavior: returns empty dict when EV module disabled. Not a stub. |
| `config_flow.py` | 464-480 | Stub OptionsFlow (empty schema, placeholder comment) | Info | Unchanged from previous verification -- explicitly deferred to Phase 6 by design |

No anti-patterns introduced by the 03-05 changes.

## Human Verification Required

### 1. Re-run EMS Config Flow Auto-Detection with Real SigenStor Hardware

**Test:** On a live HA instance with a real SigenStor inverter, navigate to the EMS config step (or reconfigure).

**Expected:**
- Phase A grid power: pre-filled with an entity matching `phase_a_active_power` (e.g. `sensor.sigen_plant_grid_phase_a_active_power`)
- Phase B grid power: pre-filled with `phase_b_active_power` entity
- Phase C grid power: pre-filled with `phase_c_active_power` entity
- Total grid power: pre-filled with a `grid_active_power` entity (fallback)
- Max charging limit, discharging limit, EMS select, PV power: all pre-filled as before

**Why human:** Requires real SigenStor device with actual HA entity registry. Unit tests use mocks -- real hardware confirms firmware entity naming matches the patterns.

### 2. Verify Per-Phase Worst-Case Fuse Headroom on Unbalanced Load

**Test:** After configuring all three per-phase sensors, apply an unbalanced load (e.g. heater on phase A only). Check `fuse_headroom_amps` attribute on the EMS status sensor.

**Expected:** `fuse_headroom_amps` reflects the worst-case (highest) phase current, not an average. Example: phase A at 15A draw, fuse 20A, buffer 2A -- headroom should be 3A (not ~11A from a balanced-load assumption).

**Why human:** Requires live 3-phase load variation and a running HA instance with real per-phase sensor state updates to observe the dynamic change.

## Gaps Summary

No gaps remain. All 5 new truths from plan 03-05 are verified against the actual codebase:

- Per-phase config keys: VERIFIED in const.py lines 91-93
- Per-phase auto-detection (sigen scan + global fallback): VERIFIED in auto_detect.py lines 230-316
- Per-phase EntitySelector fields in config flow (schema + storage + options): VERIFIED in config_flow.py lines 310-318, 354-362, 446-448
- Per-phase max() calculation in coordinator: VERIFIED in coordinator.py lines 780-797
- ems_controller.py untouched: VERIFIED via git log

All 11 original truths pass regression checks. 54/54 tests pass.

---

_Verified: 2026-02-23T12:00:00Z_
_Verifier: Claude (gsd-verifier)_
_Re-verification after plan 03-05 (per-phase fuse protection)_

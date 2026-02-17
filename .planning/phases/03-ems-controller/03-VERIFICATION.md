---
phase: 03-ems-controller
verified: 2026-02-17T21:30:00Z
status: passed
score: 5/5 truths verified
re_verification: false
---

# Phase 3: EMS Controller Verification Report

**Phase Goal:** The integration actively controls the battery EMS mode in real time based on the schedule, with fuse protection ensuring safe operation across all connected devices

**Verified:** 2026-02-17T21:30:00Z
**Status:** passed
**Re-verification:** No - initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Battery EMS mode changes automatically based on the current schedule slot | ✓ VERIFIED | EMSCoordinator._async_update_data() calls compute_ems_state() with target_ems_mode from BatteryScheduleCoordinator, sends commands via _send_ems_mode() |
| 2 | User can see an EMS status sensor showing the current mode and available fuse headroom in amps | ✓ VERIFIED | EMSStatusSensor exists in sensor.py, shows current_mode as state, fuse_headroom_amps in attributes, conditionally created when ems_coordinator exists |
| 3 | Fuse protection dynamically limits battery charging power when phase load approaches the configured fuse rating, calculated amp values are always clamped to safe range | ✓ VERIFIED | compute_ems_state() calculates headroom with max(0.0, ...), clamp_amps() hard-clamps all values, charge_limit_kw computed from headroom_kw. Test test_fuse_headroom_never_negative passes |
| 4 | When a car is scheduled to charge and plugged in, battery charging pauses automatically to free fuse capacity | ✓ VERIFIED | compute_ems_state() checks car_scheduled AND car_plugged_in, returns standby with override_reason="car_charging_priority". Test test_car_priority_pauses_battery_charging passes |
| 5 | After sending a mode-change command, the integration reads back actual device state to verify the command took effect | ✓ VERIFIED | _schedule_verification() stores pending verification, _check_verification() reads entity state and logs warnings after 60s timeout |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `custom_components/energy_manager/ems_controller.py` | Pure EMS calculation module with compute_ems_state, clamp_amps, EMSDecision, PVHysteresisTracker | ✓ VERIFIED | 247 lines, zero HA imports, all functions exist |
| `tests/test_ems_controller.py` | Exhaustive unit tests for all EMS decision paths | ✓ VERIFIED | 465 lines, 27 tests covering EMS-01 through EMS-08, all pass |
| `custom_components/energy_manager/coordinator.py` | EMSCoordinator class and EMSData dataclass | ✓ VERIFIED | EMSCoordinator at line 510, EMSData dataclass exists, chains to BatteryScheduleCoordinator |
| `custom_components/energy_manager/const.py` | EMS configuration constants | ✓ VERIFIED | CONF_FUSE_RATING, EMS_MODE_MAP, EMS_UPDATE_INTERVAL_SECONDS, MAX_CHARGE_LIMIT_KW all exist |
| `custom_components/energy_manager/config_flow.py` | EMS config step with fuse rating and control entity fields | ✓ VERIFIED | async_step_ems() at line 283, fuse rating required field with validation 10-63A |
| `custom_components/energy_manager/auto_detect.py` | SigenStor EMS entity auto-detection | ✓ VERIFIED | find_sigenstor_ems_entities() function exists |
| `custom_components/energy_manager/__init__.py` | EMSCoordinator lifecycle management | ✓ VERIFIED | EMSCoordinator created at line 71 when battery_coordinator exists, passed to EnergyManagerData |
| `custom_components/energy_manager/sensor.py` | EMSStatusSensor entity | ✓ VERIFIED | EMSStatusSensor class at line 293, conditionally created when ems_coordinator exists |
| `custom_components/energy_manager/strings.json` | Translation strings for EMS sensor and config step | ✓ VERIFIED | ems config step and ems_status sensor translations exist, valid JSON |
| `custom_components/energy_manager/translations/en.json` | English translations for EMS sensor and config step | ✓ VERIFIED | Mirrors strings.json, ems config step and ems_status sensor translations exist, valid JSON |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| tests/test_ems_controller.py | ems_controller.py | direct import of compute_ems_state, clamp_amps | ✓ WIRED | Line 16: from custom_components.energy_manager.ems_controller import |
| coordinator.py | ems_controller.py | import compute_ems_state | ✓ WIRED | Line 66: from .ems_controller import PVHysteresisTracker, compute_ems_state |
| coordinator.py | BatteryScheduleCoordinator | async_add_listener chaining | ✓ WIRED | Line 580: battery_coordinator.async_add_listener() |
| config_flow.py | auto_detect.py | find_sigenstor_ems_entities call | ✓ WIRED | Function imported and called in async_step_ems |
| __init__.py | coordinator.py | EMSCoordinator import and instantiation | ✓ WIRED | Line 71: EMSCoordinator(hass, entry, battery_coordinator) |
| sensor.py | coordinator.py | EMSData import for type annotation | ✓ WIRED | EMSStatusSensor accesses coordinator.data typed as EMSData |
| sensor.py | __init__.py | entry.runtime_data.ems_coordinator access | ✓ WIRED | Line 60: if ems_coordinator is not None, EMSStatusSensor created |

### Requirements Coverage

| Requirement | Status | Supporting Evidence |
|-------------|--------|---------------------|
| EMS-01: Integration sets battery EMS mode based on current schedule | ✓ SATISFIED | compute_ems_state() selects mode from target_ems_mode, EMSCoordinator sends commands via _send_ems_mode() |
| EMS-02: Fuse protection dynamically limits battery charging power | ✓ SATISFIED | Fuse headroom calculation in compute_ems_state() line 187, charge_limit_kw capped by headroom_kw |
| EMS-03: Car priority pauses battery charging | ✓ SATISFIED | car_scheduled AND car_plugged_in override in compute_ems_state(), returns standby with override_reason |
| EMS-04: Safety guards enforce hard limits, amp values clamped | ✓ SATISFIED | clamp_amps() at line 228, max(0.0, ...) for headroom, tests verify negative values clamped to 0 |
| EMS-05: Command verification reads back actual state | ✓ SATISFIED | _schedule_verification() and _check_verification() methods in EMSCoordinator, 60s timeout with warnings |
| EMS-06: Fuse rating is required config field with validation | ✓ SATISFIED | async_step_ems() in config_flow.py line 283, fuse_rating required, validated 10-63A range |
| EMS-07: User can view EMS status sensor showing mode and fuse headroom | ✓ SATISFIED | EMSStatusSensor shows current_mode as state, fuse_headroom_amps in attributes |
| EMS-08: PV opportunistic charging activates with sufficient solar | ✓ SATISFIED | PVHysteresisTracker state machine, compute_ems_state() checks pv_hysteresis_active and battery SOC, tests verify |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| config_flow.py | 446-450 | Placeholder options flow | ℹ️ Info | Options flow deferred to Phase 6, not a blocker for Phase 3 |

**No blockers found.** The placeholder in config_flow.py is for the options flow (Phase 6), not EMS functionality.

### Human Verification Required

#### 1. Real Device Command Execution

**Test:** Configure the integration with a real SigenStor battery system, set up a charging schedule, and observe the battery EMS mode changing automatically at schedule transitions.

**Expected:** 
- Battery EMS mode select entity changes from "Maximum Self Consumption" to "Command Charging (PV First)" when the schedule enters a charging slot
- Battery EMS mode changes back to "Maximum Self Consumption" when the charging slot ends
- EMS status sensor updates to reflect the current mode
- Charging limit is set before the mode changes to command_charging
- Mode changes before charging limit is set to zero when exiting command_charging

**Why human:** Real hardware integration requires actual SigenStor device with working entity IDs. Cannot be verified without physical hardware or device simulator.

#### 2. Fuse Protection Limiting

**Test:** During a scheduled battery charging period, increase household load (e.g., turn on high-power appliances) to approach the configured fuse rating. Observe the battery charging limit decrease dynamically.

**Expected:**
- As L-current sensor increases, fuse_headroom_amps in EMS status sensor attributes decreases
- When fuse headroom approaches zero, charge_limit_kw automatically reduces
- Battery charging limit entity value updates to reflect the safe charging power
- No circuit breaker trips occur

**Why human:** Requires real electrical load monitoring, physical appliances, and verification that actual current draw is safely limited. Safety-critical behavior that must be validated with real electrical systems.

#### 3. Car Priority Override

**Test:** Set up a car charging schedule. When a car is plugged into the Easee charger during a battery charging period, observe that battery charging pauses (EMS mode goes to standby).

**Expected:**
- car_override_active attribute in EMS status sensor becomes true
- current_mode changes to "standby"
- override_reason shows "car_charging_priority"
- Battery stops charging (verifiable via SigenStor power sensors)
- When car unplugs or charging schedule ends, battery resumes charging

**Why human:** Requires coordinated testing of car charger state detection and battery control. Timing-sensitive behavior that needs human observation of multiple devices.

#### 4. Command Verification and Error Handling

**Test:** Temporarily make the EMS select entity unavailable (e.g., unplug the battery or disable the SigenStor integration) during a schedule transition. Observe that the integration logs warnings and handles the unavailable state gracefully.

**Expected:**
- Integration logs "EMS entity X is unavailable, skipping command"
- No exceptions or crashes occur
- command_verified attribute becomes false
- After entity becomes available again, commands resume successfully

**Why human:** Requires deliberately creating error conditions (device unavailability) and verifying logging output and recovery behavior. Error path testing that needs human-triggered failures.

#### 5. PV Opportunistic Charging with Hysteresis

**Test:** On a partly cloudy day with fluctuating solar production, observe that PV opportunistic charging does not rapidly turn on/off. The system should require consecutive high-power readings before activating and consecutive low-power readings before deactivating.

**Expected:**
- PV power fluctuates above and below 500W threshold
- pv_charging_active does not change immediately on threshold crossing
- System waits for 2+ consecutive readings above threshold before activating
- Battery charging starts only when PV is stable above threshold
- No rapid on/off cycling visible in battery control commands

**Why human:** Requires real-time observation of solar power fluctuations and battery response over an extended period (multiple update cycles). Timing and hysteresis behavior best validated by human observation of actual solar conditions.

---

## Verification Summary

**Status: PASSED** - All automated checks passed, all must-haves verified, no blockers found.

### What Was Verified

**Artifacts (10/10 verified):**
- Pure EMS controller module exists with zero HA imports
- Comprehensive test suite with 27 tests, all passing
- EMSCoordinator chains to BatteryScheduleCoordinator
- Config flow EMS step with fuse rating validation
- EMS status sensor exposing mode and fuse headroom
- Complete translations in strings.json and en.json

**Key Links (7/7 wired):**
- Tests import and use ems_controller module
- EMSCoordinator imports and calls compute_ems_state()
- BatteryScheduleCoordinator listener chaining functional
- Config flow calls auto-detection functions
- __init__.py creates EMSCoordinator and wires lifecycle
- Sensor accesses coordinator data
- All imports resolved, no orphaned code

**Requirements (8/8 satisfied):**
- EMS-01 through EMS-08 all satisfied with verifiable evidence
- Mode selection logic complete
- Fuse protection math correct with negative-value clamping
- Car priority override implemented
- Command verification with timeout tracking
- Config flow with fuse rating validation
- EMS status sensor with all required attributes
- PV opportunistic charging with hysteresis

**Safety-Critical:**
- Fuse headroom calculation never returns negative values (test verified)
- clamp_amps() hard-clamps all values to [0, max_amps] range
- Safe command ordering: limit before mode when entering charging, mode before limit when exiting
- Entity availability checked before every service call
- Command verification tracks pending verifications and logs warnings on timeout

**Test Coverage:**
- 27 EMS controller tests (all passing)
- Covers all decision paths: mode selection, fuse protection, car priority, safety guards, PV opportunistic, PV hysteresis
- Zero HA imports in ems_controller.py (verifiable: grep returns 0)
- All 40 total tests pass with zero regressions

### What Needs Human Verification

**5 items require real hardware testing:**

1. Real SigenStor device command execution and mode changes
2. Fuse protection behavior under actual electrical load
3. Car priority override with Easee charger state detection
4. Command verification error handling with unavailable entities
5. PV opportunistic charging hysteresis under real solar conditions

These are inherently non-automatable (require physical devices, real electrical systems, timing-dependent behavior, and error condition creation).

### Commits Verified

All 6 task commits from SUMMARYs exist in git history:

- `5b96ac9` - test(03-01): add failing tests for EMS controller calculations
- `5a373bb` - feat(03-01): implement pure EMS controller calculations
- `338742c` - feat(03-02): add EMS constants, auto-detection, and config flow EMS step
- `1a88de0` - feat(03-02): add EMSCoordinator with command sending and verification
- `576473d` - feat(03-03): wire EMSCoordinator into lifecycle and create EMS status sensor
- `6b64e16` - feat(03-03): add EMS status sensor translation and sync en.json with EMS config step

### Technical Quality

- **Pure module pattern:** ems_controller.py follows Phase 2 battery_scheduler.py pattern (zero HA imports)
- **TDD compliance:** RED-GREEN-REFACTOR cycle followed (failing tests committed before implementation)
- **Safety-first ordering:** Fuse headroom calculated before mode decisions
- **Coordinator chaining:** Three-tier chain operational: PriceCoordinator -> BatteryScheduleCoordinator -> EMSCoordinator
- **Conditional creation:** EMS components only created when battery module enabled (follows established pattern)
- **Translation completeness:** Both strings.json and en.json have matching EMS entries, valid JSON
- **Zero anti-patterns:** No stub code, no TODO/FIXME in EMS files, no placeholder implementations

---

**Verified:** 2026-02-17T21:30:00Z  
**Verifier:** Claude (gsd-verifier)  
**Conclusion:** Phase 3 goal achieved. Integration actively controls battery EMS mode in real time based on schedule with fuse protection. Ready to proceed to Phase 4 (Car Charging).

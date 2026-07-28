---
phase: 03-ems-controller
plan: 02
subsystem: ems
tags: [coordinator, service-calls, fuse-protection, config-flow, auto-detect, command-verification]

# Dependency graph
requires:
  - phase: 03-ems-controller
    plan: 01
    provides: "compute_ems_state(), PVHysteresisTracker, EMSDecision dataclass"
  - phase: 02-home-battery-schedule
    provides: "BatteryScheduleCoordinator with target_ems_mode field"
provides:
  - "EMSCoordinator -- real-time EMS control coordinator"
  - "EMSData dataclass -- EMS coordinator output"
  - "find_sigenstor_ems_entities() -- SigenStor EMS entity auto-detection"
  - "async_step_ems() -- config flow EMS step with fuse rating and control entities"
  - "EMS constants (CONF_FUSE_RATING, EMS_MODE_MAP, etc.)"
affects: [03-03-ems-sensors]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "EMSCoordinator chains to BatteryScheduleCoordinator via async_add_listener"
    - "Safe command ordering: limit-first for charge mode, mode-first for others"
    - "Command verification with 60-second timeout via _schedule_verification/_check_verification"
    - "Entity availability checks before service calls (Pitfall 1 mitigation)"
    - "Config flow EMS step between battery and EV with auto-detected SigenStor entities"

key-files:
  created: []
  modified:
    - "custom_components/energy_manager/coordinator.py"
    - "custom_components/energy_manager/const.py"
    - "custom_components/energy_manager/config_flow.py"
    - "custom_components/energy_manager/auto_detect.py"
    - "custom_components/energy_manager/strings.json"

key-decisions:
  - "Fuse rating in config flow (not NumberEntity) -- hardware constant that should not change casually"
  - "EMS config flow as separate step between battery and EV (not merged into battery step)"
  - "L-current fallback scan across ALL entities (not just SigenStor) for template sensor support"
  - "Car plugged-in detection via charger status entity (fast Easee) not car integration (slow cloud polling)"
  - "Command verification uses coordinator polling cycle (30s) not asyncio.sleep"

patterns-established:
  - "Safe command ordering pattern for mode transitions (Research Pitfall 3)"
  - "Entity availability guard before every service call"
  - "Fallback entity scan across full registry when primary detection misses"

# Metrics
duration: 4min
completed: 2026-02-17
---

# Phase 3 Plan 02: EMSCoordinator and Config Flow Summary

**EMSCoordinator with BatteryScheduleCoordinator chaining, safe-ordered service calls, command verification, config flow EMS step with fuse rating validation and SigenStor auto-detection**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-17T21:02:31Z
- **Completed:** 2026-02-17T21:07:28Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- EMSCoordinator chains to BatteryScheduleCoordinator and re-evaluates on schedule updates, L-current changes, and charger status changes
- Safe command ordering: when switching TO command_charging, charge limit is sent before mode; when switching away, mode changes first
- Command verification tracks pending verifications with 60-second timeout and logs warnings on failure
- Config flow has a dedicated EMS step with fuse rating (required, validated 10-63A) and auto-detected SigenStor control entities
- SigenStor EMS entities auto-detected (select, number, sensor) with L-current fallback scan across all entities
- All 40 existing tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Add EMS constants, auto-detection, and config flow EMS step** - `338742c` (feat)
2. **Task 2: Build EMSCoordinator with command sending and verification** - `1a88de0` (feat)

## Files Created/Modified
- `custom_components/energy_manager/const.py` - Added EMS constants: CONF_FUSE_RATING, EMS_MODE_MAP, EMS_UPDATE_INTERVAL_SECONDS, MAX_CHARGE_LIMIT_KW, and entity config keys
- `custom_components/energy_manager/auto_detect.py` - Added find_sigenstor_ems_entities() scanning for EMS select, charge/discharge limit numbers, L-current and PV power sensors
- `custom_components/energy_manager/config_flow.py` - Added async_step_ems() between battery and EV steps with fuse rating validation and auto-detection pre-fill
- `custom_components/energy_manager/coordinator.py` - Added EMSData dataclass, EMSCoordinator class with full command lifecycle, ems_coordinator field on EnergyManagerData
- `custom_components/energy_manager/strings.json` - Added EMS step translations for all config flow fields

## Decisions Made
- Fuse rating configured in config flow (not NumberEntity) because it is a hardware constant that should not change casually -- matches Research open question 3 recommendation
- EMS config as separate step between battery and EV (not merged into battery step) for separation of concerns -- scheduling inputs vs control/safety inputs
- L-current sensor scan falls back to all entities when SigenStor scan misses, supporting template sensors like `sensor.highest_l_current`
- Car plugged-in detection uses Easee charger status entity (local, sub-second updates) rather than car integration sensors (cloud polling, 5-30 min latency) -- per Research Pitfall 4
- Command verification uses coordinator polling cycle (checked each _async_update_data) rather than asyncio.sleep, avoiding event loop blocking

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- EMSCoordinator ready for sensor creation in Plan 03-03
- EMSData provides all fields needed for EMS status sensor and fuse headroom diagnostic sensor
- Config flow collects all entity IDs needed for EMS operation
- All 40 tests pass (27 EMS + 13 battery scheduler)

## Self-Check: PASSED

- [x] custom_components/energy_manager/coordinator.py exists with EMSCoordinator and EMSData
- [x] custom_components/energy_manager/const.py exists with EMS constants
- [x] custom_components/energy_manager/config_flow.py exists with async_step_ems
- [x] custom_components/energy_manager/auto_detect.py exists with find_sigenstor_ems_entities
- [x] custom_components/energy_manager/strings.json exists with EMS step translations
- [x] Commit 338742c (Task 1 - EMS constants, auto-detect, config flow) exists
- [x] Commit 1a88de0 (Task 2 - EMSCoordinator) exists
- [x] All 40 tests pass (no regressions)

---
*Phase: 03-ems-controller*
*Completed: 2026-02-17*

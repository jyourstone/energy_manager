---
phase: 03-ems-controller
plan: 03
subsystem: ems
tags: [sensor, lifecycle-wiring, translations, ems-status]

# Dependency graph
requires:
  - phase: 03-ems-controller
    plan: 02
    provides: "EMSCoordinator, EMSData dataclass, config flow EMS step"
  - phase: 02-home-battery-schedule
    provides: "BatteryScheduleCoordinator, EnergyManagerData with ems_coordinator field"
provides:
  - "EMSStatusSensor -- user-visible EMS mode and fuse headroom sensor"
  - "EMSCoordinator lifecycle wiring in __init__.py"
  - "Complete EMS translations in strings.json and translations/en.json"
affects: [04-ev-charging]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "EMSCoordinator conditional creation follows BatteryScheduleCoordinator pattern (battery_coordinator is not None)"
    - "EMSStatusSensor follows BatteryScheduleSensor pattern: EnergyManagerEntity + SensorEntity with translation_key"

key-files:
  created: []
  modified:
    - "custom_components/energy_manager/__init__.py"
    - "custom_components/energy_manager/sensor.py"
    - "custom_components/energy_manager/strings.json"
    - "custom_components/energy_manager/translations/en.json"

key-decisions:
  - "EMSCoordinator only created when battery_coordinator exists (not via separate toggle)"
  - "EMS sensor conditionally created only when ems_coordinator is not None (same guard pattern as battery sensors)"

patterns-established:
  - "Three-tier coordinator chain: PriceCoordinator -> BatteryScheduleCoordinator -> EMSCoordinator"

# Metrics
duration: 2min
completed: 2026-02-17
---

# Phase 3 Plan 03: EMS Sensor and Lifecycle Wiring Summary

**EMSCoordinator lifecycle wiring in __init__.py with conditional creation, EMSStatusSensor showing mode/fuse headroom/overrides, and complete EMS translations in both strings.json and en.json**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-17T21:10:18Z
- **Completed:** 2026-02-17T21:12:26Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- EMSCoordinator created in __init__.py after BatteryScheduleCoordinator, conditional on battery_coordinator existence
- EMSStatusSensor exposes current EMS mode as state with target_mode, charge_limit_kw, fuse_headroom_amps, override_reason, command_verified, car_override_active, and pv_charging_active in attributes
- EMS sensor conditionally created only when ems_coordinator is not None (matching battery sensor guard pattern)
- translations/en.json synced with strings.json for EMS config step (6 fields with descriptions) and ems_status sensor
- All 40 existing tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire EMSCoordinator into __init__.py and create EMS status sensor** - `576473d` (feat)
2. **Task 2: Add translations for EMS config step and sensor** - `6b64e16` (feat)

## Files Created/Modified
- `custom_components/energy_manager/__init__.py` - Added EMSCoordinator import, conditional creation after BatteryScheduleCoordinator, passed to EnergyManagerData
- `custom_components/energy_manager/sensor.py` - Added EMSStatusSensor class with mode state and control attributes, conditional creation in async_setup_entry
- `custom_components/energy_manager/strings.json` - Added ems_status sensor translation
- `custom_components/energy_manager/translations/en.json` - Added EMS config step translations (mirroring strings.json) and ems_status sensor translation

## Decisions Made
- EMSCoordinator only created when battery_coordinator exists (not via a separate config toggle) -- EMS control is inherent to having a battery module, no reason for separate gating
- EMS sensor uses the same conditional guard pattern as battery sensors (coordinator is not None check) for consistency

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 3 (EMS Controller) is now complete: pure module, coordinator, config flow, and sensor all wired
- Full three-tier coordinator chain operational: PriceCoordinator -> BatteryScheduleCoordinator -> EMSCoordinator
- All 40 tests pass (13 battery scheduler + 27 EMS controller)
- Ready for Phase 4 (EV Charging) which will build on the EMS infrastructure

## Self-Check: PASSED

- [x] custom_components/energy_manager/__init__.py exists with EMSCoordinator import and creation
- [x] custom_components/energy_manager/sensor.py exists with EMSStatusSensor class
- [x] custom_components/energy_manager/strings.json exists with ems_status sensor translation
- [x] custom_components/energy_manager/translations/en.json exists with EMS step and ems_status translations
- [x] Commit 576473d (Task 1 - EMSCoordinator wiring and EMS status sensor) exists
- [x] Commit 6b64e16 (Task 2 - EMS translations) exists
- [x] All 40 tests pass (no regressions)

---
*Phase: 03-ems-controller*
*Completed: 2026-02-17*

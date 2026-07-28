---
phase: 04-car-charging
plan: 03
subsystem: entities
tags: [ev-charging, sensor, time-entity, number-entity, restore-state, translations]

# Dependency graph
requires:
  - phase: 04-car-charging
    provides: "CarChargingCoordinator, CarChargingData, CarEntity base class, car_coordinators dict"
  - phase: 02-battery-schedule
    provides: "RestoreNumber pattern, BatteryScheduleSensor pattern for schedule attributes"
provides:
  - "CarScheduleSensor per car showing current_action with full schedule in attributes"
  - "CarDepartureTime (TimeEntity + RestoreEntity) per car with coordinator refresh on change"
  - "CarTargetSOC (RestoreNumber) per car with 10-100% range and coordinator refresh"
  - "CarMaxChargePower (RestoreNumber) per car with 1.4-22.0 kW range and coordinator refresh"
  - "time.py platform file with async_setup_entry for per-car departure time entities"
  - "Entity translations for all 4 car entity types in strings.json and en.json"
affects: [05-easee-control, 06-polish]

# Tech tracking
tech-stack:
  added: []
  patterns: ["TimeEntity with RestoreEntity for persistent time-of-day config", "Per-subentry entity creation with config_subentry_id"]

key-files:
  created:
    - "custom_components/energy_manager/time.py"
  modified:
    - "custom_components/energy_manager/sensor.py"
    - "custom_components/energy_manager/number.py"
    - "custom_components/energy_manager/strings.json"
    - "custom_components/energy_manager/translations/en.json"

key-decisions:
  - "TimeEntity uses async_set_value (not async_set_native_value) and RestoreEntity's async_get_last_state (not number-specific)"
  - "number.py async_setup_entry restructured to not return early when battery_coordinator is None (allows car entities without battery module)"

patterns-established:
  - "CarEntity + TimeEntity + RestoreEntity triple inheritance for persistent per-car config entities"
  - "Per-subentry entity creation with config_subentry_id parameter for HA device grouping"

requirements-completed: [EV-03, EV-04, EV-11]

# Metrics
duration: 3min
completed: 2026-02-23
---

# Phase 4 Plan 03: Per-Car Entities and Time Platform Summary

**CarScheduleSensor, CarDepartureTime (TimeEntity+RestoreEntity), CarTargetSOC, and CarMaxChargePower per car subentry with persistent config and coordinator refresh**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-23T12:35:05Z
- **Completed:** 2026-02-23T12:38:29Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- CarScheduleSensor shows current_action state with schedule (filtered+capped at 48), energy_needed_kwh, hours_needed, SOC info in attributes
- CarDepartureTime uses TimeEntity + RestoreEntity for persistent departure time with 07:00 default, triggers coordinator refresh
- CarTargetSOC and CarMaxChargePower use RestoreNumber for persistent config values, both trigger coordinator refresh
- All car entities created per subentry with config_subentry_id for proper HA device grouping under car device
- Translations added for all 4 new entity types in strings.json and translations/en.json

## Task Commits

Each task was committed atomically:

1. **Task 1: CarScheduleSensor in sensor.py and CarDepartureTime in time.py** - `ff81c5d` (feat)
2. **Task 2: CarTargetSOC and CarMaxChargePower in number.py, translations** - `3766570` (feat)

## Files Created/Modified
- `custom_components/energy_manager/time.py` - New platform: CarDepartureTime with RestoreEntity persistence and coordinator refresh
- `custom_components/energy_manager/sensor.py` - Added CarScheduleSensor per car subentry with schedule attributes
- `custom_components/energy_manager/number.py` - Added CarTargetSOC and CarMaxChargePower per car subentry, fixed early return bug
- `custom_components/energy_manager/strings.json` - Entity translations for car_schedule, departure_time, car_target_soc, car_max_charge_power
- `custom_components/energy_manager/translations/en.json` - English translations matching strings.json

## Decisions Made
- TimeEntity uses async_set_value (not async_set_native_value like NumberEntity) per HA TimeEntity API
- RestoreEntity uses async_get_last_state (general) for TimeEntity, while RestoreNumber uses async_get_last_number_data (number-specific)
- number.py async_setup_entry restructured: car entities created independently of battery_coordinator existence

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed number.py early return blocking car entity creation**
- **Found during:** Task 2 (CarTargetSOC and CarMaxChargePower)
- **Issue:** async_setup_entry returned early when battery_coordinator was None, which would skip car entity creation for users with EV but no battery module
- **Fix:** Changed from early return to conditional block, allowing car entities to be created regardless of battery module state
- **Files modified:** custom_components/energy_manager/number.py
- **Verification:** Code structure verified to create car entities in all cases
- **Committed in:** 3766570 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix for correctness -- without it, EV-only users would get no car number entities. No scope creep.

## Issues Encountered
None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All Phase 4 car charging entities complete and functional
- Phase 4 (Car Charging) fully complete: scheduler (Plan 01), coordinator+entity (Plan 02), entities+time platform (Plan 03)
- Ready for Phase 5 (Easee Control) which will wire actual charger commands
- CarScheduleSensor attributes provide the UI data layer for charging schedule visualization

## Self-Check: PASSED

- FOUND: custom_components/energy_manager/time.py (CarDepartureTime)
- FOUND: custom_components/energy_manager/sensor.py (CarScheduleSensor)
- FOUND: custom_components/energy_manager/number.py (CarTargetSOC, CarMaxChargePower)
- FOUND: custom_components/energy_manager/strings.json (entity translations)
- FOUND: custom_components/energy_manager/translations/en.json (entity translations)

---
*Phase: 04-car-charging*
*Completed: 2026-02-23*

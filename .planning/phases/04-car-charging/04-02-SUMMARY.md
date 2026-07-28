---
phase: 04-car-charging
plan: 02
subsystem: coordinator
tags: [ev-charging, coordinator, car-entity, fallback-detection, ha-integration]

# Dependency graph
requires:
  - phase: 04-car-charging
    provides: "car_charging_scheduler.py with build_car_charging_schedule() and CarScheduleResult"
  - phase: 02-battery-schedule
    provides: "BatteryScheduleCoordinator pattern (coordinator chaining, DataUpdateCoordinator)"
  - phase: 03-ems-control
    provides: "EMSCoordinator and EnergyManagerData runtime data pattern"
provides:
  - "CarChargingCoordinator (one per car subentry) with price chaining and fallback detection"
  - "CarChargingData frozen dataclass with schedule, SOC, and car state"
  - "CarEntity base class with per-car DeviceInfo and subentry identifiers"
  - "EnergyManagerData.car_coordinators dict keyed by subentry_id"
  - "Per-subentry coordinator creation loop in __init__.py"
  - "Platform.TIME and Platform.NUMBER forwarding when EV module enabled"
affects: [04-car-charging, 05-easee-control]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Per-subentry coordinator instantiation loop", "SOC staleness tracking for fallback detection", "TYPE_CHECKING import for circular dependency avoidance"]

key-files:
  created: []
  modified:
    - "custom_components/energy_manager/coordinator.py"
    - "custom_components/energy_manager/entity.py"
    - "custom_components/energy_manager/const.py"
    - "custom_components/energy_manager/__init__.py"

key-decisions:
  - "CarChargingCoordinator reads charger_status_entity from entry.options (shared across all cars) for fallback detection"
  - "_detect_fallback_needed checks per-coordinator SOC staleness independently (each car evaluates its own freshness)"
  - "solar_surplus_available always False in Phase 4 (Phase 5 wires actual PV detection)"
  - "CarEntity uses TYPE_CHECKING import for CarChargingCoordinator to avoid circular imports"
  - "departure_time defaults to 07:00 local time; rolled to tomorrow when <= now"

patterns-established:
  - "Per-subentry coordinator loop: iterate entry.subentries, filter by subentry_type, create coordinator per match"
  - "CarEntity base class: subentry-based device identifiers with via_device linking to hub"
  - "SOC staleness tracking via _soc_last_updated for fallback heuristic"

requirements-completed: [EV-02, EV-03, EV-04, EV-05, EV-06, EV-08]

# Metrics
duration: 4min
completed: 2026-02-23
---

# Phase 4 Plan 02: Car Charging Coordinator and Entity Base Summary

**CarChargingCoordinator with PriceCoordinator chaining, fallback detection (EV-08), CarEntity base class, and per-subentry wiring in __init__.py**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-23T12:27:06Z
- **Completed:** 2026-02-23T12:31:50Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- CarChargingCoordinator chains to PriceCoordinator and calls build_car_charging_schedule with solar_surplus_available=False
- CarChargingData frozen dataclass with current_action, schedule, energy_needed_kwh, is_preliminary, car_name, current_soc, target_soc fields
- Fallback detection (_detect_fallback_needed) reads charger_status_entity and checks SOC staleness threshold (EV-08)
- CarEntity base class with per-car DeviceInfo using subentry_id identifiers and via_device link to hub
- __init__.py creates one CarChargingCoordinator per car subentry when EV module enabled
- Platform.TIME and Platform.NUMBER forwarded when EV module enabled
- 11 car charging constants added to const.py (power limits, SOC limits, intervals, fallback threshold)

## Task Commits

Each task was committed atomically:

1. **Task 1: CarChargingData, CarChargingCoordinator, and constants** - `748416d` (feat)
2. **Task 2: Fallback detection, CarEntity base, and __init__.py wiring** - `946629d` (feat)

## Files Created/Modified
- `custom_components/energy_manager/coordinator.py` - CarChargingData dataclass, CarChargingCoordinator class with price chaining/fallback/SOC tracking, car_coordinators on EnergyManagerData
- `custom_components/energy_manager/entity.py` - CarEntity base class with per-car DeviceInfo using subentry identifiers
- `custom_components/energy_manager/const.py` - 11 new car charging constants (power, SOC, intervals, fallback)
- `custom_components/energy_manager/__init__.py` - Per-subentry coordinator creation loop, platform forwarding for TIME and NUMBER

## Decisions Made
- CarChargingCoordinator reads charger_status_entity from entry.options (same entity the EMSCoordinator uses) for shared charger state
- Each car coordinator checks its own SOC staleness independently via _soc_last_updated
- solar_surplus_available always False in Phase 4; Phase 5 will wire PVHysteresisTracker for actual PV surplus detection
- CarEntity uses TYPE_CHECKING import to avoid circular import between entity.py and coordinator.py
- departure_time defaults to 07:00 local; rolled to tomorrow when departure <= now (same-day past handling)
- EV-05/EV-06 confirmed already satisfied by Phase 1 CarSubentryFlowHandler (no additional work needed)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- CarChargingCoordinator ready for consumption by Plan 04-03 (time/number entity layer)
- CarEntity base ready for TimeEntity and NumberEntity subclasses
- EnergyManagerData.car_coordinators available for entity platform setup functions
- Platform.TIME and Platform.NUMBER will be forwarded, enabling Plan 03 entity creation

## Self-Check: PASSED

- FOUND: custom_components/energy_manager/coordinator.py (CarChargingCoordinator, CarChargingData)
- FOUND: custom_components/energy_manager/entity.py (CarEntity)
- FOUND: custom_components/energy_manager/const.py (car charging constants)
- FOUND: custom_components/energy_manager/__init__.py (per-subentry creation loop)
- FOUND: 748416d (Task 1 commit)
- FOUND: 946629d (Task 2 commit)

---
*Phase: 04-car-charging*
*Completed: 2026-02-23*

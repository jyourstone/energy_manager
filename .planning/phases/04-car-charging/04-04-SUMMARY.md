---
phase: 04-car-charging
plan: 04
subsystem: ev-charging
tags: [auto-detect, myskoda, config-flow, car-subentry, home-plugged-derivation]

# Dependency graph
requires:
  - phase: 04-car-charging plan 02
    provides: CarChargingCoordinator with charger_status_entity and fallback detection
  - phase: 04-car-charging plan 03
    provides: Per-car entities (CarTargetSOC, CarMaxChargePower, DepartureTime)
provides:
  - Expanded find_car_integrations() detecting mySkoda battery_percentage entities
  - Per-car charger_connected binary sensor and location device_tracker detection
  - CarChargingCoordinator._is_home_and_plugged_in() deriving state from 3 signals
  - Config flow car subentry without home_plugged_entity manual field
affects: [05-easee-control, 06-polish]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Multi-signal state derivation: combine charger status + car binary sensor + device_tracker for home+plugged inference"
    - "Debug logging for auto-detection misses: log available sensors when domain matches but target entity not found"

key-files:
  created: []
  modified:
    - custom_components/energy_manager/auto_detect.py
    - custom_components/energy_manager/config_flow.py
    - custom_components/energy_manager/coordinator.py
    - custom_components/energy_manager/const.py
    - custom_components/energy_manager/strings.json
    - custom_components/energy_manager/translations/en.json

key-decisions:
  - "mySkoda added as explicit platform pattern alongside 'skoda' for broader car integration support"
  - "battery_percentage and charging_level added to SOC entity patterns for mySkoda compatibility"
  - "_is_home_and_plugged_in() uses 3-signal cascade: Easee charger status (required), car charger_connected (optional confirmation), vehicle location (optional confirmation)"
  - "home_plugged_entity replaced by charger_connected_entity + location_entity (auto-detected, optional manual override)"

patterns-established:
  - "Auto-detection debug logging: log sensor list when domain matches but specific entity pattern not found"
  - "Multi-signal state derivation: require primary signal, use secondary/tertiary as optional confirmation"

requirements-completed: [EV-05, EV-06]

# Metrics
duration: 3min
completed: 2026-02-23
---

# Phase 4 Plan 04: UAT Gap Closure Summary

**Expanded car auto-detection for mySkoda (battery_percentage pattern) and auto-derived home+plugged state from Easee charger + car binary sensor + vehicle location**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-23T13:17:09Z
- **Completed:** 2026-02-23T13:20:12Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- mySkoda cars with battery_percentage entities now auto-detected in config flow
- Config flow car subentry no longer asks for a manual "Home and plugged in sensor" field
- CarChargingCoordinator._is_home_and_plugged_in() derives home+plugged state from 3 signals (Easee status, car charger_connected, vehicle location)
- Debug logging fires when a car integration domain matches but no battery entity is found
- All 23 existing car charging scheduler tests still pass (no regression)

## Task Commits

Each task was committed atomically:

1. **Task 1: Expand car auto-detection patterns and detect charger_connected + location entities** - `607376c` (feat)
2. **Task 2: Remove home_plugged_entity from config flow, auto-derive in coordinator** - `7401b0d` (feat)

## Files Created/Modified
- `custom_components/energy_manager/const.py` - Added CONF_CHARGER_CONNECTED_ENTITY and CONF_LOCATION_ENTITY constants
- `custom_components/energy_manager/auto_detect.py` - Expanded platform_patterns, battery SOC patterns, added charger_connected/location detection and debug logging
- `custom_components/energy_manager/config_flow.py` - Replaced home_plugged_entity with charger_connected_entity and location_entity in car subentry forms
- `custom_components/energy_manager/coordinator.py` - Replaced _home_plugged_entity with _charger_connected_entity/_location_entity, added _is_home_and_plugged_in() method
- `custom_components/energy_manager/strings.json` - Updated car subentry field labels and descriptions
- `custom_components/energy_manager/translations/en.json` - Updated car subentry field labels and descriptions

## Decisions Made
- mySkoda added as explicit platform pattern alongside "skoda" for broader car integration support
- battery_percentage and charging_level added to SOC entity patterns for mySkoda compatibility
- _is_home_and_plugged_in() uses 3-signal cascade: Easee charger status (required), car charger_connected (optional confirmation), vehicle location (optional confirmation)
- home_plugged_entity replaced by charger_connected_entity + location_entity (auto-detected, optional manual override)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Car auto-detection now covers mySkoda integration entities
- _is_home_and_plugged_in() ready for Phase 5 to consume in Easee control logic
- All car charging foundation complete for Phase 5 (Easee control)

---
*Phase: 04-car-charging*
*Completed: 2026-02-23*

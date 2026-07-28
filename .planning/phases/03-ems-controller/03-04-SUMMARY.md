---
phase: 03-ems-controller
plan: 04
subsystem: auto-detect, config-flow, ems
tags: [entity-registry, auto-detection, sigenstor, fuse-protection, regression-tests]

# Dependency graph
requires:
  - phase: 03-ems-controller (plans 01-03)
    provides: "EMS pure module, coordinator, config flow, sensor wiring"
provides:
  - "Fixed find_sigenstor_ems_entities() detecting charge/discharge limits, L-current, and PV power"
  - "Config flow EMS step accepting sensor domain for charge/discharge limit selectors"
  - "Startup warning when L-current entity is unconfigured"
  - "6 regression tests for auto-detection patterns"
affects: [phase-04-ev-charging, phase-06-polish]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Global fallback scan pattern for entities not under expected config entry"
    - "Multi-domain entity selector pattern (sensor+number)"

key-files:
  created:
    - tests/test_auto_detect_ems.py
  modified:
    - custom_components/energy_manager/auto_detect.py
    - custom_components/energy_manager/config_flow.py
    - custom_components/energy_manager/coordinator.py

key-decisions:
  - "Accept both sensor and number domains for charge/discharge limit detection (SigenStor exposes these as sensors)"
  - "Add phase_a_active_power as L-current fallback (kW power sensor, approximate but better than nothing)"
  - "Global PV power fallback prefers plant-level over inverter-level entity (total after clipping is more accurate)"

patterns-established:
  - "Global fallback scan: when entity not under expected config entry, scan all entities with preference ordering"
  - "Multi-domain selector: EntitySelectorConfig(domain=[...]) for entities that may appear in multiple domains"

# Metrics
duration: 3min
completed: 2026-02-22
---

# Phase 3 Plan 4: UAT Gap Closure Summary

**Fixed 4 auto-detection bugs in find_sigenstor_ems_entities() for charge/discharge limits, L-current, and PV power with regression tests**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-22T13:45:01Z
- **Completed:** 2026-02-22T13:47:51Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Fixed charge/discharge limit detection: added sensor domain support and ess_rated_charging/discharging patterns
- Fixed L-current detection: added phase_a_active_power, phase_active_power, grid_phase patterns to both sigen scan and global fallback
- Fixed PV power detection: added global fallback scan with sigen preference and plant-over-inverter prioritization
- Updated config flow EMS step to accept sensor+number domains for charge/discharge limit entity selectors
- Added startup warning in EMSCoordinator when L-current entity is unconfigured (explains static 18A fuse headroom)
- Created 6 regression tests covering all 4 fixed patterns plus EMS select sanity check

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix auto-detection patterns and config flow entity selectors** - `a181333` (fix)
2. **Task 2: Add startup warning and auto-detect regression tests** - `f9917ca` (test)

## Files Created/Modified
- `custom_components/energy_manager/auto_detect.py` - Fixed find_sigenstor_ems_entities() with correct domains, patterns, and global PV fallback
- `custom_components/energy_manager/config_flow.py` - Updated EMS step entity selectors to accept sensor+number domains
- `custom_components/energy_manager/coordinator.py` - Added startup warning for unconfigured L-current entity
- `tests/test_auto_detect_ems.py` - 6 regression tests for auto-detection fixes

## Decisions Made
- Accept both sensor and number domains for charge/discharge limit entities -- SigenStor firmware exposes rated charging/discharging power as sensors, not number entities
- Add phase_a_active_power as L-current fallback pattern -- these are kW power sensors, not amp current sensors, making fuse headroom approximate but far better than assuming 0A
- PV global fallback prefers plant-level entity over inverter-level -- plant total is post-clipping and more accurate for scheduling

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All Phase 3 UAT gaps are closed: auto-detection finds all 4 missing entity types
- Fuse headroom root cause resolved: L-current entity will be auto-detected, making headroom dynamic
- Phase 3 is fully complete (plans 01-04), ready for Phase 4 (EV Charging)
- Total test count: 46 (40 existing + 6 new) with zero failures

## Self-Check: PASSED

- All 4 modified/created files verified on disk
- Both task commits (a181333, f9917ca) verified in git log
- 46 tests pass with zero failures

---
*Phase: 03-ems-controller*
*Completed: 2026-02-22*

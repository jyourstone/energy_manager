---
phase: 03-ems-controller
plan: 05
subsystem: ems
tags: [fuse-protection, per-phase, 3-phase, grid-power, safety]

# Dependency graph
requires:
  - phase: 03-ems-controller/03-04
    provides: "Auto-detection and fuse headroom gap closure (foundation for per-phase)"
provides:
  - "Per-phase grid power config keys (CONF_GRID_PHASE_A/B/C_ENTITY)"
  - "Per-phase sensor auto-detection (phase_a/b/c_active_power patterns)"
  - "Per-phase EntitySelector fields in config flow EMS step"
  - "Per-phase worst-case fuse protection via max(abs(P)/230) in coordinator"
  - "Balanced-load fallback for single-phase installations"
affects: [04-ev-charging, 06-polish]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-phase worst-case current: max(abs(P_a)/230, abs(P_b)/230, abs(P_c)/230)"
    - "Phase entity listener registration: per-phase preferred, total as fallback"

key-files:
  created: []
  modified:
    - "custom_components/energy_manager/const.py"
    - "custom_components/energy_manager/auto_detect.py"
    - "custom_components/energy_manager/config_flow.py"
    - "custom_components/energy_manager/coordinator.py"
    - "custom_components/energy_manager/strings.json"
    - "custom_components/energy_manager/translations/en.json"
    - "tests/test_auto_detect_ems.py"

key-decisions:
  - "Per-phase sensors require ALL three phases configured to activate per-phase mode (partial = fallback)"
  - "Per-phase mode uses abs(P_phase)/230.0 per phase, max() across all three for worst-case fuse protection"
  - "Total grid power remains as fallback with balanced-load assumption: abs(P_total)/(3*230)"
  - "State change listeners registered for all three per-phase entities when configured (fuse-critical events)"

patterns-established:
  - "Per-phase fuse protection: worst-case phase current prevents overload on unbalanced loads"
  - "Graceful degradation: per-phase sensors preferred, total power fallback for single-phase"

# Metrics
duration: 3min
completed: 2026-02-23
---

# Phase 03 Plan 05: Per-Phase Fuse Protection Summary

**Per-phase grid power monitoring with worst-case max() fuse protection replacing balanced-load averaging**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-23T09:43:54Z
- **Completed:** 2026-02-23T09:47:00Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Fixed critical fuse protection safety bug: unbalanced 3-phase loads no longer masked by averaging
- Per-phase grid power auto-detection finds sigen_plant_grid_phase_a/b/c_active_power sensors
- Config flow EMS step now shows three per-phase EntitySelector fields alongside total power fallback
- coordinator._read_grid_current_amps() returns max(abs(P_a)/230, abs(P_b)/230, abs(P_c)/230) when per-phase configured
- ems_controller.py has zero changes (already expects highest-phase current)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add per-phase config keys, auto-detection, config flow fields, and translations** - `a2c374c` (feat)
2. **Task 2: Replace coordinator balanced-load calculation with per-phase max() and add tests** - `3ad8cd6` (feat)

## Files Created/Modified
- `custom_components/energy_manager/const.py` - Added CONF_GRID_PHASE_A/B/C_ENTITY config keys
- `custom_components/energy_manager/auto_detect.py` - Per-phase sensor detection in sigen scan and global fallback
- `custom_components/energy_manager/config_flow.py` - Three per-phase EntitySelector fields in EMS step + _create_entry() options
- `custom_components/energy_manager/coordinator.py` - Per-phase entity init, phase-aware listener registration, max(phase_amps) calculation
- `custom_components/energy_manager/strings.json` - Per-phase labels and descriptions, updated grid_power_entity description
- `custom_components/energy_manager/translations/en.json` - Mirrored strings.json changes
- `tests/test_auto_detect_ems.py` - 4 new per-phase detection tests (TestPerPhaseGridPower class)

## Decisions Made
- Per-phase mode requires ALL three phases configured (partial config falls back to total power)
- Per-phase uses abs(P_phase)/230.0 per phase, max() across all three for worst-case
- Total grid power fallback preserved with abs(P_total)/(3*230) balanced-load assumption
- State change listeners registered for all three per-phase entities (fuse-critical events)
- grid_power_entity description updated to clarify its fallback role

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 03 fully complete with all 5 plans executed (including gap closure)
- Per-phase fuse protection resolves the critical safety issue identified during UAT
- Ready for Phase 04 (EV charging)

## Self-Check: PASSED

- All 8 files verified present on disk
- Commit a2c374c (Task 1) verified in git log
- Commit 3ad8cd6 (Task 2) verified in git log
- All 54 tests passing

---
*Phase: 03-ems-controller*
*Completed: 2026-02-23*

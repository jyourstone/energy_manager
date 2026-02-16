---
phase: 02-home-battery-schedule
plan: 04
subsystem: battery-schedule
tags: [uat, gap-closure, float-precision, sensor-availability, kw-conversion]

# Dependency graph
requires:
  - phase: 02-home-battery-schedule
    provides: "Battery scheduler, coordinator, sensors, number entities from plans 01-03"
provides:
  - "Corrected default thresholds (charge 1.0, discharge 0.50 SEK/kWh)"
  - "IEEE 754 float artifact prevention via round(price, 4)"
  - "Proper available/unavailable semantics on NextCharge/NextDischarge sensors"
  - "Max charge power entity in kW with kW-to-W conversion at coordinator boundary"
affects: [03-ems-controller, 06-polish]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Unit conversion at entity-coordinator boundary (kW display, W internal)"
    - "Explicit available() property for distinguishing no-data from no-slots"

key-files:
  created: []
  modified:
    - custom_components/energy_manager/const.py
    - custom_components/energy_manager/coordinator.py
    - custom_components/energy_manager/sensor.py
    - custom_components/energy_manager/number.py

key-decisions:
  - "kW-to-W conversion at entity boundary, not in scheduler -- coordinator stays in watts"
  - "available() returns coordinator.data is not None -- green icon when data exists but no slots"

patterns-established:
  - "Unit conversion boundary: display units differ from internal units, conversion at entity layer"
  - "Float precision: round(value, 4) for all price serialization paths"

# Metrics
duration: 2min
completed: 2026-02-16
---

# Phase 2 Plan 4: UAT Gap Closure Summary

**Closed 5 UAT gaps: float precision rounding, sensor availability semantics, updated threshold defaults, and max charge power W-to-kW unit change**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-16T19:49:43Z
- **Completed:** 2026-02-16T19:52:36Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Schedule slot prices now display cleanly (max 4 decimal places, no IEEE 754 artifacts)
- NextCharge/NextDischarge sensors show available (green) with no state when no slots scheduled, instead of Unknown/red
- Charge threshold default updated to 1.0 SEK/kWh, discharge threshold to 0.50 SEK/kWh
- Max Charge Power entity displays in kW (step 0.1, max 15.0, default 5.0) while scheduler still receives watts

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix defaults, float precision, and sensor availability** - `a1f8515` (fix)
2. **Task 2: Change max charge power from W to kW** - `9114ab4` (feat)

**Plan metadata:** (pending) (docs: complete plan)

## Files Created/Modified
- `custom_components/energy_manager/const.py` - Updated default thresholds and renamed power constants from W to kW scale
- `custom_components/energy_manager/coordinator.py` - Added round(slot.price, 4) in _serialize_slot, updated default import to KW
- `custom_components/energy_manager/sensor.py` - Added round(slot.price, 4) in schedule attributes, added available() to NextCharge/NextDischarge
- `custom_components/energy_manager/number.py` - Changed unit to kW, step to 0.1, max to 15.0, added kW*1000 conversion at boundary

## Decisions Made
- kW-to-W conversion happens at the entity-coordinator boundary (number.py), not inside the scheduler. The coordinator's `max_charge_power_w` attribute name stays as `_w` because the pure scheduler expects watts.
- `available()` property returns `self.coordinator.data is not None` -- this means when coordinator has data but no slots exist, the entity shows as available (green icon) with None state. When coordinator has no data at all (error), entity shows as unavailable (red icon).

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- pytest was not installed in the system Python. Installed via pip3 with `--break-system-packages` flag to run verification tests. All 11 existing tests passed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 2 fully complete with all UAT gaps closed
- Clean foundation for Phase 3 (EMS Controller)
- All 5 UAT issues resolved, all existing tests pass

## Self-Check: PASSED

All 4 modified files exist. All 2 task commits verified (a1f8515, 9114ab4). Summary file exists.

---
*Phase: 02-home-battery-schedule*
*Completed: 2026-02-16*

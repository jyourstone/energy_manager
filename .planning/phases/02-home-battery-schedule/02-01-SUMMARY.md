---
phase: 02-home-battery-schedule
plan: 01
subsystem: scheduling
tags: [battery, scheduling, peak-grouping, virtual-energy, tdd, pure-python]

# Dependency graph
requires:
  - phase: 01-scaffold-and-prices
    provides: "PriceSlot data structure and PriceCoordinator for price data"
provides:
  - "build_battery_schedule() pure function for multi-cycle charge/discharge scheduling"
  - "ScheduleSlot and BatteryScheduleResult data structures"
  - "Peak grouping algorithm (_group_into_peaks)"
  - "Virtual energy tracking for SOC-constrained optimization"
  - "Solar forecast integration reducing grid charge slots"
  - "Test infrastructure with HA stubs for pure-module testing"
affects: [02-home-battery-schedule, 03-ems-controller]

# Tech tracking
tech-stack:
  added: [pytest]
  patterns: [pure-python-scheduling, tdd-red-green-refactor, ha-stub-meta-path-finder]

key-files:
  created:
    - custom_components/energy_manager/battery_scheduler.py
    - tests/test_battery_scheduler.py
    - tests/__init__.py
    - tests/conftest.py
    - conftest.py
  modified: []

key-decisions:
  - "Virtual energy tracking optimizes charge allocation per-peak with cheapest-slot-first selection"
  - "Peak grouping uses configurable gap_hours threshold to separate discharge windows"
  - "Solar forecast distributes production across 05:00-17:00 UTC daylight hours"
  - "HA stub uses importlib MetaPathFinder for Python 3.14 compatibility (find_spec/create_module)"
  - "Test parameters use energy-realistic scenarios (battery size vs charge rate)"

patterns-established:
  - "Pure Python module with zero HA imports for unit testability"
  - "Root conftest.py with MetaPathFinder stubs for homeassistant package"
  - "TDD workflow: RED (failing tests) -> GREEN (implementation) -> REFACTOR (cleanup)"

# Metrics
duration: 11min
completed: 2026-02-15
---

# Phase 2 Plan 1: Battery Scheduler Summary

**Multi-cycle charge/discharge scheduling algorithm with peak grouping, virtual energy tracking, and solar forecast integration -- pure Python with 11 passing unit tests**

## Performance

- **Duration:** 11 min
- **Started:** 2026-02-15T20:51:28Z
- **Completed:** 2026-02-15T21:02:23Z
- **Tasks:** 2
- **Files created:** 5

## Accomplishments
- Pure Python battery scheduling module with zero HA dependencies (577 lines)
- Peak grouping algorithm identifies separate profitable discharge windows separated by configurable time gaps
- Virtual energy tracking simulates battery SOC through the schedule, selecting cheapest charge slots and prioritizing most expensive discharge slots per peak
- Solar forecast reduces grid charging by converting daylight charge slots to solar_charge
- Comprehensive test suite with 11 test functions covering: basic classification, peak grouping, virtual energy limits, multi-cycle charging, edge cases (empty input, all below threshold), solar forecast, current action/EMS mode, next slot lookup, and SOC constraints
- Test infrastructure with importlib MetaPathFinder for Python 3.14-compatible HA stubs

## Task Commits

Each task was committed atomically:

1. **Task 1: RED -- Write failing tests** - `68fffde` (test)
2. **Task 2: GREEN + REFACTOR -- Implement algorithm** - `b98ce69` (feat)

## Files Created/Modified
- `custom_components/energy_manager/battery_scheduler.py` - Pure scheduling algorithm: build_battery_schedule(), ScheduleSlot, BatteryScheduleResult, peak grouping, virtual energy tracking, solar forecast
- `tests/test_battery_scheduler.py` - 11 unit tests covering all scheduling behaviors
- `conftest.py` - Root-level pytest config with importlib MetaPathFinder HA stubs
- `tests/__init__.py` - Tests package marker
- `tests/conftest.py` - Tests-level pytest config (delegates to root)

## Decisions Made
- **Virtual energy tracking per-peak**: The algorithm processes peaks chronologically, calculating charge needs before each peak and discharge limits within it. Cheapest charge slots are selected first. Most expensive discharge slots are prioritized when energy is limited.
- **Solar forecast daylight model**: Simplified to 05:00-17:00 UTC (approximately 06:00-18:00 CET) with even distribution. Converts grid charge slots to solar_charge when solar covers at least half the slot's energy.
- **HA stub approach**: Used importlib.abc.MetaPathFinder with find_spec (not deprecated find_module) for Python 3.14 compatibility. MagicMock-backed stub modules satisfy the full import chain without real HA installation.
- **Test parameters**: Tests use energy-realistic battery configurations (capacity vs charge rate) to ensure assertions match physical constraints. A 20 kWh battery at 10% SOC with 3 kW charge genuinely needs all 6 cheap hours to fill.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created virtual environment and installed pytest**
- **Found during:** Task 1 (test setup)
- **Issue:** No pytest available on system, pip install blocked by PEP 668
- **Fix:** Created .venv and installed pytest there
- **Files modified:** .venv/ (gitignored)
- **Verification:** pytest runs successfully

**2. [Rule 3 - Blocking] Created importlib-based HA stubs for Python 3.14**
- **Found during:** Task 1 (test collection)
- **Issue:** Importing battery_scheduler triggered __init__.py which requires homeassistant package. Initial meta-path finder using deprecated find_module/load_module API did not work on Python 3.14
- **Fix:** Implemented MetaPathFinder with find_spec/create_module (modern importlib.abc API). Created root conftest.py loaded before test collection
- **Files modified:** conftest.py, tests/conftest.py
- **Verification:** All tests collect and run without import errors

**3. [Rule 1 - Bug] Adjusted test parameters for energy-realistic scenarios**
- **Found during:** Task 2 (GREEN phase)
- **Issue:** Tests 1 and 8 used default 10 kWh battery at 50% SOC with 5 kW charge -- battery fills in 1 hour, so test assertion that all 6 cheap hours charge was physically impossible
- **Fix:** Changed to 20 kWh battery at 10% SOC with 3 kW charge rate, requiring all 6 cheap hours to fill
- **Files modified:** tests/test_battery_scheduler.py
- **Verification:** All 11 tests pass with realistic parameters

---

**Total deviations:** 3 auto-fixed (2 blocking, 1 bug)
**Impact on plan:** All fixes necessary for test infrastructure and correctness. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- battery_scheduler.py is ready for Plan 02 (BatteryScheduleCoordinator wrapping)
- ScheduleSlot and BatteryScheduleResult data structures are the API contract for Phase 3 (EMS Controller)
- Test infrastructure is established for future pure-module tests

---
*Phase: 02-home-battery-schedule*
*Completed: 2026-02-15*

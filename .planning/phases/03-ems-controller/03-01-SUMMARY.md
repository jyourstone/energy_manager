---
phase: 03-ems-controller
plan: 01
subsystem: ems
tags: [pure-python, tdd, fuse-protection, pv-hysteresis, dataclass, state-machine]

# Dependency graph
requires:
  - phase: 02-home-battery-schedule
    provides: "battery_scheduler.py pure module pattern, target_ems_mode from BatteryScheduleResult"
provides:
  - "compute_ems_state() -- pure EMS mode/limit calculation"
  - "clamp_amps() -- safety guard for all amp values"
  - "EMSDecision dataclass -- structured EMS computation result"
  - "PVHysteresisTracker -- state machine preventing PV charging oscillation"
affects: [03-02-ems-coordinator, 03-03-ems-sensors]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure Python EMS calculation module with zero HA imports"
    - "PV hysteresis state machine (off/pending_on/on/pending_off) with consecutive-check counting"
    - "Safety-first processing order in compute_ems_state"
    - "Frozen dataclass for immutable computation results"

key-files:
  created:
    - "custom_components/energy_manager/ems_controller.py"
    - "tests/test_ems_controller.py"
  modified: []

key-decisions:
  - "PV opportunistic charging triggers on standby OR max_self_consumption modes (not just standby)"
  - "Fuse headroom calculation uses safety_buffer_amps=2.0A default margin"
  - "PVHysteresisTracker uses separate activate/deactivate thresholds (500W/300W) for proper hysteresis band"
  - "Car priority override only affects command_charging mode (discharge/standby unaffected)"

patterns-established:
  - "Pure module TDD: write failing tests importing non-existent module, then implement to pass"
  - "Safety-first processing order: fuse math before mode decisions"
  - "PV hysteresis state machine pattern for oscillation prevention"

# Metrics
duration: 3min
completed: 2026-02-17
---

# Phase 3 Plan 01: EMS Controller Calculations Summary

**Pure-Python EMS controller with fuse protection, car priority override, PV opportunistic charging with hysteresis, and 27 exhaustive unit tests**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-17T20:56:50Z
- **Completed:** 2026-02-17T20:59:44Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- TDD-built pure EMS calculation module with zero HA imports following Phase 2 battery_scheduler.py pattern
- compute_ems_state() handles all EMS decision paths: mode selection, fuse limiting, car priority override, PV opportunistic charging
- PVHysteresisTracker state machine prevents rapid mode oscillation from fluctuating solar power
- 27 comprehensive tests covering all decision paths including edge cases (negative headroom, battery full, custom clamp ranges)
- Full test suite (40 tests) passes with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: RED -- failing tests for all EMS decision paths** - `5b96ac9` (test)
2. **Task 2: GREEN + REFACTOR -- implement ems_controller.py** - `5a373bb` (feat)

_TDD plan: test commit followed by implementation commit._

## Files Created/Modified
- `custom_components/energy_manager/ems_controller.py` - Pure EMS calculation module: EMSDecision dataclass, PVHysteresisTracker state machine, compute_ems_state(), clamp_amps()
- `tests/test_ems_controller.py` - 27 unit tests covering EMS-01 mode selection, EMS-02 fuse protection, EMS-03 car priority, EMS-04 safety guards, EMS-08 PV opportunistic charging, PV hysteresis state transitions

## Decisions Made
- PV opportunistic charging triggers on both "standby" and "max_self_consumption" modes, not just standby -- this matches the research recommendation and ensures PV charging activates during discharge periods too when solar is abundant
- Processing order in compute_ems_state is safety-first: fuse headroom calculated before any mode decisions, car priority checked before charge limit calculation
- PVHysteresisTracker returns True (still active) during "pending_off" state to avoid premature deactivation from single-sample noise

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- ems_controller.py ready for consumption by EMSCoordinator (Plan 03-02)
- compute_ems_state() signature matches the coordinator integration pattern from 03-RESEARCH.md
- PVHysteresisTracker instance will live on EMSCoordinator, calling update() each cycle and passing result to compute_ems_state()

## Self-Check: PASSED

- [x] custom_components/energy_manager/ems_controller.py exists
- [x] tests/test_ems_controller.py exists
- [x] Commit 5b96ac9 (Task 1 - RED) exists
- [x] Commit 5a373bb (Task 2 - GREEN) exists
- [x] All 27 EMS tests pass
- [x] All 40 tests pass (no regressions)
- [x] Zero HA imports in ems_controller.py

---
*Phase: 03-ems-controller*
*Completed: 2026-02-17*

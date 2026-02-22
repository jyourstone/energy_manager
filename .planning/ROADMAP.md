# Roadmap: Energy Manager

## Overview

This roadmap delivers a modular HACS integration that unifies home battery scheduling, EV charging optimization, and fuse-aware device coordination into a single installable package. The build follows the dependency chain: price data first (everything needs it), then battery scheduling (most complex algorithm, validates architecture), then real-time device control (EMS), then car charging (independent scheduler, validates subentry pattern), then charger control (depends on both EMS and car schedules), and finally polish and release prep. Each phase delivers a complete, independently verifiable capability.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Core Infrastructure + Price Foundation** - Integration skeleton with config flow, Nordpool adapter, and module architecture
- [x] **Phase 2: Home Battery Schedule** - Multi-cycle charge/discharge scheduling based on electricity prices and solar production
- [ ] **Phase 3: EMS Controller** - Real-time battery mode execution with fuse protection and safety guards
- [ ] **Phase 4: Car Charging** - Per-car price-optimized charging schedules with departure constraints
- [ ] **Phase 5: Easee Charger Control** - Physical charger control with state machine, solar charging, and fuse limiting
- [ ] **Phase 6: Polish + Release** - Options flow, diagnostics, helper migration, test coverage, and HACS publication

## Phase Details

### Phase 1: Core Infrastructure + Price Foundation
**Goal**: Users can install the integration via HACS, configure it through the UI with auto-detected integrations, and see current and future electricity prices -- proving the integration skeleton works end-to-end
**Depends on**: Nothing (first phase)
**Requirements**: CORE-01, CORE-02, CORE-03, CORE-04, CORE-06, CORE-07, CORE-08, CORE-09, CORE-12, CORE-13
**Success Criteria** (what must be TRUE):
  1. User can add the integration from HACS, and the config flow walks them through setup with auto-detected Nordpool, SigenStor, Easee, and car integrations
  2. User can manually enter entity IDs in config flow when auto-detection does not find their setup
  3. User can enable/disable Home Battery and EV Charging modules independently during setup
  4. Integration survives HA restart -- setup, unload, and reload work without errors or ghost entities
  5. Price sensor shows current electricity price with today's and tomorrow's hourly prices in attributes
**Plans**: 5 plans

Plans:
- [x] 01-01-PLAN.md -- Foundation scaffold, Nordpool adapter, auto-detection module
- [x] 01-02-PLAN.md -- Multi-step config flow wizard, car subentry flow, translations
- [x] 01-03-PLAN.md -- PriceCoordinator, integration lifecycle (__init__.py), hub device
- [x] 01-04-PLAN.md -- Gap closure: Price sensor entity exposing coordinator data to users
- [x] 01-05-PLAN.md -- UAT fix: Remove oversized attributes and wrong state class from price sensor

### Phase 2: Home Battery Schedule
**Goal**: Users can view an automatically generated multi-cycle battery charge/discharge schedule that optimizes for electricity price, with adjustable thresholds and solar awareness
**Depends on**: Phase 1 (price data, entity model, coordinator pattern)
**Requirements**: BATT-01, BATT-02, BATT-03, BATT-04, BATT-05, BATT-06, BATT-07, BATT-08, BATT-09, BATT-10, BATT-11, BATT-12
**Success Criteria** (what must be TRUE):
  1. User can see a schedule sensor showing current battery state (idle/grid_charging/discharging/solar_charging) with the full charge/discharge schedule in attributes
  2. User can see next charging slot and next discharging slot as separate sensors
  3. User can adjust charge price threshold, discharge price threshold, and max charging power via number entities in the UI
  4. Schedule automatically recalculates when Nordpool prices update or when Forecast.Solar data changes
  5. Schedule correctly identifies multiple profitable discharge windows separated by charging periods (multi-cycle with peak grouping)
**Plans**: 5 plans

Plans:
- [x] 02-01-PLAN.md -- TDD: Pure battery scheduling algorithm (peak grouping, virtual energy tracking)
- [x] 02-02-PLAN.md -- BatteryScheduleCoordinator, number entities, integration plumbing
- [x] 02-03-PLAN.md -- Battery schedule sensors, config flow Forecast.Solar step, translations
- [x] 02-04-PLAN.md -- UAT gap closure: float precision, sensor availability, defaults, kW unit
- [x] 02-05-PLAN.md -- UAT gap closure: filter schedule attributes to show current/future slots

### Phase 3: EMS Controller
**Goal**: The integration actively controls the battery EMS mode in real time based on the schedule, with fuse protection ensuring safe operation across all connected devices
**Depends on**: Phase 2 (battery schedule to execute)
**Requirements**: EMS-01, EMS-02, EMS-03, EMS-04, EMS-05, EMS-06, EMS-07, EMS-08
**Success Criteria** (what must be TRUE):
  1. Battery EMS mode (command_charging, max_self_consumption, standby) changes automatically based on the current schedule slot
  2. User can see an EMS status sensor showing the current mode and available fuse headroom in amps
  3. Fuse protection dynamically limits battery charging power when phase load approaches the configured fuse rating -- calculated amp values are always clamped to safe range
  4. When a car is scheduled to charge and plugged in, battery charging pauses automatically to free fuse capacity
  5. After sending a mode-change command, the integration reads back actual device state to verify the command took effect
**Plans**: 4 plans

Plans:
- [x] 03-01-PLAN.md -- TDD: Pure EMS controller calculations (mode selection, fuse math, amp clamping, car priority, PV hysteresis)
- [x] 03-02-PLAN.md -- EMSCoordinator, config flow EMS step, auto-detection, command sending and verification
- [x] 03-03-PLAN.md -- EMS status sensor, __init__.py wiring, translations
- [ ] 03-04-PLAN.md -- UAT gap closure: fix auto-detection patterns and fuse headroom input wiring

### Phase 4: Car Charging
**Goal**: Users can configure per-car charging schedules with departure times and target SOC, and the integration selects the cheapest charging slots automatically
**Depends on**: Phase 1 (price data, config flow), Phase 3 (fuse protection foundation)
**Requirements**: EV-01, EV-02, EV-03, EV-04, EV-05, EV-06, EV-07, EV-08, EV-09, EV-10, EV-11
**Success Criteria** (what must be TRUE):
  1. User can add a car via config flow with auto-detected Skoda/VW integration, or manually map entity IDs for unsupported cars
  2. Each car appears as a separate device with its own schedule sensor, departure time entity, and target SOC entity
  3. User can see a per-car charging schedule that selects the cheapest hours before the departure deadline to reach the target SOC
  4. When an unrecognized vehicle is connected, fallback charging activates during off-peak hours
  5. Solar-surplus EV charging routes excess PV power to the charger with hysteresis to prevent on/off cycling
**Plans**: TBD

Plans:
- [ ] 04-01: TBD
- [ ] 04-02: TBD

### Phase 5: Easee Charger Control
**Goal**: The integration physically controls the Easee charger -- starting/stopping charge sessions, setting dynamic amp limits, and managing solar vs grid charging modes through a robust state machine
**Depends on**: Phase 3 (fuse protection), Phase 4 (car charging schedule)
**Requirements**: EASE-01, EASE-02, EASE-03, EASE-04, EASE-05, EASE-06, EASE-07
**Success Criteria** (what must be TRUE):
  1. Charger dynamic amp limit changes automatically based on the active schedule and current fuse load
  2. Start/stop charging sequences follow a defined state machine that handles stuck states with timeout detection and recovery
  3. User can toggle a force-grid-charging switch to override the schedule and charge immediately
  4. Solar charging mode sets the charger limit based on available PV power after home consumption, and waits for the battery to reach a configurable SOC threshold before activating
  5. Fuse protection limits charger amps based on current phase load with safety buffer -- never exceeds the configured fuse rating
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

### Phase 6: Polish + Release
**Goal**: The integration is self-contained with zero external helper dependencies, fully configurable via options flow, comprehensively tested, and ready for HACS publication
**Depends on**: Phase 1-5 (all modules must exist before full options flow and testing)
**Requirements**: CORE-05, CORE-10, CORE-11, QUAL-01, QUAL-02, QUAL-03, QUAL-04, QUAL-05
**Success Criteria** (what must be TRUE):
  1. User can adjust all runtime settings (thresholds, capacities, targets, departure times) via options flow without reconfiguring the integration
  2. All 24 previously manual HA helpers are replaced by integration-owned entities -- user needs zero manual helpers
  3. Integration exposes diagnostics data for debugging (accessible via HA Settings > Devices > Diagnostics)
  4. All entity states handle "unavailable" and "unknown" sensor data gracefully -- no crashes, sensible fallback behavior
  5. CI pipeline runs unit tests, integration tests, linting (ruff), HACS validation, and manifest validation on every PR
**Plans**: TBD

Plans:
- [ ] 06-01: TBD
- [ ] 06-02: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6

| Phase | Plans Complete | Status | Completed |
|-------|---------------|--------|-----------|
| 1. Core Infrastructure + Price Foundation | 5/5 | Complete | 2026-02-15 |
| 2. Home Battery Schedule | 5/5 | Complete | 2026-02-16 |
| 3. EMS Controller | 3/4 | UAT gap closure | - |
| 4. Car Charging | 0/0 | Not started | - |
| 5. Easee Charger Control | 0/0 | Not started | - |
| 6. Polish + Release | 0/0 | Not started | - |

---
*Roadmap created: 2026-02-15*
*Last updated: 2026-02-22 (Phase 3 UAT gap closure plan)*

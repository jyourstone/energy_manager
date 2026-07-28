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
- [x] **Phase 3: EMS Controller** - Real-time battery mode execution with fuse protection and safety guards
- [ ] **Phase 4: Car Charging** - Per-car price-optimized charging schedules with departure constraints (executed; human UAT re-run pending)
- [x] **Phase 4.1: Correctness + Safety Fixes and HACS Packaging** (INSERTED) - Cross-cutting fixes found in the 2026-07-28 full-system audit, plus repo packaging for HACS compliance
- [x] **Phase 5: Easee Charger Control** - Physical charger control with state machine, solar charging, fuse limiting, and observe-only mode
- [x] **Phase 6: Polish + Release** - Options flow, diagnostics, helper/template internalization, economics model, algorithm improvements, test coverage, and HACS publication

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
**Plans**: 5 plans

Plans:
- [x] 03-01-PLAN.md -- TDD: Pure EMS controller calculations (mode selection, fuse math, amp clamping, car priority, PV hysteresis)
- [x] 03-02-PLAN.md -- EMSCoordinator, config flow EMS step, auto-detection, command sending and verification
- [x] 03-03-PLAN.md -- EMS status sensor, __init__.py wiring, translations
- [x] 03-04-PLAN.md -- UAT gap closure: fix auto-detection patterns and fuse headroom input wiring
- [x] 03-05-PLAN.md -- UAT gap closure: per-phase fuse protection (replace balanced-load averaging with max-phase current)

### Phase 4: Car Charging
**Goal**: Users can configure per-car charging schedules with departure times and target SOC, and the integration selects the cheapest charging slots automatically
**Depends on**: Phase 1 (price data, config flow), Phase 3 (fuse protection foundation)
**Requirements**: EV-01, EV-02, EV-03, EV-04, EV-05, EV-06, EV-07, EV-08, EV-11
**Success Criteria** (what must be TRUE):
  1. User can add a car via config flow with auto-detected Skoda/VW integration, or manually map entity IDs for unsupported cars
  2. Each car appears as a separate device with its own schedule sensor, departure time entity, and target SOC entity
  3. User can see a per-car charging schedule that selects the cheapest hours before the departure deadline to reach the target SOC
  4. When an unrecognized vehicle is connected, fallback charging activates during off-peak hours
**Plans**: 4 plans

Plans:
- [x] 04-01-PLAN.md -- TDD: Pure car charging scheduler algorithm (cheapest N slots, fallback mode, solar marking)
- [x] 04-02-PLAN.md -- CarChargingCoordinator, CarEntity base, fallback detection, per-subentry wiring
- [x] 04-03-PLAN.md -- Per-car entities (schedule sensor, departure time, target SOC, max charge power) and translations
- [x] 04-04-PLAN.md -- UAT gap closure: expand car auto-detection patterns and auto-derive home+plugged state

### Phase 4.1: Correctness + Safety Fixes and HACS Packaging (INSERTED)
**Goal**: The cross-cutting defects found in the 2026-07-28 audit are fixed before any actuation work starts, and the repository is HACS-compliant
**Depends on**: Phase 4
**Requirements**: CORE-15, EMS-09, EMS-10, EMS-11, EMS-12 (+ re-validates CORE-08)
**Success Criteria** (what must be TRUE):
  1. Car scheduler is slot-duration-aware -- correct energy math with live 15-minute Nordpool slots (was booking ~25% of needed time)
  2. Fuse math uses signed phase currents (import positive); PV export increases headroom; fuse rating and buffer are advanced options (20 A / 1 A defaults)
  3. Sensor-unavailable behavior is a config option (degrade with assumed 10 A default, or block) -- the silent 0 A / static 18 A headroom bug is gone
  4. Battery self-draw add-back and asymmetric ESS-limit timing (immediate decrease / 180 s increase) match the tuned live system
  5. EMS car-priority uses real signals (active slot AND home+plugged via the wired _is_home_and_plugged_in) instead of the module-enabled flag
  6. Repo passes HACS + hassfest validation in CI: root hacs.json, README, clean manifest, workflows, repo topics; Swedish translations complete
**Plans**: executed directly as parallel-agent work, 2026-07-28 (no numbered plan files)

### Phase 5: Easee Charger Control
**Goal**: The integration physically controls the Easee charger -- starting/stopping charge sessions, setting dynamic amp limits, and managing solar vs grid charging modes through a robust state machine
**Depends on**: Phase 4.1 (correct fuse math and car-priority signals)
**Requirements**: EASE-01, EASE-02, EASE-03, EASE-04, EASE-05, EASE-06, EASE-07, EASE-08, EASE-09, EV-09, EV-10, EV-12, CORE-14, EMS-13
**Success Criteria** (what must be TRUE):
  1. Charger dynamic amp limit changes automatically based on the active schedule and current fuse load
  2. Start/stop charging sequences follow a defined state machine that handles stuck states with timeout detection and recovery -- treating Easee status as unreliable (power-based cross-checks; live system needed a watchdog automation for stuck status)
  3. User can toggle a force-grid-charging switch to override the schedule and charge immediately
  4. Solar charging mode sets the charger limit based on available PV power after home consumption, and waits for the battery to reach a configurable SOC threshold (default 100%) before activating
  5. Fuse protection limits charger amps based on current phase load with safety buffer -- never exceeds the configured fuse rating; battery ESS limit and charger amps draw from ONE shared fuse-headroom arbiter, and the charger's own current is excluded from the house load it sees
  6. Solar-surplus EV charging routes excess PV power to the charger with hysteresis to prevent on/off cycling (EV-09); surplus = PV - house consumption - battery charging + charger draw (live formula), with EMS-13 exclusion list applied to house consumption
  7. Dynamic phase switching based on available power respects per-car phase capability (1/2/3, default 3) with conversion factors 4.3 / 2.5 / 1.45 A-per-kW (EV-10, EV-12)
  8. Observe-only (dry-run) mode + master switch: all decisions computed, logged and visible in status sensors while device commands are suppressed (CORE-14) -- required before running in parallel with the live AppDaemon system
  9. Safety events reach a configurable notify target (EASE-08); tuned constants exposed as options with live-system defaults (EASE-09)
**Plans**: 5 planned (see 05-RESEARCH.md)

Plans:
- [x] 05-01 (Wave A) -- TDD: charger_state_machine.py pure module (arbitration, amp calc, conversion factors, hysteresis, fuse layers, unauthorized-charge detection)
- [x] 05-02 (Wave A) -- TDD: phase-switch + start/stop sequence states, stuck-state timeouts, power cross-checks
- [x] 05-03 (Wave B) -- EaseeCoordinator, command executor, observe-only master switch, config flow step, shared fuse arbiter
- [x] 05-04 (Wave C) -- Charger entities (status sensor, force switch, master switch), EN+SV translations, solar-surplus wiring (EV-09)
- [x] 05-05 -- UAT prep: observe-only parallel-run checklist, cutover checklist

### Phase 6: Polish + Release
**Goal**: The integration is self-contained with zero external helper dependencies, fully configurable via options flow, comprehensively tested, and ready for HACS publication
**Depends on**: Phase 1-5 (all modules must exist before full options flow and testing)
**Requirements**: CORE-05, CORE-10, CORE-11, BATT-13, BATT-14, BATT-15, QUAL-01, QUAL-02, QUAL-03, QUAL-04, QUAL-05
**Success Criteria** (what must be TRUE):
  1. User can adjust all runtime settings (thresholds, capacities, targets, departure times) via options flow without reconfiguring the integration
  2. ALL previously manual HA helpers AND template sensors are replaced -- inputs as integration entities, derived values computed internally and exposed as diagnostic sensors (fuse headroom, filtered house load, solar surplus, actual price incl. fees); zero Jinja
  3. Economics model: transfer fees + battery cycle cost as number entities; discharge threshold default derived as cycle cost minus transfer fee (live-system parity); multiple Forecast.Solar sensors summed (BATT-13/14)
  4. March 2026 algorithm improvements ported: solar recharge estimation between peaks, refined future-peak energy reservation (BATT-15)
  5. Integration exposes diagnostics data for debugging (accessible via HA Settings > Devices > Diagnostics)
  6. All entity states handle "unavailable" and "unknown" sensor data gracefully -- no crashes, sensible fallback behavior
  7. CI pipeline runs unit tests, integration tests, linting (ruff), HACS validation, and manifest validation on every PR
**Plans**: TBD

Plans:
- [ ] 06-01: TBD
- [ ] 06-02: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 4.1 -> 5 -> 6

| Phase | Plans Complete | Status | Completed |
|-------|---------------|--------|-----------|
| 1. Core Infrastructure + Price Foundation | 5/5 | Complete | 2026-02-15 |
| 2. Home Battery Schedule | 5/5 | Complete | 2026-02-16 |
| 3. EMS Controller | 5/5 | Complete | 2026-02-23 |
| 4. Car Charging | 4/4 | Executed -- human UAT re-run pending (8/10 tests) | - |
| 4.1 Correctness + Safety Fixes and HACS Packaging | - | Complete (incl. local UAT + bug fixes) | 2026-07-28 |
| 5. Easee Charger Control | 3 waves | Executed + observe-only verified locally -- live parallel-run UAT pending | 2026-07-28 |
| 6. Polish + Release | 3 waves | Executed -- QUAL-03 (real-HA lifecycle tests) open; brands PR + v0.1.0 tag pending owner | 2026-07-28 |

---
*Roadmap created: 2026-02-15*
*Last updated: 2026-07-28 (Phase 4.1 inserted from full-system audit; Phase 5/6 scope expanded with owner decisions: observe-only mode, per-car phases, shared fuse arbiter, economics model, March algorithm improvements)*

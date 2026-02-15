# Requirements: Energy Manager

**Defined:** 2025-02-15
**Core Value:** Users can optimize their energy costs by automatically scheduling battery charging/discharging and EV charging based on electricity prices, solar production, and fuse constraints -- without any manual helpers or complex setup.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Core Infrastructure

- [ ] **CORE-01**: User can install integration via HACS and set up via config flow UI
- [ ] **CORE-02**: Config flow auto-detects compatible integrations (Nordpool, SigenStor, Easee, Skoda, VW, Forecast.Solar) and pre-populates entity selections
- [ ] **CORE-03**: User can manually configure all entity IDs for advanced/unsupported setups
- [ ] **CORE-04**: Config flow auto-detects Nordpool integration (required -- blocks setup if not found) with both HACS and native variants supported
- [ ] **CORE-05**: User can adjust all runtime settings via options flow without reconfiguring
- [ ] **CORE-06**: Integration properly handles setup, unload, reload, and config migration lifecycle
- [ ] **CORE-07**: All operations are fully async -- no blocking calls in the event loop
- [ ] **CORE-08**: Integration follows HACS publishing guidelines (hacs.json, manifest.json, proper structure)
- [ ] **CORE-09**: All UI text uses translation system (strings.json / translations/)
- [ ] **CORE-10**: Integration exposes diagnostics platform for debug data export
- [ ] **CORE-11**: All 24 current manual HA helpers are replaced by integration-owned entities (number, switch, select, datetime)
- [ ] **CORE-12**: Integration modules (Home Battery, EV Charging) can be enabled/disabled independently
- [ ] **CORE-13**: Each module works standalone without requiring other modules to be enabled

### Home Battery

- [ ] **BATT-01**: User can view multi-cycle charge/discharge schedule based on Nordpool electricity prices
- [ ] **BATT-02**: Schedule uses peak grouping algorithm to identify separate profitable discharge windows
- [ ] **BATT-03**: Virtual energy tracking simulates battery state through schedule for optimal multi-cycle decisions
- [ ] **BATT-04**: Schedule sensor exposes current state (idle/grid_charging/discharging/solar_charging) with full schedule in attributes
- [ ] **BATT-05**: User can view next charging slot and next discharging slot
- [ ] **BATT-06**: Solar production is factored into scheduling (surplus reduces needed grid charging)
- [ ] **BATT-07**: Config flow auto-detects Forecast.Solar integration (optional) and offers to use it for better production estimates
- [ ] **BATT-08**: User can adjust charge price threshold via number entity
- [ ] **BATT-09**: User can adjust discharge price threshold via number entity
- [ ] **BATT-10**: User can set maximum charging power limit via number entity
- [ ] **BATT-11**: Battery SOC is tracked and used in scheduling decisions
- [ ] **BATT-12**: Schedule recalculates when prices update (event-driven via state change listener)

### EMS Controller

- [ ] **EMS-01**: Integration sets battery EMS mode (command_charging, max_self_consumption, standby) based on current schedule
- [ ] **EMS-02**: Fuse protection dynamically limits battery charging power based on current phase load
- [ ] **EMS-03**: When car is scheduled to charge AND plugged in, battery charging pauses to free fuse capacity
- [ ] **EMS-04**: Safety guards enforce hard limits -- calculated amp values clamped to safe range, never negative
- [ ] **EMS-05**: Command verification reads back actual state after sending control commands
- [ ] **EMS-06**: Fuse rating is a required config field with validation
- [ ] **EMS-07**: User can view EMS status sensor showing current mode and fuse headroom
- [ ] **EMS-08**: PV-based opportunistic charging activates when sufficient solar power detected and battery not full

### EV Charging

- [ ] **EV-01**: User can view price-optimized charging schedule per car
- [ ] **EV-02**: User can set departure time per car via datetime entity
- [ ] **EV-03**: User can set target SOC percentage per car via number entity
- [ ] **EV-04**: Each car is configured as a separate device (via subentry or per-car config)
- [ ] **EV-05**: Integration auto-detects compatible car integrations (Skoda, VW) and offers setup
- [ ] **EV-06**: User can manually add cars with custom entity mappings
- [ ] **EV-07**: Schedule considers car battery capacity and current SOC to calculate energy needed
- [ ] **EV-08**: Fallback charging activates for unrecognized connected vehicles during off-peak hours
- [ ] **EV-09**: Solar-surplus EV charging routes excess PV power to charger with hysteresis to prevent cycling
- [ ] **EV-10**: Dynamic phase switching between 1-phase and 3-phase based on available power
- [ ] **EV-11**: Schedule sensor exposes current state with full schedule in attributes

### Easee Charger Control

- [ ] **EASE-01**: Integration controls Easee charger dynamic amp limit based on schedule and conditions
- [ ] **EASE-02**: Start/stop charging sequences use formal state machine with defined states and transitions
- [ ] **EASE-03**: User can force grid charging via switch entity (replaces input_boolean.easee_force_charging)
- [ ] **EASE-04**: Solar charging mode sets charger limit based on available PV power after home consumption
- [ ] **EASE-05**: Fuse protection limits charger amps based on current phase load and safety buffer
- [ ] **EASE-06**: State machine handles stuck states with timeout detection and recovery
- [ ] **EASE-07**: Battery SOC check prevents solar EV charging until battery is full (configurable threshold)

### Quality & Testing

- [ ] **QUAL-01**: Safety-critical calculations (fuse capacity, amp limits) have exhaustive unit tests
- [ ] **QUAL-02**: Core scheduling algorithms (peak grouping, virtual energy tracking) have unit tests
- [ ] **QUAL-03**: Integration setup/unload/reload lifecycle has integration tests
- [ ] **QUAL-04**: CI pipeline runs tests, linting (ruff), HACS validation, and manifest validation on every PR
- [ ] **QUAL-05**: All entity states handle "unavailable" and "unknown" gracefully -- no crashes on missing sensor data

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Extended Device Support

- **DEV-01**: Support for additional battery systems beyond SigenStor
- **DEV-02**: Support for additional EV chargers beyond Easee
- **DEV-03**: Support for additional car brands beyond Skoda and VW

### Extended Price Sources

- **PRICE-01**: Support for Entso-e price integration
- **PRICE-02**: Support for Tibber price integration
- **PRICE-03**: Generic price sensor adapter (any sensor with hourly prices)

### Advanced Features

- **ADV-01**: Historical cost savings calculator
- **ADV-02**: Calendar entity showing charge/discharge schedule as events
- **ADV-03**: Repair issue raised when state machine stuck for extended period

## Out of Scope

| Feature | Reason |
|---------|--------|
| Energy dashboard / charts | HA Energy Dashboard handles this -- expose proper device_class and state_class instead |
| Mobile notifications | HA automations handle this better -- expose sensor states and fire events |
| Grid tariff / rate management | Nordpool/Entso-e integrations handle pricing -- consume via adapter |
| Weather forecasting | Forecast.Solar already exists -- accept as optional input |
| Inverter/charger firmware updates | Device vendor responsibility -- only use documented HA service calls |
| Real-time power flow visualization | HA power flow cards handle this -- ensure correct entity attributes |
| Complex pricing rules (tiered/demand) | Out of scope for Nordpool spot market -- design adapter to not preclude |
| Manual start/stop of individual charge cycles | Adds complexity -- single force-charge override is sufficient |
| PowerSaver functionality | Stays as separate HACS integration |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CORE-01 | Phase 1 | Pending |
| CORE-02 | Phase 1 | Pending |
| CORE-03 | Phase 1 | Pending |
| CORE-04 | Phase 1 | Pending |
| CORE-05 | Phase 6 | Pending |
| CORE-06 | Phase 1 | Pending |
| CORE-07 | Phase 1 | Pending |
| CORE-08 | Phase 1 | Pending |
| CORE-09 | Phase 1 | Pending |
| CORE-10 | Phase 6 | Pending |
| CORE-11 | Phase 6 | Pending |
| CORE-12 | Phase 1 | Pending |
| CORE-13 | Phase 1 | Pending |
| BATT-01 | Phase 2 | Pending |
| BATT-02 | Phase 2 | Pending |
| BATT-03 | Phase 2 | Pending |
| BATT-04 | Phase 2 | Pending |
| BATT-05 | Phase 2 | Pending |
| BATT-06 | Phase 2 | Pending |
| BATT-07 | Phase 2 | Pending |
| BATT-08 | Phase 2 | Pending |
| BATT-09 | Phase 2 | Pending |
| BATT-10 | Phase 2 | Pending |
| BATT-11 | Phase 2 | Pending |
| BATT-12 | Phase 2 | Pending |
| EMS-01 | Phase 3 | Pending |
| EMS-02 | Phase 3 | Pending |
| EMS-03 | Phase 3 | Pending |
| EMS-04 | Phase 3 | Pending |
| EMS-05 | Phase 3 | Pending |
| EMS-06 | Phase 3 | Pending |
| EMS-07 | Phase 3 | Pending |
| EMS-08 | Phase 3 | Pending |
| EV-01 | Phase 4 | Pending |
| EV-02 | Phase 4 | Pending |
| EV-03 | Phase 4 | Pending |
| EV-04 | Phase 4 | Pending |
| EV-05 | Phase 4 | Pending |
| EV-06 | Phase 4 | Pending |
| EV-07 | Phase 4 | Pending |
| EV-08 | Phase 4 | Pending |
| EV-09 | Phase 4 | Pending |
| EV-10 | Phase 4 | Pending |
| EV-11 | Phase 4 | Pending |
| EASE-01 | Phase 5 | Pending |
| EASE-02 | Phase 5 | Pending |
| EASE-03 | Phase 5 | Pending |
| EASE-04 | Phase 5 | Pending |
| EASE-05 | Phase 5 | Pending |
| EASE-06 | Phase 5 | Pending |
| EASE-07 | Phase 5 | Pending |
| QUAL-01 | Phase 6 | Pending |
| QUAL-02 | Phase 6 | Pending |
| QUAL-03 | Phase 6 | Pending |
| QUAL-04 | Phase 6 | Pending |
| QUAL-05 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 56 total
- Mapped to phases: 56
- Unmapped: 0

---
*Requirements defined: 2025-02-15*
*Last updated: 2026-02-15 after roadmap creation (traceability mapped)*

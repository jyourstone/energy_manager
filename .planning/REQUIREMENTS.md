# Requirements: Energy Manager

**Defined:** 2025-02-15
**Core Value:** Users can optimize their energy costs by automatically scheduling battery charging/discharging and EV charging based on electricity prices, solar production, and fuse constraints -- without any manual helpers or complex setup.

**Design principle (owner decision 2026-07-28):** Maximally customizable for other users, but with production-proven baseline defaults so setup stays simple. Every tuned constant from the live AppDaemon system becomes a config option with its tuned value as default. No manual helpers or template sensors -- everything lives in the integration.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Core Infrastructure

- [x] **CORE-01**: User can install integration via HACS and set up via config flow UI
- [x] **CORE-02**: Config flow auto-detects compatible integrations (Nordpool, SigenStor, Easee, Skoda, VW, Forecast.Solar) and pre-populates entity selections
- [x] **CORE-03**: User can manually configure all entity IDs for advanced/unsupported setups
- [x] **CORE-04**: Config flow auto-detects Nordpool integration (required -- blocks setup if not found) with both HACS and native variants supported
- [ ] **CORE-05**: User can adjust all runtime settings via options flow without reconfiguring
- [x] **CORE-06**: Integration properly handles setup, unload, reload, and config migration lifecycle
- [x] **CORE-07**: All operations are fully async -- no blocking calls in the event loop
- [x] **CORE-08**: Integration follows HACS publishing guidelines (hacs.json, manifest.json, proper structure)
- [x] **CORE-09**: All UI text uses translation system (strings.json / translations/)
- [ ] **CORE-10**: Integration exposes diagnostics platform for debug data export
- [ ] **CORE-11**: ALL manual HA helpers AND template sensors are replaced -- inputs become integration-owned entities (number, switch, select, datetime), derived values (fuse headroom, filtered house load, solar surplus, actual price incl. fees) are computed internally and exposed as diagnostic sensors; zero Jinja templates required
- [x] **CORE-12**: Integration modules (Home Battery, EV Charging) can be enabled/disabled independently
- [x] **CORE-13**: Each module works standalone without requiring other modules to be enabled
- [ ] **CORE-14**: Observe-only (dry-run) mode plus master enable switch -- integration computes and logs all decisions without sending any device commands, for safe parallel running next to a legacy system
- [ ] **CORE-15**: English and Swedish translations for all UI text (extends CORE-09)

### Home Battery

- [x] **BATT-01**: User can view multi-cycle charge/discharge schedule based on Nordpool electricity prices
- [x] **BATT-02**: Schedule uses peak grouping algorithm to identify separate profitable discharge windows
- [x] **BATT-03**: Virtual energy tracking simulates battery state through schedule for optimal multi-cycle decisions
- [x] **BATT-04**: Schedule sensor exposes current state (idle/grid_charging/discharging/solar_charging) with full schedule in attributes
- [x] **BATT-05**: User can view next charging slot and next discharging slot
- [x] **BATT-06**: Solar production is factored into scheduling (surplus reduces needed grid charging)
- [x] **BATT-07**: Config flow auto-detects Forecast.Solar integration (optional) and offers to use it for better production estimates
- [x] **BATT-08**: User can adjust charge price threshold via number entity
- [x] **BATT-09**: User can adjust discharge price threshold via number entity
- [x] **BATT-10**: User can set maximum charging power limit via number entity
- [x] **BATT-11**: Battery SOC is tracked and used in scheduling decisions
- [x] **BATT-12**: Schedule recalculates when prices update (event-driven via state change listener)
- [ ] **BATT-13**: Multiple Forecast.Solar sensors (e.g. east + west arrays) can be selected and are summed; solar features activate only when Forecast.Solar is installed
- [ ] **BATT-14**: Economics model -- charge/discharge decisions account for grid transfer fees and battery cycle cost via number entities; default discharge threshold derives from cycle cost minus transfer fee (parity with live system formula)
- [ ] **BATT-15**: March 2026 algorithm improvements ported -- solar recharge estimation between peaks and refined future-peak energy reservation

### EMS Controller

- [x] **EMS-01**: Integration sets battery EMS mode (command_charging, max_self_consumption, standby) based on current schedule
- [x] **EMS-02**: Fuse protection dynamically limits battery charging power based on current phase load
- [x] **EMS-03**: When car is scheduled to charge AND plugged in, battery charging pauses to free fuse capacity
- [x] **EMS-04**: Safety guards enforce hard limits -- calculated amp values clamped to safe range, never negative
- [x] **EMS-05**: Command verification reads back actual state after sending control commands
- [x] **EMS-06**: Fuse rating is a required config field with validation
- [x] **EMS-07**: User can view EMS status sensor showing current mode and fuse headroom
- [x] **EMS-08**: PV-based opportunistic charging activates when sufficient solar power detected and battery not full
- [ ] **EMS-09**: Fuse math uses SIGNED phase currents (import positive, export negative) -- PV export increases available headroom; fuse rating (default 20 A) and safety buffer (default 1 A) are advanced config options
- [ ] **EMS-10**: Sensor-unavailable behavior is configurable: degrade with assumed load (default, 10 A configurable) or block charging -- never a silent 0 A assumption
- [ ] **EMS-11**: Battery's own charging draw is added back when computing available ESS headroom (no self-ratcheting limit)
- [ ] **EMS-12**: ESS charge-limit changes use asymmetric timing -- decreases apply immediately, increases only after a configurable stability delay (default 180 s)
- [ ] **EMS-13**: User can exclude specific power sensors (e.g. a separately-managed water heater) from the house consumption calculation

### EV Charging

- [x] **EV-01**: User can view price-optimized charging schedule per car
- [x] **EV-02**: User can set departure time per car via datetime entity
- [x] **EV-03**: User can set target SOC percentage per car via number entity
- [x] **EV-04**: Each car is configured as a separate device (via subentry or per-car config)
- [x] **EV-05**: Integration auto-detects compatible car integrations (Skoda, VW) and offers setup
- [x] **EV-06**: User can manually add cars with custom entity mappings
- [x] **EV-07**: Schedule considers car battery capacity and current SOC to calculate energy needed
- [x] **EV-08**: Fallback charging activates for unrecognized connected vehicles during off-peak hours (window configurable, default 00:00-06:00)
- [ ] **EV-09**: Solar-surplus EV charging routes excess PV power to charger with hysteresis to prevent cycling
- [ ] **EV-10**: Dynamic phase switching between 1-phase and 3-phase based on available power
- [x] **EV-11**: Schedule sensor exposes current state with full schedule in attributes
- [ ] **EV-12**: Per-car phase capability option (1, 2 or 3 phases, default 3) with matching amp/kW conversion factors -- supports cars that charge on fewer phases (e.g. VW ID.3 on 2)

### Easee Charger Control

- [ ] **EASE-01**: Integration controls Easee charger dynamic amp limit based on schedule and conditions
- [ ] **EASE-02**: Start/stop charging sequences use formal state machine with defined states and transitions
- [ ] **EASE-03**: User can force grid charging via switch entity (replaces input_boolean.easee_force_charging)
- [ ] **EASE-04**: Solar charging mode sets charger limit based on available PV power after home consumption
- [ ] **EASE-05**: Fuse protection limits charger amps based on current phase load and safety buffer
- [ ] **EASE-06**: State machine handles stuck states with timeout detection and recovery
- [ ] **EASE-07**: Battery SOC check prevents solar EV charging until battery is full (configurable threshold, default 100%)
- [ ] **EASE-08**: Safety events (fuse emergency pause, 0A safety stop, command verification failure) send push notifications to a user-configurable notify target
- [ ] **EASE-09**: All tuned control constants are config options with production-proven defaults: amp increase delay 120 s / decrease delay 5 s, phase switch threshold 4.1 kW, max grid charge power 12 kW, solar start threshold 1.5 kW with 300 s activation delay and configurable deactivation delay, min/max charge amps 6/16 A

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
| Multiple chargers / simultaneous multi-car charging | v1 supports one charger with one car plugged at a time -- arbitration deferred to v2 |
| Backwards compatibility / migrations | Integration is unreleased and unused -- option keys, entity ids, and defaults may change freely until first release |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CORE-01 | Phase 1 | Complete |
| CORE-02 | Phase 1 | Complete |
| CORE-03 | Phase 1 | Complete |
| CORE-04 | Phase 1 | Complete |
| CORE-05 | Phase 6 | Pending |
| CORE-06 | Phase 1 | Complete |
| CORE-07 | Phase 1 | Complete |
| CORE-08 | Phase 1 | Complete |
| CORE-09 | Phase 1 | Complete |
| CORE-10 | Phase 6 | Pending |
| CORE-11 | Phase 6 | Pending |
| CORE-12 | Phase 1 | Complete |
| CORE-13 | Phase 1 | Complete |
| CORE-14 | Phase 5 | Pending |
| CORE-15 | Phase 4.1 | Pending |
| BATT-01 | Phase 2 | Complete |
| BATT-02 | Phase 2 | Complete |
| BATT-03 | Phase 2 | Complete |
| BATT-04 | Phase 2 | Complete |
| BATT-05 | Phase 2 | Complete |
| BATT-06 | Phase 2 | Complete |
| BATT-07 | Phase 2 | Complete |
| BATT-08 | Phase 2 | Complete |
| BATT-09 | Phase 2 | Complete |
| BATT-10 | Phase 2 | Complete |
| BATT-11 | Phase 2 | Complete |
| BATT-12 | Phase 2 | Complete |
| BATT-13 | Phase 6 | Pending |
| BATT-14 | Phase 6 | Pending |
| BATT-15 | Phase 6 | Pending |
| EMS-01 | Phase 3 | Complete |
| EMS-02 | Phase 3 | Complete |
| EMS-03 | Phase 3 | Complete |
| EMS-04 | Phase 3 | Complete |
| EMS-05 | Phase 3 | Complete |
| EMS-06 | Phase 3 | Complete |
| EMS-07 | Phase 3 | Complete |
| EMS-08 | Phase 3 | Complete |
| EMS-09 | Phase 4.1 | Pending |
| EMS-10 | Phase 4.1 | Pending |
| EMS-11 | Phase 4.1 | Pending |
| EMS-12 | Phase 4.1 | Pending |
| EMS-13 | Phase 5 | Pending |
| EV-01 | Phase 4 | Complete |
| EV-02 | Phase 4 | Complete |
| EV-03 | Phase 4 | Complete |
| EV-04 | Phase 4 | Complete |
| EV-05 | Phase 4 | Complete |
| EV-06 | Phase 4 | Complete |
| EV-07 | Phase 4 | Complete |
| EV-08 | Phase 4 | Complete |
| EV-09 | Phase 5 | Pending |
| EV-10 | Phase 5 | Pending |
| EV-11 | Phase 4 | Complete |
| EV-12 | Phase 5 | Pending |
| EASE-01 | Phase 5 | Pending |
| EASE-02 | Phase 5 | Pending |
| EASE-03 | Phase 5 | Pending |
| EASE-04 | Phase 5 | Pending |
| EASE-05 | Phase 5 | Pending |
| EASE-06 | Phase 5 | Pending |
| EASE-07 | Phase 5 | Pending |
| EASE-08 | Phase 5 | Pending |
| EASE-09 | Phase 5 | Pending |
| QUAL-01 | Phase 6 | Pending |
| QUAL-02 | Phase 6 | Pending |
| QUAL-03 | Phase 6 | Pending |
| QUAL-04 | Phase 6 | Pending |
| QUAL-05 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 69 total
- Mapped to phases: 69
- Unmapped: 0

---
*Requirements defined: 2025-02-15*
*Last updated: 2026-07-28 (owner decisions folded in: 13 new requirements CORE-14/15, BATT-13..15, EMS-09..13, EV-12, EASE-08/09; Phase 1-3 requirements marked Complete per verified phases; Phase 4.1 inserted)*

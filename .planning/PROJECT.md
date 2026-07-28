# Energy Manager

## What This Is

A modular Home Assistant HACS integration that manages home energy systems — battery storage, EV charging, and the coordination between them. It converts the proven logic from an existing AppDaemon-based energy management system into a native HA integration with proper config flows, auto-discovery, and a polished user experience. Each module (Home Battery, EV Charging) works independently or together, coordinated by a lightweight core.

## Core Value

Users can optimize their energy costs by automatically scheduling battery charging/discharging and EV charging based on electricity prices, solar production, and fuse constraints — without any manual helpers or complex setup.

## Requirements

### Validated

<!-- Proven in AppDaemon — these algorithms and behaviors work in production today. -->

- ✓ Multi-cycle battery charge/discharge scheduling based on Nordpool prices — existing (HomeBatteryManager)
- ✓ Peak grouping algorithm for identifying profitable discharge windows — existing (HomeBatteryManager)
- ✓ Virtual energy tracking for multi-cycle charging decisions — existing (HomeBatteryManager)
- ✓ EMS mode state machine (command_charging, max_self_consumption, standby) — existing (EMSController)
- ✓ Car charging optimization with price-aware scheduling — existing (CarChargingManager)
- ✓ Multi-car support with independent schedules per vehicle — existing (2x CarChargingManager instances)
- ✓ Fuse protection: dynamic current limiting across battery + car charging — existing (EMSController + EaseeController)
- ✓ Solar-aware EV charging (net available PV power for car charging) — existing (EaseeController)
- ✓ Car/battery charging priority coordination — existing (EMSController)
- ✓ Easee charger control: dynamic limits, start/stop sequences, solar/grid modes — existing (EaseeController)
- ✓ SigenStor battery EMS mode control — existing (EMSController)
- ✓ Nordpool price integration (both HACS and native variants) — existing (proven pattern from PowerSaver)

### Active

<!-- v1 scope: Feature parity + polish as a HACS integration. -->

- [ ] Modular architecture: Core + Home Battery + EV Charging modules, each works independently
- [ ] Config flow with auto-discovery of compatible integrations (SigenStor, Easee, Skoda, VW)
- [ ] Manual configuration option for advanced users
- [ ] Nordpool adapter supporting both native and HACS variants (reuse PowerSaver pattern)
- [ ] Optional Forecast.Solar integration for better production estimates
- [ ] All derived values computed internally — no user-created helpers needed
- [ ] Per-car settings (battery capacity, departure time, charging target) in config/options flow
- [ ] Auto-detect compatible car integrations + manual add
- [ ] Fuse current calculated from device sensors (replaces manual sensor.highest_l_current)
- [ ] Car home+plugged status derived from car/charger integrations (replaces manual helpers)
- [ ] Net available solar power computed internally (replaces sensor.solar_net_available_power_adjusted_easee)
- [ ] Price thresholds configurable via options flow (replaces input_number helpers)
- [ ] Proper HA error handling, retry logic, and graceful degradation
- [ ] HACS-compliant repository structure and publishing guidelines
- [ ] Home Assistant integration guidelines compliance where possible

### Out of Scope

- PowerSaver functionality — stays as separate HACS integration
- Support for battery/charger brands beyond SigenStor and Easee — future milestone
- Support for car brands beyond Skoda and VW — future milestone
- Price sources beyond Nordpool — future milestone (design should not preclude this)
- Real-time energy dashboards — HA Energy dashboard already covers this
- Mobile app notifications — HA automations can handle this from exposed sensors

## Context

### Source Codebase
The AppDaemon scripts at `/Volumes/addon_configs/a0d7b954_appdaemon/` are the reference implementation. Five apps (HomeBatteryManager, CarChargingManager x2, EMSController, EaseeController) communicate exclusively through HA entity state. The codebase map at `.planning/codebase/` documents the full architecture, concerns, and tech debt.

### Key Algorithms to Port
- **Peak grouping:** Groups profitable discharge slots with configurable gap threshold (`peak_gap_hours`)
- **Virtual energy tracking:** Simulates battery state through schedule for multi-cycle decisions
- **Fuse protection:** Converts power/current to amps, applies buffer, enforces max across all devices
- **Car charging optimization:** Price-sorted slot selection considering departure time, target SOC, and available power

### Prior Art
- **PowerSaver HACS integration** (`jyourstone/power_saver`): Already converted from AppDaemon. Provides proven patterns for Nordpool adapter, config flows, DataUpdateCoordinator usage, HACS structure.
- **ev_smart_charging** (`jonasbkarlsson/ev_smart_charging`): Existing HACS addon for EV charging. Research for inspiration and potential reuse of charging logic.

### External Dependencies (HA Integrations)
- **Nordpool** (native or HACS) — electricity prices
- **SigenStor** — home battery system sensors and control
- **Easee** — EV charger sensors and control
- **Skoda Connect / VW We Connect** — car battery level and status
- **Forecast.Solar** (optional) — solar production forecasts

### Currently Manual Helpers (24 total — all to be internalized)
The AppDaemon scripts depend on 24 user-created helpers (input_number, input_datetime, input_boolean, template sensors). The integration must compute or configure all of these internally:
- 8 input_number entities (thresholds, capacities, targets)
- 2 input_datetime entities (departure times)
- 1 input_boolean entity (force charging override)
- 13 template sensors (highest current, home+plugged status, solar available power, production tracking)

## Constraints

- **HACS compliance**: Must follow HACS publishing guidelines (hacs.json, manifest.json, proper repo structure)
- **HA guidelines**: Follow Home Assistant integration development guidelines where possible
- **New repository**: Code lives in `jyourstone/energy_manager`, separate from AppDaemon scripts
- **Nordpool pricing**: v1 supports Nordpool only (both HACS and native variants)
- **Device support**: v1 supports SigenStor, Easee, Skoda, VW only
- **No external helpers**: Users must not need to create any manual HA helpers
- **Independent modules**: Each module (Home Battery, EV Charging) must function without the others

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Modular architecture by device type | Users may have only battery OR only EV charger — each should work standalone | — Pending |
| Core/hub always present | Provides coordination when multiple modules active, lightweight when single module | — Pending |
| Auto-discovery + manual override | Easy setup for most users, flexibility for power users | — Pending |
| Nordpool adapter from PowerSaver | Proven pattern, handles both HACS and native variants | — Pending |
| Forecast.Solar optional | Not all users have it; fall back to actual production data | — Pending |
| New repository | Clean separation from AppDaemon, proper HACS structure | — Pending |
| PowerSaver stays separate | Different concern, already published as standalone | — Pending |

---
*Last updated: 2025-02-15 after initialization*

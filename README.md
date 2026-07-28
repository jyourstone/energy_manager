<h1 align="center">Energy Manager</h1>

<p align="center">
  A Home Assistant custom integration for price-optimized home energy management:<br>
  home battery scheduling, EV charging and solar — built around<br>
  <a href="https://www.home-assistant.io/integrations/nordpool/">Nordpool</a> electricity prices.
</p>

<p align="center">
  <a href="https://github.com/jyourstone/energy_manager/releases"><img src="https://img.shields.io/github/v/release/jyourstone/energy_manager" alt="Release"></a>
  <a href="https://github.com/jyourstone/energy_manager/blob/main/LICENSE"><img src="https://img.shields.io/github/license/jyourstone/energy_manager" alt="License"></a>
  <a href="https://buymeacoffee.com/jyourstone"><img src="https://img.shields.io/badge/Buy_Me_A_Coffee-FFDD00?logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee"></a>
</p>

---

## Status: pre-release

Energy Manager is under active development and has not reached a stable 1.0 yet. What works today:

- Nordpool price ingestion
- 4-step config wizard with auto-detection
- Home battery charge/discharge scheduling
- EMS mode control of a SigenStor inverter, including per-phase fuse protection
- Per-car charging schedule computation (cheapest slots under a deadline + target SOC)
- PV opportunistic charging with hysteresis
- Easee charger actuation implemented, gated behind the observe-only "Device control" switch; being validated in parallel with the legacy system

Not yet implemented:

- **Full options flow** — most settings are configured once via the setup wizard; a complete "Configure" flow for changing them afterwards is still in development

Expect rough edges, and expect entities/config to change before 1.0.

## What it does

Energy Manager replaces a pile of manual Home Assistant helpers, template sensors, and automations with a single integration that:

- Reads Nordpool spot prices (both the official native integration and the HACS custom component)
- Computes a multi-cycle home battery charge/discharge schedule using peak grouping and virtual energy tracking, so the battery charges before the most profitable price peaks rather than just reacting to the current price
- Drives a SigenStor inverter's EMS mode (`Command Charging (PV First)` / `Maximum Self Consumption` / `Standby`) to actually execute that schedule, with dynamic per-phase fuse headroom limiting and command read-back verification
- Computes a price-optimized charging schedule per car, picking the cheapest available slots that get the car to its target SOC by its departure time
- Adds PV-opportunistic charging with hysteresis, so surplus solar production nudges the battery/car into charging without rapid on/off cycling

## Features

- **Nordpool price ingestion** — native Home Assistant Nordpool integration and the HACS Nordpool custom component are both supported and auto-detected
- **4-step config wizard with auto-detection** — scans your entity registry for Nordpool, SigenStor, Easee, and car integrations (Skoda Connect, VW We Connect) and pre-fills the setup form
- **Home battery scheduling** — multi-cycle charge/discharge schedule based on configurable price thresholds, exposed as `number` entities that persist across restarts (`RestoreNumber`)
- **EMS mode control** — sets the SigenStor EMS mode (command charging / max self-consumption / standby) to follow the computed schedule, with per-phase fuse protection and verification that commands actually took effect
- **Per-car configuration** — each car is a config subentry with its own departure time, target SOC, and max charge power entities, plus a cheapest-slot charging schedule constrained by the departure deadline
- **PV opportunistic charging** — surplus solar production can trigger charging outside the price-driven schedule, with a hysteresis band to avoid rapid mode switching
- **Easee charger control** — mode arbitration (forced/scheduled/solar/idle), dynamic amp limit with fuse protection, 1/2/3-phase awareness, solar-surplus charging, and a force-charging switch
- **Modular** — Home Battery and EV Charging can each be enabled independently; a module works standalone without the other being configured
- **Translations** — UI strings use Home Assistant's translation system; English and Swedish are both complete

## Requirements

- Home Assistant 2024.12.0 or newer
- A Nordpool sensor (native or HACS integration), configured and providing prices
- Optional: a SigenStor battery inverter for home battery scheduling + EMS control
- Optional: an Easee charger and/or a supported car integration (Skoda Connect, VW We Connect) for EV charging

## Installation

### HACS (custom repository)

Energy Manager is not yet in the HACS default store. Add it as a custom repository:

[![Add custom repository to your Home Assistant instance.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jyourstone&repository=energy_manager&category=integration)

1. Click the button above, or in HACS go to **Integrations** -> the **⋮** menu -> **Custom repositories**, and add `jyourstone/energy_manager` as category **Integration**
2. Search for **Energy Manager** in HACS and click **Install**
3. Restart Home Assistant

### Manual

1. Copy the `custom_components/energy_manager` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

[![Add integration to your Home Assistant instance.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=energy_manager)

Click the button above, or add it manually via **Settings** -> **Devices & Services** -> **Add Integration** -> search for **Energy Manager**.

### Setup wizard

**Step 1 — Price source:** Select your Nordpool sensor. Auto-detected if either the native or HACS Nordpool integration is installed.

**Step 2 — Modules:** Choose which modules to enable — Home Battery and/or EV Charging. Both are optional and independent; you can enable just one.

**Step 3 — Home Battery** *(if enabled)*: Battery SOC and power entities are auto-detected from a SigenStor integration if present. Also configures battery capacity (kWh) and, optionally, one or more Forecast.Solar "remaining today" entities for solar-aware scheduling (e.g. separate east + west arrays — their readings are summed), plus the BATT-15 algorithm tuning options (charge buffer %, solar production factor, estimated charge power, and peak-grouping gap). Continues into EMS setup: fuse rating, EMS mode select entity, charge/discharge limit entities, and grid power/phase entities (all auto-detected from SigenStor where possible), plus an optional PV power entity for opportunistic charging.

**Step 4 — EV Charging** *(if enabled)*: Charger status and power entities, auto-detected from an Easee integration if present, plus the charger's device ID (auto-detected) used to address the Easee control services. Also configures charger amp limits, grid charging power cap, phase-switch and solar-charging thresholds, and an optional notify service for charger safety alerts — all pre-filled with tuned defaults and grouped with the advanced options at the end of the step.

After setup, each car is added separately as a **subentry** on the Energy Manager device: give it a name, battery capacity, optionally battery-level, charger-connected, and location entities (auto-detected from Skoda Connect or VW We Connect if present), and how many charger phases it actually uses (1/2/3, default 3).

## Entities created

### Sensors

| Sensor | Description |
|--------|--------------|
| Electricity Price | Current Nordpool price |
| Battery Schedule | Full multi-cycle charge/discharge schedule with status and attributes |
| Next Charging Slot | Timestamp of the next scheduled charge slot |
| Next Discharging Slot | Timestamp of the next scheduled discharge slot |
| EMS Status | Current SigenStor EMS mode and fuse headroom |
| Actual Electricity Price | Spot price + grid transfer fee + electricity company fee (diagnostic; no long-term statistics) |
| Car Schedule *(per car)* | Cheapest-slot charging schedule for that car |
| Charger Status | Easee charger decision mode (forced/scheduled/solar/idle), target amps/phase mode, fuse headroom, and more |
| House Load *(diagnostic)* | Filtered house consumption (house consumption minus excluded power entities), with the BATT-15 rolling mean consumption as an attribute |
| Solar Surplus *(diagnostic)* | Raw computed solar surplus (PV minus house load minus battery charging plus charger draw) before the charger's own activation gating |

### Switches

| Entity | Description |
|--------|--------------|
| Device control | Master observe-only switch (CORE-14); OFF means every coordinator still computes and publishes decisions, but no device command is actually sent |
| Force charging | Forces the Easee charger to grid-charge regardless of schedule or solar state (EASE-03) |

### Numbers

| Entity | Description |
|--------|--------------|
| Charge Price Threshold | Spread threshold (SEK/kWh): a slot is a charge candidate for a peak when that peak's max price minus the slot's price exceeds this value |
| Discharge Price Threshold | Spread threshold (SEK/kWh): a slot discharges when its price minus the period's minimum price exceeds this value. Overridden by the Battery Cycle Cost formula below when that is set above 0 |
| Max Charge Power | Maximum battery charge power (kW) |
| Battery Cycle Cost | Cost of one battery charge/discharge cycle (SEK/kWh). When above 0, the effective discharge threshold becomes `battery_cycle_cost - grid_transfer_fee`, overriding the manual Discharge Price Threshold above (parity with the live system's economics formula). Default 0 (disabled) |
| Grid Transfer Fee | Grid transfer fee (SEK/kWh); feeds the Battery Cycle Cost formula and the Actual Electricity Price sensor |
| Electricity Company Fee | Electricity company fee (SEK/kWh); used only by the Actual Electricity Price sensor |
| Car Target SOC *(per car)* | Target state of charge for that car |
| Car Max Charge Power *(per car)* | Maximum charge power for that car (kW) |

### Time

| Entity | Description |
|--------|--------------|
| Car Departure Time *(per car)* | Deadline used to compute that car's charging schedule |

All number entities persist their value across Home Assistant restarts.

## Diagnostics

Settings > Devices & Services > Energy Manager > Download diagnostics gives a full snapshot of the config entry (data/options), every active coordinator's current state, and the runtime control flags (device control, force charging, forwarded platforms) — useful when reporting a bug.

## Disclaimer

The vast majority of this project was developed by an AI assistant. While I do have some basic experience with programming from a long time ago, I'm essentially the architect, guiding the AI, fixing its occasional goofs, and trying to keep it from becoming self-aware.

This is pre-release software: expect breaking changes, missing features (see Status above), and use it at your own risk — especially anything that actuates a battery inverter or EV charger.

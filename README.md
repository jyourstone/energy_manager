<p align="center">
  <img src="https://raw.githubusercontent.com/jyourstone/energy_manager/main/images/logo.png" alt="Energy Manager logo" width="140">
</p>

<h1 align="center">Energy Manager</h1>

<p align="center">
  A Home Assistant custom integration for price-optimized home energy management:<br>
  home battery scheduling, EV charging and solar — built around<br>
  <a href="https://www.home-assistant.io/integrations/nordpool/">Nordpool</a> electricity prices.
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Default-41BDF5.svg" alt="HACS"></a>
  <a href="https://github.com/jyourstone/energy_manager/releases"><img src="https://img.shields.io/github/v/release/jyourstone/energy_manager" alt="Release"></a>
  <a href="https://github.com/jyourstone/energy_manager/blob/main/LICENSE"><img src="https://img.shields.io/github/license/jyourstone/energy_manager" alt="License"></a>
  <a href="https://buymeacoffee.com/jyourstone"><img src="https://img.shields.io/badge/Buy_Me_A_Coffee-FFDD00?logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee"></a>
</p>

<p align="center">
  <b>Plug and play</b> with SigenStor battery inverters and Easee EV chargers —<br>
  <b>bring your own hardware</b> for everything else.
</p>

---

> **📖 Full documentation: [energy-manager.dinsten.se](https://energy-manager.dinsten.se)** — setup wizard walkthrough, user guides for every module, the bring-your-own-hardware contracts with worked examples, and the full entity reference.

## What it does

Electricity prices swing a lot from hour to hour. Energy Manager reads the day-ahead Nordpool prices and plans around them: it charges your home battery and/or your car during the cheapest hours, runs the house on battery power during the expensive ones, and puts solar surplus to work. Then it executes those plans — natively on SigenStor inverters and Easee chargers, or through your own automations on any other hardware.

Three independent modules — enable any combination:

- **Home Battery** — charges the battery in cheap hours and discharges it into expensive ones, planning several charge/discharge cycles ahead instead of just reacting to the current price. Optionally sells battery energy to the grid during extreme price spikes ([export arbitrage](https://energy-manager.dinsten.se/user-guide/battery-export-arbitrage/)).
- **EV Charging** — a schedule per car: the cheapest hours that reach its target charge level before its departure time, plus charging on surplus solar.
- **Solar Appliances** — turns switch loads (water heater, pool pump) on while measured solar surplus covers them, and off when it disappears ([details](https://energy-manager.dinsten.se/user-guide/solar-appliances/)).

## Two ways to use it

Energy Manager always does the planning. How the plans reach your hardware depends on what you own:

**1. Plug and play — SigenStor + Easee.** With a SigenStor battery inverter and/or an Easee EV charger (their Home Assistant integrations installed), Energy Manager controls the hardware directly: it sets the inverter's EMS (Energy Management System) mode and charge/discharge limits, and sends the charger current and phase-mode commands. Nothing for you to build.

**2. Bring your own hardware — any inverter, any charger.** Energy Manager plans, your automations execute: all inputs (state of charge, power, and price entities) are free entity pickers, and every decision is published on diagnostic **command sensors** (battery EMS mode, charge/discharge limits, charger current, phase mode). You write one small automation per sensor that forwards the value to your hardware — the full sensor contract and worked YAML examples are in the [Bring your own hardware docs](https://energy-manager.dinsten.se/bring-your-own-hardware/command-sensors/).

| | **Plug and play**<br>SigenStor + Easee | **Bring your own hardware**<br>any inverter, any charger |
|---|---|---|
| Price-optimized battery & EV schedules | Yes | Yes |
| Battery control (EMS mode, charge/discharge limits) | Automatic | Your automations, following the [command sensors](https://energy-manager.dinsten.se/bring-your-own-hardware/command-sensors/) |
| EV charger control (current, phase mode) | Automatic | Your automations, following the command sensors |
| Fuse protection — power capped to your main fuse's headroom, per phase | Yes | Yes, applied to the published commands |
| Solar Appliances (any `switch`/`input_boolean`) | Yes | Yes |
| Automations you write | None | One small one per command you follow |

Optional on either path: a car integration for automatic battery-level and location detection (any car integration works -- point Energy Manager at the car's device and its entities are suggested for you), and a notify service for charger safety alerts.

## Features

- **Nordpool, native or HACS** — both the official Nordpool integration and the HACS custom component are supported and auto-detected. All price entities follow your Nordpool sensor's currency (SEK, NOK, DKK, EUR, ...) — no conversion, everything stays in your area's currency.
- **Solar-aware** — [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) forecasts shape the battery schedule, and surplus solar production can charge the battery or car outside the price plan — without rapid on/off cycling.
- **Observe-only by default** — the master **Device control** switch ships OFF: Energy Manager computes and publishes every decision but sends no hardware command until you turn it on, so you can watch what it *would* do first.
- **Guided setup** — the config wizard auto-detects Nordpool, SigenStor, Easee, and car integrations and pre-fills the forms. Every setting can be changed later via **Configure** — no re-adding needed.
- **Translations** — English and Swedish, both complete.

## Status

Energy Manager is pre-release: under active development, with no stable 1.0 yet. Everything above is implemented, but expect rough edges — and expect entities and configuration to change between releases before 1.0. See the [Disclaimer](#disclaimer) for the fine print.

## Requirements

- Home Assistant 2025.3.0 or newer (config subentry support)
- A [Nordpool](https://www.home-assistant.io/integrations/nordpool/) sensor (native or HACS integration), configured and providing prices
- Recommended: the [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) integration for solar-aware battery scheduling

No specific battery, inverter, or charger is required to install — which hardware is controlled natively versus via your own automations is covered in [Two ways to use it](#two-ways-to-use-it). Full details in the [installation docs](https://energy-manager.dinsten.se/getting-started/installation/).

## Installation

### HACS (Recommended)

Energy Manager is in the HACS default store:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jyourstone&repository=energy_manager&category=integration)

1. Click the button above, or search for **Energy Manager** in HACS
2. Click **Download**
3. Restart Home Assistant

### Manual

1. Copy the `custom_components/energy_manager` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

[![Add integration to your Home Assistant instance.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=energy_manager)

Click the button above, or add it manually via **Settings** -> **Devices & Services** -> **Add Integration** -> search for **Energy Manager**. A guided wizard walks you through the rest — the [Setup Wizard docs](https://energy-manager.dinsten.se/getting-started/setup-wizard/) cover every step and field.

## Disclaimer

The vast majority of this project was developed by an AI assistant. While I do have some basic experience with programming from a long time ago, I'm essentially the architect, guiding the AI, fixing its occasional goofs, and trying to keep it from becoming self-aware.

This is pre-release software: expect breaking changes and missing features, and use it at your own risk — especially anything that actuates a battery inverter or EV charger.

# Energy Manager

<div align="center">
    <p>
        <img alt="Energy Manager logo" src="images/logo.png" style="height: 160px; width: auto;"/>
    </p>
    <p>
        <a href="https://github.com/hacs/integration"><img alt="HACS" src="https://img.shields.io/badge/HACS-Default-41BDF5.svg"/></a>
        <a href="https://github.com/jyourstone/energy_manager/releases"><img alt="Release" src="https://img.shields.io/github/v/release/jyourstone/energy_manager"/></a>
        <a href="https://github.com/jyourstone/energy_manager/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/jyourstone/energy_manager"/></a>
        <a href="https://buymeacoffee.com/jyourstone"><img alt="Buy Me A Coffee" src="https://img.shields.io/badge/Buy_Me_A_Coffee-FFDD00?logo=buy-me-a-coffee&logoColor=black"/></a>
    </p>
</div>

**Plug and play** with SigenStor battery inverters and Easee EV chargers — **bring your own hardware** for everything else.

Official documentation for the Energy Manager Home Assistant custom integration: price-optimized home energy management built around [Nordpool](https://www.home-assistant.io/integrations/nordpool/) electricity prices.

## What it does

Electricity prices swing a lot from hour to hour. Energy Manager reads the day-ahead Nordpool prices and plans around them: it charges your home battery and your car during the cheapest hours, runs the house on battery power during the expensive ones, and puts solar surplus to work. Then it executes those plans — natively on SigenStor inverters and Easee chargers, or through your own automations on any other hardware.

Three independent modules — enable any combination:

- **[Home Battery](user-guide/home-battery.md)** — charges the battery in cheap hours and discharges it into expensive ones, planning several charge/discharge cycles ahead instead of just reacting to the current price. Optionally sells battery energy to the grid during extreme price spikes ([export arbitrage](user-guide/battery-export-arbitrage.md)).
- **[EV Charging](user-guide/ev-charging.md)** — a schedule per car: the cheapest hours that reach its target charge level before its departure time, plus charging on surplus solar.
- **[Solar Appliances](user-guide/solar-appliances.md)** — turns switch loads (water heater, pool pump) on while measured solar surplus covers them, and off when it disappears.

## Two ways to use it

Energy Manager always does the planning. How the plans reach your hardware depends on what you own:

**1. Plug and play — SigenStor + Easee.** With a SigenStor battery inverter and/or an Easee EV charger (their Home Assistant integrations installed), Energy Manager controls the hardware directly: it sets the inverter's EMS (Energy Management System) mode and charge/discharge limits, and sends the charger current and phase-mode commands. Nothing for you to build.

**2. Bring your own hardware — any inverter, any charger.** Energy Manager plans, your automations execute: all inputs (state of charge, power, and price entities) are free entity pickers, and every decision is published on diagnostic **command sensors** (battery EMS mode, charge/discharge limits, charger current, phase mode). You write one small automation per sensor that forwards the value to your hardware — the full sensor contract and worked YAML examples are in [Bring your own hardware](bring-your-own-hardware/command-sensors.md).

| | **Plug and play**<br>SigenStor + Easee | **Bring your own hardware**<br>any inverter, any charger |
|---|---|---|
| Price-optimized battery & EV schedules | Yes | Yes |
| Battery control (EMS mode, charge/discharge limits) | Automatic | Your automations, following the [command sensors](bring-your-own-hardware/command-sensors.md) |
| EV charger control (current, phase mode) | Automatic | Your automations, following the command sensors |
| Fuse protection — power capped to your main fuse's headroom, per phase | Yes | Yes, applied to the published commands |
| Solar Appliances (any `switch`/`input_boolean`) | Yes | Yes |
| Automations you write | None | One small one per command you follow |

Optional on either path: a car integration for automatic battery-level and location detection (any car integration works -- point Energy Manager at the car's device and its entities are suggested for you), and a notify service for charger safety alerts.

## Features

- **Nordpool, native or HACS** — both the official Nordpool integration and the HACS custom component are supported and auto-detected. All price entities follow your Nordpool sensor's currency (SEK, NOK, DKK, EUR, ...) — no conversion, everything stays in your area's currency.
- **Solar-aware** — [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) forecasts shape the battery schedule, and surplus solar production can charge the battery or car outside the price plan — without rapid on/off cycling.
- **Observe-only by default** — the master **Device control** switch ships OFF: Energy Manager computes and publishes every decision but sends no hardware command until you turn it on, so you can watch what it *would* do first. See [Your First Days](getting-started/first-days.md).
- **Guided setup** — the [config wizard](getting-started/setup-wizard.md) auto-detects Nordpool, SigenStor and Easee and pre-fills the forms; cars are added afterwards, where picking the car's device suggests its entities. Every setting can be changed later via **Configure** — no re-adding needed.
- **Translations** — English and Swedish, both complete.

## Project links

- **Source code**: [github.com/jyourstone/energy_manager](https://github.com/jyourstone/energy_manager)
- **HACS store**: [Add to Home Assistant via HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=jyourstone&repository=energy_manager&category=integration)
- **Releases**: [GitHub Releases](https://github.com/jyourstone/energy_manager/releases)
- **License**: [MIT](https://github.com/jyourstone/energy_manager/blob/main/LICENSE)
- **Support the project**: [Buy Me a Coffee](https://buymeacoffee.com/jyourstone)

## Status

Energy Manager is pre-release: under active development, with no stable 1.0 yet. Everything described in this documentation is implemented, but expect rough edges — and expect entities and configuration to change between releases before 1.0.

!!! warning "Disclaimer"
    The vast majority of this project was developed by an AI assistant. While the author does have some basic experience with programming from a long time ago, they are essentially the architect, guiding the AI, fixing its occasional goofs, and trying to keep it from becoming self-aware.

    This is pre-release software: expect breaking changes and missing features, and use it at your own risk — especially anything that actuates a battery inverter or EV charger.

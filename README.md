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
- Full options flow — every setting can be changed later via **Configure**, no re-adding needed

Expect rough edges, and expect entities/config to change before 1.0.

## What it does

Energy Manager replaces a pile of manual Home Assistant helpers, template sensors, and automations with a single integration that:

- Reads Nordpool spot prices (both the official native integration and the HACS custom component)
- Computes a multi-cycle home battery charge/discharge schedule using peak grouping and virtual energy tracking, so the battery charges before the most profitable price peaks rather than just reacting to the current price
- Drives a SigenStor inverter's EMS mode (`Command Charging (PV First)` / `Maximum Self Consumption`) to actually execute that schedule, with dynamic per-phase fuse headroom limiting and command read-back verification
- Gates battery self-consumption via the SigenStor discharge-limit number: outside scheduled discharge slots the battery may still cover house load when the price spread beats the effective discharge threshold — but not when that energy is already reserved for an upcoming scheduled peak (unless a recharge is planned first)
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
- **Solar-surplus appliance control** — switch any `switch`/`input_boolean` load (water heater, pool pump) on when measured grid export exceeds its rated draw with margin, and off when the surplus disappears — with priority allocation, hysteresis, anti-short-cycling floors, and fuse admission
- **Modular** — Home Battery, EV Charging, and Solar Appliances can each be enabled independently; a module works standalone without the others being configured
- **Translations** — UI strings use Home Assistant's translation system; English and Swedish are both complete

## Requirements

- Home Assistant 2025.3.0 or newer (config subentry support)
- A Nordpool sensor (native or HACS integration), configured and providing prices
- Optional: a SigenStor battery inverter for home battery scheduling + EMS control
- Optional: the [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) integration (a separate integration that must be installed and configured on its own) for solar-aware battery scheduling. Solar-aware scheduling additionally relies on Home Assistant's built-in [Sun](https://www.home-assistant.io/integrations/sun/) integration (`sun.sun`) — part of `default_config` and enabled on standard installs. If it has been removed, solar forecast data is ignored: the schedule still works, just without solar awareness
- Optional: an Easee charger and/or a supported car integration (Skoda Connect, VW We Connect) for EV charging

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

Click the button above, or add it manually via **Settings** -> **Devices & Services** -> **Add Integration** -> search for **Energy Manager**.

### Setup wizard

**Step 1 — Price source:** Select your Nordpool sensor. Auto-detected if either the native or HACS Nordpool integration is installed.

**Step 2 — Modules:** Choose which modules to enable — Home Battery, EV Charging, and/or Solar Appliances. All are optional and independent; you can enable just one.

**Step 3 — Home Battery** *(if enabled)*: Battery SOC and power entities are auto-detected from a SigenStor integration if present. Also configures battery capacity (kWh) and, optionally, one or more Forecast.Solar "remaining today" entities for solar-aware scheduling (e.g. separate east + west arrays — their readings are summed), plus the BATT-15 algorithm tuning options (charge buffer %, solar production factor, estimated charge power, and peak-grouping gap). Matching Forecast.Solar "tomorrow" sensors are derived automatically from the configured "remaining today" sensors, so 48h planning also accounts for tomorrow's sun. Continues into EMS setup: fuse rating, EMS mode select entity, charge/discharge limit entities, and grid power/phase entities (all auto-detected from SigenStor where possible), plus an optional PV power entity for opportunistic charging.

**Step 4 — EV Charging** *(if enabled)*: Charger status and power entities, auto-detected from an Easee integration if present, plus the charger's device ID (auto-detected) used to address the Easee control services. Also configures charger amp limits, grid charging power cap, phase-switch and solar-charging thresholds, and an optional notify service for charger safety alerts — all pre-filled with tuned defaults and grouped with the advanced options at the end of the step.

After setup, each car is added separately as a **subentry** on the Energy Manager device: give it a name, battery capacity, optionally battery-level, charger-connected, and location entities (auto-detected from Skoda Connect or VW We Connect if present), and how many charger phases it actually uses (1/2/3, default 3).

With Solar Appliances enabled, each appliance is likewise added as a **subentry**: a name, the actuator entity (`switch` or `input_boolean`), its rated power (W) and phases, an optional power sensor for measured credit-back, plus priority and the on/off threshold and timing fields (all pre-filled with tuned defaults). See [Appliances (solar surplus)](#appliances-solar-surplus) below.

## Entities created

### Sensors

| Sensor | Description |
|--------|--------------|
| Electricity Price | Current Nordpool price |
| Battery Schedule | Full multi-cycle charge/discharge schedule with status and attributes |
| Battery next charging slot | Timestamp of the battery's next scheduled charge slot |
| Battery next discharging slot | Timestamp of the battery's next scheduled discharge slot |
| Battery EMS status | Current SigenStor EMS mode and fuse headroom |
| Actual Electricity Price | Spot price + grid transfer fee + electricity company fee (diagnostic; no long-term statistics) |
| Car Schedule *(per car)* | Cheapest-slot charging schedule for that car |
| EV charger status | Easee charger decision mode (forced/scheduled/solar/idle), target amps/phase mode, fuse headroom, and more |
| House Load *(diagnostic)* | Filtered house consumption (house consumption minus excluded power entities), with the BATT-15 rolling mean consumption as an attribute (rolling window persists across restarts) |
| Forecast Accuracy *(diagnostic)* | Observe-only solar forecast accuracy tracking: daily forecast-vs-actual ratios and a suggested production factor (needs 7+ valid days; does not affect scheduling) |
| Battery effective discharge threshold *(diagnostic)* | The discharge spread threshold the scheduler is actually using right now, with attributes showing whether it comes from the manual entity or the Battery Cycle Cost formula |
| Solar Balance *(diagnostic)* | Signed net solar balance (PV minus house load minus battery charging plus charger draw): positive means surplus available for the charger, negative means deficit. Raw value before the charger's own activation gating |
| Status *(per appliance)* | Surplus-control decision status (`off_no_surplus`, `on_surplus`, `blocked_fuse`, ...) with attributes (thresholds, surplus components, allocation, last command message) that explain every decision |

### Switches

| Entity | Description |
|--------|--------------|
| Device control | Master observe-only switch (CORE-14); OFF means every coordinator still computes and publishes decisions, but no device command is actually sent |
| EV charger force charging | Forces the Easee charger to grid-charge regardless of schedule or solar state (EASE-03) |
| EM control *(per appliance)* | Hand-over valve: Energy Manager only manages this appliance's actuator while this is ON (on top of the master Device control switch). Default OFF |

### Numbers

| Entity | Description |
|--------|--------------|
| Battery charge spread threshold | Spread (SEK/kWh): a slot is a charge candidate for a peak when that peak's max price minus the slot's price exceeds this value |
| Battery discharge spread threshold | Spread (SEK/kWh): a slot discharges when its price minus the period's minimum price exceeds this value. Overridden by the Battery Cycle Cost formula below when that is set above 0 -- the entity shows as unavailable while overridden |
| Battery max charging power | Maximum battery charge power (kW) |
| Battery Cycle Cost | Cost of one battery charge/discharge cycle (SEK/kWh). When above 0, the effective discharge threshold becomes `max(0, battery_cycle_cost - grid_transfer_fee)` — clamped to 0 when the fee exceeds the cycle cost, in which case any spread above the horizon minimum qualifies — overriding the manual Battery discharge spread threshold above (which shows as unavailable while overridden) (parity with the live system's economics formula). Default 0 (disabled) |
| Grid Transfer Fee | Grid transfer fee (SEK/kWh); feeds the Battery Cycle Cost formula and the Actual Electricity Price sensor |
| Electricity Company Fee | Electricity company fee (SEK/kWh); used only by the Actual Electricity Price sensor |
| Grid charging target *(per car)* | Target state of charge for scheduled price-based charging |
| Solar charging target *(per car)* | SOC ceiling for solar-surplus charging (default 100%) |
| Car Max Charge Power *(per car)* | Maximum charge power for that car (kW) |

### Time

| Entity | Description |
|--------|--------------|
| Car Departure Time *(per car)* | Deadline used to compute that car's charging schedule |

All number entities persist their value across Home Assistant restarts.

> **All price thresholds are spreads, not absolute prices.** A slot qualifies by its price *relative to the cheapest slot in the horizon* (or the peak's max, for charging) — never by crossing a fixed price. This keeps the thresholds meaningful when the overall price level shifts between weeks.

## Battery → grid export arbitrage (BATT-17)

Opt-in feature that sells battery energy to the grid during extreme price spikes. **Off by default**: with the *Battery export spread threshold* unset or 0, no export slot is ever scheduled and battery schedules are unchanged.

- **Battery export spread threshold** — a number entity (like the charge/discharge thresholds): the price spread above the period's cheapest hour at or above which a slot may become an export slot. Adapts automatically when the overall price level shifts. Set to 0 to disable (default). The **Battery export reserve level** number entity (default 20%) is the SOC floor below which the battery never sells.
- **Export reserve SOC (%)** — never export below this battery level (default 20%). Enforced twice: the scheduler's export energy budget only plans with energy above the floor, and at runtime the floor is re-checked every 30 s — export drops back to self-consumption at or below the floor, or whenever the battery SOC sensor is unavailable.
- **Fuse-capped export power** — the discharge limit commanded during an export slot is capped at `(fuse rating − safety buffer) × 3 × 230 V`, without adding house load (house load can sit on a single phase, so the cap is derived per-phase-safe). The plant's own discharge limit is never commanded during export: a 20 A main fuse with the default 1 A safety buffer gives a ~13.1 kW ceiling.
- **Qualification** — a slot only exports when its spread above the period's cheapest hour is at or above the threshold AND selling now beats buying the same energy back later: `spot > (cheapest future spot + grid transfer fee + electricity company fee) / 0.9 + battery cycle cost` (0.9 = assumed round-trip efficiency). Export never demotes a scheduled self-consumption discharge slot worth more per kWh.
- **Observe-only first** — like all device actuation, export commands sit behind the master *Device control* switch. Leave it off to watch the scheduled `export` slots and the `exporting` state across a few spike events before enabling.

> [!IMPORTANT]
> **Setup precondition:** the SigenStor inverter's own backup/minimum SOC must be configured at the plant level to at least Energy Manager's minimum SOC. This hardware floor is the last line of defense if Home Assistant goes down mid-export — it is documented here as a requirement and is **not** runtime-verified in v1.

## Appliances (solar surplus)

Off by default (the **Solar Appliances** module flag in the Modules step). Turns user-selected switch loads on when measured solar surplus (grid export) exceeds the load's rated draw with margin, and off when the surplus disappears. Surplus-only on purpose: there is no cheap-hours appliance scheduling in Energy Manager — price-based schedulers like [Power Saver](https://github.com/jyourstone/power_saver) keep that job, and the two coexist (see the recipe below).

- **Surplus signal** — measured grid export minus battery discharge power (arbitrage export is not solar surplus, BATT-17), never computed PV-minus-loads. Battery and car charging need no arbitration: export only appears at the meter once everything upstream is satisfied.
- **Priority allocation** — appliances are evaluated in priority order (1 = highest); each admitted appliance consumes its measured (if a power sensor is configured) or rated draw from the surplus pool.
- **Anti-short-cycling** — per-appliance on/off sustain delays plus hard minimum on/off times, so a water heater or heat-pump plug is never rapid-cycled.
- **Fuse admission** — an appliance only turns on if its rated amps fit the live measured fuse headroom minus the safety buffer.
- **Observe-only by default** — commands are only sent when BOTH the master **Device control** switch AND that appliance's **EM control** switch are ON. Until then the status sensor and dry-run messages show exactly what would happen.

### Per-appliance configuration

Each appliance subentry has these fields — every threshold and timing is user-configurable per appliance, pre-filled with these defaults:

| Field | Default | Description |
|-------|---------|--------------|
| Appliance name | — | Friendly name, e.g. "Water heater" |
| Actuator switch | — | The `switch` or `input_boolean` Energy Manager toggles (smart plug, relay, or another integration's override switch) |
| Rated power (W) | — | Expected draw when running, e.g. 4200 |
| Phases | 3 | 1 or 3; converts rated power to per-phase amps for the fuse admission check |
| Power sensor | *(optional)* | Sensor measuring actual draw; if set, the measured draw is credited back to the surplus pool instead of the rated power |
| Priority | 5 | Allocation priority when appliances compete for the surplus (1 = highest); ties broken by the order appliances were added |
| On threshold (%) | 110 | Turn on when the surplus pool reaches this percentage of rated power, sustained |
| Off threshold (%) | 90 | Turn off when the pool stays below this percentage of rated power, sustained |
| On sustain time (min) | 5 | How long the surplus must persist before turning on (filters passing clouds) |
| Off sustain time (min) | 15 | How long the deficit must persist before turning off (deliberately slower than the on side) |
| Minimum on time (min) | 15 | Hard floor once on, regardless of surplus |
| Minimum off time (min) | 5 | Hard floor once off (heat-pump plugs: raise this) |

Each appliance gets its own device with exactly two entities: the **EM control** switch (the per-appliance hand-over valve, default OFF, restored across restarts) and the **Status** sensor whose state and attributes explain every decision — see the entity tables above.

### Managing a load another integration schedules

Never point Energy Manager at a relay that another integration already reconciles — one writer per switch (single-writer rule). Instead, point the appliance's actuator picker at that integration's own override/boost switch. With [Power Saver](https://github.com/jyourstone/power_saver) as the example: PS keeps cheap-hours scheduling for guaranteed runtime, and you set the appliance's actuator to PS's `always_on` override switch (e.g. `switch.heater_power_saver_always_on`) instead of the physical relay. Energy Manager forces the load on while solar surplus lasts, Power Saver keeps its year-round guarantee, and no two integrations ever fight over the same relay.

## Repairs

Persistent degraded conditions surface in **Settings → Repairs** instead of only the log: fuse-protection sensors continuously falling back to the assumed load (5+ minutes), and misconfigured charge/discharge limit entities (wrong domain). Issues clear automatically when the condition recovers.

## Diagnostics

Settings > Devices & Services > Energy Manager > Download diagnostics gives a full snapshot of the config entry (data/options), every active coordinator's current state, and the runtime control flags (device control, force charging, forwarded platforms) — useful when reporting a bug.

## Disclaimer

The vast majority of this project was developed by an AI assistant. While I do have some basic experience with programming from a long time ago, I'm essentially the architect, guiding the AI, fixing its occasional goofs, and trying to keep it from becoming self-aware.

This is pre-release software: expect breaking changes, missing features (see Status above), and use it at your own risk — especially anything that actuates a battery inverter or EV charger.

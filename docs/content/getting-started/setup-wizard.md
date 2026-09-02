# Setup Wizard

Energy Manager is configured through a guided config flow that auto-detects your Nordpool, SigenStor and Easee integrations and pre-fills the forms wherever it can. Cars are added separately afterwards, where picking the car's device suggests its entities.

## Starting the Wizard

[![Add integration to your Home Assistant instance.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=energy_manager)

Click the button above, or add it manually via **Settings** → **Devices & Services** → **Add Integration** → search for **Energy Manager**.

!!! tip "Everything stays editable"
    Nothing in the wizard is a one-shot decision. Wiring and safety settings — price source, modules, entities, fuse limits — can be changed later via **Configure** (which shows the same steps your enabled modules use) on the Energy Manager integration card, and the tuning values live on as number entities on the Energy Manager device page. There is no need to remove and re-add the integration to fix or tune something.

## Step 1 — Price Source

Select your Nordpool sensor. This is auto-detected if either the native Home Assistant Nordpool integration or the HACS Nordpool integration is installed and providing prices.

## Step 2 — Modules

Choose which modules to enable:

- **Home Battery**
- **EV Charging**
- **Solar Appliances**

All three are optional and independent — enable any combination, including just one. The rest of the wizard only shows steps for the modules you turned on here, plus a shared Grid & Fuse Protection step, which every module needs.

## Step 3 — Home Battery *(if enabled)*

This step configures the battery itself and solar-aware scheduling.

**Battery and solar forecast:**

| Field | Notes |
|-------|-------|
| Battery SOC sensor | Auto-detected from a SigenStor integration if present |
| Battery capacity (kWh) | Total usable capacity |
| Solar forecast sensors | Optional one-or-more Forecast.Solar "remaining today" entities — pick multiple (e.g. separate east + west arrays) and their readings are summed. Matching "tomorrow" sensors are derived automatically, so 48h planning also accounts for tomorrow's sun |
| Charge buffer (%) | Seeds the **Charge buffer** number entity: extra margin added on top of the calculated charge deficit |
| Solar production factor | Seeds the **Solar production factor** number entity: multiplier applied to the forecast to correct for forecast optimism |
| Estimated charge power (kW) | Seeds the **Estimated charge power** number entity: assumed charging rate used to size how many slots a charge deficit needs |
| Peak grouping gap (h) | Seeds the **Peak grouping gap** number entity: max gap between expensive hours still treated as the same discharge peak |

These four values just seed their corresponding number entities on first setup — every one stays adjustable afterward from the Energy Manager device page, without going back through the wizard.

See the [Home Battery](../user-guide/home-battery.md) page for what each field means and how it drives scheduling.

## Step 4 — Grid & Fuse Protection *(all modules)*

Appears for every module combination — appliances-only installs see this step too, since appliance admission needs the same fuse headroom and grid export signal as the battery and charger. The fuse rating defaults to 20 A and is never auto-detected — confirm it matches the fuse in your meter cabinet before continuing.

**Shared (always shown):**

| Field | Notes |
|-------|-------|
| Fuse rating (A) | Your main fuse rating — the basis for every safe charging/discharging/admission limit |
| Fuse safety buffer (A) | Extra margin subtracted from the fuse rating before any available headroom is calculated |
| Grid power / phase entities | Signed power sensors (positive = import, negative = export) used for fuse protection — auto-detected from SigenStor where possible |
| Battery power sensor | Signed power sensor (positive = charging, negative = discharging) — auto-detected from SigenStor. Configure it if you have a house battery, even when the Home Battery module is off; used for fuse headroom and to keep appliance surplus from counting battery discharge as solar |
| If current sensors are unavailable | What to do when the grid current sensors are unavailable, unknown, or not configured: assume a fixed load, or block new load until they recover |
| Assumed load (A) | Fallback load assumed when sensors are unavailable and the above is set to assume a fixed load |

**Shown with Home Battery or EV Charging:**

| Field | Notes |
|-------|-------|
| PV power sensor | Optional, enables opportunistic solar charging |
| House consumption sensor | Sensor showing total house power consumption, used for solar-surplus charging — auto-detected from SigenStor |
| Excluded power sensors | Optional: power sensors to subtract from house consumption (e.g. a separately-managed water heater) before computing solar surplus |

**Home Battery only:**

| Field | Notes |
|-------|-------|
| EMS mode select entity | Auto-detected from SigenStor; leave empty for non-SigenStor hardware (see [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md)) |
| Max charging / discharging limit entities | Auto-detected from SigenStor |
| Max battery charging current (A) | Hard safety cap on the battery's own charging current, independent of fuse headroom |
| Charge limit increase delay (s) | How long a higher charge limit must stay stable before it is applied; decreases always apply immediately |

## Step 5 — EV Charging *(if enabled)*

Fuse rating, grid power sensors, house consumption and excluded power sensors are configured on the shared **Grid & Fuse Protection** step above (Step 4).

| Field | Notes |
|-------|-------|
| Charger status / power sensors | Auto-detected from an Easee integration if present |
| Charger device ID | Auto-detected — used to address the `easee.*` control services. Leave empty for a non-Easee charger: commands are then never sent |
| Amp limits | Minimum and maximum charging current |
| Maximum grid charging power (kW) | Seeds the **Max grid charge power** number entity: absolute grid charging power ceiling |
| Phase-switch threshold (kW) | Available power below which the charger drops to single-phase |
| Solar charging start threshold (kW) | Seeds the **Solar start threshold** number entity; activation/deactivation delays stay wizard-owned |
| Battery SOC gate (advanced options) | Seeds the **Battery SOC gate** number entity: minimum house-battery SOC before solar EV charging starts (only shown when the Home Battery module is enabled — the gate compares against the house battery's SOC) |
| Notification service | Optional — for charger safety alerts (e.g. a fuse overload that load balancing could not clear) |

All advanced options are pre-filled with tuned defaults and grouped at the end of the step. See the [EV Charging](../user-guide/ev-charging.md) page for what each field means. The three seeded fields above stay adjustable afterward from the Energy Manager device page, without going back through the wizard.

## Step 6 — Economics *(if Home Battery enabled)*

| Field | Notes |
|-------|-------|
| Battery Cycle Cost | Wear cost per charged kWh |
| Grid Transfer Fee | Your grid operator's transfer fee |
| Electricity Company Fee | Your electricity supplier's markup |
| Max battery charging power | Power used when grid-charging the battery |

These values just seed the corresponding tunable number entities on first setup — every one of them stays adjustable afterward from the Energy Manager device page, without going back through the wizard.

## Step 7 — Done

A setup-complete summary with next steps: add your car(s) with the **Add car** button on the integration page (if EV Charging is enabled) to get scheduled charging, add each appliance with the **Add appliance** button (if Solar Appliances is enabled), and turn on the **Device control** switch when you're ready to leave observe-only mode.

## After Setup: Cars and Appliances

**Cars.** With EV Charging enabled, each car is added separately as a **subentry** on the Energy Manager device — not part of the main wizard. Adding a car starts with an optional step where you pick the car's **device** — whichever integration created it. Energy Manager then suggests that device's battery-level, charger-connected and location entities on the next step, where you also give a name, battery capacity, and how many charger phases the car actually uses (1/2/3, default 3). Skip the device step to fill everything in by hand.

**Appliances.** With Solar Appliances enabled, each appliance is likewise added as its own **subentry**: a name, the actuator entity, its rated power and phases, an optional power sensor for measured credit-back, plus priority and the on/off threshold and timing fields — all pre-filled with tuned defaults. Priority and the on/off threshold and sustain-time fields only seed that appliance's number entities; adjust them afterward per appliance without a reload. See [Solar Appliances](../user-guide/solar-appliances.md) for the full field table.

## Next Steps

Continue to [Your First Days (Observe-Only Mode)](first-days.md) to see how Energy Manager behaves before you turn on device control.

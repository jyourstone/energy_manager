# Feature Landscape

**Domain:** Home Assistant energy management integration (battery scheduling, EV charging optimization, device coordination)
**Researched:** 2026-02-15
**Overall confidence:** MEDIUM-HIGH (existing codebase HIGH, competitor analysis MEDIUM due to limited WebSearch access)

## Competitive Landscape

The HA energy management ecosystem has several notable players, each occupying a different niche:

| Integration | Focus | Star Count | Notes |
|-------------|-------|------------|-------|
| **Predbat/Batpred** | Home battery prediction + auto-charging | ~240+ | UK-focused (Octopus Energy, GivEnergy). Most mature battery scheduler. |
| **ev_smart_charging** | EV charging price optimization | ~278+ | Multi-price-source. EV-only, no battery. Reference integration for this project. |
| **FoxESS EM** | FoxESS inverter energy management | Moderate | Vendor-locked to FoxESS. Solcast required. |
| **Nordpool/Entso-e** | Price data sourcing | High (utilities) | Data providers, not schedulers. Foundation layer. |
| **This project** | Unified battery + EV + coordination | N/A | Differentiator: multi-device coordination with fuse protection. |

**Key gap in the market:** No existing HACS integration combines home battery scheduling AND EV charging optimization AND fuse-aware device coordination in a single modular package. Predbat does battery well but is UK/GivEnergy-centric. ev_smart_charging does EV well but has no battery awareness. Nobody does the coordination layer.

---

## Table Stakes

Features users expect. Missing = product feels incomplete or users pick a competitor instead.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Price-based charge scheduling** | Core value proposition. Predbat, ev_smart_charging, and every competitor does this. | Med | Proven algorithm exists in HomeBatteryManager. Port directly. |
| **Cheapest-slot EV charging** | ev_smart_charging sets the bar. Users compare against it. | Med | Proven in CarChargingManager. Price-sorted slot selection with departure constraint. |
| **Departure time for EV** | Every EV charging optimizer has this. Users set when they need the car ready. | Low | Already implemented. Config flow + options flow exposure. |
| **Target SOC for EV** | Users want "charge to 80%" not "charge for N hours". Percentage-based target is standard. | Low | Already implemented. Per-car configurable. |
| **Schedule visualization via sensor attributes** | Users need to see what the integration decided. Predbat and ev_smart_charging expose this. | Low | Already implemented. Schedule list in sensor attributes for ApexCharts/Lovelace cards. |
| **Current state sensor** | "Is it charging? Discharging? Idle?" Must be visible at a glance. | Low | Already implemented. State string + attributes. |
| **Next action indicator** | "When will the next charge/discharge start?" Standard for scheduling integrations. | Low | Already implemented (next_charging_slot, next_discharging_slot attributes). |
| **Config flow (UI setup)** | HACS integrations without config flows feel amateur. Users expect point-and-click setup. | Med | Must build. Auto-discovery of SigenStor, Easee, Skoda/VW entities. |
| **Options flow (runtime tuning)** | Price thresholds, SOC limits, and other tunables must be changeable without YAML editing. | Med | Must build. Replaces the 24 manual helpers. |
| **Nordpool price support** | The dominant electricity pricing source in Scandinavia. Both HACS and native HA variants. | Low | Proven adapter pattern from PowerSaver integration. |
| **Solar production awareness** | Users with solar expect the integration to account for free energy. Predbat and FoxESS EM do this. | Med | Already implemented. Dawn/dusk gating, production factor, surplus/deficit calculation. |
| **Battery SOC tracking** | Must know current battery level to make charging decisions. Basic requirement. | Low | Already reads from SigenStor sensors. |
| **Graceful degradation** | If a sensor is unavailable, don't crash. Log a warning and use safe defaults. | Med | Partially implemented. Needs systematic improvement for native integration. |
| **HACS compliance** | hacs.json, manifest.json, proper repo structure, version tagging. | Low | Known pattern from PowerSaver. |

---

## Differentiators

Features that set this project apart from competitors. Not expected by users switching from nothing, but provide clear competitive advantage.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Unified battery + EV in one integration** | No competitor does both. Users currently need Predbat + ev_smart_charging + manual automations. This eliminates that fragmentation. | High | Core differentiator. Modular design lets users enable only what they need. |
| **Fuse protection / current limiting** | Safety feature that no competitor offers. Prevents blown fuses when battery AND car charge simultaneously. Dynamically adjusts limits based on household load. | High | Proven in EMSController + EaseeController. Critical safety feature that builds trust. |
| **Multi-device coordination** | Battery yields to car when car is scheduled. Car respects fuse capacity. Solar goes to battery first, surplus to car. No competitor coordinates across device types. | High | The killer feature. Proven logic in EMS/Easee coordination. |
| **Multi-cycle charge/discharge scheduling** | Peak grouping algorithm identifies multiple profitable windows (e.g., morning peak + evening peak) and schedules independent charge-discharge cycles for each. More sophisticated than Predbat's single-window approach. | Med | Proven in HomeBatteryManager._group_into_peaks(). Unique algorithm. |
| **Virtual energy tracking** | Simulates battery state through the entire schedule to make optimal multi-cycle decisions. Prevents over-committing energy to one peak at the expense of another. | Med | Proven. Part of the multi-cycle scheduler. |
| **Zero manual helpers** | Competitors like ev_smart_charging require users to create template sensors or input_number helpers. This integration computes everything internally. Setup is: install, configure via UI, done. | Med | Requires internalizing all 24 current manual helpers. Major UX improvement. |
| **Auto-discovery of compatible devices** | Detects SigenStor, Easee, Skoda/VW integrations automatically and pre-populates config. No "paste entity_id" tedium. | Med | Must build. Scan HA registry for matching domains/manufacturers. |
| **Fallback/guest car charging** | When an unknown car is connected (not a tracked vehicle), automatically charge it during off-peak hours. No other integration handles unrecognized vehicles. | Low | Already implemented in CarChargingManager._check_fallback_mode(). |
| **Solar-surplus EV charging** | Routes excess solar production to EV charger with hysteresis to prevent rapid cycling. Battery gets solar first, then EV gets surplus. | Med | Proven in EaseeController. Phase switching (1-phase/3-phase) for efficiency. |
| **Dynamic phase switching** | Automatically switches Easee charger between 1-phase and 3-phase based on available power. Maximizes solar utilization at low production levels. | Med | Proven in EaseeController. Threshold-based with hysteresis. |
| **Per-car configuration** | Multiple EVs with independent departure times, SOC targets, battery capacities. Each gets its own schedule. | Low | Already works (2x CarChargingManager instances). Config flow needs per-car sections. |
| **Car charging priority over battery** | When a car needs to charge AND battery wants grid charging, the car wins (it has a departure deadline). Battery goes to standby to free fuse capacity. | Med | Proven in EMSController.battery_charge_manager_check(). |

---

## Anti-Features

Features to explicitly NOT build. Either out of scope, harmful to UX, or better served by existing HA capabilities.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Energy dashboard / charts** | HA Energy Dashboard already does this excellently. Duplicating it adds maintenance burden and confuses users about which dashboard to use. | Expose sensors with proper device_class and state_class so they integrate natively with HA Energy Dashboard. |
| **Mobile notifications** | HA automations handle notifications better (user chooses service, format, conditions). Baking in notification logic couples the integration to specific notification services. | Expose sensor states and fire HA events. Users build their own notification automations. |
| **Grid tariff / rate management** | Complex problem space with regional variation. Nordpool/Entso-e integrations already handle this well. Building a tariff engine is a separate product. | Consume price data from existing integrations via adapter pattern. |
| **Weather forecasting** | Forecast.Solar and other weather integrations already exist. Building our own weather service is out of scope and redundant. | Accept Forecast.Solar entities as optional input. Fall back to actual production data. |
| **Inverter/charger firmware updates** | Device vendor responsibility. Risky and out of scope. | Do not touch firmware. Only use documented HA service calls for control. |
| **Support for every battery/charger brand** | v1 scope is SigenStor + Easee. Trying to support everything dilutes quality and explodes testing surface. | Design abstractions that allow future brand additions, but only ship what is tested. |
| **Historical cost savings calculator** | Nice-to-have but complex to implement correctly (requires baseline comparison). Not a scheduling feature. | Maybe v2. For now, users can use HA statistics and utility meters. |
| **Real-time power flow visualization** | HA has this via power flow cards. Building a custom one adds significant frontend work for little benefit. | Ensure sensor entities have correct attributes for existing power flow cards. |
| **Complex pricing rules (tiered rates, demand charges)** | Nordpool markets are simpler (hourly spot price). Supporting tiered/demand pricing is a different product for a different market. | Stick to hourly/sub-hourly spot prices. Design adapter to not preclude future expansion. |
| **Manual start/stop of individual charge cycles** | Adds UI complexity and state management edge cases. The whole point is automation. | Expose an override switch (pause/resume) but not granular cycle control. A single "force charge now" override suffices. |

---

## Feature Dependencies

```
Nordpool Price Adapter ---------> Battery Charge Scheduler
                        \-------> EV Charging Scheduler

Battery Charge Scheduler -------> EMS Mode Controller (sets battery mode)
EV Charging Scheduler ----------> Charger Controller (sets charger limits)

EMS Mode Controller ---\
                        +-------> Fuse Protection (coordinates limits)
Charger Controller ----/

Solar Production Input ---------> Battery Charge Scheduler (surplus calc)
                        \-------> Charger Controller (solar surplus charging)
                         \------> EMS Mode Controller (opportunistic PV charging)

Config Flow --------------------> All modules (entity discovery, initial setup)
Options Flow -------------------> All modules (runtime tuning of thresholds)

Core/Hub -----------------------> Module registry, shared price data, fuse state
Home Battery Module ------------> Battery Charge Scheduler + EMS Mode Controller
EV Charging Module -------------> EV Charging Scheduler + Charger Controller
```

**Critical path for MVP:**
1. Core/Hub with Nordpool adapter (everything depends on price data)
2. Home Battery Module OR EV Charging Module (either can ship first)
3. Fuse Protection (needed when both modules are active)
4. Config/Options flows (needed for any user-facing release)

**Independent tracks:**
- Home Battery Module and EV Charging Module can be developed in parallel
- Solar awareness is an enhancement to both, not a blocker

---

## MVP Recommendation

### Phase 1: Core + One Module

Prioritize for first usable release:

1. **Core/Hub with Nordpool price adapter** -- Foundation. Everything needs prices.
2. **Config flow with auto-discovery** -- Must have for HACS. No YAML-only setup.
3. **Home Battery Module** (charge/discharge scheduler + EMS controller) -- More complex, more differentiated, and the author's primary use case. Ship this first to validate the architecture.
4. **Options flow for threshold tuning** -- Replaces the manual input_number helpers. Essential UX.
5. **Schedule sensors with attributes** -- Visualization is table stakes.
6. **Solar production awareness** -- Expected when battery + solar are common combos.

### Phase 2: EV Module + Coordination

7. **EV Charging Module** (scheduler + charger controller) -- Second module. Validates modularity.
8. **Per-car configuration** -- Multiple cars, each with own settings.
9. **Fuse protection / coordination layer** -- The differentiator. Only matters when both modules active.
10. **Fallback/guest car charging** -- Nice differentiator, low complexity.

### Defer to Phase 3+

- **Dynamic phase switching** -- Enhancement to solar charging. Complex Easee-specific logic.
- **Additional price sources** -- Nordpool-only is fine for Scandinavia v1.
- **Additional device brands** -- Design for extensibility, ship for SigenStor + Easee.
- **Advanced solar-surplus EV charging** with hysteresis -- Enhancement. Basic solar awareness in Phase 1, full surplus routing in Phase 2+.

### Rationale

Ship the battery module first because:
- It is the more complex and unique offering (multi-cycle scheduling, virtual energy tracking)
- It validates the core architecture (price adapter, coordinator pattern, config flow)
- It provides immediate value to the author's own use case (dogfooding)
- The EV module can then validate that the architecture truly supports modularity

---

## Sources

- **Existing codebase analysis:** `/Volumes/addon_configs/a0d7b954_appdaemon/apps/` -- HIGH confidence (production code)
- **ev_smart_charging:** GitHub repo + wiki (WebFetch) -- MEDIUM confidence (fetched feature list and supported chargers/price sources)
- **Predbat/Batpred:** GitHub repo overview (WebFetch) -- MEDIUM confidence (feature summary, supported hardware, UK-centric focus)
- **FoxESS EM:** GitHub repo overview (WebFetch) -- MEDIUM confidence (feature summary, vendor-locked to FoxESS)
- **PROJECT.md:** `/Volumes/addon_configs/a0d7b954_appdaemon/.planning/PROJECT.md` -- HIGH confidence (project scope and constraints)
- **Codebase concerns:** `/Volumes/addon_configs/a0d7b954_appdaemon/.planning/codebase/CONCERNS.md` -- HIGH confidence (known tech debt and gaps)

---

*Feature landscape researched: 2026-02-15*

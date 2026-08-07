# Entities

Every entity Energy Manager creates, in one place. This page is a lookup table — for the behavior behind each entity, see [Home Battery](../user-guide/home-battery.md), [EV Charging](../user-guide/ev-charging.md), [Battery → Grid Export Arbitrage](../user-guide/battery-export-arbitrage.md), [Solar Appliances](../user-guide/solar-appliances.md), and [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md).

!!! note "Entities follow your configuration"
    Only the Electricity Price sensor always exists. The rest appear with what you configure: battery and EMS sensors with the Home Battery module, charger/EV and house-load sensors with the EV Charging module, one Car Charging Schedule per car subentry, and appliance entities with the Solar Appliances module.

## Sensors

| Sensor | Description |
|--------|--------------|
| Electricity Price | Current Nordpool price |
| Battery status | Live battery state (`self_consumption` / `holding` / `solar_charging` / `grid_charging` / `discharging` / `exporting` / `paused_car_priority`) — what EM is driving the battery to do right now (`holding` = battery genuinely doing nothing, e.g. at night with discharge blocked). Attributes carry the full schedule, EMS mode, charge limit, fuse headroom, and gate reasons |
| Battery next charging slot | Timestamp of the battery's next scheduled charge slot |
| Battery next discharging slot | Timestamp of the battery's next scheduled discharge slot |
| Battery commanded charge limit *(diagnostic)* | Charge power limit EM sends to the battery (tracks live PV during solar charging; the would-be value in observe-only mode) |
| Battery commanded EMS mode *(diagnostic)* | EMS mode EM commands the battery plant right now (`command_charging` / `command_discharging` / `max_self_consumption` / `standby`), including the car-priority, PV-opportunistic, and discharge-gate overrides — `standby` also fires while the discharge gate is closed, with `override_reason: discharge_gate_closed`. The trigger surface for non-SigenStor automations (see [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md)) |
| Battery commanded discharge limit *(diagnostic)* | Discharge power limit EM sends to the battery (`0` while the discharge gate is closed; the fuse-capped export limit during export slots) |
| Actual Electricity Price | Spot price + grid transfer fee + electricity company fee (diagnostic; no long-term statistics) |
| Car Charging Schedule *(per car)* | Cheapest-slot charging schedule for that car |
| EV charger status | Easee charger decision mode (forced/scheduled/solar/idle), target amps/phase mode, fuse headroom, and more |
| Commanded charging current *(diagnostic)* | Charging current EM commands the charger (`0` = pause/stop, above `0` but below the 6 A minimum = do not start, `>= 6` = charge at up to this limit) — the trigger surface for non-Easee automations (see [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md)) |
| Commanded phase mode *(diagnostic)* | Charger phase mode EM commands (`single` / `three`) |
| House Load *(diagnostic)* | Filtered house consumption (house consumption minus excluded power entities), with the rolling mean consumption as an attribute (rolling window persists across restarts) |
| Solar Forecast Accuracy *(diagnostic)* | Observe-only solar forecast accuracy tracking: daily forecast-vs-actual ratios and a suggested production factor (needs 7+ valid days; does not affect scheduling; the in-flight day persists across restarts) |
| Battery effective discharge threshold *(diagnostic)* | The discharge spread threshold the scheduler is actually using right now, with attributes showing whether it comes from the manual entity or the Battery Cycle Cost formula |
| Solar Balance *(diagnostic)* | Signed net solar balance (PV minus house load minus battery charging plus charger draw): positive means surplus available for the charger, negative means deficit. Raw value before the charger's own activation gating |
| Status *(per appliance)* | Surplus-control decision status (`off_no_surplus`, `on_surplus`, `blocked_fuse`, ...) with attributes (thresholds, surplus components, allocation, last command message) that explain every decision |

The diagnostic command sensors (Battery commanded charge/discharge limit, Battery commanded EMS mode, Commanded charging current, Commanded phase mode) double as the [command sensor contract](../bring-your-own-hardware/command-sensors.md) for anyone bringing their own hardware — every state change on one of these is a trigger surface for your own automations.

## Switches

| Entity | Description |
|--------|--------------|
| Device control | Master observe-only switch; OFF means every coordinator still computes and publishes decisions, but no device command is actually sent |
| EV charger force charging | Forces the Easee charger to grid-charge regardless of schedule or solar state |
| EM control *(per appliance)* | Hand-over valve: Energy Manager only manages this appliance's actuator while this is ON (on top of the master Device control switch). Default OFF |

## Numbers

Price-valued entities follow your Nordpool sensor's currency (SEK, NOK, DKK, EUR, ...) — every "per kWh" below is in that currency.

| Entity | Description |
|--------|--------------|
| Battery charge spread threshold | Spread per kWh: a slot is a charge candidate for a peak when that peak's max price minus the slot's price exceeds this value |
| Battery discharge spread threshold | Spread per kWh: a slot discharges when its price minus the period's minimum price exceeds this value. Overridden by Battery Cycle Cost when that is set above 0 — the entity shows as unavailable while overridden |
| Battery max charging power | Maximum battery charge power (kW) |
| Battery max SoC target | SOC ceiling (default 100%) that PV-opportunistic battery charging stops at |
| Battery Cycle Cost | Cost per kWh of one battery charge/discharge cycle. When above 0, it replaces the manual discharge spread threshold with a derived value — see [Tuning the discharge and charge thresholds](../user-guide/home-battery.md#tuning-the-discharge-and-charge-thresholds) for the formula and its edge cases. Default 0 (disabled) |
| Battery export spread threshold | Spread per kWh above the horizon's cheapest hour at or above which a slot may become an export slot; 0 = export arbitrage disabled (the default) — see [Battery to Grid Export Arbitrage](../user-guide/battery-export-arbitrage.md) |
| Battery export reserve level | SOC floor (default 20%) below which the battery never sells energy during export slots — see [Battery to Grid Export Arbitrage](../user-guide/battery-export-arbitrage.md) |
| Grid Transfer Fee | Grid transfer fee per kWh; feeds the Battery Cycle Cost formula and the Actual Electricity Price sensor |
| Electricity Company Fee | Electricity company fee per kWh; used only by the Actual Electricity Price sensor |
| Grid charging target *(per car)* | Target state of charge for scheduled price-based charging |
| Solar charging target *(per car)* | SOC ceiling for solar-surplus charging (default 100%) |
| Max Charge Power *(per car)* | Maximum charge power for that car (kW) |

## Time

| Entity | Description |
|--------|--------------|
| Car Departure Time *(per car)* | Deadline used to compute that car's charging schedule |

!!! note "Numbers persist"
    All number entities persist their value across Home Assistant restarts.

!!! important "Spreads, not absolute prices"
    All price thresholds are spreads relative to the horizon — never absolute prices. See the [full explanation](../user-guide/home-battery.md#tuning-the-discharge-and-charge-thresholds) on the Home Battery page.

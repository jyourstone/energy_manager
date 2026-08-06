# Home Battery

The Home Battery module charges your battery during cheap Nordpool hours and discharges it into expensive ones, so the house runs on stored cheap energy instead of expensive grid power when prices spike.

## How scheduling works

Energy Manager doesn't just watch the current price and react to it. It reads the full Nordpool horizon (today, plus tomorrow once tomorrow's prices are published) and plans several charge/discharge cycles ahead: it identifies the expensive peaks worth covering, works out how much energy each one needs, and picks the cheapest available slots — solar or grid — to fill that need before the peak arrives.

That forward planning is what makes the schedule stable. A slot's role (charge, discharge, idle) is decided against the whole horizon, not re-decided every time a new price ticks in, so the plan doesn't flip-flop hour to hour.

Optionally, the same schedule can also sell battery energy to the grid during extreme price spikes — see [Battery to Grid Export Arbitrage](battery-export-arbitrage.md).

## Solar-aware scheduling

If you configure one or more [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) "remaining today" entities in the wizard, Energy Manager folds expected solar production into the plan instead of charging from the grid regardless. Configuring multiple entities (e.g. separate east + west arrays) works exactly like one — their readings are summed. Matching "tomorrow" sensors are derived automatically from the ones you picked, so the 48-hour plan also accounts for tomorrow's sun, not just today's.

A few tuning options (set in the wizard, adjustable later via **Configure**) shape how much the forecast is trusted and how it's translated into a charging plan:

- **Solar production factor** — a multiplier applied to the raw forecast to correct for its typical optimism. Forecast.Solar tends to over-predict, so this scales the forecast down before it's used to size how much grid charging is still needed for a given peak.
- **Charge buffer (%)** — extra margin added on top of the calculated charge deficit, so a slightly-worse-than-expected day (dimmer sun, more house load) doesn't leave the battery short right when a price peak hits.
- **Estimated charge power (kW)** — the assumed charging rate used to work out how many slots a charge deficit needs. The actual per-slot energy uses whichever is lower: this value or your fuse-limited maximum.
- **Peak-grouping gap (h)** — expensive hours within this many hours of each other are treated as one discharge peak rather than several separate ones, so the plan sizes a single charge deficit (and a single solar recharge window) to cover the whole peak instead of fragmenting it.

!!! tip "Solar Forecast Accuracy is observe-only"
    The diagnostic **Solar Forecast Accuracy** sensor tracks daily forecast-vs-actual production ratios and, once it has 7 or more valid days of history, suggests a production factor you could set instead. It never changes your configured value itself — it's there so you can tune the production factor from real data rather than guesswork. Days with a near-zero forecast are skipped so they can't skew the suggestion. The current day's progress is persisted too, so a mid-day restart or config reload no longer loses that day's record.

## Tuning the discharge and charge thresholds

Two number entities control how aggressively the battery charges and discharges:

- **Battery charge spread threshold** — a slot becomes a charge candidate for a given peak when that peak's highest price minus the slot's price exceeds this value.
- **Battery discharge spread threshold** — a slot discharges when its price minus the horizon's minimum price exceeds this value.

The discharge threshold can be overridden automatically. When **Battery Cycle Cost** is set above 0, the effective discharge threshold becomes `max(0, battery_cycle_cost − grid_transfer_fee)` — the wear cost of one charge/discharge cycle, net of the grid transfer fee you'd otherwise pay to import that energy instead. While the formula is active, the manual **Battery discharge spread threshold** entity shows as **unavailable** in Home Assistant, since the scheduler is ignoring it in favor of the derived value. If the transfer fee exceeds the cycle cost, the formula clamps to 0, meaning any spread above the horizon minimum qualifies for discharge.

To see which source is actually driving the schedule at any moment, check the diagnostic **Battery effective discharge threshold** sensor — its state is the threshold in use right now, and its attributes show whether that came from the manual entity or the cycle-cost formula (plus the cycle cost and transfer fee values that fed it).

!!! note "All price thresholds are spreads, not absolute prices"
    Every threshold on this page — charge, discharge, and the export threshold on the [export arbitrage](battery-export-arbitrage.md) page — is a spread relative to the horizon: how far a slot's price sits above the cheapest slot (or below a peak's highest price), never a fixed price level. That keeps the thresholds meaningful even as the overall price level shifts week to week — you don't need to re-tune them every time prices move.

## Fees

Two number entities feed into the economics without changing the charge/discharge decision directly:

- **Grid Transfer Fee** — a per-kWh fee in your Nordpool sensor's currency; feeds the Battery Cycle Cost formula above, and the **Actual Electricity Price** sensor.
- **Electricity Company Fee** — a per-kWh fee in your Nordpool sensor's currency; used only by the **Actual Electricity Price** sensor.

**Actual Electricity Price** reports spot price plus both fees — a diagnostic sensor useful for dashboards, with no long-term statistics recorded.

## EMS setup fields

Configured in Step 3 of the [Setup Wizard](../getting-started/setup-wizard.md), these fields feed the EMS (Energy Management System) control layer that actually drives the battery:

| Field | What it's for |
|-------|-----------------|
| Fuse rating (A) | Your main fuse rating — every charge/discharge/export limit Energy Manager commands is capped so it never trips this fuse |
| EMS mode select entity | Your inverter's operating-mode select entity (auto-detected from a SigenStor integration). Leave empty for non-SigenStor hardware — see [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md) |
| Charge/discharge limit entities | Your inverter's power-limit number entities (auto-detected from SigenStor). Leave empty to let your own automations follow the commanded-limit sensors instead |
| Grid power / phase entities | Signed power sensors (positive = import, negative = export) that fuse protection reads every control cycle to compute live headroom |
| PV power entity *(optional)* | Enables opportunistic solar charging: when there's PV surplus outside a scheduled charge slot, the battery can still charge from it |

## Reading battery status

The **Battery status** sensor is the single place to see what Energy Manager is driving the battery to do right now. Its state is one of:

| State | Meaning |
|-------|---------|
| `self_consumption` | Normal operation — the battery balances solar/house load, no scheduled charge or discharge active |
| `holding` | The battery is genuinely doing nothing — e.g. overnight with the discharge gate closed |
| `solar_charging` | Charging opportunistically from PV surplus outside a scheduled slot |
| `grid_charging` | Charging from the grid during a scheduled cheap slot |
| `discharging` | Discharging to serve house load during a scheduled expensive slot |
| `exporting` | Selling battery energy to the grid during a scheduled export slot — see [Battery to Grid Export Arbitrage](battery-export-arbitrage.md) |
| `paused_car_priority` | Battery charging paused because a car is being prioritized for charging instead |

Its attributes carry the full multi-day schedule, the current EMS mode and charge limit, live fuse headroom, and — when a scheduled discharge slot isn't actually discharging — the `discharge_gate_reason` explaining why (this is how `holding` and `self_consumption` stay honest instead of claiming an action that isn't really happening).

Two companion sensors give you the next scheduled transitions without digging into the schedule attribute: **Battery next charging slot** and **Battery next discharging slot**, both timestamps.

## Diagnostics

Three diagnostic sensors expose exactly what Energy Manager is commanding the battery to do, independent of whether it's actually reaching your hardware: **Battery commanded EMS mode**, **Battery commanded charge limit**, and **Battery commanded discharge limit**. These are the trigger surface for your own automations if you're not on native SigenStor control — see [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md) for the full command sensor contract, states, and worked automation examples.

For the complete entity tables (every sensor, number, and switch this module creates, with exact names and descriptions), see [Entities](../reference/entities.md).

---

Want to also sell battery energy to the grid during price spikes? See [Battery to Grid Export Arbitrage](battery-export-arbitrage.md).

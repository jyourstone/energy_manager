# EV Charging

Energy Manager builds a price-optimized charging schedule for each car you add, tops it up with solar surplus when the sun cooperates, and — with an Easee charger configured — drives the charger's current and phase mode directly, all under the same fuse protection as the rest of the system.

## How Scheduling Works

Each car gets its own schedule: the cheapest available hours between now and its departure time that deliver enough energy to reach its **Grid charging target** SOC, given its battery capacity and max charge power. The scheduler is duration-aware (it works whether Nordpool is publishing hourly or 15-minute slots) and always includes the slot in progress, so a schedule computed mid-hour still drives the charger correctly.

On top of the price schedule, **solar-surplus charging** can divert live PV surplus to the car outside its scheduled hours, up to its separate **Solar charging target** ceiling:

- Solar charging only activates once the raw solar signal clears a configured start threshold continuously for an activation delay (filters passing clouds), and deactivates after its own, shorter deactivation delay. The activation state persists across restarts and config reloads, so a reload during active solar charging does not interrupt the session or restart the delay.
- With the Home Battery module enabled, it only kicks in once the house battery itself is at or above the configured battery SOC gate (100% by default) — the battery fills first, and only the surplus left over goes to the car. Without that module there is no gate, and surplus goes straight to the car.
- Mode arbitration is strict priority order: **forced** charging (see below) beats a **scheduled** price slot, which beats **solar** surplus, which beats **idle**. A car mid-way through a cheap scheduled hour is never bumped for solar.

## Charger Setup

Configured in Step 5 of the [Setup Wizard](../getting-started/setup-wizard.md). Fuse rating, grid power sensors and house consumption live in the shared **Grid & Fuse Protection** step:

| Field | Default | Description |
|-------|---------|--------------|
| Charger status sensor | — | Auto-detected from an Easee integration; reports connection/charging state — see the [Charger Status Contract](../bring-your-own-hardware/charger-status.md) for the exact vocabulary |
| Charger power sensor | — | Auto-detected from Easee; measured charger power draw |
| Charger device ID | auto-detected | HA device ID used to address the `easee.*` control services. Leave it empty for a non-Easee charger — commands are then never sent, and you automate on the command sensors instead |
| Minimum charging current (A) | 6 | The lowest current the charger will ever be set to (Easee's own minimum) |
| Maximum charging current (A) | 16 | The highest current EM will ever request |
| Maximum grid charging power (kW) | 12 | Absolute ceiling on grid-charging power, converted to amps for the car currently charging |
| 3-phase switch threshold (kW) | 4.1 | Available power below which the charger drops to single-phase |
| Solar charging start threshold (kW) | 1.5 | Minimum net solar surplus, sustained past the activation delay, before solar charging begins |
| Battery SOC gate (%) | 100 | Advanced option: minimum house-battery SOC before solar EV charging starts — the battery fills first, only the leftover surplus goes to the car. Requires the Home Battery module; the field and its number entity are hidden without it, and the gate is not applied |
| Notification service | — | Optional `notify.*` service for safety alerts — fuse emergency overload pauses and the 0A safety stop |

A handful of further tuning knobs (current increase/decrease delay, solar activation/deactivation delay, the emergency overload margin) live in the same step's advanced options, pre-filled with tuned defaults.

!!! tip "No reload needed"
    **Maximum grid charging power**, **Solar charging start threshold**, and, with the Home Battery module enabled, the advanced-options **battery SOC gate** field only seed their number entities on first setup (see [Entities](../reference/entities.md)) — adjust them afterward from the Energy Manager device page and the new value applies on the next coordinator refresh (triggered immediately on change), no reload, and persists across restarts. If you disable the Home Battery module for more than a week, the SOC gate number entity's restored value expires and it returns to the setup-wizard seed when you re-enable the module.

## Adding a Car

Each car is added as its own **subentry** on the Energy Manager device (not part of the main wizard) — click **Add car** on the integration page:

| Field | Description |
|-------|--------------|
| Car name | Friendly name, e.g. "Enyaq" |
| Battery capacity (kWh) | Total battery capacity, used to convert an SOC gap into energy needed |
| Battery level sensor | Optional; auto-detected from Skoda Connect or VW We Connect if present |
| Car charger connected sensor | Optional binary sensor confirming the cable is plugged in; also auto-detected |
| Car location tracker | Optional device tracker used to confirm the car is actually home |
| Charger phases used by this car | 1, 2, or 3 — how many of the charger's phases this car actually draws on in 3-phase mode. Most cars use all 3; some (e.g. VW ID.3) only use 2. Default 3 |

## Per-Car Controls

| Entity | Default | Description |
|--------|---------|--------------|
| Car Departure Time | — | Deadline the price schedule charges toward |
| Grid charging target | 80% | Target SOC for scheduled price-based charging |
| Solar charging target | 100% | SOC ceiling for solar-surplus charging — solar mode skips the car once it reaches this level, even if its grid charging target is lower |
| Max Charge Power | 7.4 kW (1-phase default) | Maximum charge power for that car; also caps the amps EM will ever request for it |

## Reading the Sensors

- **Car Charging Schedule** *(per car)* — state is the current action (`charge`, `solar_charge`, or `idle`); attributes carry the full upcoming schedule (up to 48 slots), energy and hours needed, and current/target SOC.
- **EV charger status** — state is the decision mode (`forced` / `scheduled` / `solar` / `idle`); attributes carry the target amps and phase mode, fuse headroom, the raw charger status, and whether the tick is `dry_run`.
- **Solar Balance** *(diagnostic)* — the signed live solar signal available to the charger, computed as PV production minus house load minus battery charging plus the charger's own draw. Positive means surplus is available; this is the raw value *before* the safety-buffer and start-threshold gating described above, so it can go positive slightly before solar charging actually activates.

!!! tip "Non-Easee automations"
    The **Commanded charging current** and **Commanded phase mode** diagnostic sensors carry the same target values as the attributes above, but as their own entities — the trigger surface described in [Non-Easee Chargers](#non-easee-chargers) below.

## Force Charging

The **EV charger force charging** switch overrides both the schedule and the solar state: while it's ON, the charger controller treats "forced" as the highest-priority mode and starts grid-charging at the computed grid amp target immediately, regardless of what the price schedule or solar surplus says. It's OFF by default and restores its previous state across restarts.

## Fuse Protection

Whatever mode wins, the amp target actually sent to the charger is capped by fuse headroom, computed independently every control cycle: the fuse rating minus the safety buffer — both configured in the shared **Grid & Fuse Protection** step — minus the worst-loaded phase's current draw, with the charger's own current draw added back so it never counts against its own headroom. That figure is combined with the grid charging power cap and the car's own max charge power — EM always requests the lowest of the three. This applies identically to Easee charging and to the commanded-current sensor a non-Easee automation follows.

## Non-Easee Chargers

Without a configured *Charger device ID*, EM never calls an Easee service — it plans exactly the same way, and publishes its decisions on two diagnostic command sensors instead:

- **Commanded charging current** — `0` = pause/stop; above `0` but below the 6 A minimum = do not start; `>= 6` = charge at up to this current.
- **Commanded phase mode** — `single` or `three`.

Your own automation follows these sensors and calls your charger's own services. You'll also need to map your charger's native status strings onto the vocabulary EM expects from the *Charger status sensor* — see [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md) for the full command sensor contract and [Charger Status Contract](../bring-your-own-hardware/charger-status.md) for the status mapping, including a worked template-sensor example.

---

For the complete entity tables (every sensor, number, and switch this module creates, with exact names and descriptions), see [Entities](../reference/entities.md).

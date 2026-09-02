# EV Charging

Energy Manager builds a price-optimized charging schedule for each car you add, tops it up with solar surplus when the sun cooperates, and — with an Easee charger configured — drives the charger's current and phase mode directly, all under the same fuse protection as the rest of the system.

## How Scheduling Works

Each car gets its own schedule: the cheapest available hours between now and its departure time that deliver enough energy to reach its **Grid charging target** SOC, given its battery capacity and the charge power it actually achieves (see [Measured Charge Power](#measured-charge-power)). The scheduler is duration-aware (it works whether Nordpool is publishing hourly or 15-minute slots) and always includes the slot in progress, so a schedule computed mid-hour still drives the charger correctly.

On top of the price schedule, **solar-surplus charging** can divert live PV surplus to the car outside its scheduled hours, up to its separate **Solar charging target** ceiling:

- Solar charging only activates once the raw solar signal clears a configured start threshold continuously for an activation delay (filters passing clouds), and deactivates after its own, shorter deactivation delay. A cloud dip mid-session does not hard-pause the charger: as long as fuse headroom allows it, charging rides through at the minimum current until the surplus recovers or the deactivation delay ends the session. The activation state persists across restarts and config reloads, so a reload during active solar charging does not interrupt the session or restart the delay.
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
| Notification service | — | Optional `notify.*` service for safety alerts — an unclearable fuse overload and the 0A safety stop |

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
| Max Charge Power | 3.7 / 6.4 / 11.0 kW | Ceiling on that car's charge power: it caps the amps EM will ever request for it, and caps the measured throughput the scheduler plans with. Seeded from **Charger phases used by this car** and the charger's **Maximum charging current** — the three defaults are for a 1-, 2-, and 3-phase car at the default 16 A. See [Measured Charge Power](#measured-charge-power) |

## Measured Charge Power

To decide how many hours a car needs, the scheduler has to know how many kW that car actually pulls. Energy Manager measures it during ordinary grid charging rather than assuming.

**What is measured is the power the car really receives** — the charger's own power sensor, integrated over each continuous run of EM-directed grid charging. So the figure already has your real conditions baked in: fuse throttling, the grid charging power cap, and whatever the rest of the house was drawing at the time. If the fuse regularly holds the car at 10 A on a winter evening, the measurement says so, and the schedule books the extra hours instead of planning as if the car got its full rate. Minutes when EM pauses the charger *inside* a booked slot — a fuse emergency pause, a phase switch — count toward the run at the power actually seen, for the same reason: the planner buys wall-clock hours, so it has to know what an hour of booked time is really worth.

**Solar sessions are excluded.** Solar charging runs at whatever surplus happens to be available, often down at the 6 A minimum, and says nothing about what the car takes from the grid. A 1.4 kW solar measurement applied to a 3-phase grid plan would book several times the hours actually needed. Also excluded: anything drawn while the master **Device control** switch is off (EM sent no command, so the draw is somebody else's), sessions where the plugged-in car is not one of your configured cars, and any moment EM cannot pin the draw on exactly one car.

Measurements are filed separately per number of phases in use, so a car that charges single-phase some nights and three-phase on others never blends the two. EM waits for real evidence before using anything — at least two separate runs and an hour of charging in total — and measurements older than 30 days drop out, so a swapped car or a changed installation converges again within a few nights.

**Max Charge Power is a ceiling, not the planning figure.** The scheduler plans with the lower of the measured throughput and this number. The live amp target sent to the charger is still computed from the number alone and never from the measurement — deliberately: if a measurement could throttle the charger, one slow night would teach EM to charge slowly forever.

The per-car diagnostic **Planned Charge Power** sensor shows the figure the last plan actually used, with both inputs beside it as attributes.

!!! warning "Cars added before this feature keep their old 7.4 kW"
    Number entities restore the value they were last set to, so existing cars keep the flat 7.4 kW they were created with — the phase-derived seed above only applies to cars added from now on, and changing **Charger phases used by this car** afterward does not move the number either. Check **Planned Charge Power**: if its `learned_power_kw` attribute sits well above the sensor's own value, the ceiling is what is holding the plan down, and it is worth raising **Max Charge Power** by hand to match what the car and charger actually do. There is no automatic migration: quietly raising a limit you configured yourself would be the worse mistake.

!!! note "Installs that never measure anything"
    The phase count is read straight off the charger status entity. If the charger sits in Easee's **auto** phase mode, or you run a non-Easee charger whose status sensor carries no phase-mode attribute, EM cannot tell which phases a session used and files nothing. **Planned Charge Power** then reads `source: ceiling` indefinitely and planning works exactly as it did before, from **Max Charge Power** alone.

## Reading the Sensors

- **Car Charging Schedule** *(per car)* — state is the current action (`charge`, `solar_charge`, or `idle`); attributes carry the full upcoming schedule (up to 48 slots), energy and hours needed, current/target SOC, and the charge power the plan was sized with.
- **Planned Charge Power** *(per car, diagnostic)* — the kW the scheduler used to size that car's slots. Attributes carry `learned_power_kw` (the measured throughput, empty until there is one), `ceiling_power_kw` (the **Max Charge Power** number), `phase_bucket` (the phase count the measurement was filed under), and `source` — `learned` once EM has a measurement for that car and phase count, `ceiling` while it has none. When `source` is `learned` but the state equals `ceiling_power_kw`, the measurement came out above the ceiling and was capped. See [Measured Charge Power](#measured-charge-power).
- **EV charger status** — state is the decision mode (`forced` / `scheduled` / `solar` / `idle`); attributes carry the target amps and phase mode, fuse headroom, the raw charger status, and whether the tick is `dry_run`.
- **Solar Balance** *(diagnostic)* — the signed live solar signal available to the charger, computed as PV production minus house load minus battery charging plus the charger's own draw. Positive means surplus is available; this is the raw value *before* the safety-buffer and start-threshold gating described above, so it can go positive slightly before solar charging actually activates.

!!! tip "Non-Easee automations"
    The **Commanded charging current** and **Commanded phase mode** diagnostic sensors carry the same target values as the attributes above, but as their own entities — the trigger surface described in [Non-Easee Chargers](#non-easee-chargers) below.

## Force Charging

The **EV charger force charging** switch overrides both the schedule and the solar state: while it's ON, the charger controller treats "forced" as the highest-priority mode and starts grid-charging at the computed grid amp target immediately, regardless of what the price schedule or solar surplus says. It's OFF by default and restores its previous state across restarts.

## Fuse Protection

Whatever mode wins, the amp target actually sent to the charger is capped by fuse headroom, computed independently every control cycle: the fuse rating minus the safety buffer — both configured in the shared **Grid & Fuse Protection** step — minus the worst-loaded phase's current draw, with the charger's own current draw added back so it never counts against its own headroom. That figure is combined with the grid charging power cap and the car's own max charge power — EM always requests the lowest of the three. This applies identically to Easee charging and to the commanded-current sensor a non-Easee automation follows.

### Overload Alerts

Routine load balancing is silent. When the measured current does cross the fuse rating plus the emergency overload margin, EM pauses the charger immediately — but sends nothing, because that pause (or the house load stepping back down) normally clears the overload within a cycle or two, and a spike is not something worth a notification.

An alert is only sent when the overload holds continuously for two minutes, i.e. when pausing the charger did **not** fix it and the remaining house load is what is eating the fuse. That one is sent as a critical notification — it overrides silent mode and Do Not Disturb — once per episode, re-arming when the overload clears. It is also sent when the charger is already paused or the car is unplugged, which is precisely the case where EM has no lever left to pull.

!!! note "iOS"
    Critical alerts require the Home Assistant companion app's *Critical Alerts* permission. Without it the alert still arrives, as a normal notification.

## Non-Easee Chargers

Without a configured *Charger device ID*, EM never calls an Easee service — it plans exactly the same way, and publishes its decisions on two diagnostic command sensors instead:

- **Commanded charging current** — `0` = pause/stop; above `0` but below the 6 A minimum = do not start; `>= 6` = charge at up to this current.
- **Commanded phase mode** — `single` or `three`.

Your own automation follows these sensors and calls your charger's own services. You'll also need to map your charger's native status strings onto the vocabulary EM expects from the *Charger status sensor* — see [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md) for the full command sensor contract and [Charger Status Contract](../bring-your-own-hardware/charger-status.md) for the status mapping, including a worked template-sensor example.

---

For the complete entity tables (every sensor, number, and switch this module creates, with exact names and descriptions), see [Entities](../reference/entities.md).

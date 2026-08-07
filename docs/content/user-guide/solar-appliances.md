# Solar Appliances

Solar Appliances turns `switch` or `input_boolean` loads on while measured solar surplus covers them, and off again when the surplus disappears — a water heater or pool pump that soaks up whatever export would otherwise go to the grid, with no price scheduling of its own. Off by default (the **Solar Appliances** module flag in the Modules step).

## What It Does

There is deliberately no cheap-hours appliance scheduling in Energy Manager — that job belongs to price-based schedulers like [Power Saver](https://github.com/jyourstone/power_saver). Solar Appliances only reacts to surplus that's actually there right now, and the two coexist by design: Power Saver keeps a load's guaranteed cheap-hours runtime, and Solar Appliances tops it up with free solar whenever there's export to spare (see the [recipe](#recipe-managing-a-load-another-integration-schedules) below).

## The Surplus Signal

The pool every appliance draws from is **measured grid export minus battery discharge power** — never a computed PV-minus-loads estimate. Battery discharge is subtracted because export driven by [battery arbitrage](battery-export-arbitrage.md) at a price spike isn't solar surplus and must never feed an appliance.

This also means the battery and car charging need no explicit arbitration with appliances: both are satisfied upstream of the grid meter, so export — and therefore the appliance pool — only appears once the battery and any scheduled/solar car charging already have what they need.

!!! note "A different signal from the EV charger's"
    This is not the same surplus signal [EV solar charging](ev-charging.md#how-scheduling-works) uses. The charger's **Solar Balance** sensor is a computed PV-minus-loads estimate meant to catch surplus *before* it reaches the meter; appliances deliberately wait for it to actually show up as export.

## Priority Allocation

Appliances are evaluated in priority order, 1 = highest, ties broken by the order they were added. Each appliance already running is credited back into the pool at its measured draw (if a power sensor is configured) or its rated draw otherwise — since the export reading already contains that appliance's own consumption, crediting it back is what lets a lower-priority appliance still see the true remaining surplus. Walking the list in priority order, each appliance newly admitted then subtracts its own draw from what's left for the next one.

## Anti-Short-Cycling

Two independent guards stop a load from chattering on and off as surplus flickers:

- **Sustain delays** — the pool must clear the on threshold continuously for the on-sustain time before turning on (filters passing clouds), and stay below the off threshold continuously for the (deliberately longer) off-sustain time before turning off.
- **Hard minimum times** — once on, an appliance stays on for at least its minimum on time regardless of surplus; once off, it stays off for at least its minimum off time. These are absolute floors, checked independently of the sustain delays.

## Fuse Admission

An appliance only turns on if its rated amps fit the live measured fuse headroom minus the safety buffer. Rated power is converted to per-phase current using the appliance's configured phase count: `rated_amps = rated_power_w / (230V × phases)`. Only *new* turn-ons are checked this way — a load already running is already part of the measured grid current, so it's never re-checked against headroom while it stays on.

## Observe-Only Gating

Commands are only sent when **both** the master **Device control** switch and that appliance's own **EM control** switch are ON. Until then, the appliance's **Status** sensor and its dry-run messages show exactly what Energy Manager would do — the same decision logic runs either way, so what you see in observe-only mode is what happens once you flip both switches on. See [Your First Days](../getting-started/first-days.md) for the general pattern.

## Per-Appliance Configuration

Each appliance is added as its own **subentry** — click **Add appliance** on the integration page. Every threshold and timing field is configurable per appliance, pre-filled with these defaults:

!!! tip "Tuning fields live as number entities afterward"
    Priority, the on/off thresholds, and the on/off sustain times only seed their number entities when you add the appliance (see [Per-Appliance Entities](#per-appliance-entities) below). Editing the appliance afterward no longer offers these five fields — adjust them via their number entities instead, which apply on the next 30-second coordinator tick, no reload, and persist across restarts. Rated power, phases, the power sensor, and the minimum on/off times remain editable through the appliance's edit form.

| Field | Default | Description |
|-------|---------|--------------|
| Appliance name | — | Friendly name, e.g. "Water heater" |
| Actuator switch | — | The `switch` or `input_boolean` Energy Manager toggles — a smart plug, a relay, or another integration's override switch |
| Rated power (W) | — | Expected draw when running, e.g. 4200 |
| Phases | 3 | 1 or 3; converts rated power to per-phase amps for the fuse admission check |
| Power sensor | *(optional)* | Sensor measuring actual draw; when set, the measured draw is credited back to the surplus pool instead of the rated power |
| Priority | 5 | Allocation priority when appliances compete for the surplus (1 = highest); ties broken by the order appliances were added |
| On threshold (%) | 110 | Turn on when the surplus pool reaches this percentage of rated power, sustained |
| Off threshold (%) | 90 | Turn off when the pool stays below this percentage of rated power, sustained — must be lower than the on threshold |
| On sustain time (min) | 5 | How long the surplus must persist before turning on |
| Off sustain time (min) | 15 | How long the deficit must persist before turning off (deliberately slower than the on side) |
| Minimum on time (min) | 15 | Hard floor once on, regardless of surplus |
| Minimum off time (min) | 5 | Hard floor once off (heat-pump plugs: raise this) |

## Per-Appliance Entities

Each appliance gets its own device with a switch, a status sensor, and five tuning number entities:

- **EM control** — the per-appliance hand-over valve described above. Default OFF, restored across restarts, so appliances stay opt-in one at a time rather than all activating the moment the module is enabled.
- **Priority**, **On threshold**, **Off threshold**, **On sustain time**, **Off sustain time** — the five fields from the table above, live-adjustable per appliance without a reload; see [Entities](../reference/entities.md) for their exact ranges and defaults. The off threshold is always clamped below the on threshold, even if you set it higher.
- **Status** — state is the current decision status; attributes explain exactly why:

| Status | Meaning |
|--------|---------|
| `disabled` | This appliance's EM control switch is OFF |
| `actuator_unavailable` | The configured actuator entity is missing or unavailable |
| `off_no_surplus` | Off — the pool hasn't reached the on threshold, or a sustained deficit already released it |
| `waiting_on_sustain` | The pool has cleared the on threshold but hasn't held long enough yet |
| `on_surplus` | On, and the pool still covers it |
| `holding_min_on` | The pool has dropped below the off threshold, but the minimum on-time floor hasn't elapsed yet |
| `blocked_min_off` | Off, and the minimum off-time floor is still counting down |
| `blocked_fuse` | The on threshold is cleared, but this appliance's rated amps don't fit the remaining fuse headroom |
| `blocked_priority` | The on threshold is cleared for the whole pool, but higher-priority appliances already claimed enough of it |
| `on_external` | The actuator was switched on outside Energy Manager — left alone: no allocation, no credit-back, no command |

Besides `reason` (a human-readable version of the table above), the Status sensor's attributes carry `allocated_kw`, the surplus signal's own components (`raw_surplus_kw`, `export_kw`, `battery_discharge_kw`), the appliance's own `threshold_on_kw` / `threshold_off_kw`, `measured_power_w` when a power sensor is configured, an `idle_while_on` flag (drawing under 10% of rated while EM has it on — a sign the load isn't actually running), `last_command_message`, and `observe_only`.

## Recipe: Managing a Load Another Integration Schedules

Never point Energy Manager's actuator at a relay that another integration already reconciles — one writer per switch (the single-writer rule). If a load already has price-based scheduling from something like [Power Saver](https://github.com/jyourstone/power_saver), point Solar Appliances' actuator at *that* integration's own override switch instead of the physical relay:

- Power Saver keeps scheduling the load's guaranteed cheap-hours runtime as usual.
- Set the appliance's **Actuator switch** to Power Saver's `always_on` override entity, e.g. `switch.heater_power_saver_always_on`, instead of `switch.heater_relay`.
- Energy Manager forces the load on through that override switch while solar surplus lasts; Power Saver still owns the physical relay and its year-round guarantee.

No two integrations ever fight over the same switch, and the load gets both a guaranteed cheap-hours schedule and free solar on top.

---

For the complete entity tables (every sensor, number, and switch this module creates, with exact names and descriptions), see [Entities](../reference/entities.md).

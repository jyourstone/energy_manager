# Command Sensors

Energy Manager's built-in actuation only speaks two dialects: SigenStor (EMS mode select + charge/discharge limit number entities) and Easee (`easee.*` services). Everything upstream of that is hardware-neutral — every decision the controllers make is published on a **command sensor** before any device command is sent, so any inverter or charger can be driven from your own automations.

## EM plans, your automations execute

Think of Energy Manager as the planning brain, not the hands. It reads prices, SOC, and power, decides what the battery and charger should do next, and writes that decision to a diagnostic sensor. With a SigenStor and an Easee charger configured, EM also pushes the same value straight to the device. With anything else, that push never happens — but the sensor is computed and updated exactly the same way, so your own automations can pick it up and act on it.

## How to go hardware-neutral

- **Battery:** leave the *EMS mode select entity* and the *charge/discharge limit entities* empty in the Home Battery step.
- **EV:** leave the *Charger device ID* empty in the EV Charging step — Easee commands are then never sent.
- Point the input pickers (SOC, powers, charger status) at your own hardware's sensors as usual.

## Or just keep Device control off

Alternatively — or in addition — leave the master **Device control** switch OFF. The command sensors publish the would-be values either way; while the switch is off, every command sensor carries `dry_run: true` in its attributes instead of a value actually reaching a device. This is the same mechanism [observe-only mode](../getting-started/first-days.md) uses, and it works whether or not SigenStor/Easee entities are configured.

## The five command sensors

All five are diagnostic entities on the Energy Manager device.

| Sensor | State | What your automation should do |
|--------|-------|--------------------------------|
| Battery commanded EMS mode | `command_charging` (grid-charge the battery — scheduled or PV-opportunistic), `command_discharging` (export-arbitrage slot, opt-in), `max_self_consumption` (normal operation), `standby` (hold the battery — a car has charging priority: scheduled and plugged in during a battery charge slot, or actively drawing power; or the discharge gate is closed for economic reasons); `unknown` until the first compute | Map each mode to your inverter's equivalent operating mode — `standby` is the authoritative hold signal: it must actually keep the battery from charging or discharging, even when the commanded discharge limit is nonzero (the car-active case with the gate open) |
| Battery commanded charge limit | Maximum battery charge power in kW (fuse-limited; tracks live PV during solar charging) | Write it to your inverter's charge-power limit |
| Battery commanded discharge limit | Maximum battery discharge power in kW; `0` = discharge blocked by the scheduler (the `discharge_gate_reason` attribute says why). During a `command_discharging` export slot this is the fuse-capped export power (reserve-SOC- and PV-aware). Outside export slots, when no SigenStor discharge-limit entity is configured the value is a 15 kW placeholder ceiling, not a device rating | Write it to your inverter's discharge-power limit, clamped to your inverter's own maximum (`min(value, your_max)`) — `0` must actually block discharge |
| Commanded charging current | EV charging current in A: `0` = charging should be paused/stopped; above `0` but below the 6 A minimum = do **not** start charging (EM's own state machine never starts in this range — it avoids start/stop churn below the charger minimum — and leaves a running session's limit untouched); `>= 6` = charge at (up to) this current | Set your charger's dynamic current limit; pause/stop at `0`; never start below `6` |
| Commanded phase mode | `single` or `three` | Switch the charger's phase mode if it supports that; ignore otherwise |

!!! note "Behavior change"
    Previously, `standby` was only commanded while a scheduled car was plugged in during a battery charge slot. It now also fires whenever the discharge gate is closed for economic reasons (spread below threshold, energy reserved for a later peak) and whenever a car is actively drawing charging power — automations should treat `standby`, not the discharge-limit value, as the signal to stop discharge.

!!! tip "Entity IDs"
    Entity IDs are slugs of the sensor names — e.g. `sensor.energy_manager_battery_commanded_charge_limit` on an English install. IDs follow your Home Assistant language at entity creation, so always check the exact IDs on the Energy Manager device page rather than assuming the English slug.

## Context attributes

Every command sensor carries extra attributes that explain the state — read them in your templates rather than guessing why a value changed.

| Attribute | On which sensors | Meaning |
|-----------|-------------------|---------|
| `dry_run` | All five | `true` while the **Device control** switch is off — the state is still the value EM would have written, but nothing was sent to a device |
| `override_reason` | EMS mode, commanded current | Why this tick's value differs from the plain schedule, e.g. `car_charging_priority`, `discharge_gate_closed`, `pv_opportunistic`, or a terminal-status reason |
| `charge_limit_delivered` | Commanded charge limit | Whether the value also reached a configured SigenStor charge-limit entity (`false` when the send was skipped or failed) |
| `discharge_limit_delivered` | Commanded discharge limit | Whether the value also reached a configured SigenStor discharge-limit entity (`false` when the send was skipped or failed) |
| `discharge_gate_reason` | Commanded discharge limit | Why discharge is currently allowed or blocked, e.g. `scheduled_discharge`, `no_schedule` |

The commanded charging current sensor also carries `charger_mode` and `charger_status` (EM's internal decision mode and the raw charger status this tick) — useful context if you're debugging why a current value changed, but not part of the contract your automation needs to act on.

!!! note "Export arbitrage doesn't need any SigenStor entities"
    [Export arbitrage](../user-guide/battery-export-arbitrage.md) works without any SigenStor entities: during export slots the commanded EMS mode shows `command_discharging` and the commanded discharge limit carries the fuse-capped export power, computed from the fuse rating and safety buffer you configure in Energy Manager's options. The demotion to `max_self_consumption` only happens on real SigenStor hardware (EMS select entity configured) that is missing its discharge-limit entity, where the command would otherwise run uncapped.

## Update cadence

Command sensors are recomputed on the 30-second control loops (battery EMS and charger controllers) — there's no separate polling schedule to configure. State-change automations follow automatically: a trigger only fires when the value actually changes, not on every tick, so you don't need to add your own debouncing.

## Next steps

- [Charger Status Contract](charger-status.md) — the vocabulary EM expects back from a non-Easee charger's status sensor.
- [Worked Examples](examples.md) — full automations that follow the battery charge limit and the EV commanded current.

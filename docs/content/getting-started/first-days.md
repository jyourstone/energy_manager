# Your First Days (Observe-Only Mode)

Energy Manager starts in observe-only mode after setup. Spend your first few days watching before you let it touch any hardware.

## Why Device Control Ships OFF

The master **Device control** switch defaults to OFF. With it off, every coordinator — battery, EV charger, appliances — still runs its full 30-second control loop and computes exactly what it *would* do, but no command is ever sent to your inverter, charger, or switches. Nothing physically changes until you flip that switch on.

This gives you a safe window to check that Energy Manager's decisions match what you'd actually want, before it starts steering real hardware.

## What to Watch

- **Battery status** sensor — the live state EM is driving the battery toward (`self_consumption` / `holding` / `solar_charging` / `grid_charging` / `discharging` / `exporting` / `paused_car_priority`), with the full schedule, EMS mode, and fuse headroom in its attributes.
- **EV charger status** sensor — decision mode (forced/scheduled/solar/idle), target amps and phase mode, fuse headroom.
- **Status** sensor, per appliance — the surplus-control decision (`off_no_surplus`, `on_surplus`, `blocked_fuse`, ...) with thresholds, surplus components, and the last command message.
- The five **command sensors** (diagnostic entities on the Energy Manager device): Battery commanded EMS mode, Battery commanded charge limit, Battery commanded discharge limit, Commanded charging current, Commanded phase mode. These publish the exact values EM would send to your hardware.

!!! tip "Check the `dry_run` attribute"
    Every command sensor carries a `dry_run` attribute: `true` while the master Device control switch is off, so you can confirm at a glance that a value is a *would-be* value and not something actually sent to a device.

## Same Pattern for Advanced Features

A couple of optional features layer their own gating on top of this:

- **[Battery → grid export arbitrage](../user-guide/battery-export-arbitrage.md)** is off by default until you set a non-zero *Battery export spread threshold*. Once enabled, export commands still sit behind the same master Device control switch — leave it off to watch the scheduled `export` slots and the `exporting` state across a few price spikes before enabling.
- **[Solar Appliances](../user-guide/solar-appliances.md)** adds a second layer: commands for a given appliance are only sent when *both* the master Device control switch *and* that appliance's own **EM control** switch are ON. Until then, its Status sensor and dry-run messages show exactly what would happen.

Give each feature a few control cycles in observe-only mode before you turn it on — the whole point of this stage is to catch a misconfigured entity or an unexpected threshold before it can move real hardware.

## Turning It On

When you're satisfied with what you're seeing:

1. Turn on the master **Device control** switch — this is the one switch that governs the battery and EV charger.
2. For Solar Appliances, also turn on the **EM control** switch for each appliance you want Energy Manager to actually operate (it defaults to OFF and is restored across restarts, so appliances stay opt-in one at a time).

## Where Next

- Tune thresholds and read what every sensor and entity does in the [User Guide](../user-guide/home-battery.md).
- Don't have a SigenStor inverter or Easee charger? Wire up your own hardware against the command sensor and charger status contracts in [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md).
- Something looking wrong — a sensor stuck unavailable, a decision you can't explain? Check [Repairs & Diagnostics](../reference/repairs-diagnostics.md) for the built-in Repairs issues and the diagnostics snapshot to attach to a bug report.

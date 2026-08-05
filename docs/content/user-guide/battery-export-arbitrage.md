# Battery to Grid Export Arbitrage

Export arbitrage is an opt-in extension of [Home Battery](home-battery.md): instead of only ever using stored energy to cover your own house load, the battery can sell energy back to the grid during extreme price spikes, when the spread is worth more than the wear and reserve you'd be spending.

It's **off by default**. With the *Battery export spread threshold* left unset or at 0, no export slot is ever scheduled and your battery schedule is completely unchanged — export arbitrage adds nothing until you deliberately turn it on.

## Controls

Two number entities govern the feature:

- **Battery export spread threshold** — the price spread above the horizon's cheapest hour at or above which a slot may become an export slot. Like the charge and discharge thresholds, it's a spread rather than an absolute price, so it adapts automatically as the overall price level shifts. Set it to `0` to disable export arbitrage (the default).
- **Battery export reserve level** — a SOC floor (default 20%) below which the battery never sells energy. This is enforced twice: the scheduler's export energy budget only ever plans with energy above the reserve, and at runtime the floor is re-checked every 30 seconds — if the battery's SOC drops to or below the reserve, export stops and the battery falls back to self-consumption immediately, without waiting for the next planning pass. The same fallback happens if the SOC sensor itself becomes unavailable: an unknown SOC must never be treated as "above the floor," so export simply doesn't run.

## Fuse-capped export power

The discharge limit commanded during an export slot is capped at:

```text
(fuse rating − safety buffer) × 3 × 230 V ÷ 1000  →  kW
```

For example, a 20 A main fuse with the default 1 A safety buffer gives roughly a 13.1 kW export ceiling.

A few deliberate properties of this cap:

- **Independent of your inverter's rating** — a plant that can physically discharge well above your fuse's headroom is still held to this ceiling.
- **Per-phase safe** — house load can sit on a single phase while the battery exports balanced across all three, so the cap deliberately doesn't add house load back on top. That keeps every phase within its safe limit regardless of how load is distributed, at the cost of a little export capacity on an otherwise-idle phase.
- **Live PV is subtracted** — PV production is deducted from the export allowance moment to moment, since PV and the battery share the same grid connection on a hybrid inverter.

## Qualification

A slot only becomes an export slot when **both** of these hold:

1. Its spread above the horizon's cheapest hour is at or above the export spread threshold.
2. Selling now genuinely beats buying the same energy back later:

```text
spot price > (cheapest future spot + grid transfer fee + electricity company fee) / 0.9 + battery cycle cost
```

(0.9 is the assumed round-trip efficiency of a charge/discharge cycle.) In other words, export only happens when the price you'd get for the energy now, minus the cost of re-buying and re-storing it later, still comes out ahead after fees and cycle wear.

Export arbitrage never demotes a slot that's already more valuable as a scheduled self-consumption discharge — it only claims price spikes that wouldn't otherwise be used to cover your own house load.

## How it shows up

During an export slot, the [Battery status](home-battery.md#reading-battery-status) sensor reads `exporting`, the **Battery commanded EMS mode** diagnostic sensor reads `command_discharging`, and the **Battery commanded discharge limit** sensor carries the fuse-capped export power computed above.

Because the export power is derived entirely from your fuse rating and safety buffer — not from any SigenStor entity — export arbitrage works even without native SigenStor control, as long as your own automations follow the commanded sensors. See [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md) for the full command sensor contract.

## Observe-only first

Like all device actuation in Energy Manager, export commands sit behind the master **Device control** switch. Before turning it on, it's worth leaving export arbitrage in observe-only mode for a few price-spike events and watching the scheduled `export` slots (and the `exporting` state) show up at the times you'd expect, with the power levels you'd expect, before letting it actually discharge to the grid.

!!! important "Setup precondition: SigenStor backup SOC"
    If you're running native SigenStor control, the inverter's own backup/minimum SOC must be configured at the plant level to **at least** Energy Manager's minimum SOC. This hardware floor is the last line of defense if Home Assistant goes down mid-export — it protects the battery from being drained below your safety margin by a runaway command with no software left to stop it.

    This is documented here as a requirement, not something Energy Manager checks or enforces at runtime. Setting the SigenStor floor correctly is on you.

---

Export arbitrage builds on the [Home Battery](home-battery.md) schedule — head back there for the charge and discharge thresholds it shares. For the complete entity tables (every sensor, number, and switch, with exact names and descriptions), see [Entities](../reference/entities.md).

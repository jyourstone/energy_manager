# Charger Status Contract

Energy Manager's EV logic reads one input more than any other: the charger status sensor. This page documents the exact vocabulary it expects, and how to produce it from a non-Easee charger.

## The status vocabulary

The *Charger status sensor* configured in the [Setup Wizard](../getting-started/setup-wizard.md) is read every control cycle and must report one of the Easee integration's status strings:

| State | How EM uses it |
|-------|-----------------|
| `charging` | The car is actually drawing power — confirms that a start/resume command took effect. EM also infers "drawing" independently from measured charger power, so this state isn't the only signal it trusts |
| `awaiting_start` | Car connected and waiting; counts as "car connected" for unrecognized-car fallback detection |
| `paused` | Charging paused — EM **resumes** (rather than starts) from here |
| `disconnected` / `completed` / `error` | Terminal: the controller resets to idle and commands `0` A |

!!! note "Physics beats status"
    A terminal status (`disconnected` / `completed` / `error`) is only trusted once measured charger power has actually dropped below 0.5 kW. If the status sensor reports a terminal state while the charger is still visibly drawing power, Energy Manager keeps fuse supervision engaged and logs a warning instead of walking away from a charging session that hasn't actually stopped.

`unavailable`, `unknown`, and a missing entity are all treated as `disconnected`.

## Mapping a non-Easee charger

With any other charger, map its native states onto this vocabulary with a [template sensor](https://www.home-assistant.io/integrations/template/) and select that template sensor as the *Charger status sensor* in the EV Charging step:

```yaml
template:
  - sensor:
      - name: "Wallbox status for Energy Manager"
        state: >-
          {{ {
            "Idle": "disconnected",
            "Connected": "awaiting_start",
            "Charging": "charging",
            "Paused": "paused",
            "Finished": "completed",
            "Error": "error",
          }.get(states("sensor.my_wallbox_status"), "disconnected") }}
        attributes:
          config_phaseMode: 3
```

Any state your charger reports that isn't in the mapping dictionary falls back to `disconnected` via the template's `.get(..., "disconnected")` default — keep the fallback so an unexpected native state never gets stuck reporting stale data.

## The `config_phaseMode` attribute

The `config_phaseMode` attribute is optional. When present, Energy Manager reads it from the status sensor to learn the charger's *current* phase wiring, using the raw Easee convention:

- `1` = single-phase
- Anything else — or the attribute missing entirely — is treated as three-phase

This only tells EM what the charger is *currently* wired to; it doesn't request a phase switch. Phase-switch commands are published separately on the [Commanded phase mode](command-sensors.md) sensor, which your own automation follows if your hardware supports switching.

!!! tip "Fixed-phase installations"
    A charger that's wired three-phase and never switches can omit the attribute entirely — EM already defaults to three-phase. If your charger is single-phase-only, set `config_phaseMode: 1` explicitly in the template; the default only covers three-phase, it won't infer single-phase wiring on its own.

See [Command Sensors](command-sensors.md) for the rest of the command-sensor contract, and [Worked Examples](examples.md) for full automation YAML.

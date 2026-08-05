# Worked Examples

Full automations that follow the [command sensors](command-sensors.md) for a battery and an EV charger that aren't SigenStor/Easee. Adapt the `action:` calls to your own hardware's services — the trigger and condition are the reusable part.

!!! tip "Check your entity IDs"
    Entity IDs follow the entity's name at creation time and your Home Assistant language, so yours may differ from the `sensor.energy_manager_*` IDs below — copy the real IDs from the Energy Manager device page before pasting these automations.

## Battery: follow the commanded charge limit

The commanded charge limit's state is in kW — convert if your inverter's entity takes W.

```yaml
automation:
  - alias: "EM: follow battery commanded charge limit"
    triggers:
      - trigger: state
        entity_id: sensor.energy_manager_battery_commanded_charge_limit
    conditions:
      - condition: template
        value_template: >-
          {{ trigger.to_state.state not in ('unknown', 'unavailable')
             and not trigger.to_state.attributes.get('dry_run', true) }}
    actions:
      - action: number.set_value # your inverter's charge-power limit (assumed to take kW here)
        target:
          entity_id: number.my_inverter_max_charge_power
        data:
          # Clamp to your inverter's own maximum -- EM does not know your
          # hardware's rating (5.0 kW here as an example).
          value: "{{ [trigger.to_state.state | float, 5.0] | min }}"
```

!!! warning "Always clamp to your own hardware's maximum"
    Energy Manager does not know your inverter's rated power — it only knows the fuse limit and, if configured, the SigenStor entity's own reported maximum. Clamp with `min()` against your hardware's real ceiling, as above, or a hardware-neutral automation could ask for more than the inverter can deliver.

## EV: follow the commanded current

`0` means pause/stop. Values above `0` but below the 6 A minimum are an intentional dead zone — EM's own state machine never starts a session that low, so there's deliberately no branch for it below: leave the charger exactly as it is.

```yaml
automation:
  - alias: "EM: follow EV commanded current"
    triggers:
      - trigger: state
        entity_id: sensor.energy_manager_commanded_charging_current
    conditions:
      - condition: template
        value_template: >-
          {{ trigger.to_state.state not in ('unknown', 'unavailable')
             and not trigger.to_state.attributes.get('dry_run', true) }}
    actions:
      - choose:
          # 0 = pause/stop
          - conditions:
              - condition: template
                value_template: "{{ trigger.to_state.state | float(0) == 0 }}"
            sequence:
              - action: switch.turn_off # your charger's charging/pause switch or stop service
                target:
                  entity_id: switch.my_charger_charging
          # >= 6 = charge at (up to) this current
          - conditions:
              - condition: template
                value_template: "{{ trigger.to_state.state | float(0) >= 6 }}"
            sequence:
              - action: number.set_value # your charger's current-limit entity or set-current service
                target:
                  entity_id: number.my_charger_dynamic_limit
                data:
                  value: "{{ trigger.to_state.state | float | round(0, 'floor') }}"
              - action: switch.turn_on
                target:
                  entity_id: switch.my_charger_charging
        # no branch for 0 < value < 6: below the charger minimum, do not start
```

## The shared pattern

Both automations use the same skeleton, and it's the one to copy for any other [command sensor](command-sensors.md#the-five-command-sensors):

- **Trigger:** `state` on the command sensor's entity ID.
- **Condition:** a template guard that skips the run while the sensor is `unknown` or `unavailable` — before Energy Manager's first compute, or if a coordinator hasn't populated data yet — and while the sensor's `dry_run` attribute is `true`, so your automation respects the master **Device control** switch exactly like the native SigenStor/Easee paths do.
- **Action:** a `choose:` block (or a single action, for sensors with only one meaningful value) that maps each state to your hardware's own service call.

```yaml
conditions:
  - condition: template
    value_template: >-
      {{ trigger.to_state.state not in ('unknown', 'unavailable')
         and not trigger.to_state.attributes.get('dry_run', true) }}
```

Reuse that condition verbatim for the commanded EMS mode and commanded discharge limit sensors too — every command sensor can be `unknown` before Energy Manager's first calculation, and every one carries the `dry_run` attribute. Without the `dry_run` guard, your automation would actuate hardware while Energy Manager is still in [observe-only mode](../getting-started/first-days.md).

# Phase 09 — Generic EMS / EV charger support (command sensors)

Owner decision (2026-08-05): support non-Sigenergy EMS and non-Easee chargers by
exposing Energy Manager's *commanded* values as sensors. Users with other
hardware build their own HA automations that trigger on these sensors and call
their hardware's services themselves. EM stays the planning brain; actuation is
the user's automation. No generic actuator adapter in this phase (that would be
the v2 upgrade path if sensor-driven automations prove insufficient).

## What already works (verified in code, do not re-derive)

- All *input* entities are generic pickers (SoC, battery power, grid phases,
  PV power, house consumption, charger power) — any integration works.
- EMS control entities (`ems_select_entity`, `charge_limit_entity`,
  `discharge_limit_entity`) and `charger_device_id` are `vol.Optional` in the
  flow; every send path skips cleanly when unconfigured
  (`_send_charge_limit`/`_send_discharge_limit` return False,
  `_send_charger_command` guards on empty device_id at coordinator.py:3391).
- `EaseeData` already carries `mode`, `target_amps`, `target_phase_mode`,
  `sequence_state`, `dry_run`, `override_reason`, `charger_status`.
- `EMSData` carries `target_mode`, `charge_limit_kw`, `charge_limit_delivered`,
  `discharge_allowed`, `discharge_gate_reason` — but NOT the computed
  `target_discharge_limit` (local var, coordinator.py:1855-1871).
- `sensor.battery_commanded_charge_limit` exists (CommandedChargeLimitSensor,
  sensor.py:613) — the pattern to mirror.
- Charger status vocabulary consumed by the state machine
  (charger_state_machine.py): `disconnected`, `awaiting_start`, `charging`,
  `paused`, `completed`, `error` (TERMINAL_STATUSES + CHARGING_STATUS).
  `config_phaseMode` attribute is read from the status entity and falls back to
  `"three"` when absent.

## Requirements

- **GEN-01 — Battery commanded EMS mode sensor.** `battery_commanded_mode`
  (translation_key), diagnostic, enum-style states from `EMSData.target_mode`
  (`max_self_consumption` / `command_charging`; use the actual internal mode
  strings found in code). Attributes: `override_reason`, `car_override_active`,
  `pv_charging_active`, `dry_run`, `command_verified`. Unknown when EMS data
  is None (same honesty rule as battery_status).
- **GEN-02 — Battery commanded discharge limit sensor.**
  `battery_commanded_discharge_limit`, kW, POWER device class, diagnostic —
  mirrors CommandedChargeLimitSensor. Requires new `EMSData` fields:
  `discharge_limit_kw: float | None` and `discharge_limit_delivered: bool`
  (mirror the charge-limit delivered tracking:
  `self._last_sent_discharge_limit == target`). If `target_discharge_limit`
  computation turns out to be gated on `discharge_limit_entity` being
  configured, hoist it so the commanded value is computed regardless — the
  whole point is publishing intent without the hardware entity.
  Attributes: `discharge_allowed`, `discharge_gate_reason`, `dry_run`,
  `discharge_limit_delivered`.
- **GEN-03 — EV commanded current sensor.** `ev_commanded_current`, unit A,
  CURRENT device class, diagnostic, state = `EaseeData.target_amps`.
  Contract: `0` = charging should be paused/stopped; `> 0` = charging should
  run at (up to) this limit. Attributes: `charger_mode` (mode),
  `target_phase_mode`, `sequence_state`, `dry_run`, `override_reason`,
  `charger_status`. Only created when the Easee coordinator exists (same
  gating as EaseeChargerStatusSensor, sensor.py:675).
- **GEN-04 — EV commanded phase mode sensor.** `ev_commanded_phase_mode`,
  diagnostic, states `single` / `three` from `EaseeData.target_phase_mode`.
  Separate sensor so automations get clean state triggers for phase switching.
- **GEN-05 — README "Bring your own hardware" section.** Documents:
  the concept (leave control entities empty / Device control OFF, automate on
  command sensors); a contract table (sensor → meaning → what your automation
  should do); the charger-status template-sensor recipe with the exact state
  vocabulary above as EM's documented contract (map your charger's states to
  it; optionally expose `config_phaseMode` attribute); one worked example
  automation for the battery side (commanded charge limit → your inverter
  service) and one for the EV side (commanded current → your charger service);
  the 30 s update cadence caveat. Also: soften the `charger_device_id`
  form description ("leave empty for non-Easee chargers — commands are then
  never sent and you automate on the command sensors instead").
- **GEN-06 — Translations + tests.** strings.json + en.json + sv.json for all
  four new keys (key-order-preserving python edits, existing style:
  "Batteriets beordrade laddgräns" naming family). Tests: new EMSData field
  defaults + delivered-flag logic; `_ENTITY_KEYS` additions in
  test_options_flow_support.py; state-mapping tests for the two EV sensors
  where a pure function exists to test (keep sensor classes thin, logic in
  coordinator/pure modules).

## Non-goals (v2 candidates)

- Generic actuator adapter (pick number/switch entities for charger control,
  configurable EMS select option strings) — only if the automation route
  proves too weak.
- Events/services API. Sensors only, per owner decision.
- Any change to the Easee/Sigen control paths themselves.

## Conventions for implementers

- ruff==0.16.0 via `uvx ruff@0.16.0 check`; repo is NOT ruff-format-clean —
  never reformat pre-existing files, only files you create/edit.
- Translation JSON edits via key-order-preserving python scripts.
- Tests: `python3 -m pytest tests/` from repo root (HA stubs in conftest).
- Sensor classes stay thin; derive/compute in coordinator or pure modules.
- EN + SV translations mandatory; strings.json is the source of truth.

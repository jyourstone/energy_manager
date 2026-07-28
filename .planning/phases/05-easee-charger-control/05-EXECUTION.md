# Phase 5 Execution Plan (2026-07-28)

Executed as three sequential agent waves + live verification, following 05-RESEARCH.md.
Waves map to the roadmap plans: A = 05-01+05-02, B = 05-03, C = 05-04, verification = 05-05.

## Wave A — pure `charger_state_machine.py` + exhaustive tests

Zero HA imports, frozen dataclasses, same pattern as ems_controller.py. One
`ChargerInputs` snapshot per tick in; `ChargerDecision` out (commands to send,
updated state, status fields). Stateful trackers (hysteresis, sequence state)
as small classes owned by the coordinator (PVHysteresisTracker/ESSLimitRateLimiter
precedent).

Behavior (tuned constants from live AppDaemon, all become options in Wave B):
- Mode arbitration: forced > scheduled > solar > idle. Scheduled = active car
  slot AND home+plugged. Solar = internal state w/ SOC gate (default 100%,
  ceil-round option), start threshold 1.5 kW net (minus 0.5 kW safety),
  activation delay 300 s, deactivation delay 60 s (NEW, AppDaemon was instant).
- Grid amp target: wanted 16 A capped by fuse headroom
  available = (fuse − buffer) − ceil(worst_signed_phase_amps) + current_easee_amps
  (add back charger's own draw); grid power ceiling 12 kW − 0.5 kW.
- Solar amp target: floor(available_kW × conversion_factor), clamp [6, 16].
- Conversion factors A/kW: 1-phase 4.3, 2-phase 2.5, 3-phase 1.45; per-car
  phase capability from subentry (default 3).
- Amp hysteresis: increase delay 120 s / decrease 5 s; pending increase
  re-validated each tick against fresh headroom, always keep the LOWER pending;
  decrease cancels pending increase. Never lengthen the decrease path.
- Phase switching: threshold 4.1 kW on available power; explicit sequence
  states (PAUSING → SET_PHASE → RESUMING → SET_LIMIT) with per-state entry
  timestamps and timeouts; fuse re-verified before resume AND before limit;
  insufficient → abort to paused.
- Fuse layers: (1) emergency pause at fuse+2 A while charging (+ notify);
  (2) headroom-based target; (3) 0A-target safety stop: target 0 but measured
  charger power > 0.5 kW → pause + notify; pre-start gate: capacity ≤ 0 →
  proactively set 0 A; 0 < capacity < 6 A → do not start.
- Unauthorized-charge suppression: no mode authorized AND (status charging OR
  charger power > 0.5 kW) → stop.
- Status is UNRELIABLE (live watchdog evidence): cross-check with charger
  power; stuck-state detection (command sent, no observable effect within
  timeout → recovery action + status flag).
- Disconnect/terminal states (disconnected/completed/error): clear internal
  state, cancel pendings, no limit adjustments.

## Wave B — EaseeCoordinator + config flow + observe-only extension

- EaseeCoordinator: ~30 s poll + state-change listeners (charger status,
  charger power, force switch); builds ChargerInputs (reusing the EMS fuse
  reads — ONE shared headroom source; charger view adds back charger current),
  calls pure module, executes commands.
- Command executor: easee services (easee.action_command start/pause/resume/
  stop, easee.set_charger_dynamic_limit, easee.set_charger_phase_mode) — exact
  schemas read from dev/config/custom_components/easee (services.yaml). ALL
  calls go through the existing build_command_decision choke point (observe-only
  master switch suppresses; dry_run surfaced on the charger status sensor).
- Config flow EV step additions (defaults = tuned values): min/max charge amps
  6/16, max grid charge power 12 kW, increase/decrease delays 120/5 s, phase
  switch threshold 4.1 kW, solar start 1.5 kW, solar activation/deactivation
  delays 300/60 s, battery SOC gate 100%, emergency margin 2 A, notify target
  (optional notify.* service — safety notifications ALWAYS sent when
  configured, even in observe-only, since they report real measured
  conditions), charger id/device for service calls (auto-detected from the
  Easee integration), house consumption entity + optional excluded power
  entities (EMS-13) for the solar-surplus calc.
- Per-car phases option (1/2/3, default 3) added to the car subentry flow (EV-12).

## Wave C — entities + solar surplus + translations

- Charger status sensor (state = controller mode; attrs: target amps, actual
  amps, phase mode, sequence state, stuck flag, dry_run, last command).
- Force-grid-charging switch (EASE-03; replaces input_boolean.easee_force_charging).
- EV-09 solar surplus wiring: surplus = pv − house_consumption − max(battery
  charging, 0) + charger power (live formula), minus EMS-13 exclusions;
  PVHysteresisTracker; replaces the solar_surplus_available=False stub.
- EN + SV translations for everything; README feature/status update.

## Verification (05-05)

Dev docker instance, observe-only ON (control switch OFF): wizard re-run,
charger decisions computed and suppressed, dry_run attrs on charger sensor,
zero easee service calls in log; simulated slot transitions observed.
Cutover checklist doc for live migration (disable 5 AppDaemon apps, remove
dead/reset automations, retire Easee watchdog).

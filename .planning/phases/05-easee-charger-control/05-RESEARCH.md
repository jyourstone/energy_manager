# Phase 5: Easee Charger Control - Research

**Researched:** 2026-07-28 (full-system audit: 8-agent workflow over AppDaemon source + live HA + integration code; owner decisions folded in)
**Confidence:** High — every claim below verified against the live system or the AppDaemon source, not assumed.

## Summary

Phase 5 ports the AppDaemon `easee_controller.py` (1,583 lines, the largest and most battle-tested app) into the integration: physical Easee charger control with mode arbitration, dynamic amp limits, 1/2/3-phase switching, solar-surplus charging, and fuse protection. The AppDaemon original works but has one fatal architectural flaw for HA porting — parallel `run_in` timers with 4-5 s sleeps choreographing pause/switch/resume sequences — which must become a non-blocking async state machine evaluated on coordinator ticks. This phase also delivers observe-only mode (CORE-14), required before the integration is ever installed next to the live AppDaemon system.

## Verified Facts (from live HA + AppDaemon source)

- **Easee control surface**: HA `easee` integration services confirmed present in live HA: `easee.action_command` (start/pause/resume/stop), `easee.set_charger_dynamic_limit`, `easee.set_charger_phase_mode` ('1_phase'/'3_phase'). No pyeasee dependency needed. Charger: "easee_home_25562".
- **Easee status is unreliable**: owner runs a production watchdog automation (`ladda_om_easee_konfiguration_om_den_hangt_sig`) that reloads the Easee config entry every 5 min when status is stuck while power > 0.5 kW. The state machine MUST cross-check status against measured charger power and carry stuck-state timeouts (EASE-06). At cutover the watchdog automation should be retired in favor of integration-native recovery.
- **Charger's own current must be excluded from its house-load input**: the live "minus Easee" template chain (`highest_sigen_phase_current − sensor.easee_home_25562_current`) exists precisely so the charger does not count its own draw against its own headroom. Battery-side EMS math (Phase 4.1) keeps the charger's draw as real load; charger-side math adds back the charger's own contribution (AppDaemon pattern: `available = effective_limit − ceil(highest_l_current) + current_easee_dynamic_amps`).
- **Solar surplus formula (live)**: `pv_power − consumed_power − max(battery_power, 0) + easee_power` — adds charger draw back since house consumption includes it. House-load filtering excludes owner's water heater (Power Saver-controlled) → EMS-13 exclusion list feeds this.
- **Unauthorized charging**: Easee auto-starts when a car plugs in; AppDaemon suppresses it (two detectors: status-transition and power-based). Must port.
- **Tuned constants (production, ~1 year of tuning; become option defaults per EASE-09)**:
  - amp increase delay 120 s / decrease delay 5 s (never lengthen decrease)
  - phase switch threshold 4.1 kW
  - conversion factors: 1-phase 4.3 A/kW, 2-phase 2.5, 3-phase 1.45 (fallback reverse: 3p P=0.69·I, 1p P=0.23·I)
  - min/max charge amps 6/16 A (Easee minimum is 6 A per phase)
  - max grid charge power 12 kW, general safety buffer 0.5 kW
  - solar start threshold 1.5 kW, activation delay 300 s, battery SOC gate 100% (default; option per EASE-07); deactivation delay NEW option (AppDaemon stops instantly on one cloudy sample — costs a 5-min restart; default a small symmetric delay, e.g. 60 s, owner-tunable)
  - emergency overload margin +2 A over fuse limit → Swedish push notification + pause
- **Per-car phase capability (EV-12, owner decision)**: subentry option 1/2/3 phases, default 3. ID.3 charges on 2 phases (confirmed by owner). The old sensor-based override (`sensor.id_3_home_and_plugged_in`) is dead in live HA — do NOT port the sensor pattern; use the subentry option.
- **Mode arbitration priority (port as-is)**: forced > scheduled > solar > idle. Forced = new switch entity (EASE-03, replaces `input_boolean.easee_force_charging`). Scheduled = car coordinator's active slot AND home+plugged (Phase 4.1 wiring). Solar = internal state with SOC gate + hysteresis.
- **Fuse protection layers (port all three)**: (1) emergency pause at fuse+2 A while charging; (2) headroom-based limit calc; (3) 0 A-target safety stop (Easee sometimes ignores a 0 A dynamic limit — if target 0 but still drawing > 0.5 kW → pause + notify). Plus pre-start gate: capacity ≤ 0 → set 0 A proactively; 0 < capacity < 6 A → don't start (avoids start/stop loop below Easee's 6 A minimum).
- **Phase-switch choreography (the dangerous part)**: pause → set_charger_phase_mode → resume → set limit, with fuse re-verification before resume AND before limit (a ~15 s vulnerable window). AppDaemon uses blocking-ish sleeps; integration must model this as explicit state-machine states with per-state entry timestamps and timeout recovery.

## Architecture

Follow the established pattern exactly:

- **`charger_state_machine.py`** — NEW pure-Python module (zero HA imports, frozen dataclasses). Owns: mode arbitration, target amp calculation (grid + solar branches), per-car phase conversion, hysteresis timers (as timestamp comparisons, no sleeps), phase-switch sequence states, stuck-state detection, fuse layers 2+3, unauthorized-charge detection. Input: one `ChargerInputs` snapshot per tick; output: `ChargerDecision` (list of commands + new state + status fields). Fully unit-testable.
- **`EaseeCoordinator`** — thin DataUpdateCoordinator (~30 s poll + state-change listeners on charger status/power and force switch), reads sensors, builds `ChargerInputs`, calls the pure module, executes returned commands via easee services, publishes status. Chained after EMSCoordinator so both draw from ONE fuse-headroom source.
- **Shared fuse arbiter**: single headroom computation (Phase 4.1's signed worst-case + sensor-fail behavior) feeds both ESS limit and charger amps. Battery yields to car per EMS-03 (already wired); the arbiter must subtract the CHARGER's draw for battery math and add back the charger's own contribution for charger math — one module, two views, no duplicated formulas.
- **Observe-only mode (CORE-14)**: a master `switch` entity (default OFF = observe) gating ALL service calls in BOTH EMSCoordinator and EaseeCoordinator at the command-execution boundary (one choke point, not scattered ifs). Decisions still computed and published to status sensors with a `dry_run: true` attribute so behavior is verifiable in parallel with AppDaemon before cutover.
- **New entities**: charger status sensor (mode, target amps, actual amps, phase mode, state-machine state, last command, dry_run), force-charging switch (EASE-03), master enable switch (CORE-14). Notify target config option (EASE-08) — safety events call the configured notify service.

### Anti-Patterns to Avoid (inherited + phase-specific)

- No `asyncio.sleep` choreography — every multi-step sequence is a state with entry timestamp, evaluated on ticks
- No trusting Easee status alone — always cross-check with charger power
- Do not port: `or 10`/`or 0` inconsistent fallbacks (Phase 4.1 fail-behavior option covers this), the `_py` sensor naming, file-based log rotation, the sensor-based 2-phase override, AppDaemon's future_after last-slot quirk
- Never lengthen the 5 s decrease path; hysteresis asymmetry is a safety property, not a tuning preference

## Common Pitfalls

1. **Event-loop blocking**: highest-risk conversion (research PITFALLS.md Pitfall 1). The AppDaemon 4-5 s parallel delays MUST become state-machine states.
2. **Mid-sequence reload**: if the user reloads the entry (or the legacy watchdog fires) during a phase-switch sequence, the charger may be left paused. `async_shutdown` must attempt a safe park (resume or explicit pause + log), and startup must reconcile actual charger state before assuming any mode.
3. **Command race with AppDaemon**: until cutover, AppDaemon controls the charger. Observe-only default prevents fights; the master switch must ship OFF by default.
4. **Easee 6 A minimum**: targets in (0, 6) A are invalid — pre-start gate and limit clamping must respect this per phase mode.
5. **Slot-boundary churn**: schedule slot transitions at 15-min boundaries + hysteresis interplay — increase delay applies across slot boundaries; decrease is immediate.

## Plan Breakdown (proposed)

- **05-01 (TDD)**: `charger_state_machine.py` pure module — arbitration, amp calc, conversions, hysteresis, fuse layers, unauthorized detection + exhaustive tests (QUAL-01 seed)
- **05-02 (TDD)**: phase-switch + start/stop sequence states, stuck-state timeouts, power cross-checks + tests
- **05-03**: EaseeCoordinator + command executor + observe-only master switch + config flow step (charger entities auto-detect, constants as options, notify target) + shared fuse arbiter refactor
- **05-04**: entities (charger status sensor, force switch, master switch), translations (EN+SV), wiring solar surplus (EV-09: replace `solar_surplus_available=False` stub using live formula + PVHysteresisTracker + SOC gate + EMS-13 exclusions)
- **05-05**: UAT prep — observe-only parallel-run checklist vs live AppDaemon, cutover checklist doc (disable 5 apps, remove dead/reset automations, retire watchdog)

## Open Questions (for owner, non-blocking)

- Solar deactivation delay default: 60 s suggested (AppDaemon = instant). Confirm or tune during UAT.
- Emergency notification wording: keep Swedish message text as default template?

# APPL: Solar-Surplus Appliance Control — Plan

**Status: APPROVED FOR BUILD (owner, 2026-08-03) — build during parallel run, ships
observe-only and disabled by default.** Sequencing exception to the post-cutover
convention is deliberate: the module actuates nothing until both the CORE-14
Device control master switch AND the per-appliance control switch are ON.

## Owner decisions (2026-08-03 session, all confirmed)

1. **Coexist with Power Saver — no coupling.** PS keeps cheap-hours scheduling
   for guaranteed runtime (proven in prod). EM adds what PS cannot do:
   opportunistic switching on *solar surplus*. No EM↔PS integration, no shared
   state. For a PS-managed load the user simply points EM's actuator picker at
   PS's override switch (e.g. `switch.heater_power_saver_always_on`) — a README
   recipe, not code. EM never touches a relay another integration reconciles
   (single-writer rule).
2. **Surplus-only mode.** No cheap-hours scheduling in EM's appliance module —
   that is PS's job. Consequence: no slot scheduler, no daily runtime targets,
   no max-off-gap logic. This is an April–September feature in SE4; PS covers
   the year-round guarantee.
3. **Generic actuator picker.** Each appliance = any `switch.*` /
   `input_boolean.*` entity chosen by the user. Which physical thing that
   controls (relay, PS override, smart plug) is the user's decision.
4. **Expected load + %-based defaults.** User enters rated power (W); on/off
   thresholds default to percentages of it, all overridable per appliance.
5. **Switch-only actuators in v1.** `climate.*` (Sensibo heat pump, floor
   thermostats) deferred to v2 — needs hvac_mode/target-temp handling and
   state restore.
6. **Optional per-appliance power sensor.** If set, credit-back uses measured
   draw and a "switched on but idle" diagnostic becomes possible; falls back
   to rated W.

## Goal

Turn user-selected switch loads ON when measured solar surplus (grid export)
exceeds the load's rated draw with margin, and OFF when the surplus disappears —
with per-appliance priority allocation and anti-short-cycling protection, so a
water heater or heat-pump plug is never damaged by rapid on/off cycling.

"After battery and cars are charged" comes free from the signal choice: the
SigenStor absorbs surplus into the battery until full/at-limit, and Easee solar
charging consumes it for the car — grid export only appears once everything
upstream is satisfied. No arbitration code against battery or cars is needed.

## Requirements

| ID | Requirement |
|---|---|
| APPL-01 | New module flag `CONF_APPLIANCES_ENABLED` in the modules step, default **False** (CORE-12/13 modularity — standalone, independent of battery/EV modules) |
| APPL-02 | New subentry type `appliance` (parallel to `car`): add/reconfigure/remove via config flow, per-appliance HA device via `via_device=hub` |
| APPL-03 | Surplus signal = measured grid export **minus battery discharge power** (BATT-17 guard, see below), never computed PV-minus-loads |
| APPL-04 | Priority allocation: appliances evaluated in priority order (1 = highest); each admitted appliance consumes its (measured or rated) draw from the pool; credit-back of own draw before its own keep-on comparison |
| APPL-05 | Anti-short-cycling, per-appliance configurable (SEM #688 lesson): ON-sustain delay, OFF-sustain delay, `min_on_minutes`, `min_off_minutes` |
| APPL-06 | Fuse admission: appliance turns ON only if its rated amps fit live measured headroom minus safety margin (worst phase; 1-phase loads charged against worst phase in v1) |
| APPL-07 | All actuation through a CORE-14-gated send site (`build_command_decision` + `_read_control_enabled` pattern) AND a per-appliance "EM control" switch, RestoreEntity, default **OFF** |
| APPL-08 | Per-appliance status sensor (enum + diagnostic attributes) so every decision is explainable |
| APPL-09 | Restart-safe: trackers re-seed from actual actuator state with `last_transition = now` (freeze, never flip); declarative re-assert every tick, no one-shot commands (07-RESEARCH.md:66-70 lesson) |
| APPL-10 | EN + SV translations, README section with the PS-override recipe, HACS-end-user-comprehensible config (≤ 10 fields) |

## Non-goals (v1)

- Cheap-hours / price-based appliance scheduling (PS's job)
- `climate.*` / SG-Ready / variable-power (`number.*`) actuators
- Cross-priority with cars (cars implicitly always win via the export signal)
- Shedding a running appliance on fuse pressure beyond min_on expiry
  (admission-only gate; battery + Easee react to measured amps and back off —
  documented limitation for installs without either)
- Runtime-mutable number/select entities for thresholds (static subentry
  fields; reconfigure flow reloads the entry — tune there)

## Surplus signal (the core formula)

```
export_kw            = -grid_power_kw                  # measured SIGNED grid flow (negative = importing)
battery_discharge_kw = max(0, battery_discharge_power) # from configured battery power entity
raw_surplus_kw       = export_kw - battery_discharge_kw   # SIGNED — goes negative on import

# allocation pool: credit back what EM's own appliances already consume
pool_kw = raw_surplus_kw + Σ draw_kw(appliance ON via EM)   # measured if sensor set, else rated
```

**The surplus signal is deliberately signed** (as-built decision, 2026-08-03):
with a clamped-at-zero signal and rated-power credit-back (no power sensor
configured), an ON appliance's pool would floor at rated kW and could never
cross below `off_threshold_pct ≤ 100` — the 110/90 release band would be dead
code. Signed export makes import visible to the release comparison, which is
exactly what the band's "~10% of rated import tolerance" requires.

- **BATT-17 guard is mandatory:** when export arbitrage discharges the battery
  into the grid at spike prices, grid export is NOT solar surplus. Without the
  subtraction the VVB would eat arbitrage revenue at 4.2 kW. EM has both
  signals internally — this is exactly the bug a standalone surplus controller
  cannot avoid.
- **Battery charging needs no term:** while the battery absorbs PV below its
  charge limit, export ≈ 0 and appliances stay off — the desired "battery
  first" ordering, encoded by physics.
- **Cars need no term:** Easee solar charging consumes surplus before it
  reaches the meter. Car not plugged → export appears. No car-state
  special-casing (owner pushback accepted 2026-08-03).
- **Credit-back matters because of EMS-13:** the VVB power sensor sits in
  `CONF_EXCLUDED_POWER_ENTITIES`, so house load doesn't move when it runs —
  but the *meter* does. Crediting each EM-ON appliance's draw back into the
  pool turns the feedback loop into deterministic arithmetic (PV-Excess-Control
  idiom) and kills self-eating flap.
- Implementation-time verification: exact source for `grid_power_kw` — reuse
  the signed per-phase readings `FuseSensorReader` already consumes
  (Σ phases × 230 V) or a configured grid power entity, whichever the config
  already carries. Do not add a new required config field if the signal is
  already present.

## Data model

Subentry `data` (static; edit via reconfigure → entry reload):

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | — | subentry title, e.g. "Varmvattenberedare" |
| `switch_entity` | entity selector (switch, input_boolean) | — | the actuator EM toggles |
| `rated_power_w` | int | — | expected load, e.g. 4200 |
| `phases` | 1 \| 3 | 3 | `rated_amps = rated_power_w / (230 × phases)` |
| `power_sensor_entity` | entity selector (sensor, power) | none | optional; measured credit-back + idle diagnostic |
| `priority` | int 1–10 | 5 | 1 = highest; ties broken by subentry insertion order |
| `on_threshold_pct` | int | 110 | ON when pool ≥ rated × pct/100, sustained |
| `off_threshold_pct` | int | 90 | OFF when pool < rated × pct/100, sustained (band 110/90 ⇒ tolerates ~10% of rated as import before release) |
| `on_sustain_minutes` | int | 5 | export must persist before ON (cloud filter) |
| `off_sustain_minutes` | int | 15 | deficit must persist before OFF (asymmetric on purpose — slow release) |
| `min_on_minutes` | int | 15 | hard floor once ON (resistive-friendly default) |
| `min_off_minutes` | int | 5 | hard floor once OFF (heat-pump users raise this) |

All threshold/timing fields (`on_threshold_pct`, `off_threshold_pct`, both
sustain times, `min_on_minutes`, `min_off_minutes`) are user-configurable per
appliance (owner requirement 2026-08-03) — defaults pre-filled, editable in the
add and reconfigure flows. Form shows ≤ 10 fields by collapsing them under an
"advanced" section if the subentry flow supports sections cleanly, defaults
visible otherwise. No auto-calibration of rated power in v1.

## Entities (per appliance device)

1. `switch.<name>_em_control` — "EM controls this load". RestoreEntity,
   default OFF. The hand-over valve: nothing moves until the user deliberately
   enables each load (on top of the CORE-14 master switch).
2. `sensor.<name>_status` — enum:
   `disabled` / `off_no_surplus` / `waiting_on_sustain` / `on_surplus` /
   `holding_min_on` / `blocked_min_off` / `blocked_fuse` / `blocked_priority` /
   `actuator_unavailable` / `on_external` (actuator on but not commanded by
   EM — left alone).
   Observe-only is an **attribute** (`observe_only: true/false`), not a state
   (as-built decision: during the observe-only soak the at-a-glance value must
   show the *decision* EM would make, not a blanket `observe_only`).
   Attributes: observe_only, reason, allocated_kw, raw_surplus_kw, export_kw,
   battery_discharge_kw, threshold_on_kw, threshold_off_kw, measured_power_w
   (if sensor set), idle_while_on (drawing <10% of rated while commanded on),
   last_command_message (dry-run or actual).

No other entities. Hub gets nothing new.

## Architecture

- **One `ApplianceCoordinator` for all appliance subentries** (unlike per-car
  coordinators — the priority allocation walk wants a single loop). 30 s tick,
  matching EMS/Easee. Field in `EnergyManagerData`; defensive
  `getattr(entry, "runtime_data", None)` reads (coordinator.py:2083 pattern).
  Standalone: must work with battery and EV modules disabled (reads whatever
  power signals the config carries).
- **Pure decision module `appliance_controller.py`** — zero HA imports.
  `MinCycleGuard` tracker (same shape as `SolarActivationTracker`,
  charger_state_machine.py:495-546) + sustain trackers + the allocation walk
  as pure functions. This is the tested surface.
- **Send site**: one per-tick reconcile that compares desired vs actual state
  and calls `homeassistant.turn_on` / `homeassistant.turn_off` (domain-agnostic
  — `switch.turn_on` on an `input_boolean` is a silent no-op), wrapped in
  `build_command_decision` (ems_controller.py:582-608 pattern) exactly like the
  four existing send sites. Observe-only produces the same dry-run messages.

## Decision algorithm (per 30 s tick)

```
pool = raw_surplus + Σ credit-back of EM-ON appliances
for appliance in sorted(by priority, then insertion order):
    if not module_enabled or not em_control_switch: status=disabled; continue
    if actuator unavailable/unknown: status=actuator_unavailable; continue (no command)
    if ON via EM:
        if pool >= rated*off_pct: stay ON; pool -= draw; continue
        if within min_on: status=holding_min_on; stay ON; pool -= draw; continue
        if deficit sustained >= off_sustain: turn OFF (start min_off) else stay ON
    else:
        if within min_off: status=blocked_min_off; continue
        if pool < rated*on_pct: status=off_no_surplus; continue
        if surplus sustained < on_sustain: status=waiting_on_sustain; continue
        if rated_amps > live_headroom - margin: status=blocked_fuse; continue
        turn ON (start min_on); pool -= rated_kw
```

Manual toggles by the user are respected: EM only reconciles state *it* decided
(desired-state comparison, not blind re-assert of OFF). An appliance turned on
manually outside EM is left alone and its draw naturally shrinks the measured
export — no special handling.

## Restart / reload semantics

- Trackers live in-memory. On coordinator start, seed from the actuator's
  actual state with `last_transition = now` — worst case a state change is
  *delayed* by one min_on/min_off window, never flipped. Benign by design.
  As-built details: an ON actuator with EM control enabled is *adopted*
  (`em_commanded_on=True, last_on_ts=now`); an OFF actuator seeds
  `last_off_ts=now` so min_off survives restarts/reloads too; adoption is
  deferred while the actuator is still `unavailable` (late-connecting
  Zigbee/Shelly integrations) so a late-arriving ON state is adopted, not
  stranded as external. EM turn-offs set a `release_pending` flag that
  re-issues the command every tick until the actuator is *observed* off —
  a single lost service call can never strand a load ON (the SEM #532
  stranded-one-shot lesson).
- Entry reloads (options/subentry edits) re-seed the same way.
- No persistence of debounce timers (deliberate — freeze-safe, and the
  declarative re-assert loop self-corrects, unlike SEM's stranded one-shot
  force-discharge incident, 07-RESEARCH.md).

## Failure modes

| Condition | Behavior |
|---|---|
| Grid/battery power signal unavailable | pool = 0 → appliances release via normal OFF path (sustain + min_on respected), status shows why |
| Actuator unavailable | no command, `actuator_unavailable`, retained until it returns |
| Power sensor (optional) unavailable | fall back to rated W credit-back |
| Nordpool outage | irrelevant — surplus mode needs no prices (PS owns guaranteed runtime) |
| HA restart mid-ON | re-seed keeps it ON until surplus logic releases it; PS/thermostat remain the safety net for the physical load |

## Testing

- Pure-function tests for `appliance_controller.py`: allocation walk (priority,
  credit-back, pool exhaustion), hysteresis band, sustain timers, min_on/min_off
  interactions, fuse admission, BATT-17 discharge guard, restart re-seed.
- Translation-key JSON tests (existing pattern, test_options_flow_support.py).
- Prod soak: observe-only, compare dry-run decisions against
  `sensor.grid_export_surplus_for_water_heater` history and actual export
  curves on sunny days before flipping any `_em_control` switch ON.

## Effort

~3 dev-days: pure module + tests 1.0, coordinator + send site + wiring 0.75,
subentry flow + module flag + EN/SV 0.75, entities + README + planning updates
0.5. Calendar: soak across at least a few sunny days before first live enable.

## v2 / deferred (recorded, not designed)

- `climate.*` actuators (Sensibo heat pump, floor heating) with state restore
- Cross-priority with cars (appliance above car in the order)
- Fuse-pressure shed of running appliances (lowest priority first)
- PS-side integration prize: PS skipping cheap night hours when surplus already
  delivered runtime, or when tomorrow's solar forecast covers it (lives in
  jyourstone/power_saver, not EM; check whether PS already counts
  `always_on`-forced hours toward `min_hours`)
- Auto-calibration of rated power from the optional power sensor

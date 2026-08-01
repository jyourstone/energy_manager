# BATT-16: Feed tomorrow's solar forecast into the 48h battery schedule

**Status:** planned (ultracode investigation 2026-08-01: 3 code readers, 3 independent designs, 2-judge panel — both judges picked the zero-config design; adversarial judge proved the discharge-gate fix must ship in the same release).

## Problem

The scheduler plans over 48h (today + tomorrow Nordpool) but only the summed Forecast.Solar **remaining-today** reading feeds `build_battery_schedule`. There is exactly ONE daylight window (`_normalize_daylight_window`), so tomorrow's peaks always get `peak_recharge == 0`. Concrete symptom: in the evening, remaining-today ≈ 0 and BATT-15b reserves tomorrow's pricier peaks' FULL consumption need — over-reserving tonight and scheduling grid charging the sun will make redundant (e.g. tomorrow forecast 46.4 kWh, treated as 0).

## Design (winner: zero new config, second daylight window)

### 1. `battery_scheduler.py` — multi-window solar model (~+55/−25 LOC)

- `build_battery_schedule` gains two optional kwargs: `solar_forecast_tomorrow_wh: float | None = None`, `tomorrow_start: datetime | None = None` (UTC instant of next local midnight — the only calendar knowledge the pure scheduler needs). Defaults preserve behavior byte-identically.
- New pure helper `_resolve_solar_windows(dawn, dusk, remaining_wh, tomorrow_wh, production_factor, tomorrow_start) -> list[tuple[window, rate_kw]]` replacing Step 5 (lines 221–225). Reuses `_normalize_daylight_window` and `_estimate_solar_rate_kw` unchanged:
  - `W0 = _normalize_daylight_window(dawn, dusk)`; None → `[]` (current degrade path).
  - `w0_is_tomorrow = tomorrow_start is not None and W0.start >= tomorrow_start` — true in the evening when sun.sun's next_dawn AND next_dusk both point at tomorrow. **This comparison is the load-bearing correctness piece**: naive +24h shifting in the evening would misplace tomorrow's energy beyond the horizon and silently reproduce the bug.
  - Evening: `windows = [(W0, rate(tomorrow_wh if tomorrow_wh is not None else remaining_wh, W0))]` — fallback = today's exact behavior when tomorrow sensors absent.
  - Daytime/morning: `windows = [(W0, rate(remaining_wh, W0))]`; if `tomorrow_wh is not None`, append `W1 = (W0.start+24h, W0.end+24h)` with `rate(tomorrow_wh, W1)`. (+24h in UTC: DST-immune, only ~1–3 min/day seasonal sun drift — add code comment.)
- `_optimize_schedule`: replace params `solar_rate_kw` + `daylight_window` with `solar_windows` (tests import neither — verified safe). `peak_recharge[i] = Σ over (w, r) with r>0 of r × _overlap_hours(gap_i, w)`. BATT-15a cap (line 673) and BATT-15b netting (680–684) stay byte-identical — the whole fix propagates through them. `solar_charge` relabel: any-window overlap, keyed on window presence not rate (current semantics).

### 2. `compute_discharge_gate` — REQUIRED in same release (adversarial judge verdict)

Without it we ship a regression **by construction**: the reservation scan (lines 384–394) breaks only at the first charge/solar_charge slot; this feature removes those overnight grid-charge slots, so on a sunny-forecast evening with tight SOC the gate accumulates tomorrow's full peak need into `reserved_energy_kwh` and blocks evening self-consumption that today's schedule permits. Guaranteed-to-fire scenario: evening 20:30, SOC 50%, spread above threshold, tomorrow's forecast covers tomorrow's peak → zero charge slots in plan → reserved 4 kWh → `reserved_for_peak` all evening. Strictly worse than status quo.

Fix (from design 1, prefix-max net-of-solar): optional kwarg `solar_windows: list | None = None` (default None = exact current behavior, all direct gate tests pass unchanged). When provided:

```
needed = 0; reserved = 0
for each future slot until first charge/solar_charge slot:
    if discharge:
        needed += mean_consumption_kw * duration
        reserved = max(reserved, needed - solar_energy_kwh(now, slot.start, solar_windows))
reserved = max(0, reserved)
```

Classic prefix requirement: energy needed NOW = max over future discharge slots k of (cumulative need through k − solar arriving before k). `build_battery_schedule` passes its windows into its internal gate call (line 272). Accepted mild optimism: solar credit not capacity-capped — bounded by the 0.8 production factor and the existing 0.5 × mean_consumption margin; document under risks.

### 3. `coordinator.py` — auto-derived tomorrow entities, zero new config (~+50/−10 LOC)

- New pure module-level `derive_tomorrow_forecast_entities(entity_ids)`: substring replace `energy_production_today_remaining` → `energy_production_tomorrow`, drop non-matching. Forecast.Solar's stable naming carries the `_2` multi-array suffix through. Place next to `sum_solar_forecast_wh` (line 726) for HA-free unit tests.
- Extract `_get_solar_forecast_remaining_wh` body into `_read_solar_forecast_wh(entity_ids)`; add `_get_solar_forecast_tomorrow_wh()` using the derived list; None when nothing derivable (graceful degrade).
- **INFO-level one-time log** (not debug — judge graft) when today entities are configured but derivation yields `[]` (renamed / non-Forecast.Solar sensors; parallel run needs this visible).
- Listener block (~484–493): subscribe derived entities too, **deduplicated** — `list(dict.fromkeys(today + derived))` (judge graft; bare `+=` can double-subscribe).
- Update cycle: read tomorrow Wh, compute `tomorrow_start = dt_util.as_utc(dt_util.start_of_local_day(dt_util.now() + timedelta(days=1)))` (this exact form — the `start_of_local_day() + timedelta` variant is 1h off on DST days), pass both kwargs at call site (567–585).
- `BatteryScheduleData` += `solar_forecast_tomorrow_used: bool = False` (defaulted, non-breaking; `solar_forecast_used` keeps exact current meaning).

### 4. `sensor.py` + README (~+5 LOC)

- Battery Schedule attributes += `solar_forecast_tomorrow_used` (parallel-run verification needs it).
- README: one sentence in wizard Step 3 (tomorrow sensors auto-derived); attribute doc row.

### 5. Explicitly NOT in this release

- **Design 1's clamp-to-now / remaining-window rate recalibration** — changes live mid-day schedules for every install and contaminates the observe-only AppDaemon baseline. Corrected diagnosis for the future ticket: `rate = remaining/full-span` + gap-overlap-including-elapsed-hours conserves total energy only when the gap spans the whole window; phantom pre-peak credit appears for peaks starting mid-window. (NOT a double-count — total credit is bounded by remaining × factor.)
- **Escape-hatch config key** (from design 2, ready-made if derivation no-ops for a real user): `CONF_FORECAST_SOLAR_TOMORROW_ENTITY`, multi `EntitySelector(domain=sensor)` in wizard battery step (~config_flow.py:351 persist / :388 schema / :928 _create_entry) + options flow (~:1161/:1202), auto-detect via `energy_production_tomorrow` substring (verified no collision with today matcher or `power_highest_peak_time_tomorrow`), 4 keys × 3 translation files. Do not build until needed.

## Edge cases (all verified against code by designers/judges)

- Evening + tomorrow sensors unavailable → remaining-today residual over W0 = today's exact behavior.
- Tomorrow Nordpool not yet published → W1 overlaps no gap → correct no-op until ~13:00.
- Midnight rollover → Forecast.Solar sensors roll, `tomorrow_start` recomputed per update; brief under-credit if sensors lag rollover is conservative and self-heals via the tomorrow-entity listeners.
- DST days → `start_of_local_day(now + 1 day)` handles 23/25h days; UTC sun times don't jump.
- Polar day/night → dawn/dusk None → `[]` → same degrade as today.
- Tomorrow forecast 0.0 (cloudy) → window present, rate 0 — matches today's window-present/rate-zero semantics.
- Huge forecast vs small battery → existing clamps (min at 673, max(0,…) at 680–684) already floor/cap correctly.
- Renamed entity IDs (auto-detect matched unique_id) → derivation `[]` → status quo + INFO log + attribute false.

## Test plan

- Unit `_resolve_solar_windows` (add to direct-import list): morning two-window; evening single-window-from-tomorrow-wh; evening fallback tomorrow_wh=None; dawn/dusk None → []; tomorrow_start None → legacy output.
- Unit `derive_tomorrow_forecast_entities`: both prod entities map; `_2` suffix carries; non-matching dropped; empty → empty.
- Exact-arithmetic gap-spanning-two-windows test (judge graft): hand-built windows, `assert recharge == rate1 × overlap`.
- Integration THE-bug scenario: 48h slots (`_make_24h_slots(p1) + _make_24h_slots(p2, day=16)`), now 20:00, remaining=0, expensive day-16 peak → strictly fewer grid-charge slots tonight with tomorrow_wh set vs None.
- Integration BATT-15b: tomorrow_wh covers day-16 peak → today's cheap peak discharges more.
- Gate tests (from design 1): None-windows byte-identical; overnight-refill unblocks (`reserved_for_peak` → allowed); prefix-max: late solar does not retroactively cover an early peak.
- Explicit no-tomorrow identity test: identical schedule with kwargs at None.
- Full suite green with ZERO existing-test modifications (38 scheduler tests pin current contracts).
- Live: watch `solar_forecast_tomorrow_used`, `reserved_energy_kwh`, `discharge_gate_reason`, evening `charging_slot_count` vs AppDaemon for several evenings.

## Scope

6 files: battery_scheduler.py (~+70/−25 incl. gate), coordinator.py (~+50/−10), sensor.py (+1), test_battery_scheduler.py (~+180), test_coordinator.py (~+30), README.md (+3). No config_flow/const/auto_detect/translation changes. Release: **minor** (new behavior, backward compatible).

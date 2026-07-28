# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-15)

**Core value:** Users can optimize their energy costs by automatically scheduling battery charging/discharging and EV charging based on electricity prices, solar production, and fuse constraints -- without any manual helpers or complex setup.
**Current focus:** Phase 5 EXECUTED (charger control, observe-only verified locally) -- next: live parallel-run UAT per 05-CUTOVER.md, then Phase 6

## Current Position

Phase: 5 of 6 (Easee Charger Control) -- EXECUTED, observe-only verified in dev; live parallel-run UAT pending (05-CUTOVER.md)
Plan: parallel-agent execution (no numbered plans)
Status: Phase 5 waves A/B/C landed (124d0a7, 7df81e8, 9f5d7bb): charger_state_machine.py (111 tests), EaseeCoordinator + options + observe-only command gating, charger entities + solar surplus. 287 tests green, ruff clean. Dev verification: laddarstatus sensor computing, dry_run true, zero easee calls, shared fuse arbiter (16.3A both sensors)
Last activity: 2026-07-28 -- Phase 5 executed via 3 sequential agent waves + headless dev verification; cutover checklist written (05-CUTOVER.md)

Progress: [#########.] 90%

## Performance Metrics

**Velocity:**
- Total plans completed: 19
- Average duration: 3min
- Total execution time: 1.00 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| Phase 01 P01 | 2min | 2 tasks | 6 files |
| Phase 01 P02 | 2min | 2 tasks | 3 files |
| Phase 01 P03 | 2min | 2 tasks | 2 files |
| Phase 01 P04 | 2min | 2 tasks | 5 files |
| Phase 01 P05 | 1min | 1 task | 1 file |
| Phase 02 P01 | 11min | 2 tasks | 5 files |
| Phase 02 P02 | 3min | 2 tasks | 4 files |
| Phase 02 P03 | 3min | 2 tasks | 5 files |
| Phase 02 P04 | 2min | 2 tasks | 4 files |
| Phase 02 P05 | 2min | 2 tasks | 2 files |
| Phase 03 P01 | 3min | 2 tasks | 2 files |
| Phase 03 P02 | 4min | 2 tasks | 5 files |
| Phase 03 P03 | 2min | 2 tasks | 4 files |
| Phase 03 P04 | 3min | 2 tasks | 4 files |
| Phase 03 P05 | 3min | 2 tasks | 7 files |
| Phase 04 P01 | 3min | 2 tasks | 2 files |
| Phase 04 P02 | 4min | 2 tasks | 4 files |
| Phase 04 P03 | 3min | 2 tasks | 5 files |
| Phase 04 P04 | 3min | 2 tasks | 6 files |

**Recent Trend:**
- Last 5 plans: 3min, 3min, 4min, 3min, 3min
- Trend: stable

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: 6 phases following dependency chain (prices -> battery schedule -> EMS control -> car charging -> Easee control -> polish)
- Roadmap: QUAL requirements deferred to Phase 6 since meaningful tests require modules to exist first
- Roadmap: CORE split across Phase 1 (scaffold/config) and Phase 6 (options flow/helpers/diagnostics)
- [Phase 01]: Ported nordpool_adapter.py from PowerSaver verbatim (proven production code)
- [Phase 01]: Auto-detect uses entity_id pattern matching and unique_id for entity identification
- [Phase 01]: Car detection groups entities by device_id to associate battery level with correct vehicle
- [Phase 01]: Config flow uses _add_suggested_values helper for auto-detection pre-fill (suggested_value, not default)
- [Phase 01]: Immutable entry.data = Nordpool sensor/type; mutable entry.options = module toggles + entity configs
- [Phase 01]: Car subentry gated by CONF_EV_ENABLED in async_get_supported_subentry_types
- [Phase 01]: Module-level type alias for EnergyManagerConfigEntry (broader tooling compatibility)
- [Phase 01]: always_update=False on PriceCoordinator to avoid unnecessary listener notifications
- [Phase 01]: SEK/kWh as native unit for price sensor (pass-through from Nordpool)
- [Phase 01]: Platform.SENSOR unconditionally included (core price sensor not gated by module toggles)
- [Phase 01]: Entity attributes for UI metadata only; bulk price data accessed via coordinator directly
- [Phase 01]: state_class=None for monetary spot prices (MEASUREMENT incompatible with MONETARY device class)
- [Phase 02]: Pure Python battery_scheduler.py with zero HA imports for independent testability
- [Phase 02]: Virtual energy tracking per-peak with cheapest charge slot selection and most expensive discharge prioritization
- [Phase 02]: Solar forecast simplified to 05:00-17:00 UTC daylight with even distribution
- [Phase 02]: Test infrastructure uses importlib MetaPathFinder for Python 3.14-compatible HA stubs
- [Phase 02]: BatteryScheduleCoordinator chains to PriceCoordinator via async_add_listener
- [Phase 02]: RestoreNumber entities for threshold persistence across HA restarts
- [Phase 02]: Solar forecast auto-detects kWh vs Wh via unit_of_measurement attribute
- [Phase 02]: NumberMode.BOX for precise threshold input; EntityCategory.CONFIG for settings classification
- [Phase 02]: Battery sensors conditionally created based on battery_coordinator existence (not config toggle)
- [Phase 02]: Schedule attributes capped at 48 slots to keep state compact per Phase 1 lesson
- [Phase 02]: NextCharge/NextDischarge use SensorDeviceClass.TIMESTAMP for native datetime display
- [Phase 02]: Config flow merges SigenStor and Forecast.Solar auto-detection into single suggested values dict
- [Phase 02]: kW-to-W conversion at entity-coordinator boundary; coordinator stays in watts for scheduler
- [Phase 02]: available() property on NextCharge/NextDischarge returns coordinator.data is not None (green when data exists, red on error)
- [Phase 02]: Schedule attributes filter by slot.end > now before 48-slot cap (keeps in-progress slots, excludes fully past ones)
- [Phase 03]: Pure Python ems_controller.py with zero HA imports for independent testability (same pattern as battery_scheduler.py)
- [Phase 03]: PV opportunistic charging triggers on standby OR max_self_consumption modes
- [Phase 03]: PVHysteresisTracker uses separate activate/deactivate thresholds (500W/300W) for hysteresis band
- [Phase 03]: Car priority override only affects command_charging mode (discharge/standby unaffected)
- [Phase 03]: Safety-first processing order: fuse headroom before mode decisions, car priority before charge limit
- [Phase 03]: Fuse rating in config flow (not NumberEntity) -- hardware constant that should not change casually
- [Phase 03]: EMS config flow as separate step between battery and EV (not merged into battery step)
- [Phase 03]: L-current fallback scan across ALL entities (not just SigenStor) for template sensor support
- [Phase 03]: Car plugged-in detection via Easee charger status (fast local) not car integration (slow cloud)
- [Phase 03]: Safe command ordering: limit-first when switching to charge mode, mode-first when switching away
- [Phase 03]: EMSCoordinator only created when battery_coordinator exists (not via separate toggle -- EMS inherent to battery module)
- [Phase 03]: EMS sensor conditional guard matches battery sensor pattern (coordinator is not None check)
- [Phase 03]: Accept both sensor and number domains for charge/discharge limit auto-detection (SigenStor firmware variant)
- [Phase 03]: L-current fallback includes phase_a_active_power (kW power, approximate but better than 0A assumption)
- [Phase 03]: Global PV power fallback prefers plant-level over inverter-level entity (post-clipping total more accurate)
- [Phase 03]: Per-phase fuse protection uses max(abs(P_phase)/230) across all three phases for worst-case current
- [Phase 03]: Per-phase mode requires ALL three phases configured; partial falls back to total power balanced-load estimate
- [Phase 03]: Per-phase state change listeners replace single grid_power listener for fuse-critical events
- [Phase 04]: Pure Python car_charging_scheduler.py follows battery_scheduler.py pattern (zero HA imports, frozen dataclasses)
- [Phase 04]: Slot window filter uses start >= now (excludes partially-elapsed slots from charge selection)
- [Phase 04]: solar_surplus_available flag marks all charge slots as solar_charge when True (Phase 5 handles actual PV routing)
- [Phase 04]: CarChargingCoordinator reads charger_status_entity from entry.options for fallback detection (shared with EMSCoordinator)
- [Phase 04]: Per-coordinator SOC staleness tracking (_soc_last_updated) for independent fallback evaluation per car
- [Phase 04]: CarEntity uses TYPE_CHECKING import for CarChargingCoordinator to avoid circular imports
- [Phase 04]: departure_time defaults to 07:00 local; rolls to tomorrow when departure <= now
- [Phase 04]: solar_surplus_available always False in Phase 4 (Phase 5 wires actual PV detection via PVHysteresisTracker)
- [Phase 04]: TimeEntity uses async_set_value (not async_set_native_value); RestoreEntity uses async_get_last_state (not number-specific)
- [Phase 04]: number.py async_setup_entry restructured to allow car entities without battery module (no early return)
- [Phase 04]: mySkoda added as explicit platform pattern alongside "skoda" for broader car integration support
- [Phase 04]: battery_percentage and charging_level added to SOC entity patterns for mySkoda compatibility
- [Phase 04]: _is_home_and_plugged_in() uses 3-signal cascade: Easee charger status (required), car charger_connected (optional), vehicle location (optional)
- [Phase 04]: home_plugged_entity replaced by charger_connected_entity + location_entity (auto-detected, optional manual override)
- [2026-07-28 owner decisions]: Maximally customizable with production-tuned defaults; NO helpers or template sensors -- everything integration-owned; observe-only mode + master switch before parallel running; per-car phase capability 1/2/3 (default 3, ID.3 = 2); single charger v1 (no multi-car arbitration); guest fallback window configurable (default 00-06); single persistent departure/target entities (drop default+temporary helper UX); economics >= live parity (fees + cycle cost; discharge threshold = cycle_cost - transfer_fee); port March 2026 algorithm improvements; Forecast.Solar only, multiple sensors summed; configurable notify target; EN + SV locales; NO backwards-compat constraints (unreleased)
- [2026-07-28 verified live]: grid phase current sensors are SIGNED (import +, export -, read -8.1 A during export); fuse headroom for the charger must exclude the charger's own current (live "minus Easee" template chain); house load excludes water heater (Power Saver's) and charger; AppDaemon tuned: 20 A fuse, 1 A buffer, ESS 5 s down/180 s up, charger 5 s down/120 s up, W-to-A divisor 692.8; Nordpool delivers 15-min slots (96/day); HA easee services confirmed present (easee.action_command, set_charger_dynamic_limit, set_charger_phase_mode); live formulas archived in session memory (energy-manager-live-formulas)

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 1: Subentry flow pattern (HA 2024.12+) is relatively new -- needs validation during implementation
- Phase 3: EMS safety guards need careful specification of hard limits and failure modes
- ~~Phase 5: Easee API interaction (pyeasee vs HA service calls) needs verification~~ RESOLVED 2026-07-28: HA easee integration services confirmed present in live HA (easee.action_command, easee.set_charger_dynamic_limit, easee.set_charger_phase_mode) -- use service calls, no pyeasee dependency
- Phase 4: human UAT re-run pending (8/10 tests never executed after 04-04 gap closure)
- Phase 5: live Easee status is flaky (owner runs a watchdog automation reloading the config entry when stuck) -- state machine must tolerate unreliable status via power-based cross-checks
- Cutover (post-Phase-5): AppDaemon apps live NOW; parallel install without observe-only mode means two controllers fighting over EMS select, ESS limit, charger. Cutover checklist needed: disable 5 AppDaemon apps, remove dead laddningsautomatik automations, remove satt_avresetid/satt_malladdning helper-reset automations, decide Easee watchdog fate

## Session Continuity

Last session: 2026-07-28
Stopped at: Phase 4.1 in progress -- parallel agents running (scheduler fix, coordinator safety fixes, HACS packaging); sv translations queued
Resume file: None

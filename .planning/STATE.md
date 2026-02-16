# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-15)

**Core value:** Users can optimize their energy costs by automatically scheduling battery charging/discharging and EV charging based on electricity prices, solar production, and fuse constraints -- without any manual helpers or complex setup.
**Current focus:** Phase 2 complete (all 5 plans including schedule attribute filtering) — ready for Phase 3 (EMS Controller)

## Current Position

Phase: 2 of 6 (Home Battery Schedule) -- COMPLETE
Plan: 5 of 5 in current phase
Status: Phase 2 Complete — all 5 plans executed (scheduler, coordinator, sensors, UAT gap closures)
Last activity: 2026-02-16 -- Plan 02-05 complete: UAT gap closure (schedule attribute time filtering)

Progress: [####......] 40%

## Performance Metrics

**Velocity:**
- Total plans completed: 10
- Average duration: 3min
- Total execution time: 0.53 hours

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

**Recent Trend:**
- Last 5 plans: 11min, 3min, 3min, 2min, 2min
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

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 1: Subentry flow pattern (HA 2024.12+) is relatively new -- needs validation during implementation
- Phase 3: EMS safety guards need careful specification of hard limits and failure modes
- Phase 5: Easee API interaction (pyeasee vs HA service calls) needs verification

## Session Continuity

Last session: 2026-02-16
Stopped at: Completed 02-05-PLAN.md (UAT Gap Closure: schedule attribute filtering) -- Phase 2 COMPLETE (all 5 plans)
Resume file: None

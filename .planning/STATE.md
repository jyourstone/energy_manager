# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-15)

**Core value:** Users can optimize their energy costs by automatically scheduling battery charging/discharging and EV charging based on electricity prices, solar production, and fuse constraints -- without any manual helpers or complex setup.
**Current focus:** Phase 1 - Core Infrastructure + Price Foundation

## Current Position

Phase: 1 of 6 (Core Infrastructure + Price Foundation)
Plan: 5 of 5 in current phase
Status: Phase Complete
Last activity: 2026-02-15 -- Completed 01-05 (Gap Closure: Price Sensor Warnings)

Progress: [##........] 22%

## Performance Metrics

**Velocity:**
- Total plans completed: 5
- Average duration: 2min
- Total execution time: 0.15 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| Phase 01 P01 | 2min | 2 tasks | 6 files |
| Phase 01 P02 | 2min | 2 tasks | 3 files |
| Phase 01 P03 | 2min | 2 tasks | 2 files |
| Phase 01 P04 | 2min | 2 tasks | 5 files |
| Phase 01 P05 | 1min | 1 task | 1 file |

**Recent Trend:**
- Last 5 plans: 2min, 2min, 2min, 2min, 1min
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

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 1: Subentry flow pattern (HA 2024.12+) is relatively new -- needs validation during implementation
- Phase 3: EMS safety guards need careful specification of hard limits and failure modes
- Phase 5: Easee API interaction (pyeasee vs HA service calls) needs verification

## Session Continuity

Last session: 2026-02-15
Stopped at: Completed 01-05-PLAN.md (Gap Closure: Price Sensor Warnings) -- Phase 01 fully complete with all UAT gaps closed
Resume file: None

# Project Research Summary

**Project:** Energy Manager (Unified HACS Integration)
**Domain:** Home Assistant energy management -- battery scheduling, EV charging optimization, multi-device coordination
**Researched:** 2026-02-15
**Confidence:** MEDIUM-HIGH

## Executive Summary

This project converts five production-tested AppDaemon apps (HomeBatteryManager, CarChargingManager x2, EMSController, EaseeController) into a single modular HACS integration. The recommended approach is a **single config entry with multiple DataUpdateCoordinators** -- one per module -- using **subentry flows** for per-car configuration. All 24 manual HA helpers become integration-owned entities. The architecture preserves the existing entity-state-based communication pattern between modules, which enables incremental migration where AppDaemon and the new integration coexist during development. The core algorithms (peak grouping, virtual energy tracking, fuse protection, price-optimized scheduling) are proven and can be ported directly; the challenge is adapting them to HA's async event loop and entity platform model.

The competitive landscape validates this project's differentiation: no existing HACS integration combines home battery scheduling AND EV charging optimization AND fuse-aware device coordination. Predbat owns UK battery scheduling, ev_smart_charging owns standalone EV optimization, but nobody coordinates across device types. The **multi-device coordination with fuse protection** is the killer feature that justifies building a unified integration rather than using existing point solutions.

The primary risks are: (1) blocking the HA event loop when porting synchronous AppDaemon code, (2) unsafe physical device control without proper safety guards (fuse math can produce negative values, no command verification exists), and (3) config flow complexity explosion given 90+ configuration parameters across 5 modules. All three are mitigable with deliberate architecture decisions in the first phase. The biggest unknown is the subentry flow pattern for per-car configuration -- it is relatively new in HA (2024.12+) and needs validation during implementation.

## Key Findings

### Recommended Stack

The integration is built entirely on HA core APIs with no external PyPI dependencies for v1. `DataUpdateCoordinator` handles periodic data polling with built-in retry and error handling. `async_track_state_change_event` provides real-time reactions to price changes, battery SOC, and fuse current. `ConfigFlow` + `OptionsFlowWithConfigEntry` replaces all manual helpers with proper UI configuration. Subentry flows model per-car settings. `helpers.storage.Store` persists schedules and history across restarts.

**Core technologies:**
- **DataUpdateCoordinator** (one per module): Handles polling, retry, entity update coordination -- avoids reinventing this
- **async_track_state_change_event**: Real-time response to fuse current, price updates, charger state -- replaces AppDaemon's polling-for-changes pattern
- **Subentry flows** (HA 2024.12+): Per-car configuration without touching main config entry -- the modern replacement for YAML sub-configs
- **helpers.storage.Store**: Persistent schedule and history storage with atomic writes and schema migration
- **ruff**: Single tool for linting and formatting, replaces flake8+black+isort, used by HA core
- **pytest-homeassistant-custom-component**: Standard test fixtures for custom integrations outside HA core

**Critical version requirements:** HA 2024.12+ (for subentry flows and Python 3.12+). Set `homeassistant: "2024.12.0"` in hacs.json.

**What NOT to use:** numpy/scipy (too heavy for HA, breaks ARM), APScheduler (conflicts with event loop), YAML config (deprecated), `hass.data[DOMAIN]` dict soup (use typed `entry.runtime_data` instead).

### Expected Features

**Must have (table stakes):**
- Price-based charge/discharge scheduling (proven algorithm, port directly)
- Cheapest-slot EV charging with departure time and target SOC
- Schedule visualization via sensor attributes (ApexCharts compatibility)
- Config flow with auto-discovery (no YAML-only setup for HACS)
- Options flow replacing all 24 manual helpers
- Nordpool price support (both HACS and native variants via adapter)
- Solar production awareness (dawn/dusk gating, surplus calculation)
- Graceful degradation when external integrations are unavailable

**Should have (differentiators):**
- Unified battery + EV in one integration (no competitor does both)
- Fuse protection / dynamic current limiting (safety feature, builds trust)
- Multi-device coordination (car yields to battery schedule, battery yields to car deadline)
- Multi-cycle charge/discharge scheduling with virtual energy tracking (more sophisticated than Predbat)
- Zero manual helpers (install, configure via UI, done)
- Fallback/guest car charging for unrecognized vehicles
- Auto-discovery of compatible devices (SigenStor, Easee, Skoda/VW)

**Defer to v2+:**
- Dynamic phase switching (1-phase/3-phase Easee, complex)
- Additional price sources beyond Nordpool
- Additional device brands beyond SigenStor + Easee
- Historical cost savings calculator
- Advanced solar-surplus EV charging with hysteresis

### Architecture Approach

Single HACS integration with one config entry. Five coordinators with different update cadences: PriceCoordinator (5 min), BatteryScheduleCoordinator (5 min), EMSCoordinator (5 sec + event-driven), CarScheduleCoordinator (15 min per car), EaseeCoordinator (30 sec). Modules communicate via HA entity state (preserving the AppDaemon contract for migration coexistence). Price data shared in-memory via coordinator chaining. Cars modeled as subentries with independent lifecycles.

**Major components:**
1. **Core/Price Coordinator** -- fetches Nordpool prices, normalizes to slots, shared by all schedulers
2. **Battery Schedule Module** -- charge/discharge scheduling, peak grouping, virtual energy tracking
3. **EMS Controller Module** -- real-time battery mode execution, fuse protection (5-second loop)
4. **Car Schedule Module** -- per-car charging optimization with departure constraints (one coordinator per car)
5. **Easee Controller Module** -- charger amp control, solar charging, start/stop sequences

### Critical Pitfalls

1. **Blocking the HA event loop** -- AppDaemon code is synchronous; every ported line must be audited. Replace `time.sleep()` with `asyncio.sleep()`, all I/O with `await`. The Easee start/stop 4-5 second delays are the highest-risk code.

2. **Unsafe physical device control** -- Fuse calculation can produce negative values, no command verification exists. Implement hard safety clamps (`max(0, min(calculated, absolute_max))`), watchdog timeouts, and command read-back verification before any physical control logic ships.

3. **Config entry lifecycle (unload/reload)** -- AppDaemon has no unload concept. Every `async_setup_entry` must have a matching `async_unload_entry`. Use `entry.async_on_unload()` from day one. Forgetting this causes memory leaks, ghost entities, and broken reloads.

4. **Entity state as database** -- AppDaemon stores full schedules in sensor attributes. In native HA, this bloats the recorder database. Use `_unrecorded_attributes` for schedule arrays, expose summary data as separate entities.

5. **Polling intervals without adaptation** -- EMSController's 5-second poll becomes event-driven via `async_track_state_change_event`. Coordinator polling is backup, not primary. Direct porting of AppDaemon intervals wastes event loop time.

## Implications for Roadmap

Based on combined research, the integration should be built in 6 phases following the dependency chain: prices first, then schedulers, then controllers, then coordination, then polish.

### Phase 1: Core Infrastructure + Price Foundation

**Rationale:** Everything depends on price data. This phase validates the integration skeleton (config flow, coordinator, entity model) before any complex business logic. Getting the entity architecture, async patterns, and module boundaries right here prevents rewrites later.
**Delivers:** Working HACS integration that shows current/future energy prices. Integration skeleton with proper setup/unload lifecycle, entity base classes with `has_entity_name`, translation keys, and HACS CI validation.
**Features addressed:** Nordpool price support (table stakes), config flow initial step (table stakes), HACS compliance (table stakes)
**Pitfalls addressed:** #1 (async patterns established), #3 (lifecycle from day one), #4 (entity model designed), #8 (module boundaries defined), #10 (naming conventions), #13 (HACS structure), #14 (translations)
**Architecture components:** PriceCoordinator, Nordpool adapter (reuse PowerSaver pattern), base entity class, config flow step 1

### Phase 2: Home Battery Schedule Module

**Rationale:** The most complex and unique algorithm (multi-cycle peak grouping with virtual energy tracking). Building this second validates the coordinator chaining pattern and produces the highest-value differentiator early. Can run alongside AppDaemon for output comparison.
**Delivers:** Battery charge/discharge schedule that matches AppDaemon output. Schedule sensors, threshold number entities replacing input_number helpers.
**Features addressed:** Price-based charge scheduling (table stakes), multi-cycle scheduling (differentiator), virtual energy tracking (differentiator), schedule visualization (table stakes), solar production awareness (table stakes)
**Pitfalls addressed:** #2 (attribute size -- design entity model for schedules), #5 (polling -- use coordinator chaining with price listener), #11 (handle SigenStor/Nordpool unavailability)
**Architecture components:** BatteryScheduleCoordinator, battery schedule sensors, number entities for thresholds

### Phase 3: EMS Controller Module

**Rationale:** The real-time control component that executes battery mode changes and provides fuse protection. Must be rock-solid before disabling AppDaemon EMS. This is where safety guards are most critical.
**Delivers:** Battery mode control + fuse protection. AppDaemon EMS can be disabled after validation.
**Features addressed:** Fuse protection (differentiator), multi-device coordination foundation (differentiator)
**Pitfalls addressed:** #6 (safety guards -- hard clamps, watchdog, command verification), #9 (state machine -- formal model for EMS modes), #5 (event-driven for fuse current, not polling)
**Architecture components:** EMSCoordinator (5-second + event-driven), safety guard module, EMS mode state machine

### Phase 4: Car Charging Module

**Rationale:** Port CarChargingManager with multi-car support via subentries. This validates the subentry pattern and can be developed somewhat independently of battery (both depend on Phase 1 prices, not on each other).
**Delivers:** Per-car charging schedules matching AppDaemon output. Car subentry flow, per-car sensors and number entities.
**Features addressed:** Cheapest-slot EV charging (table stakes), departure time (table stakes), target SOC (table stakes), per-car configuration (differentiator), fallback/guest car charging (differentiator)
**Pitfalls addressed:** #7 (config flow complexity -- subentries keep per-car config separate), #11 (handle car integration unavailability)
**Architecture components:** Car subentry flow, CarScheduleCoordinator (one per car), per-car device and entities

### Phase 5: Easee Controller Module

**Rationale:** Depends on both car schedules (Phase 4) and EMS/fuse state (Phase 3). Most external service calls. The Easee start/stop state machine is the most fragile component and needs the formal state machine treatment.
**Delivers:** Full Easee charger control. Solar charging. AppDaemon EaseeController can be disabled.
**Features addressed:** Solar-surplus EV charging (differentiator), car/battery charging priority (differentiator), auto-discovery of Easee (table stakes)
**Pitfalls addressed:** #1 (Easee 4-5 second delays must be async), #6 (fuse calculation with hard clamping for charger), #9 (formal state machine for Easee charging sequence), #11 (handle Easee unavailability)
**Architecture components:** EaseeCoordinator, Easee state machine, solar charging logic

### Phase 6: Polish, Options Migration, and Release Prep

**Rationale:** All core logic works. Now make it user-friendly: complete options flow for all settings, migrate remaining helpers, add diagnostics, write documentation, and finalize HACS publication.
**Delivers:** Self-contained integration with zero external helper dependencies. Full options flow. Diagnostics export. Documentation. HACS-publishable.
**Features addressed:** Zero manual helpers (differentiator), options flow for all settings (table stakes), graceful degradation (table stakes), auto-discovery (differentiator)
**Pitfalls addressed:** #7 (options flow UX -- multi-step with one page per module), #12 (Nordpool adapter for both variants), #15 (comprehensive test coverage)
**Architecture components:** Complete options flow, diagnostics.py, migration tooling

### Phase Ordering Rationale

- **Price data is the universal dependency.** Every scheduler needs it. Building Core+Price first means Phase 2-5 all have their foundation.
- **Battery before Car** because the battery scheduler is more complex (multi-cycle, virtual energy) and validates the architecture harder. If the pattern works for battery, car is straightforward.
- **EMS before Easee** because EMS provides the fuse protection foundation that Easee needs. Also, EMS is simpler (mode switching + fuse math) while Easee has the fragile charging state machine.
- **Car and Battery are somewhat parallel.** Phase 4 depends on Phase 1, not Phase 2/3. In practice, they will likely be sequential for a solo developer, but the architecture supports parallel development.
- **Polish last** because all settings need to exist before the options flow can expose them. Diagnostics needs all modules running to export meaningful data.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 1:** Subentry flow implementation details -- relatively new HA feature, limited examples in the wild. Verify against actual HA 2024.12 API.
- **Phase 3:** EMS safety guards -- needs careful specification of hard limits, watchdog behavior, and failure modes. Consider researching how other integrations handle physical device safety.
- **Phase 5:** Easee API interaction patterns -- verify current state of `pyeasee` library vs direct service calls. The Easee start/stop sequence timing is critical and fragile.

Phases with standard patterns (skip deep research):
- **Phase 2:** Battery scheduling is a pure algorithm port. The coordinator chaining pattern is well-documented in HA docs.
- **Phase 4:** Car scheduling is a simpler algorithm port. Subentry validation from Phase 1 carries forward.
- **Phase 6:** Options flow, diagnostics, and HACS publishing are well-documented patterns with prior art in PowerSaver.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All core technologies are HA built-ins verified via official docs. No exotic dependencies. |
| Features | MEDIUM-HIGH | Table stakes validated against competitors (Predbat, ev_smart_charging). Differentiators validated from working AppDaemon code. Competitor analysis limited by available data. |
| Architecture | MEDIUM-HIGH | Multi-coordinator pattern is standard HA. Subentry pattern is newer (MEDIUM confidence on details). Entity-state communication is proven in AppDaemon. |
| Pitfalls | HIGH | Critical pitfalls directly identified from codebase analysis (CONCERNS.md) and HA documentation. Safety concerns are concrete and observable in existing code. |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **Subentry flow API details:** The subentry pattern for per-car configuration is the newest HA API used. Needs hands-on validation in Phase 1 before committing to it for Phase 4. Fallback: use a multi-step options flow with dynamic car sections.
- **pyeasee library status:** Need to verify whether `pyeasee` on PyPI is adequate for the Easee control needed, or if direct HA service calls to the Easee integration are the better path. This affects whether `requirements` in manifest.json stays empty.
- **iot_class determination:** The integration both polls local entities and controls devices via service calls. It may also make cloud API calls through Easee. The correct `iot_class` needs validation -- likely `"local_polling"` if all control goes through existing HA integrations.
- **Nordpool native vs HACS attribute differences:** The PowerSaver adapter handles this, but it should be verified that the adapter covers all edge cases (missing tomorrow prices, price at exactly midnight, etc.).
- **Recorder performance with schedule attributes:** The `_unrecorded_attributes` approach for excluding schedule arrays from the recorder needs verification. If it does not work as expected, the fallback is keeping all schedule data in coordinator memory only and exposing only summary attributes.

## Sources

### Primary (HIGH confidence)
- Home Assistant Developer Documentation -- config flow, DataUpdateCoordinator, entity platform, config entries, setup failures, quality scale (verified via WebFetch 2026-02-15)
- Existing AppDaemon codebase at `/Volumes/addon_configs/a0d7b954_appdaemon/apps/` -- production code, primary algorithm source
- Codebase analysis at `.planning/codebase/` -- CONCERNS.md, ARCHITECTURE.md, INTEGRATIONS.md, CONVENTIONS.md
- PowerSaver HACS integration -- proven patterns for Nordpool adapter, config flow, HACS structure

### Secondary (MEDIUM confidence)
- ev_smart_charging GitHub repo -- feature comparison, supported price sources, charger support
- Predbat/Batpred GitHub repo -- battery scheduling approach, UK market focus, feature set
- FoxESS EM GitHub repo -- vendor-locked approach, solar integration patterns
- HA developer docs on subentry flows -- verified to exist, limited community examples

### Tertiary (LOW confidence)
- `pyeasee` PyPI package -- needs verification of current API coverage and maintenance status
- `entry.runtime_data` pattern -- known from training data, needs verification against target HA version

---
*Research completed: 2026-02-15*
*Ready for roadmap: yes*

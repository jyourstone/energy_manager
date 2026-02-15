# Domain Pitfalls

**Domain:** Home Assistant HACS energy management integration (AppDaemon brownfield conversion)
**Researched:** 2026-02-15

## Critical Pitfalls

Mistakes that cause rewrites, data loss, or major user-facing failures.

### Pitfall 1: Blocking the HA Event Loop

**What goes wrong:** AppDaemon apps run in their own threads and can do blocking I/O freely. Home Assistant integrations run on a single asyncio event loop shared with the entire HA core. Porting synchronous AppDaemon code (blocking `get_state`, `call_service`, `time.sleep` in delay sequences) directly into HA async methods freezes the entire Home Assistant instance -- not just your integration, everything.

**Why it happens:** The AppDaemon `hass.Hass` base class hides async complexity. Methods like `self.get_state()`, `self.call_service()`, and `self.run_in()` are synchronous wrappers. Developers port these line-by-line into HA `async_setup_entry` or entity `async_update` without realizing the execution model changed entirely.

**Consequences:** Home Assistant UI becomes unresponsive. Other integrations stop updating. Users see "Integration took too long to load" errors. In severe cases, the watchdog kills and restarts HA.

**Prevention:**
- Every I/O operation must use `await` or be wrapped in `hass.async_add_executor_job()` for synchronous libraries
- Replace `time.sleep()` delays (used in Easee charging sequences) with `async_call_later()` or `asyncio.sleep()`
- Never call blocking functions in `async_setup_entry`, `async_update`, property getters, or event handlers
- The EaseeController's 4-second and 5-second delays in start/stop sequences (lines 422-479) need particular attention -- these MUST become async timers
- Use `hass.async_create_task()` for fire-and-forget operations
- Lint with `pylint-homeassistant` which catches blocking calls in async context

**Detection:** HA logs show "Detected blocking call to ... in the event loop" warnings. Integration load takes >10 seconds. Other integrations report timeouts.

**Phase relevance:** Must be addressed in Phase 1 (Core/Infrastructure). Every line of ported code must be audited for blocking calls. This is the single most common reason custom integrations get rejected or cause user complaints.

**Confidence:** HIGH -- based on HA developer documentation requirements and known async architecture.

---

### Pitfall 2: Entity State as Database (Attribute Size Limits)

**What goes wrong:** The AppDaemon apps store full charge/discharge schedules, historical data, and metadata as sensor attributes (e.g., `sensor.battery_charge_schedule_py` with a `schedule` attribute containing every 15-minute slot). HA entity attributes have practical size limits. Large attributes slow down state recording, bloat the database, make the recorder skip entries, and can cause WebSocket disconnects when the frontend tries to render them.

**Why it happens:** AppDaemon's `set_state()` with attributes is convenient and the existing apps rely heavily on it. The pattern works fine in AppDaemon because state is transient. In a native integration, every state change is recorded to the database by default, and large attributes multiply this cost.

**Consequences:** Database grows rapidly. History panel becomes slow. State changes may be silently dropped if they exceed WebSocket message limits (~16KB practical). HA recorder falls behind.

**Prevention:**
- Mark volatile/large attributes as `_attr_extra_state_attributes` but also override `_unrecorded_attributes` to exclude schedule arrays and history from the recorder
- Keep schedule data in `hass.data[DOMAIN]` (in-memory coordinator data), not in entity attributes
- Expose summary attributes (next_slot, slot_count, current_action) rather than full schedule arrays
- Create dedicated sensor entities for key data points instead of cramming everything into one sensor's attributes
- For the battery schedule: expose `sensor.energy_manager_battery_next_charge`, `sensor.energy_manager_battery_next_discharge` as separate entities rather than one mega-attribute sensor

**Detection:** Database size grows disproportionately. `home-assistant.log` shows recorder warnings. State history panel loads slowly.

**Phase relevance:** Must be designed in Phase 1 (entity architecture), implemented in Phase 2 (Home Battery module) and Phase 3 (EV Charging module). Getting the entity model wrong here means a rewrite later.

**Confidence:** HIGH -- documented HA limitation. The existing codebase stores full schedules as attributes (ARCHITECTURE.md lines 80-83).

---

### Pitfall 3: Forgetting Config Entry Lifecycle (Unload/Reload/Migration)

**What goes wrong:** Developers implement `async_setup_entry` but forget or poorly implement `async_unload_entry`. When a user changes options, removes the integration, or HA reloads it, listeners are not cleaned up, coordinators keep polling, timers keep firing, and entities become ghosts in the registry. Worse: when the integration's config schema changes between versions, there is no migration path and the entry becomes invalid.

**Why it happens:** AppDaemon has no concept of unloading. Apps start in `initialize()` and run until AppDaemon restarts. There is no `terminate()` requirement. Developers building their first HA integration focus on making setup work and never test the teardown path.

**Consequences:** Memory leaks on reload. Duplicate entities after re-add. "Entity not available" errors. Users must delete and re-add the integration to fix issues. On version upgrades: config entries fail to load, integration is permanently broken until manually removed from `.storage/core.config_entries`.

**Prevention:**
- Every `async_setup_entry` must have a matching `async_unload_entry` that reverses all setup: unsubscribe listeners, cancel timers, stop coordinators, unload platforms
- Store all cancel callbacks in `entry.runtime_data` (or `hass.data[DOMAIN][entry.entry_id]`) so unload can find them
- Use `entry.async_on_unload()` to register cleanup callbacks at setup time -- this guarantees they run
- Implement `async_migrate_entry()` from day one with a version counter in `ConfigFlow.VERSION`
- Test the reload path: change an option, verify no duplicate entities, no orphan listeners
- The 5-second polling loop from EMSController and the minutely callbacks from HomeBatteryManager become `DataUpdateCoordinator` instances that must be cancelled on unload

**Detection:** After reload, entity count doubles. Memory usage increases on each reload. Changing options does not take effect until HA restart.

**Phase relevance:** Phase 1 (Core setup). Must be baked into the integration skeleton from the start. Retrofitting unload logic is painful.

**Confidence:** HIGH -- documented HA requirement (config_entries_index). Common failure mode in HACS integrations.

---

### Pitfall 4: Direct Entity State Mutation Instead of Native Entities

**What goes wrong:** AppDaemon apps create entities by calling `self.set_state("sensor.my_entity", state=value, attributes={})`. This creates "orphan" entities that are not tied to any integration, have no device association, cannot be managed through the UI, and disappear from the entity registry on HA restart (unless they persist in the state machine). Developers port this pattern into native integrations by calling `hass.states.async_set()` instead of creating proper Entity subclasses.

**Why it happens:** The AppDaemon pattern is simple and the existing 5 apps all use it (24+ output sensors created via `set_state`). The HA entity platform model (Entity subclass, `async_setup_entry`, device_info, unique_id) is more verbose and feels like boilerplate.

**Consequences:** Entities are not manageable (cannot rename, cannot disable, cannot assign to areas). No device grouping -- user sees 30+ ungrouped entities instead of organized devices. Entity IDs may collide. No automatic cleanup when integration is removed. Fails HACS review expectations.

**Prevention:**
- Create proper Entity subclasses (SensorEntity, BinarySensorEntity, NumberEntity, SelectEntity) for every exposed data point
- Use `has_entity_name = True` with `device_info` to get proper device grouping
- Map the 24 manual helpers to proper entity types: `input_number` -> `NumberEntity`, `input_boolean` -> `SwitchEntity`, `input_datetime` -> custom entity or config option
- Use `EntityDescription` dataclasses for defining entity metadata declaratively
- Never call `hass.states.async_set()` for integration-owned entities

**Detection:** Entities show "No integration" in the entity registry. Entities disappear after restart. Cannot assign entities to areas or devices.

**Phase relevance:** Phase 1 (entity architecture design), carried through all phases. This is a fundamental design decision that affects every module.

**Confidence:** HIGH -- core HA architecture requirement (entity_index documentation).

---

### Pitfall 5: Porting Polling Intervals Without Adaptation

**What goes wrong:** The AppDaemon apps use aggressive polling: EMSController checks every 5 seconds, HomeBatteryManager every 1 minute (filtered to 5-minute intervals), EaseeController monitors status continuously. Porting these as `DataUpdateCoordinator` update intervals or `async_track_time_interval` timers creates excessive load on HA. Unlike AppDaemon (separate process), a native integration's polling directly competes with HA core for event loop time.

**Why it happens:** Developers port intervals 1:1 from AppDaemon config. The EMS 5-second loop made sense when it was polling for state changes manually. In HA native, state changes fire events automatically, so polling is only needed for periodic recalculation, not for state detection.

**Consequences:** High CPU usage. Event loop saturation. Other integrations update slowly. Battery drain on HA hardware (many users run on Raspberry Pi or low-power devices).

**Prevention:**
- Replace the EMSController's 5-second poll-for-state-changes with `async_track_state_change_event()` listeners -- HA fires events on every state change, no polling needed
- Use `DataUpdateCoordinator` with appropriate intervals: 5 minutes for schedule recalculation (not 1 minute), 30 seconds for status monitoring (not 5 seconds)
- The Easee charger status already fires state change events via the Easee integration -- listen to those instead of polling
- For the Nordpool price updates: listen to the sensor's state change event (fires when new prices arrive) rather than polling every N minutes
- Keep one coordinator per update concern (prices, battery status, charger status) with different intervals
- Use `async_track_state_change_event` for all the cross-integration monitoring that EMSController currently polls for

**Detection:** HA system health shows high event loop utilization. Debug logs show excessive "Updating coordinator" entries. CPU stays elevated.

**Phase relevance:** Phase 1 (Core architecture) must define the coordinator/listener strategy. Phase 2-3 implement it per module.

**Confidence:** HIGH -- directly observable in codebase (CONCERNS.md: "Polling-based state change detection" and "check_state_changes() every 5 seconds").

---

### Pitfall 6: Unsafe Physical Device Control Without Guards

**What goes wrong:** The integration controls physical hardware (battery charge/discharge, EV charger current limits, fuse-level power). If a bug causes the wrong EMS mode, incorrect ampere limits, or simultaneous max-power charging of battery and car, the physical consequences are real: blown fuses, damaged equipment, or fire risk. The existing code has no safety guards -- fuse capacity calculation can produce negative values (CONCERNS.md), and there is no retry validation that a command actually took effect.

**Why it happens:** AppDaemon scripts evolved incrementally with manual testing. The developer was always monitoring. A HACS integration runs unsupervised in other people's homes with different electrical configurations.

**Consequences:** At minimum: blown fuse, tripped breaker. At maximum: equipment damage. Reputation damage to the integration. Liability concerns.

**Prevention:**
- Implement hard safety limits that cannot be overridden by configuration: maximum ampere per phase (from electrical code), minimum/maximum battery SOC bounds
- Add command verification: after sending a charger limit command, read back the actual limit within 10 seconds and retry or alert if mismatch
- Clamp all calculated values: `max(0, min(calculated_amps, absolute_max_amps))` -- never pass unclamped values to service calls
- The fuse calculation (`available_capacity = max_ampere - highest_current + easee_current_dynamic_amps`) must handle negative results, unavailable sensors, and NaN values
- Add a watchdog: if no successful state update in N minutes, revert to safe defaults (max_self_consumption mode, stop charger)
- Log every physical command with full context (calculated value, input values, clamped value, target entity)
- Make fuse rating a required config field with validation, not an optional parameter

**Detection:** Breaker trips. Battery or charger enters error state. Large discrepancy between commanded and actual values.

**Phase relevance:** Phase 2 (Home Battery) and Phase 3 (EV Charging) must implement safety guards before any physical control logic. Consider a dedicated safety module in Phase 1 Core.

**Confidence:** HIGH -- safety-critical concern identified in CONCERNS.md (fuse capacity math not tested, negative values possible, no retry logic).

---

## Moderate Pitfalls

### Pitfall 7: Config Flow Complexity Explosion

**What goes wrong:** The integration needs to configure: Nordpool sensor selection, battery system entities (15+ sensors), charger entities, car entities (per-car), thresholds, departure times, and module enable/disable. Trying to put all of this into a single config flow creates a terrible UX -- 10+ steps with 50+ fields. Users abandon setup or misconfigure.

**Prevention:**
- Minimal initial config flow: select which modules to enable, auto-discover what is possible
- Move all tunable parameters to Options Flow (editable after setup without re-adding)
- Use multi-step Options Flow with one page per module (Core, Battery, EV Charging)
- Auto-discover entity IDs where possible (find SigenStor entities by integration, find Easee entities by device)
- Validate entity existence in the flow step, not at runtime
- The PowerSaver integration already has a working pattern for Nordpool sensor selection -- reuse it

**Detection:** Users report setup is confusing. High rate of misconfiguration. Support issues about "entity not found" after setup.

**Phase relevance:** Phase 1 (Config Flow design). Getting this wrong means re-doing the entire flow later, which also means config migration code.

**Confidence:** MEDIUM -- based on project scope (90+ config parameters in apps.yaml) and HA config flow documentation.

---

### Pitfall 8: Tight Coupling Between Modules

**What goes wrong:** The AppDaemon apps communicate through HA entity state, which provides loose coupling. When porting to a single integration, the temptation is to have modules import each other directly, share coordinator instances, or access each other's internal state. This breaks the "modules work independently" requirement and makes it impossible to enable Battery without EV or vice versa.

**Prevention:**
- Define a clear module boundary: each module (core, home_battery, ev_charging) gets its own coordinator, its own entities, its own platform setup
- Inter-module communication goes through a defined interface on `hass.data[DOMAIN]`, not through direct imports
- Core module provides shared services (price data, fuse monitoring) that other modules consume
- Module registration pattern: each module registers its capabilities with core, core does not import module internals
- Test: disable one module, verify the other still loads and functions

**Detection:** Import cycles between modules. Errors when one module is disabled. Test failures when testing modules in isolation.

**Phase relevance:** Phase 1 (Architecture). The module boundary design must be right before building Phase 2 and 3.

**Confidence:** MEDIUM -- architectural risk specific to this project's modularity requirement.

---

### Pitfall 9: State Machine Porting Without Formal Model

**What goes wrong:** The Easee charging sequence (awaiting_start -> ready_to_charge -> charging) and the EMS mode state machine (command_charging, max_self_consumption, standby) are implemented as ad-hoc if/elif chains with timed delays. Porting these as-is preserves the fragility identified in CONCERNS.md: concurrent commands, missed transitions, and stuck states.

**Prevention:**
- Model each state machine explicitly: define states, valid transitions, guards, and actions
- Use a simple state machine pattern (enum states + transition table) rather than if/elif chains
- Add transition logging: every state change logged with previous state, trigger, and new state
- Add stuck-state detection: if a state machine has not transitioned in N minutes and conditions suggest it should have, raise a repair issue
- The Easee start sequence's parallel delays (4s + 5s) must become a proper async state machine with timeout handling
- Write state machine tests before porting the logic

**Detection:** Charger stuck in "awaiting_start". Battery stuck in "command_charging" when it should be idle. Logs show repeated failed transitions.

**Phase relevance:** Phase 3 (EV Charging module). The Easee state machine is the most fragile component in the codebase.

**Confidence:** HIGH -- directly identified in CONCERNS.md as fragile, with documented stuck-state bugs.

---

### Pitfall 10: Ignoring HA Entity Naming Conventions (has_entity_name)

**What goes wrong:** Developers create entities with hardcoded `name` properties like "Battery Charge Schedule" instead of using `has_entity_name = True` with short data-point names like "Charge Schedule". This causes entity names to NOT include the device name, or to double-include it (e.g., "Energy Manager Energy Manager Battery Schedule"). HA has moved firmly to the `has_entity_name` pattern and custom integrations that do not follow it look unprofessional and confuse users.

**Prevention:**
- Set `has_entity_name = True` on all entity classes
- Entity `name` should describe only the data point: "Charge Schedule", "Battery SOC", "Current Limit"
- Device name (from `device_info`) automatically prefixes the entity name
- Use `translation_key` for entity names to support localization
- Use `EntityDescription` with `key` and `translation_key` for declarative entity definitions
- Test: entity friendly names should read as "[Device Name] [Data Point]", e.g., "Home Battery Charge Schedule"

**Detection:** Entity names look wrong in the UI. Double device names. Entities not grouped under devices.

**Phase relevance:** Phase 1 (entity base classes). Must be correct from the start -- changing naming later requires entity registry migration.

**Confidence:** HIGH -- documented HA requirement (entity_index documentation explicitly states this pattern is mandatory for new integrations).

---

### Pitfall 11: Not Handling External Integration Unavailability

**What goes wrong:** The integration depends on 4-5 external HA integrations (Nordpool, SigenStor, Easee, Skoda/VW). If any of these is temporarily unavailable (restarting, network issue, integration update), the energy manager must degrade gracefully rather than crash or make bad decisions based on stale/missing data.

**Prevention:**
- Check entity states for "unavailable" and "unknown" before using values -- these are NOT the same as missing entities
- Use `async_track_state_change_event` with filters to detect when dependencies come back online
- Define fallback behavior for each dependency: if Nordpool unavailable, use last known prices or pause scheduling; if battery sensors unavailable, revert to safe mode (max_self_consumption); if charger unavailable, do nothing (safe default)
- Add a "dependency status" diagnostic entity showing which external integrations are healthy
- Never cache "unavailable" as a real value -- the AppDaemon code has `or 0` fallbacks that would interpret unavailable battery SOC as 0%, triggering full charge

**Detection:** Logs show "Cannot convert 'unavailable' to float". Charging decisions made on stale data. System enters unexpected modes when an external integration restarts.

**Phase relevance:** Every phase. Phase 1 should define the unavailability handling pattern, Phase 2-3 implement it per dependency.

**Confidence:** HIGH -- multiple instances of `or 0` fallback pattern in existing code (CONVENTIONS.md, CONCERNS.md).

---

## Minor Pitfalls

### Pitfall 12: Hardcoded Nordpool Attribute Names

**What goes wrong:** The existing code accesses Nordpool price data via `raw_today` and `raw_tomorrow` attributes. The HACS Nordpool integration and the native HA Nordpool integration use different attribute names and structures. Hardcoding one breaks the other.

**Prevention:**
- Use the Nordpool adapter pattern already proven in PowerSaver -- it abstracts both variants behind a common interface
- Add integration auto-detection: check which Nordpool variant is installed and select the correct adapter
- Make the adapter testable with mock data

**Detection:** Prices show as empty/None. Schedule is empty because no prices were loaded.

**Phase relevance:** Phase 1 (Core/adapters). Already solved in PowerSaver, mainly a "do not forget to reuse it" reminder.

**Confidence:** HIGH -- explicitly documented in PROJECT.md and proven in PowerSaver.

---

### Pitfall 13: HACS Validation Requirements

**What goes wrong:** HACS has specific repository structure and metadata requirements that are not enforced until you try to publish. Missing `hacs.json`, wrong `manifest.json` fields, missing `version` key, incorrect `iot_class`, or wrong directory structure causes HACS validation to fail.

**Prevention:**
- `hacs.json` at repo root with `name`, `render_readme: true`
- `manifest.json` must include `version` key (required for custom integrations, not for core)
- `iot_class` must accurately reflect the integration's I/O pattern -- this is likely `"calculated"` for the scheduling logic (no direct device I/O) but needs review since it also controls devices
- Repository must have `custom_components/energy_manager/` directory structure
- Run `hacs validate` action in CI before publishing
- Add GitHub Actions workflow with HACS validation from day one (use `hacs/action` GitHub Action)

**Detection:** HACS validation fails on publish attempt. Users cannot install via HACS.

**Phase relevance:** Phase 1 (repository setup). Should be validated in CI from the first commit.

**Confidence:** HIGH -- well-documented HACS requirements. Already navigated once with PowerSaver.

---

### Pitfall 14: Translation and Localization Gaps

**What goes wrong:** Entity names, config flow labels, and option descriptions are hardcoded in English rather than using the HA translation system (`strings.json` / `translations/`). This is not just about supporting other languages -- HA uses translation keys for consistent UI rendering, and missing translations cause ugly raw keys in the UI.

**Prevention:**
- Create `strings.json` with all config flow step titles, field labels, and descriptions from day one
- Use `translation_key` on entities rather than hardcoded `name` strings
- Structure: `strings.json` for source, `translations/en.json` as copy, additional languages later
- The config flow is where this matters most -- every step, field, and error message needs a translation key

**Detection:** Config flow shows raw keys like "config.step.user.data.nordpool_entity". Entity names show translation keys instead of human text.

**Phase relevance:** Phase 1 (Config Flow). Easy to add from the start, painful to retrofit.

**Confidence:** MEDIUM -- based on HA developer documentation. Not strictly required for HACS but creates poor UX without.

---

### Pitfall 15: Testing Strategy Mismatch (Unit vs Integration)

**What goes wrong:** Developers write unit tests that mock everything (hass object, entities, state) but never test that the integration actually loads in HA, creates the right entities, and responds to real state changes. Conversely, some write only integration tests that are slow and flaky.

**Prevention:**
- Layer the testing: pure algorithm tests (peak grouping, fuse calculation, schedule building) need zero HA mocking
- Extract algorithms into pure functions that take data in and return results -- these are trivially testable
- Use `pytest-homeassistant-custom-component` for integration tests that verify setup/unload/entity creation
- The fuse capacity math (CONCERNS.md: "Critical - safety issue") MUST have exhaustive unit tests before any integration testing
- Test the state machine transitions with a simple state machine test harness
- Do not mock what you can calculate: price data, SOC values, and schedules are all deterministic given inputs

**Detection:** Tests pass but integration fails in real HA. Tests are slow and flaky. Safety-critical math has no test coverage.

**Phase relevance:** Phase 1 (test infrastructure). Algorithm extraction and testing should happen before porting logic into HA entities.

**Confidence:** MEDIUM -- based on codebase analysis (no existing tests) and standard testing best practices.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Phase 1: Core/Infrastructure | Async event loop blocking (#1) | Audit every ported line for blocking calls; use async patterns from start |
| Phase 1: Core/Infrastructure | Config entry lifecycle (#3) | Implement unload before any feature code; test reload early |
| Phase 1: Core/Infrastructure | Entity architecture (#4, #10) | Design entity model with has_entity_name, device_info, EntityDescription before coding |
| Phase 1: Core/Infrastructure | Module coupling (#8) | Define module interfaces before implementation; test isolation |
| Phase 1: Core/Infrastructure | HACS structure (#13) | Set up repo structure and CI validation on day one |
| Phase 2: Home Battery | Attribute size (#2) | Use coordinator data, not entity attributes, for schedule storage |
| Phase 2: Home Battery | Polling intervals (#5) | Use state change listeners for Nordpool prices, coordinator for recalc |
| Phase 2: Home Battery | Safety guards (#6) | Implement SOC limits and EMS mode safety before scheduling logic |
| Phase 2: Home Battery | Dependency unavailability (#11) | Handle SigenStor and Nordpool being unavailable |
| Phase 3: EV Charging | State machine fragility (#9) | Model Easee charging sequence as formal state machine |
| Phase 3: EV Charging | Safety guards (#6) | Fuse calculation with hard clamping before any charger control |
| Phase 3: EV Charging | Dependency unavailability (#11) | Handle Easee, car integrations being unavailable |
| Phase 4: Coordination | Tight coupling (#8) | Verify modules work independently before adding coordination |
| All Phases | Testing gaps (#15) | Extract algorithms to pure functions; test before porting |
| All Phases | Translation (#14) | Add translation keys as entities are created, not after |

## Sources

- Home Assistant Developer Documentation: entity architecture (https://developers.home-assistant.io/docs/entity_index)
- Home Assistant Developer Documentation: config entries (https://developers.home-assistant.io/docs/config_entries_index)
- Home Assistant Developer Documentation: creating integrations (https://developers.home-assistant.io/docs/creating_component_index)
- Home Assistant Developer Documentation: integration quality scale (https://developers.home-assistant.io/docs/integration_quality_scale_index)
- Project codebase analysis: `.planning/codebase/CONCERNS.md` (tech debt, bugs, fragile areas)
- Project codebase analysis: `.planning/codebase/ARCHITECTURE.md` (data flow, state management)
- Project codebase analysis: `.planning/codebase/INTEGRATIONS.md` (external dependencies)
- Project codebase analysis: `.planning/codebase/CONVENTIONS.md` (error handling patterns)
- Prior art: PowerSaver HACS integration (Nordpool adapter pattern, config flow)

---

*Pitfalls audit: 2026-02-15*

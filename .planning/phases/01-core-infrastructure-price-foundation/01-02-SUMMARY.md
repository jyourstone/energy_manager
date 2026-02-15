---
phase: 01-core-infrastructure-price-foundation
plan: 02
subsystem: config-flow
tags: [config-flow, multi-step-wizard, subentry, auto-detection, translations, selectors]

# Dependency graph
requires:
  - phase: 01-01
    provides: "const.py (config keys), auto_detect.py (entity scanning), nordpool_adapter.py (variant detection)"
provides:
  - "Multi-step config flow wizard (4 steps: Nordpool, modules, battery, EV)"
  - "Car subentry flow with add/reconfigure support"
  - "Stub options flow for Phase 6"
  - "Complete UI translation strings (strings.json + translations/en.json)"
affects: [01-03 integration-core, phase-06 options-flow]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Multi-step config flow with self._data accumulator for cross-step data"
    - "BooleanSelector for module toggle checkboxes"
    - "EntitySelector with domain filtering for entity picker fields"
    - "_add_suggested_values helper for auto-detection pre-fill"
    - "try/except import for OptionsFlowWithReload vs OptionsFlowWithConfigEntry HA version compat"
    - "ConfigSubentryFlow for per-car configuration with independent lifecycle"

key-files:
  created:
    - "custom_components/energy_manager/config_flow.py"
    - "custom_components/energy_manager/strings.json"
    - "custom_components/energy_manager/translations/en.json"
  modified: []

key-decisions:
  - "Used _add_suggested_values helper instead of HA's add_suggested_values_to_schema for cleaner integration with vol.Schema rebuilding"
  - "Car subentry conditionally available only when EV module is enabled (async_get_supported_subentry_types checks options)"
  - "Immutable data contains only Nordpool sensor/type; all module config goes in mutable options for future options flow changes"
  - "Stub options flow returns existing options unchanged -- placeholder for Phase 6 full implementation"

patterns-established:
  - "Config flow wizard pattern: self._data dict accumulates across steps, _create_entry separates data/options"
  - "Auto-detection pre-fill: suggested_value in schema description, not default value (allows clearing)"
  - "Subentry registration: async_get_supported_subentry_types gates on module enabled state"

# Metrics
duration: 2min
completed: 2026-02-15
---

# Phase 1 Plan 2: Config Flow Wizard and Translations Summary

**Multi-step config flow wizard with Nordpool auto-detection, conditional module steps (battery/EV), car subentry flow, and complete UI translation strings**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-15T18:25:00Z
- **Completed:** 2026-02-15T18:27:28Z
- **Tasks:** 2
- **Files created:** 3

## Accomplishments
- Four-step config flow wizard: Nordpool sensor selection (with auto-detect), module toggles (BooleanSelector checkboxes), conditional battery config (SigenStor auto-detect), conditional EV config (Easee auto-detect)
- Car subentry flow supporting add and reconfigure operations with auto-detected Skoda/VW entity pre-fill
- Complete translation strings for all config flow steps, error messages, abort reasons, car subentry steps, and stub options flow
- Correct data/options separation: immutable Nordpool config in entry.data, mutable module settings in entry.options

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement multi-step config flow wizard with car subentry flow** - `17bcb9f` (feat)
2. **Task 2: Create translation strings for all config flow steps** - `e80d2a2` (feat)

## Files Created/Modified
- `custom_components/energy_manager/config_flow.py` - Multi-step config flow wizard, car subentry flow, stub options flow (427 lines)
- `custom_components/energy_manager/strings.json` - UI translation strings for all config flow steps, errors, aborts, subentries, options
- `custom_components/energy_manager/translations/en.json` - English translations (exact copy of strings.json per HA convention)

## Decisions Made
- Used a custom `_add_suggested_values` helper to inject `suggested_value` into schema descriptions for auto-detection pre-fill, rather than importing HA's `add_suggested_values_to_schema` -- this keeps the code self-contained and explicit about the schema rebuilding logic
- Car subentry availability is gated by `CONF_EV_ENABLED` in options -- if EV module is disabled, no car subentries can be created
- Immutable `entry.data` contains only `CONF_NORDPOOL_SENSOR` and `CONF_NORDPOOL_TYPE`; everything else (module toggles, entity configs) is in mutable `entry.options` for future options flow
- Stub options flow creates entry with unchanged options -- serves as placeholder so HA does not error when users click "Configure"

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Config flow is complete and ready for Plan 03 (integration core) to use `entry.data` and `entry.options` in `async_setup_entry`
- Translation strings cover all current UI needs; future phases add entity translation keys to the existing `entity.sensor` section
- Stub options flow ready for Phase 6 expansion
- No blockers identified

## Self-Check: PASSED

All 3 created files verified on disk. Both task commits (17bcb9f, e80d2a2) verified in git log.

---
*Phase: 01-core-infrastructure-price-foundation*
*Completed: 2026-02-15*

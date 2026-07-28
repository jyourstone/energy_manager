---
phase: 01-core-infrastructure-price-foundation
plan: 03
subsystem: infra
tags: [coordinator, nordpool, price-data, lifecycle, device-registry, runtime-data]

# Dependency graph
requires:
  - phase: 01-01
    provides: "const.py, nordpool_adapter.py, entity.py"
provides:
  - "PriceCoordinator with hybrid polling + event-driven Nordpool updates"
  - "EnergyManagerData and EnergyManagerConfigEntry typed runtime data"
  - "Integration lifecycle (setup/unload/reload) with hub device registration"
  - "Conditional platform forwarding extensible for future modules"
affects: [01-02 config-flow, all future phases that read price data or add entity platforms]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "DataUpdateCoordinator[PriceData] with hybrid update strategy"
    - "Frozen PriceSlot dataclass with slots for memory efficiency"
    - "Typed runtime_data via EnergyManagerConfigEntry alias"
    - "Hub device registered in async_setup_entry with SERVICE entry type"
    - "Conditional platform forwarding via _get_enabled_platforms helper"

key-files:
  created:
    - "custom_components/energy_manager/coordinator.py"
    - "custom_components/energy_manager/__init__.py"
  modified: []

key-decisions:
  - "Used module-level type alias (EnergyManagerConfigEntry = ConfigEntry[EnergyManagerData]) instead of Python 3.12 type statement for broader tooling compatibility"
  - "Extracted _convert_to_price_slots and _get_current_price as module-level helpers for testability"
  - "Used always_update=False on coordinator to avoid unnecessary listener notifications"

patterns-established:
  - "Price data access pattern: entry.runtime_data.price_coordinator.data"
  - "Listener cleanup pattern: config_entry.async_on_unload(async_track_state_change_event(...))"
  - "Module-enabled check pattern: entry.options.get(CONF_*_ENABLED, False)"

# Metrics
duration: 2min
completed: 2026-02-15
---

# Phase 1 Plan 3: Integration Core and Price Coordinator Summary

**PriceCoordinator with 5-min polling + event-driven Nordpool updates, typed runtime data, and full setup/unload/migrate lifecycle with hub device registration**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-15T18:25:01Z
- **Completed:** 2026-02-15T18:27:10Z
- **Tasks:** 2
- **Files created:** 2

## Accomplishments
- PriceCoordinator implementing hybrid update strategy: 5-minute polling baseline with immediate refresh on Nordpool sensor state changes (e.g., when tomorrow's prices arrive at ~13:00 CET)
- Frozen PriceSlot dataclass with UTC-aware timestamps and PriceData container for today/tomorrow hourly slots
- Full integration lifecycle: async_setup_entry creates coordinator + hub device + runtime_data, async_unload_entry cleans up platforms, async_migrate_entry provides version migration hook
- Typed runtime data (EnergyManagerData) stored on config entry for downstream module access

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement PriceCoordinator with hybrid update strategy** - `a375a79` (feat)
2. **Task 2: Implement integration lifecycle with hub device registration** - `397db99` (feat)

## Files Created/Modified
- `custom_components/energy_manager/coordinator.py` - PriceCoordinator, PriceSlot, PriceData, EnergyManagerData, EnergyManagerConfigEntry
- `custom_components/energy_manager/__init__.py` - Integration entry point with setup/unload/migrate lifecycle and hub device registration

## Decisions Made
- Used module-level type alias (`EnergyManagerConfigEntry = ConfigEntry[EnergyManagerData]`) instead of Python 3.12 `type` statement -- ensures compatibility with static analysis tools and older Python parsers while still providing type safety in HA runtime
- Extracted `_convert_to_price_slots` and `_get_current_price` as module-level helper functions rather than coordinator methods for better testability and separation of concerns
- Used `always_update=False` on DataUpdateCoordinator to avoid unnecessary listener notifications when data hasn't changed

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- PriceCoordinator ready for all downstream modules to consume price data via `entry.runtime_data.price_coordinator.data`
- Hub device registered with `(DOMAIN, entry.entry_id)` identifiers matching entity.py base class device_info
- Platform forwarding infrastructure in place for future phases to add entity platforms
- Integration lifecycle fully implemented: restarts, reloads, and unloads handled cleanly

## Self-Check: PASSED

All 2 created files verified on disk. Both task commits (a375a79, 397db99) verified in git log.

---
*Phase: 01-core-infrastructure-price-foundation*
*Completed: 2026-02-15*

---
phase: 01-core-infrastructure-price-foundation
plan: 01
subsystem: infra
tags: [hacs, nordpool, entity-registry, auto-detection, custom-integration]

# Dependency graph
requires: []
provides:
  - "HACS-compliant custom_components/energy_manager/ directory structure"
  - "Shared constants module (const.py) for all downstream plans"
  - "Nordpool price adapter supporting HACS and native variants"
  - "Auto-detection module for Nordpool, SigenStor, Easee, Skoda/VW"
  - "Base entity class (EnergyManagerEntity) for all module entities"
affects: [01-02 config-flow, 01-03 integration-core, all future phases]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CoordinatorEntity base class with hub device info"
    - "Nordpool dual-variant adapter (HACS attributes vs native service calls)"
    - "Entity registry scanning for auto-detection"

key-files:
  created:
    - "custom_components/energy_manager/manifest.json"
    - "custom_components/energy_manager/hacs.json"
    - "custom_components/energy_manager/const.py"
    - "custom_components/energy_manager/entity.py"
    - "custom_components/energy_manager/nordpool_adapter.py"
    - "custom_components/energy_manager/auto_detect.py"
  modified: []

key-decisions:
  - "Ported nordpool_adapter.py from PowerSaver verbatim (proven production code)"
  - "Auto-detect uses entity_id pattern matching and unique_id for SigenStor/Easee/car entity identification"
  - "Car detection groups entities by device_id to associate battery level with correct vehicle"

patterns-established:
  - "from .const import pattern: all modules import constants from const.py"
  - "Auto-detection returns empty results (not exceptions) when integrations not found"
  - "Hub device pattern: DeviceInfo with (DOMAIN, entry_id) identifiers and DeviceEntryType.SERVICE"

# Metrics
duration: 2min
completed: 2026-02-15
---

# Phase 1 Plan 1: Project Scaffold and Foundations Summary

**HACS-compliant integration skeleton with Nordpool dual-variant price adapter (ported from PowerSaver) and entity registry auto-detection for Nordpool, SigenStor, Easee, and Skoda/VW cars**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-15T18:20:01Z
- **Completed:** 2026-02-15T18:22:23Z
- **Tasks:** 2
- **Files created:** 6

## Accomplishments
- HACS-compliant directory structure with valid manifest.json (integration_type hub, min_version 2024.12.0) and hacs.json
- Complete shared constants module covering Nordpool, battery, EV, and car subentry configuration keys
- Nordpool adapter faithfully ported from PowerSaver -- handles HACS variant (raw_today/raw_tomorrow attributes) and native variant (nordpool.get_prices_for_date service call with MWh-to-kWh conversion)
- Auto-detection module scanning entity registry and config entries for four integration families

## Task Commits

Each task was committed atomically:

1. **Task 1: Create HACS-compliant project scaffold and shared constants** - `c0a39d5` (feat)
2. **Task 2: Port Nordpool adapter and create auto-detection module** - `c9b9b14` (feat)

## Files Created/Modified
- `custom_components/energy_manager/manifest.json` - HACS installation metadata with domain, codeowners, integration_type hub
- `custom_components/energy_manager/hacs.json` - HACS repository metadata for custom repository listing
- `custom_components/energy_manager/const.py` - All shared constants: domain, config keys, module IDs, update intervals
- `custom_components/energy_manager/entity.py` - EnergyManagerEntity base class (CoordinatorEntity) with hub DeviceInfo
- `custom_components/energy_manager/nordpool_adapter.py` - Nordpool price adapter supporting HACS and native HA variants
- `custom_components/energy_manager/auto_detect.py` - Entity registry scanner for Nordpool, SigenStor, Easee, Skoda/VW

## Decisions Made
- Ported nordpool_adapter.py from PowerSaver with minimal changes (import paths only) -- proven production code, no reason to rewrite
- Auto-detection uses both entity_id pattern matching and unique_id inspection for robust entity identification
- Car detection groups entities by device_id to correctly associate battery level sensors with individual vehicles
- SigenStor detection filters on domain containing "sigen" (not exact match) to handle potential variations

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All foundation files ready for Plan 02 (config flow) to import from const.py and use auto_detect.py
- Plan 03 (integration core) can import EnergyManagerEntity from entity.py and nordpool_adapter for price data
- No blockers identified

## Self-Check: PASSED

All 6 created files verified on disk. Both task commits (c0a39d5, c9b9b14) verified in git log.

---
*Phase: 01-core-infrastructure-price-foundation*
*Completed: 2026-02-15*

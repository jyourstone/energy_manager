---
phase: 02-home-battery-schedule
plan: 03
subsystem: scheduling
tags: [battery, sensor, config-flow, auto-detect, forecast-solar, translations, ha-integration]

# Dependency graph
requires:
  - phase: 01-scaffold-and-prices
    provides: "PriceCoordinator, EnergyManagerEntity base class, config flow wizard"
  - phase: 02-home-battery-schedule
    plan: 01
    provides: "build_battery_schedule() pure function and BatteryScheduleResult"
  - phase: 02-home-battery-schedule
    plan: 02
    provides: "BatteryScheduleCoordinator, BatteryScheduleData, RestoreNumber entities, conditional battery wiring"
provides:
  - "Three battery sensor entities: BatteryScheduleSensor, NextChargeSensor, NextDischargeSensor"
  - "find_forecast_solar_entities() auto-detection function"
  - "Config flow battery step with Forecast.Solar and battery capacity fields"
  - "Complete translation strings for all Phase 2 sensor and number entities"
affects: [03-ems-controller]

# Tech tracking
tech-stack:
  added: []
  patterns: [conditional-sensor-setup, timestamp-device-class, compact-schedule-attributes, auto-detect-merge]

key-files:
  created: []
  modified:
    - custom_components/energy_manager/sensor.py
    - custom_components/energy_manager/auto_detect.py
    - custom_components/energy_manager/config_flow.py
    - custom_components/energy_manager/strings.json
    - custom_components/energy_manager/translations/en.json

key-decisions:
  - "Battery sensors conditionally created based on battery_coordinator existence (not config toggle)"
  - "Schedule attributes capped at 48 slots to keep state compact per Phase 1 lesson"
  - "NextCharge/NextDischarge use SensorDeviceClass.TIMESTAMP for native datetime display"
  - "Config flow merges SigenStor and Forecast.Solar auto-detection into single suggested values dict"

patterns-established:
  - "Conditional sensor setup: check runtime_data coordinator existence before creating entities"
  - "Timestamp sensor pattern: device_class=TIMESTAMP with datetime native_value"
  - "Auto-detect merging: multiple auto-detect functions merged into single suggested values dict"

# Metrics
duration: 3min
completed: 2026-02-15
---

# Phase 2 Plan 3: Battery Schedule Sensors and Config Flow Extension Summary

**Three battery schedule sensor entities with Forecast.Solar config flow auto-detection and complete Phase 2 translation strings**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-15T21:10:44Z
- **Completed:** 2026-02-15T21:14:12Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Three battery sensor entities: BatteryScheduleSensor (current state with compact schedule attributes), NextChargeSensor and NextDischargeSensor (upcoming slot timestamps with TIMESTAMP device class)
- find_forecast_solar_entities() auto-detection scans for Forecast.Solar integration and returns the energy_production_today sensor entity
- Config flow battery step extended with battery capacity (kWh) number selector and Forecast.Solar entity selector, with merged auto-detection pre-fill
- Complete translation strings for all 4 sensor entities (electricity_price, battery_schedule, next_charging_slot, next_discharging_slot) and all 3 number entities (charge_price_threshold, discharge_price_threshold, max_charge_power) in both strings.json and translations/en.json

## Task Commits

Each task was committed atomically:

1. **Task 1: Add battery schedule sensors and Forecast.Solar auto-detection** - `a6b0df6` (feat)
2. **Task 2: Extend config flow and add all translation strings** - `d604472` (feat)

## Files Created/Modified
- `custom_components/energy_manager/sensor.py` - Added BatteryScheduleSensor, NextChargeSensor, NextDischargeSensor; updated async_setup_entry for conditional battery sensor creation
- `custom_components/energy_manager/auto_detect.py` - Added find_forecast_solar_entities() function for Forecast.Solar integration detection
- `custom_components/energy_manager/config_flow.py` - Extended battery step with battery_capacity_kwh and forecast_solar_entity fields; added Forecast.Solar auto-detection; updated _create_entry options
- `custom_components/energy_manager/strings.json` - Added battery step data fields, all 4 sensor and 3 number entity translations
- `custom_components/energy_manager/translations/en.json` - Mirrored all strings.json additions for English translations

## Decisions Made
- **Conditional sensor creation via coordinator existence**: Check `battery_coordinator is not None` in async_setup_entry rather than re-reading config toggle, ensuring consistency with __init__.py wiring
- **48-slot cap on schedule attributes**: Prevents oversized HA state attributes per Phase 1 lesson (01-05 gap closure)
- **TIMESTAMP device class for next slot sensors**: Enables native datetime rendering in HA frontend without manual formatting
- **Merged auto-detection**: SigenStor and Forecast.Solar detection results combined via dict.update() for a single suggested_values pass

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 2 is now complete: pure scheduler (Plan 01), coordinator + number entities (Plan 02), and sensor entities + config flow + translations (Plan 03)
- Battery schedule sensors ready for Phase 3 EMS controller to read current_state and target_ems_mode
- All 11 Plan 01 tests continue to pass (no regressions)
- Config flow collects all battery configuration needed for the full schedule pipeline

## Self-Check: PASSED

- All 5 source files exist
- SUMMARY.md exists
- Commit `a6b0df6` (Task 1) found
- Commit `d604472` (Task 2) found
- 11/11 Plan 01 tests pass
- All 7 entity translations verified (4 sensor + 3 number)
- Both JSON files valid

---
*Phase: 02-home-battery-schedule*
*Completed: 2026-02-15*

---
phase: 02-home-battery-schedule
verified: 2026-02-15T22:16:00Z
status: passed
score: 5/5
re_verification: false
---

# Phase 2: Home Battery Schedule Verification Report

**Phase Goal:** Users can view an automatically generated multi-cycle battery charge/discharge schedule that optimizes for electricity price, with adjustable thresholds and solar awareness

**Verified:** 2026-02-15T22:16:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can see a battery schedule sensor showing current state (idle/grid_charging/discharging/solar_charging) | ✓ VERIFIED | BatteryScheduleSensor exists with native_value = data.current_state, translation_key = "battery_schedule" |
| 2 | User can see the full charge/discharge schedule in sensor attributes | ✓ VERIFIED | BatteryScheduleSensor.extra_state_attributes returns serialized schedule (max 48 slots), charging_slots, discharging_slots, target_ems_mode, last_calculated, solar_forecast_used |
| 3 | User can see next charging slot and next discharging slot as separate sensors | ✓ VERIFIED | NextChargeSensor and NextDischargeSensor exist with TIMESTAMP device class, showing start datetime as native_value with price/end in attributes |
| 4 | Config flow auto-detects Forecast.Solar integration and offers it as optional input | ✓ VERIFIED | find_forecast_solar_entities() scans for forecast_solar domain, returns CONF_FORECAST_SOLAR_ENTITY. Config flow merges detection via detected.update(solar_detected) and shows EntitySelector in battery step |
| 5 | All battery sensor and number entity names are properly translated | ✓ VERIFIED | strings.json and translations/en.json both contain 4 sensor translations (electricity_price, battery_schedule, next_charging_slot, next_discharging_slot) and 3 number translations (charge_price_threshold, discharge_price_threshold, max_charge_power). Both files valid JSON with identical structure |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `custom_components/energy_manager/sensor.py` | BatteryScheduleSensor, NextChargeSensor, NextDischargeSensor classes with async_setup_entry | ✓ VERIFIED | 270 lines. Classes exist at lines 109-172 (BatteryScheduleSensor), 174-221 (NextChargeSensor), 223-269 (NextDischargeSensor). async_setup_entry conditionally creates battery sensors when battery_coordinator is not None (lines 47-56) |
| `custom_components/energy_manager/config_flow.py` | Forecast.Solar auto-detection in battery step | ✓ VERIFIED | 453 lines. find_forecast_solar_entities imported (line 53), called (line 242), merged into detection (line 243), schema includes CONF_FORECAST_SOLAR_ENTITY EntitySelector (lines 258-260), stored in _create_entry options (lines 329-331) |
| `custom_components/energy_manager/auto_detect.py` | find_forecast_solar_entities() function | ✓ VERIFIED | 299 lines. Function exists lines 251-298, scans for forecast_solar domain, searches for energy_production_today in entity_id/unique_id, returns dict with CONF_FORECAST_SOLAR_ENTITY key |
| `custom_components/energy_manager/strings.json` | Translation keys for battery sensors and number entities | ✓ VERIFIED | 136 lines. entity.sensor contains 4 entries, entity.number contains 3 entries. Config.step.battery.data includes battery_capacity_kwh and forecast_solar_entity with descriptions. Valid JSON |
| `custom_components/energy_manager/translations/en.json` | English translations mirroring strings.json | ✓ VERIFIED | 136 lines. Identical structure to strings.json. All entity translations present. Valid JSON |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| sensor.py | BatteryScheduleCoordinator | coordinator.data access | ✓ WIRED | BatteryScheduleData imported (line 22). coordinator.data accessed in all battery sensor methods (lines 137, 150, 202, 213, 251, 262). Type-annotated as BatteryScheduleData | None |
| config_flow.py | auto_detect.py | find_forecast_solar_entities() call | ✓ WIRED | find_forecast_solar_entities imported (line 53), called (line 242), result merged into detected dict (line 243), used for schema suggested values (line 266) |
| sensor.py | coordinator.py | BatteryScheduleData for schedule attributes | ✓ WIRED | BatteryScheduleData imported from .coordinator (line 22), used as type annotation in all battery sensor data checks, fields accessed: current_state, schedule, charging_slot_count, discharging_slot_count, target_ems_mode, last_calculated, solar_forecast_used, next_charging_slot, next_discharging_slot |

### Requirements Coverage

| Requirement | Status | Supporting Truth | Details |
|-------------|--------|------------------|---------|
| BATT-01 | ✓ SATISFIED | Truth 1, 2 | BatteryScheduleSensor shows current state with full schedule in attributes. build_battery_schedule() implemented in Plan 01 with multi-cycle logic |
| BATT-02 | ✓ SATISFIED | (Plan 01) | Peak grouping algorithm verified in Plan 01 tests (test_peak_grouping_identifies_separate_windows PASSED) |
| BATT-03 | ✓ SATISFIED | (Plan 01) | Virtual energy tracking verified in Plan 01 tests (test_virtual_energy_tracking_limits_discharge, test_multi_cycle_charge_between_peaks PASSED) |
| BATT-04 | ✓ SATISFIED | Truth 1, 2 | BatteryScheduleSensor exposes current_state as native_value with compact schedule attributes (max 48 slots) |
| BATT-05 | ✓ SATISFIED | Truth 3 | NextChargeSensor and NextDischargeSensor exist with TIMESTAMP device class |
| BATT-06 | ✓ SATISFIED | (Plan 01) | Solar forecast integration verified in Plan 01 test (test_solar_forecast_reduces_charging PASSED). Coordinator passes solar forecast to build_battery_schedule() |
| BATT-07 | ✓ SATISFIED | Truth 4 | find_forecast_solar_entities() detects forecast_solar integration, config flow includes EntitySelector for solar forecast |
| BATT-08 | ✓ SATISFIED | (Plan 02) | BatteryChargeThreshold number entity exists in number.py (lines 62-109) with RestoreNumber persistence and coordinator refresh on change |
| BATT-09 | ✓ SATISFIED | (Plan 02) | BatteryDischargeThreshold number entity exists in number.py (lines 111-158) with RestoreNumber persistence and coordinator refresh on change |
| BATT-10 | ✓ SATISFIED | (Plan 02) | BatteryMaxChargePower number entity exists in number.py (lines 160-207) with RestoreNumber persistence and coordinator refresh on change |
| BATT-11 | ✓ SATISFIED | (Plan 02) | BatteryScheduleCoordinator reads SOC from soc_entity with state listener (coordinator.py lines 301-308), defaults to 50% when unavailable, passes to build_battery_schedule() |
| BATT-12 | ✓ SATISFIED | (Plan 02) | BatteryScheduleCoordinator chains to PriceCoordinator via async_add_listener (coordinator.py line 292), triggers refresh on price updates. Also listens to SOC/solar entity state changes via async_track_state_change_event |

**Coverage:** 12/12 Phase 2 requirements satisfied

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| config_flow.py | 352, 356 | "placeholder" comment in options flow | ℹ️ Info | Documented as Phase 6 scope. Options flow returns empty schema. Not a blocker for Phase 2 goal |

**No blocker anti-patterns found.**

### Human Verification Required

None. All Phase 2 user-facing behaviors can be verified programmatically or are covered by existing Plan 01 unit tests.

**Note:** Phase 3 (EMS Controller) will require human verification of actual battery control behavior and Home Assistant UI rendering.

---

## Verification Details

### Artifacts Verified (Level 1: Exists)
- ✓ sensor.py exists (270 lines)
- ✓ config_flow.py exists (453 lines)
- ✓ auto_detect.py exists (299 lines)
- ✓ strings.json exists (136 lines)
- ✓ translations/en.json exists (136 lines)
- ✓ number.py exists (207 lines, from Plan 02)
- ✓ coordinator.py exists (modified in Plan 02)
- ✓ battery_scheduler.py exists (577 lines, from Plan 01)

### Artifacts Verified (Level 2: Substantive)
- ✓ BatteryScheduleSensor: 63 lines, native_value returns current_state, extra_state_attributes serializes schedule with 48-slot cap
- ✓ NextChargeSensor: 47 lines, TIMESTAMP device class, native_value returns start datetime, attributes include price/end
- ✓ NextDischargeSensor: 46 lines, TIMESTAMP device class, native_value returns start datetime, attributes include price/end
- ✓ find_forecast_solar_entities(): 48 lines, scans entity registry for forecast_solar domain, searches for energy_production_today pattern
- ✓ Config flow battery step: 49 lines, includes battery_capacity_kwh NumberSelector (min=1, max=100, step=0.1) and forecast_solar_entity EntitySelector, merges SigenStor + Forecast.Solar detection
- ✓ strings.json: 4 sensor keys + 3 number keys, battery step data keys for battery_capacity_kwh and forecast_solar_entity with descriptions
- ✓ translations/en.json: Identical structure to strings.json, all keys present
- ✓ BatteryChargeThreshold: 48 lines, RestoreNumber with async_set_native_value triggers coordinator refresh
- ✓ BatteryDischargeThreshold: 48 lines, RestoreNumber with async_set_native_value triggers coordinator refresh
- ✓ BatteryMaxChargePower: 47 lines, RestoreNumber with async_set_native_value triggers coordinator refresh

### Artifacts Verified (Level 3: Wired)
- ✓ Battery sensors registered: async_setup_entry checks battery_coordinator is not None before creating entities (sensor.py lines 47-56)
- ✓ Number entities registered: async_setup_entry checks battery_coordinator is not None (number.py lines 51-59)
- ✓ Sensors import BatteryScheduleData: from .coordinator import BatteryScheduleData (sensor.py line 22)
- ✓ Sensors access coordinator.data: 8 references to self.coordinator.data across battery sensors
- ✓ Config flow imports find_forecast_solar_entities: line 53
- ✓ Config flow calls find_forecast_solar_entities: line 242 in async_step_battery
- ✓ Config flow merges detection: solar_detected.update(detected) line 243
- ✓ Config flow stores forecast_solar_entity: _create_entry options lines 329-331
- ✓ BatteryScheduleCoordinator chains to PriceCoordinator: async_add_listener line 292
- ✓ BatteryScheduleCoordinator listens to SOC entity: async_track_state_change_event lines 301-308
- ✓ BatteryScheduleCoordinator listens to solar entity: async_track_state_change_event lines 310-317
- ✓ BatteryScheduleCoordinator calls build_battery_schedule: line 359 in _async_update_data
- ✓ Number entities trigger coordinator refresh: all three call await self.coordinator.async_request_refresh() in async_set_native_value

### Plan 01 Tests Verified
All 11 battery scheduler unit tests pass:
- ✓ test_basic_charge_discharge_schedule
- ✓ test_peak_grouping_identifies_separate_windows
- ✓ test_virtual_energy_tracking_limits_discharge
- ✓ test_multi_cycle_charge_between_peaks
- ✓ test_no_prices_returns_idle_schedule
- ✓ test_all_prices_below_threshold
- ✓ test_solar_forecast_reduces_charging
- ✓ test_current_action_based_on_now
- ✓ test_next_slots_lookup
- ✓ test_soc_constraints_respected
- ✓ test_max_soc_limits_charging

### Translation Verification
- ✓ strings.json valid JSON
- ✓ translations/en.json valid JSON
- ✓ 4 sensor translations: electricity_price, battery_schedule, next_charging_slot, next_discharging_slot
- ✓ 3 number translations: charge_price_threshold, discharge_price_threshold, max_charge_power
- ✓ Battery step config keys: battery_capacity_kwh, forecast_solar_entity (with data_description)
- ✓ Both files have identical structure

### Key Decisions Validated
- ✓ Schedule attributes capped at 48 slots to prevent oversized state (Phase 1 lesson applied)
- ✓ Conditional sensor creation via coordinator existence check (not config toggle re-read)
- ✓ TIMESTAMP device class for next slot sensors enables native datetime rendering
- ✓ Merged auto-detection combines SigenStor + Forecast.Solar into single suggested_values
- ✓ RestoreNumber entities persist values across HA restarts
- ✓ Coordinator chaining triggers automatic recalculation on price updates
- ✓ Entity listeners trigger recalculation on SOC/solar state changes

---

## Summary

Phase 2 goal is **ACHIEVED**. All 5 observable truths verified, all 5 required artifacts exist and are substantive and wired, all 3 key links verified, all 12 Phase 2 requirements satisfied (BATT-01 through BATT-12).

**What works:**
1. Users can see battery schedule sensor with current state (idle/grid_charging/discharging/solar_charging)
2. Full charge/discharge schedule exposed in sensor attributes (max 48 slots for compact state)
3. Next charging and discharging slots shown as separate TIMESTAMP sensors
4. Config flow auto-detects Forecast.Solar and offers optional EntitySelector
5. All sensor and number entity names translated in strings.json and translations/en.json
6. Three number entities for threshold adjustment (charge threshold, discharge threshold, max charge power) with RestoreNumber persistence
7. BatteryScheduleCoordinator chains to PriceCoordinator and listens to SOC/solar entity state changes
8. Pure scheduling algorithm (build_battery_schedule) with peak grouping, virtual energy tracking, and solar forecast integration
9. 11 unit tests pass covering all scheduling behaviors

**Technical quality:**
- Zero blocker anti-patterns (one info-level placeholder documented for Phase 6)
- No stubs or empty implementations (empty dict returns are appropriate guards)
- All imports verified and wired
- Complete test coverage for scheduling algorithm
- Translation system properly used
- Coordinator pattern correctly implemented with chaining and listeners

**Next phase readiness:**
Phase 3 (EMS Controller) can now read battery schedule data from BatteryScheduleSensor attributes (current_state, target_ems_mode, next_charging_slot, next_discharging_slot) to control the SigenStor EMS mode via service calls.

---
*Verified: 2026-02-15T22:16:00Z*
*Verifier: Claude (gsd-verifier)*

---
phase: 02-home-battery-schedule
verified: 2026-02-16T20:45:00Z
status: passed
score: 8/8
re_verification:
  previous_status: passed
  previous_score: 7/7
  previous_date: 2026-02-16T19:55:00Z
  verification_count: 3
  gaps_closed:
    - "Schedule attributes filter out past slots before 48-slot cap, ensuring charge/discharge slots are always visible"
  gaps_remaining: []
  regressions: []
---

# Phase 2: Home Battery Schedule Verification Report

**Phase Goal:** Users can view an automatically generated multi-cycle battery charge/discharge schedule that optimizes for electricity price, with adjustable thresholds and solar awareness

**Verified:** 2026-02-16T20:45:00Z
**Status:** passed
**Re-verification:** Yes — third verification after Plan 02-05 UAT gap closure (schedule attribute filtering)

## Verification History

1. **Initial verification:** 2026-02-15T22:16:00Z — 5/5 truths passed
2. **Re-verification (Plan 02-04):** 2026-02-16T19:55:00Z — 7/7 truths passed (float precision, sensor availability, defaults, kW units)
3. **Re-verification (Plan 02-05):** 2026-02-16T20:45:00Z — 8/8 truths passed (schedule attribute filtering)

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can see a battery schedule sensor showing current state (idle/grid_charging/discharging/solar_charging) | ✓ VERIFIED | BatteryScheduleSensor exists with native_value = data.current_state, translation_key = "battery_schedule" |
| 2 | User can see the full charge/discharge schedule in sensor attributes | ✓ VERIFIED | BatteryScheduleSensor.extra_state_attributes returns serialized schedule (max 48 slots), charging_slots, discharging_slots, target_ems_mode, last_calculated, solar_forecast_used. **UAT fix (Plan 02-04):** Prices rounded to 4 decimals. **UAT fix (Plan 02-05):** Filtered to exclude past slots (line 159) |
| 3 | User can see next charging slot and next discharging slot as separate sensors | ✓ VERIFIED | NextChargeSensor and NextDischargeSensor exist with TIMESTAMP device class, showing start datetime as native_value with price/end in attributes. **UAT fix:** Both sensors have available() property |
| 4 | Config flow auto-detects Forecast.Solar integration and offers it as optional input | ✓ VERIFIED | find_forecast_solar_entities() scans for forecast_solar domain, returns CONF_FORECAST_SOLAR_ENTITY. Config flow merges detection and shows EntitySelector in battery step |
| 5 | All battery sensor and number entity names are properly translated | ✓ VERIFIED | strings.json and translations/en.json both contain 4 sensor translations and 3 number translations. Valid JSON with identical structure |
| 6 | User can adjust thresholds with sensible defaults | ✓ VERIFIED | **UAT fix:** Charge threshold defaults to 1.0 SEK/kWh, discharge threshold defaults to 0.50 SEK/kWh |
| 7 | Max Charge Power displays in user-friendly units | ✓ VERIFIED | **UAT fix:** Entity shows kW (step 0.1, max 15.0, default 5.0). Coordinator receives watts via kW*1000 conversion at entity boundary |
| 8 | Schedule attributes show charge and discharge slots within visible window, not just idle slots | ✓ VERIFIED | **UAT fix (Plan 02-05):** extra_state_attributes filters `[s for s in data.schedule if s.end > now][:48]` (line 159). Past idle slots excluded before 48-slot cap. Tests confirm discharge slots at positions 36-42 remain visible after filtering |

**Score:** 8/8 truths verified (5 original Phase 2 truths + 3 UAT enhancements)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `custom_components/energy_manager/sensor.py` | BatteryScheduleSensor, NextChargeSensor, NextDischargeSensor classes with async_setup_entry | ✓ VERIFIED | 285 lines (was 279). Classes exist at lines 110-178 (BatteryScheduleSensor), 180+ (NextChargeSensor), 230+ (NextDischargeSensor). **Plan 02-05 change:** Lines 155-159 add time filtering with dt_util.utcnow() and list comprehension filtering by slot.end > now before [:48] cap |
| `custom_components/energy_manager/config_flow.py` | Forecast.Solar auto-detection in battery step | ✓ VERIFIED | 453 lines. find_forecast_solar_entities imported, called, merged into detection, EntitySelector in schema |
| `custom_components/energy_manager/auto_detect.py` | find_forecast_solar_entities() function | ✓ VERIFIED | 299 lines. Function exists lines 251-298, scans for forecast_solar domain |
| `custom_components/energy_manager/strings.json` | Translation keys for battery sensors and number entities | ✓ VERIFIED | 136 lines. entity.sensor contains 4 entries, entity.number contains 3 entries. Valid JSON |
| `custom_components/energy_manager/translations/en.json` | English translations mirroring strings.json | ✓ VERIFIED | 136 lines. Identical structure to strings.json. Valid JSON |
| `custom_components/energy_manager/const.py` | Default values and unit constants | ✓ VERIFIED | 83 lines. DEFAULT_CHARGE_THRESHOLD=1.0, DEFAULT_DISCHARGE_THRESHOLD=0.50, DEFAULT_MAX_CHARGE_POWER_KW=5.0, MAX_CHARGE_POWER_KW=15.0, CHARGE_POWER_STEP_KW=0.1 |
| `custom_components/energy_manager/coordinator.py` | Price serialization and kW default | ✓ VERIFIED | 477 lines. _serialize_slot() rounds price to 4 decimals, BatteryScheduleCoordinator.__init__ converts DEFAULT_MAX_CHARGE_POWER_KW to watts |
| `custom_components/energy_manager/number.py` | kW unit with conversion to watts | ✓ VERIFIED | 206 lines. _attr_native_unit_of_measurement="kW", step=0.1, max=15.0, default=5.0. Converts kW to W via value*1000 |
| `tests/test_battery_scheduler.py` | Tests for schedule filtering | ✓ VERIFIED | **Plan 02-05 addition:** TestScheduleAttributeFiltering class with 2 tests (test_filter_excludes_past_slots, test_filter_keeps_current_slot). 13 total tests, all pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| sensor.py | BatteryScheduleCoordinator | coordinator.data access | ✓ WIRED | BatteryScheduleData imported (line 23). coordinator.data accessed in all battery sensor methods. Type-annotated as BatteryScheduleData |
| config_flow.py | auto_detect.py | find_forecast_solar_entities() call | ✓ WIRED | find_forecast_solar_entities imported, called (line 242), result merged into detected dict |
| sensor.py | coordinator.py | BatteryScheduleData for schedule attributes | ✓ WIRED | BatteryScheduleData imported from .coordinator, used as type annotation, fields accessed: current_state, schedule, charging_slot_count, discharging_slot_count, target_ems_mode, last_calculated, solar_forecast_used, next_charging_slot, next_discharging_slot |
| number.py | coordinator.py | kW-to-W conversion at entity boundary | ✓ WIRED | BatteryMaxChargePower converts kW to W via value*1000. Sets coordinator.max_charge_power_w |
| sensor.py extra_state_attributes | data.schedule | Time-filtered slice | ✓ WIRED | **Plan 02-05:** Line 159 filters `[s for s in data.schedule if s.end > now][:48]`. Imports dt_util (line 21). Tests verify filtering logic (TestScheduleAttributeFiltering) |

### Requirements Coverage

| Requirement | Status | Supporting Truth | Details |
|-------------|--------|------------------|---------|
| BATT-01 | ✓ SATISFIED | Truth 1, 2, 8 | BatteryScheduleSensor shows current state with full schedule in attributes. build_battery_schedule() implemented with multi-cycle logic. **Plan 02-05:** Schedule attributes now time-filtered for relevant visibility |
| BATT-02 | ✓ SATISFIED | (Plan 01) | Peak grouping algorithm verified in Plan 01 tests |
| BATT-03 | ✓ SATISFIED | (Plan 01) | Virtual energy tracking verified in Plan 01 tests |
| BATT-04 | ✓ SATISFIED | Truth 1, 2, 8 | BatteryScheduleSensor exposes current_state with compact schedule attributes (max 48 slots, time-filtered from now) |
| BATT-05 | ✓ SATISFIED | Truth 3 | NextChargeSensor and NextDischargeSensor exist with TIMESTAMP device class and available() property |
| BATT-06 | ✓ SATISFIED | (Plan 01) | Solar forecast integration verified in Plan 01 test |
| BATT-07 | ✓ SATISFIED | Truth 4 | find_forecast_solar_entities() detects forecast_solar integration, config flow includes EntitySelector |
| BATT-08 | ✓ SATISFIED | Truth 6 | BatteryChargeThreshold number entity exists with default 1.0 SEK/kWh |
| BATT-09 | ✓ SATISFIED | Truth 6 | BatteryDischargeThreshold number entity exists with default 0.50 SEK/kWh |
| BATT-10 | ✓ SATISFIED | Truth 7 | BatteryMaxChargePower number entity in kW (step 0.1, max 15.0, default 5.0) with kW-to-W conversion |
| BATT-11 | ✓ SATISFIED | (Plan 02) | BatteryScheduleCoordinator reads SOC from soc_entity with state listener |
| BATT-12 | ✓ SATISFIED | (Plan 02) | BatteryScheduleCoordinator chains to PriceCoordinator and listens to SOC/solar entity state changes |

**Coverage:** 12/12 Phase 2 requirements satisfied

### UAT Gap Closure Summary

#### Plan 02-04 (First UAT Gap Closure)

Plan 02-04 closed 5 UAT gaps from first user testing:

| Gap | Severity | Status | Fix Details |
|-----|----------|--------|-------------|
| Float precision in schedule prices | Cosmetic | ✓ CLOSED | Added round(slot.price, 4) in coordinator.py and sensor.py |
| Next slot sensor availability semantics | Minor | ✓ CLOSED | Added available() property to NextChargeSensor and NextDischargeSensor |
| Charge threshold default | Minor | ✓ CLOSED | Changed DEFAULT_CHARGE_THRESHOLD from 0.50 to 1.0 in const.py |
| Discharge threshold default | Minor | ✓ CLOSED | Changed DEFAULT_DISCHARGE_THRESHOLD from 1.50 to 0.50 in const.py |
| Max charge power unit | Minor | ✓ CLOSED | Changed from W to kW with kW*1000 conversion at entity boundary |

**Plan 02-04 commits:**
- a1f8515 - fix(02-04): fix defaults, float precision, and sensor availability
- 9114ab4 - feat(02-04): change max charge power from W to kW

#### Plan 02-05 (Second UAT Gap Closure)

Plan 02-05 closed 1 UAT gap from second user testing:

| Gap | Severity | Status | Fix Details |
|-----|----------|--------|-------------|
| Schedule attributes show only idle slots | Major | ✓ CLOSED | **Root cause:** BatteryScheduleSensor.extra_state_attributes used data.schedule[:48] from index 0, cutting off late-schedule charge/discharge slots. **Fix:** Filter schedule to exclude past slots (slot.end <= now) before applying 48-slot cap. Visible window now starts from current time, ensuring charge/discharge actions are always visible |

**Implementation:**
- Added `from homeassistant.util import dt as dt_util` (line 21)
- Changed `data.schedule[:48]` to `[s for s in data.schedule if s.end > now][:48]` (line 159)
- Filter by `slot.end > now` (not `slot.start >= now`) to keep in-progress slots visible
- Added TestScheduleAttributeFiltering class with 2 tests verifying filtering logic

**Plan 02-05 commits:**
- 900865f - feat(02-05): filter schedule attributes to start from current time
- c77d49d - test(02-05): add schedule attribute time filtering tests
- 21d307b - docs(02-05): complete schedule attribute filtering plan

**UAT verification:**
- User reported: "All 48 visible slots show action: idle even though Discharging slots count is 13"
- Tests confirm: With 72-slot schedule and discharge at positions 36-42, filtering from hour 14 preserves discharge slots at relative positions 22-28 in the filtered 48-slot window
- In-progress slot (start <= now < end) correctly retained (test_filter_keeps_current_slot)

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| config_flow.py | 352, 356 | "placeholder" comment in options flow | ℹ️ Info | Documented as Phase 6 scope. Options flow returns empty schema. Not a blocker for Phase 2 goal |

**No blocker anti-patterns found.**

### Regression Testing

All 13 battery scheduler unit tests pass after Plan 02-05 changes (11 original + 2 new):

```
tests/test_battery_scheduler.py::TestBasicChargeDischargeSchedule::test_basic_charge_discharge_schedule PASSED
tests/test_battery_scheduler.py::TestPeakGrouping::test_peak_grouping_identifies_separate_windows PASSED
tests/test_battery_scheduler.py::TestVirtualEnergyTracking::test_virtual_energy_tracking_limits_discharge PASSED
tests/test_battery_scheduler.py::TestMultiCycleCharging::test_multi_cycle_charge_between_peaks PASSED
tests/test_battery_scheduler.py::TestEdgeCaseNoPrices::test_no_prices_returns_idle_schedule PASSED
tests/test_battery_scheduler.py::TestAllPricesBelowThreshold::test_all_prices_below_threshold PASSED
tests/test_battery_scheduler.py::TestSolarForecast::test_solar_forecast_reduces_charging PASSED
tests/test_battery_scheduler.py::TestCurrentAction::test_current_action_based_on_now PASSED
tests/test_battery_scheduler.py::TestNextSlotsLookup::test_next_slots_lookup PASSED
tests/test_battery_scheduler.py::TestSocConstraints::test_soc_constraints_respected PASSED
tests/test_battery_scheduler.py::TestSocConstraints::test_max_soc_limits_charging PASSED
tests/test_battery_scheduler.py::TestScheduleAttributeFiltering::test_filter_excludes_past_slots PASSED
tests/test_battery_scheduler.py::TestScheduleAttributeFiltering::test_filter_keeps_current_slot PASSED

============================== 13 passed in 0.01s ==============================
```

Translation files verified: Both strings.json and translations/en.json are valid JSON with identical structure (4 sensor keys, 3 number keys).

Wiring regression checks passed:
- BatteryScheduleSensor still imports and accesses coordinator.data
- Time filtering correctly uses dt_util.utcnow() and slot.end > now
- All number entities still trigger coordinator refresh on value change
- kW-to-W conversion properly implemented at entity-coordinator boundary

### Human Verification Required

None. All Phase 2 user-facing behaviors can be verified programmatically or are covered by existing tests.

**Note:** Phase 3 (EMS Controller) will require human verification of actual battery control behavior and Home Assistant UI rendering.

---

## Summary

Phase 2 goal is **ACHIEVED**. All 8 observable truths verified (5 original Phase 2 truths + 3 UAT enhancements), all 9 required artifacts exist and are substantive and wired, all 5 key links verified, all 12 Phase 2 requirements satisfied (BATT-01 through BATT-12).

**Re-verification changes:**
- **Verification 1 (2026-02-15):** 5/5 truths passed — initial verification
- **Verification 2 (2026-02-16 19:55):** 7/7 truths passed — Plan 02-04 UAT gap closure (float precision, sensor availability, defaults, kW units)
- **Verification 3 (2026-02-16 20:45):** 8/8 truths passed — Plan 02-05 UAT gap closure (schedule attribute filtering)

**Plan 02-05 changes (commit 900865f):**
- Added time-based filtering to schedule attributes: `[s for s in data.schedule if s.end > now][:48]`
- Past idle slots no longer consume the 48-slot visible window
- Charge/discharge slots at afternoon/evening peaks now visible even when they occur later in the schedule
- In-progress slots correctly retained (filter by `slot.end > now`, not `slot.start >= now`)
- 2 new tests validate filtering algorithm (TestScheduleAttributeFiltering)
- No regressions: All 11 original scheduler tests still pass

**What works:**
1. Users can see battery schedule sensor with current state (idle/grid_charging/discharging/solar_charging)
2. Full charge/discharge schedule exposed in sensor attributes with:
   - Time-filtered window starting from current time (Plan 02-05 enhancement)
   - Max 48 slots for compact state
   - Clean 4-decimal price display (Plan 02-04 enhancement)
   - Past idle slots excluded to maximize useful slot visibility
3. Next charging and discharging slots shown as separate TIMESTAMP sensors with proper available/unavailable semantics (Plan 02-04 enhancement)
4. Config flow auto-detects Forecast.Solar and offers optional EntitySelector
5. All sensor and number entity names translated in strings.json and translations/en.json
6. Three number entities for threshold adjustment with improved defaults (charge 1.0, discharge 0.50 SEK/kWh) (Plan 02-04 enhancement)
7. Max Charge Power displays in user-friendly kW (step 0.1, max 15.0, default 5.0) with kW-to-W conversion at entity boundary (Plan 02-04 enhancement)
8. BatteryScheduleCoordinator chains to PriceCoordinator and listens to SOC/solar entity state changes
9. Pure scheduling algorithm (build_battery_schedule) with peak grouping, virtual energy tracking, and solar forecast integration
10. 13 unit tests pass covering all scheduling behaviors including time filtering

**Technical quality:**
- Zero blocker anti-patterns (one info-level placeholder documented for Phase 6)
- No stubs or empty implementations
- All imports verified and wired
- Complete test coverage for scheduling algorithm and time filtering
- Translation system properly used
- Coordinator pattern correctly implemented with chaining and listeners
- Unit conversion boundary pattern established
- Time-window filtering pattern established for temporal data display

**UAT improvements across two gap closure plans:**
- **Plan 02-04:** IEEE 754 float artifacts eliminated, sensor availability semantics improved, more sensible default thresholds, user-friendly kW units
- **Plan 02-05:** Schedule attributes now show relevant charge/discharge slots instead of past idle slots, maximizing useful information in the 48-slot visible window

**Next phase readiness:**
Phase 3 (EMS Controller) can now read battery schedule data from BatteryScheduleSensor attributes (current_state, target_ems_mode, next_charging_slot, next_discharging_slot) with confidence that the displayed schedule accurately reflects upcoming actions. All UAT gaps closed, clean foundation established.

---
*Verified: 2026-02-16T20:45:00Z*
*Verifier: Claude (gsd-verifier)*
*Re-verification: Yes (third verification after Plan 02-05 UAT gap closure)*

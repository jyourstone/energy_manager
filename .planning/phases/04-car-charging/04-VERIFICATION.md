---
phase: 04-car-charging
verified: 2026-02-23T13:30:00Z
status: human_needed
score: 4/4 success criteria verified
re_verification: true
  previous_status: passed
  previous_score: 4/4
  gaps_closed:
    - "mySkoda cars with battery_percentage entities are now auto-detected in config flow (Plan 04 gap closure)"
    - "Config flow no longer asks for a manual 'Home and plugged in sensor' field (Plan 04 gap closure)"
    - "CarChargingCoordinator._is_home_and_plugged_in() derives home+plugged state from 3 signals (Plan 04 gap closure)"
    - "Debug logging fires when domain matches but no battery entity found (Plan 04 gap closure)"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Add car via config flow UI with mySkoda integration and verify auto-detection"
    expected: "Config flow shows detected car name and battery_level_entity pre-populated from find_car_integrations(); charger_connected_entity and location_entity also pre-populated if detected. No 'Home and plugged in sensor' field appears."
    why_human: "Auto-detection requires a live HA instance with mySkoda integration loaded; cannot run headlessly"
  - test: "Verify each car appears as a separate device in HA Device Registry under a unique name"
    expected: "Each configured car subentry creates a device with its car name, manufacturer='Energy Manager', model='Car', linked via_device to the hub. Four entities visible under each device."
    why_human: "Device registry population requires a running HA instance"
  - test: "Set departure time and target SOC, then verify schedule recalculates with cheapest slots selected before that deadline"
    expected: "CarScheduleSensor state transitions through charge/idle slots matching the cheapest-N algorithm output; attributes show correct schedule, energy_needed_kwh, hours_needed"
    why_human: "Live UI interaction and real Nordpool price data needed to observe end-to-end recalculation"
  - test: "Connect an unrecognized vehicle (no SOC updates for >60 min) and verify fallback charging activates"
    expected: "CarScheduleSensor state shows 'charge' during off-peak hours (cheapest half of available slots) rather than optimized N slots"
    why_human: "Requires physical charger connection and 60-minute elapsed time to trigger SOC staleness threshold"
---

# Phase 4: Car Charging Verification Report

**Phase Goal:** Users can configure per-car charging schedules with departure times and target SOC, and the integration selects the cheapest charging slots automatically
**Verified:** 2026-02-23T13:30:00Z
**Status:** human_needed
**Re-verification:** Yes -- after Plan 04 gap closure (auto-detection expansion + home+plugged derivation)

---

## Summary of Changes Since Previous Verification

Three new feature commits were added after the initial verification (2026-02-23T13:00:00Z):

| Commit | Description |
|--------|-------------|
| `607376c` | feat(04-04): expand car auto-detection patterns and detect charger_connected + location entities |
| `7401b0d` | feat(04-04): remove home_plugged_entity from config flow, add auto-derived home+plugged state |
| `b7fb134` | docs(04-04): complete UAT gap closure plan for car auto-detection and home+plugged derivation |

These changes addressed two UAT failures reported in `04-UAT.md`:
1. mySkoda integration not auto-detected (entity patterns too narrow)
2. Manual "Home and plugged in sensor" field required (should be auto-derived)

---

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | User can add a car via config flow with auto-detected Skoda/VW integration, or manually map entity IDs for unsupported cars | VERIFIED | `find_car_integrations()` now includes "myskoda" in platform_patterns (line 453), "battery_percentage" and "charging_level" in SOC patterns (lines 494-497). Config flow at line 498 calls `find_car_integrations()` and pre-fills `CONF_CAR_NAME`, `CONF_BATTERY_LEVEL_ENTITY`, `CONF_CHARGER_CONNECTED_ENTITY`, `CONF_LOCATION_ENTITY` from detected car. No `home_plugged_entity` field in form. All fields optional except `car_name`. |
| 2 | Each car appears as a separate device with its own schedule sensor, departure time entity, and target SOC entity | VERIFIED | `CarEntity.device_info` returns `DeviceInfo(identifiers={(DOMAIN, subentry_id)}, via_device=(DOMAIN, entry_id))`; sensor.py, time.py, and number.py each create entities per subentry with `config_subentry_id=subentry_id`. No regression in these files since previous verification. |
| 3 | User can see a per-car charging schedule that selects the cheapest hours before the departure deadline to reach the target SOC | VERIFIED | `build_car_charging_schedule()` in car_charging_scheduler.py (263 lines) filters slots to `[now, departure_utc]` window, sorts by price ascending, selects `ceil(hours_needed)` cheapest slots. `CarScheduleSensor.native_value` = `current_action`, `extra_state_attributes` exposes full schedule. 23 unit tests pass. No regression confirmed (77 total tests pass). |
| 4 | When an unrecognized vehicle is connected, fallback charging activates during off-peak hours | VERIFIED | `_detect_fallback_needed()` at coordinator.py line 1224 reads `charger_status_entity`; returns `True` when charger reports connected state AND `_soc_last_updated is None` or elapsed > `FALLBACK_STALE_THRESHOLD_MINUTES * 60`. `fallback_mode=True` passed to scheduler selects cheapest `len(available) // 2` slots. No regression. |

**Score: 4/4 truths verified**

---

## Required Artifacts

### Plan 01 Artifacts (no changes -- regression check)

| Artifact | Provides | Exists | Substantive | Wired | Status |
|----------|----------|--------|-------------|-------|--------|
| `custom_components/energy_manager/car_charging_scheduler.py` | Pure Python scheduling algorithm | Yes | 263 lines, `build_car_charging_schedule()`, `CarScheduleSlot`, `CarScheduleResult`, zero HA imports | Imported by coordinator.py line 83 | VERIFIED |
| `tests/test_car_charging_scheduler.py` | Unit tests for scheduler | Yes | 23 tests, 77 total pass (no regressions) | Direct import of scheduler module | VERIFIED |

### Plan 02 Artifacts (no changes -- regression check)

| Artifact | Provides | Exists | Substantive | Wired | Status |
|----------|----------|--------|-------------|-------|--------|
| `custom_components/energy_manager/coordinator.py` | `CarChargingData`, `CarChargingCoordinator` | Yes | 1323 lines; `CarChargingData`, `CarChargingCoordinator` with `_async_setup`, `_async_update_data`, `_read_car_soc`, `_departure_to_utc`, `_detect_fallback_needed`, `_is_home_and_plugged_in` | Instantiated in `__init__.py` | VERIFIED |
| `custom_components/energy_manager/entity.py` | `CarEntity` base class | Yes | 91 lines, `CarEntity(CoordinatorEntity)` with subentry-based `DeviceInfo` and `via_device` link | Inherited by all 4 car entity types | VERIFIED |
| `custom_components/energy_manager/const.py` | Car charging constants | Yes | `CONF_CHARGER_CONNECTED_ENTITY`, `CONF_LOCATION_ENTITY` now present (lines 50-51); all existing constants present | Imported by coordinator.py and config_flow.py | VERIFIED |
| `custom_components/energy_manager/__init__.py` | Per-subentry coordinator loop | Yes | Loop over `entry.subentries`, filters `SUBENTRY_TYPE_CAR`, creates `CarChargingCoordinator` per match | Entry point for Phase 4 coordinator creation | VERIFIED |

### Plan 03 Artifacts (no changes -- regression check)

| Artifact | Provides | Exists | Substantive | Wired | Status |
|----------|----------|--------|-------------|-------|--------|
| `custom_components/energy_manager/sensor.py` | `CarScheduleSensor` | Yes | 430 lines; `native_value` = `current_action`; `extra_state_attributes` includes schedule (capped at 48), charging_slots, energy_needed_kwh, hours_needed, current_soc, target_soc, is_preliminary, last_calculated | Created per subentry in `async_setup_entry` | VERIFIED |
| `custom_components/energy_manager/time.py` | `CarDepartureTime` entity | Yes | 101 lines; `RestoreEntity` pattern; `async_set_value` triggers coordinator refresh | Created per subentry | VERIFIED |
| `custom_components/energy_manager/number.py` | `CarTargetSOC`, `CarMaxChargePower` | Yes | 343 lines; both `RestoreNumber`; both trigger coordinator refresh on change | Created per subentry | VERIFIED |
| `custom_components/energy_manager/strings.json` | Entity translations | Yes | `charger_connected_entity` and `location_entity` present in car user/reconfigure steps; `home_plugged_entity` fully absent | Consumed by HA translation engine | VERIFIED |
| `custom_components/energy_manager/translations/en.json` | English entity translations | Yes | Identical to strings.json car subentry section; `charger_connected_entity` and `location_entity` present; `home_plugged_entity` absent | Consumed by HA translation engine | VERIFIED |

### Plan 04 Artifacts (new -- full 3-level check)

| Artifact | Provides | Exists | Substantive | Wired | Status |
|----------|----------|--------|-------------|-------|--------|
| `custom_components/energy_manager/auto_detect.py` | Expanded `find_car_integrations()` with broader SOC patterns, charger_connected and location detection, debug logging | Yes | "myskoda" in platform_patterns (line 453); "battery_percentage" and "charging_level" in SOC patterns (lines 494-497); `charger_connected_entity` detection (lines 501-508); `location_entity` detection (lines 510-516); debug log on match failure (lines 544-550) | Called by `CarSubentryFlowHandler.async_step_user()` in config_flow.py line 498 | VERIFIED |
| `custom_components/energy_manager/config_flow.py` | Car subentry form without `home_plugged_entity`, with `charger_connected_entity` + `location_entity` | Yes | `CONF_CHARGER_CONNECTED_ENTITY` (line 76) and `CONF_LOCATION_ENTITY` (line 78) imported; both `vol.Optional` fields in schema (lines 511-516); auto-fill from detected car (lines 530-535); same in reconfigure step (lines 568-573); `CONF_HOME_PLUGGED_ENTITY` completely absent | Schema consumed by HA UI rendering | VERIFIED |
| `custom_components/energy_manager/coordinator.py` | `CarChargingCoordinator._is_home_and_plugged_in()` + new instance fields | Yes | `self._charger_connected_entity` (line 1067-1069); `self._location_entity` (line 1070-1072); `_is_home_and_plugged_in()` method at line 1264 with 3-signal cascade (Easee charger status, car binary sensor, location device_tracker); `CONF_HOME_PLUGGED_ENTITY` absent | `_charger_connected_entity` and `_location_entity` read from `subentry.data`; `_is_home_and_plugged_in()` exists and is wired to coordinator state (note: not yet called in Phase 4 scheduler -- documented Phase 5 scope) | VERIFIED |

---

## Key Link Verification

| From | To | Via | Status |
|------|----|-----|--------|
| `auto_detect.py:find_car_integrations()` | `config_flow.py:CarSubentryFlowHandler` | `find_car_integrations` imported at line 52, called at line 498; result drives `suggested` dict for `charger_connected_entity` and `location_entity` pre-fill | WIRED |
| `auto_detect.py` | `coordinator.py` | Detected `charger_connected_entity` and `location_entity` stored in subentry.data by config flow; read by coordinator `__init__` at lines 1067-1072 via `subentry.data.get(CONF_CHARGER_CONNECTED_ENTITY)` | WIRED |
| `coordinator.py:_is_home_and_plugged_in()` | `hass.states` | Reads `_charger_status_entity` (signal 1), `_charger_connected_entity` (signal 2), `_location_entity` (signal 3) via `self.hass.states.get()` | WIRED |
| `tests/test_car_charging_scheduler.py` | `car_charging_scheduler.py` | Direct import; 23 tests; 77 total tests pass | WIRED |
| `coordinator.py` | `car_charging_scheduler.py` | `from .car_charging_scheduler import CarScheduleResult, build_car_charging_schedule` (line 83); called at line 1154 | WIRED |
| `coordinator.py` | PriceCoordinator | `self._price_coordinator.async_add_listener(self._handle_price_update)` in `_async_setup` | WIRED |
| `__init__.py` | `coordinator.py` | `CarChargingCoordinator(hass, entry, subentry, price_coordinator)` in per-subentry loop | WIRED |
| `sensor.py` | `coordinator.py` | `entry.runtime_data.car_coordinators.items()` in `async_setup_entry` | WIRED |
| `time.py` | `coordinator.py` | `self.coordinator.departure_time = value` + `await self.coordinator.async_request_refresh()` | WIRED |
| `number.py` | `coordinator.py` | `self.coordinator.target_soc = value` + `await self.coordinator.async_request_refresh()` | WIRED |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| EV-01 | 04-01 | User can view price-optimized charging schedule per car | SATISFIED | `CarScheduleSensor` exposes `current_action` state and full schedule in attributes; `build_car_charging_schedule()` selects cheapest N slots; 23 tests pass |
| EV-02 | 04-02 | User can set departure time per car via datetime entity | SATISFIED | `CarDepartureTime(CarEntity, TimeEntity, RestoreEntity)` in time.py; persists across restarts; triggers recalculation |
| EV-03 | 04-02, 04-03 | User can set target SOC percentage per car via number entity | SATISFIED | `CarTargetSOC(CarEntity, RestoreNumber)` in number.py; range 10-100%, default 80%; triggers recalculation |
| EV-04 | 04-02 | Each car is configured as a separate device (via subentry or per-car config) | SATISFIED | `CarEntity.device_info` returns `DeviceInfo(identifiers={(DOMAIN, subentry_id)}, via_device=(DOMAIN, entry_id))` |
| EV-05 | 04-02, 04-04 | Integration auto-detects compatible car integrations (Skoda, VW) and offers setup | SATISFIED | `find_car_integrations()` now includes "myskoda" in platform_patterns; matches "battery_percentage" and "charging_level" SOC patterns; detects charger_connected and location entities per car; debug log on silent failure |
| EV-06 | 04-02, 04-04 | User can manually add cars with custom entity mappings | SATISFIED | Config subentry form provides `EntitySelector` for `battery_level_entity`, `charger_connected_entity` (binary_sensor), `location_entity` (device_tracker); all optional; `home_plugged_entity` manual field removed |
| EV-07 | 04-01 | Schedule considers car battery capacity and current SOC to calculate energy needed | SATISFIED | `energy_needed_kwh = (target_soc - current_soc) / 100.0 * battery_capacity_kwh`; `hours_needed = energy_needed_kwh / max_charge_power_kw`; 23 unit tests cover this |
| EV-08 | 04-02 | Fallback charging activates for unrecognized connected vehicles during off-peak hours | SATISFIED | `_detect_fallback_needed()` returns True when charger reports connected + SOC never updated or stale > 60 min; `fallback_mode=True` selects cheapest half of available slots |
| EV-11 | 04-03 | Schedule sensor exposes current state with full schedule in attributes | SATISFIED | `CarScheduleSensor.extra_state_attributes` returns schedule list (filtered to future slots, capped at 48), plus charging_slots, energy_needed_kwh, hours_needed, current_soc, target_soc, is_preliminary, last_calculated |

**EV-09 and EV-10 Note:** Both requirements (solar-surplus EV charging, dynamic phase switching) were explicitly moved to Phase 5 in REQUIREMENTS.md (last updated 2026-02-23). They are out of scope for Phase 4. The `solar_surplus_available` flag exists as a `False` stub, ready for Phase 5.

**No orphaned requirements.** REQUIREMENTS.md traceability table maps EV-01 through EV-08, EV-11 to Phase 4 -- all accounted for.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `coordinator.py` | ~1308 | `_is_home_and_plugged_in()` method exists but is not yet called in `_async_update_data()` | Info | Documented intentional deferral to Phase 5. Not a blocker for Phase 4 goal. Plan 04 plan.md explicitly states "not yet consumed by the scheduler in Phase 4" |
| `config_flow.py` | ~474 | Options flow is a placeholder until Phase 6 | Info | Out of scope for Phase 4; intentionally deferred |
| `const.py` | 49 | `CONF_HOME_PLUGGED_ENTITY` constant retained but unused in any live code | Info | Acceptable legacy migration key retention; not imported or consumed anywhere except the constant definition |

No blockers. No Phase 4 stubs in car charging core files.

---

## Human Verification Required

### 1. Auto-detection Pre-fill in Config Flow (mySkoda specific)

**Test:** Start a fresh HA instance with the mySkoda integration loaded (with a vehicle that has a `battery_percentage` entity). Go to Integrations, add Energy Manager, enable EV module, then add a car subentry.
**Expected:** The "Car name" and "Battery level sensor" fields are pre-populated from detected mySkoda entities. "Car charger connected sensor" and "Car location tracker" fields appear (and are pre-populated if mySkoda exposes matching entities). No "Home and plugged in sensor" field appears.
**Why human:** `find_car_integrations()` requires a live HA state machine with actual mySkoda integration entities; cannot verify headlessly.

### 2. Per-Car Device Grouping in Device Registry

**Test:** After adding two cars as subentries, navigate to HA Devices. Verify each car appears as a separate named device, with child entities (schedule sensor, departure time, target SOC, max charge power) listed under that device, and a "provided by" link to the Energy Manager hub.
**Expected:** Two car devices each with 4 child entities, both linked via_device to the hub device.
**Why human:** Device registry population requires a running HA instance.

### 3. End-to-End Cheapest Slot Selection

**Test:** Configure a car with 80% target SOC, 20% current SOC, 77 kWh battery, 11 kW charge power, and a departure at 08:00. Observe CarScheduleSensor attributes with real Nordpool prices loaded.
**Expected:** `charging_slots = 5`, schedule shows 5 cheapest hours before 08:00 marked as "charge", all others as "idle"; `energy_needed_kwh ≈ 46.2`.
**Why human:** Requires live Nordpool price data and departure time rollover behavior across real time zones.

### 4. Fallback Mode Activation

**Test:** Connect a vehicle whose battery_level_entity produces no state updates for over 60 minutes. Verify the CarScheduleSensor reflects fallback charging behavior.
**Expected:** CarScheduleSensor shows "charge" during the cheapest half of available slots (not the cheapest N calculated from SOC).
**Why human:** Requires physical charger connection and 60-minute elapsed time to trigger `FALLBACK_STALE_THRESHOLD_MINUTES` threshold.

---

## Test Suite Status

All 77 unit tests pass (23 car charging scheduler + 54 other integration tests). No regressions introduced by Plan 04 changes.

```
tests/ - 77 passed in 0.06s
```

---

## Commit Verification

All 8 Phase 4 feature commits verified to exist in git history:

| Commit | Description |
|--------|-------------|
| `dc1eb5e` | test(04-01): add failing tests for car charging schedule algorithm |
| `4ad6ce5` | feat(04-01): implement car charging schedule algorithm |
| `748416d` | feat(04-02): add CarChargingData dataclass, CarChargingCoordinator, and car charging constants |
| `946629d` | feat(04-02): add CarEntity base, car_coordinators to EnergyManagerData, and __init__.py wiring |
| `ff81c5d` | feat(04-03): add CarScheduleSensor and CarDepartureTime entities |
| `3766570` | feat(04-03): add CarTargetSOC and CarMaxChargePower entities and translations |
| `607376c` | feat(04-04): expand car auto-detection patterns and detect charger_connected + location entities |
| `7401b0d` | feat(04-04): remove home_plugged_entity from config flow, add auto-derived home+plugged state |

---

## Summary

Phase 4 goal is achieved. All four observable success criteria are verified against the actual codebase, including Plan 04 gap closure changes:

1. **Auto-detection (Truth 1):** `find_car_integrations()` now matches "myskoda" domain, "battery_percentage"/"charging_level" SOC patterns, and detects per-car `charger_connected_entity` and `location_entity`. Config flow pre-fills all detected values and no longer shows the manual "Home and plugged in sensor" field. Manual entity selectors remain available for unsupported cars.

2. **Per-car devices (Truth 2):** `CarEntity` base class produces per-car `DeviceInfo` with subentry-based identifiers. All four entity types (sensor, time, number x2) are created per subentry with `config_subentry_id`. No regression.

3. **Cheapest slot schedule (Truth 3):** `build_car_charging_schedule()` is a substantive, tested algorithm (23 passing tests) that selects cheapest N slots before departure. `CarScheduleSensor` exposes this via state and attributes with full schedule. No regression.

4. **Fallback mode (Truth 4):** `_detect_fallback_needed()` correctly implements the EV-08 trigger logic. `_is_home_and_plugged_in()` is now implemented for 3-signal derivation (Phase 5 will consume it in Easee control logic). No regression.

No placeholder implementations, no orphaned artifacts, no broken key links found. Four human verification items documented for UI-level and real-device behavior.

---

_Verified: 2026-02-23_
_Verifier: Claude (gsd-verifier)_

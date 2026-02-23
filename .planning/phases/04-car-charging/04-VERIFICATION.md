---
phase: 04-car-charging
verified: 2026-02-23T13:00:00Z
status: passed
score: 4/4 success criteria verified
re_verification: false
human_verification:
  - test: "Add car via config flow UI and verify auto-detected Skoda/VW entities are pre-filled"
    expected: "Config flow shows detected car name and battery_level_entity pre-populated from find_car_integrations()"
    why_human: "Auto-detection requires a live HA instance with Skoda/VW integration loaded; cannot run headlessly"
  - test: "Verify each car appears as a separate device in HA Device Registry under a unique name"
    expected: "Each configured car subentry creates a device with its car name, manufacturer='Energy Manager', model='Car', linked via_device to the hub"
    why_human: "Device registry population requires a running HA instance"
  - test: "Set departure time and target SOC, then verify schedule recalculates with cheapest slots selected before that deadline"
    expected: "CarScheduleSensor state transitions through charge/idle slots matching the cheapest-N algorithm output; attributes show correct schedule"
    why_human: "Live UI interaction and real Nordpool price data needed to observe end-to-end recalculation"
  - test: "Connect an unrecognized vehicle (no SOC updates for >60 min) and verify fallback charging activates"
    expected: "CarScheduleSensor state shows 'charge' during off-peak hours (cheapest half of available slots) rather than optimized N slots"
    why_human: "Requires physical charger connection and time passage to trigger SOC staleness threshold"
---

# Phase 4: Car Charging Verification Report

**Phase Goal:** Users can configure per-car charging schedules with departure times and target SOC, and the integration selects the cheapest charging slots automatically
**Verified:** 2026-02-23T13:00:00Z
**Status:** passed
**Re-verification:** No -- initial verification

---

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | User can add a car via config flow with auto-detected Skoda/VW integration, or manually map entity IDs for unsupported cars | VERIFIED | `CarSubentryFlowHandler.async_step_user()` calls `find_car_integrations()` in auto_detect.py, pre-fills `CONF_BATTERY_LEVEL_ENTITY` from detected car; all fields also accept manual entity selectors |
| 2 | Each car appears as a separate device with its own schedule sensor, departure time entity, and target SOC entity | VERIFIED | `CarEntity` base returns `DeviceInfo(identifiers={(DOMAIN, subentry_id)}, via_device=(DOMAIN, entry_id))`; sensor.py, time.py, and number.py each create entities per subentry with `config_subentry_id=subentry_id` |
| 3 | User can see a per-car charging schedule that selects the cheapest hours before the departure deadline to reach the target SOC | VERIFIED | `build_car_charging_schedule()` filters slots to `[now, departure_utc]` window, sorts by price ascending, selects `ceil(hours_needed)` cheapest slots; `CarScheduleSensor.native_value` = `current_action`, `extra_state_attributes` exposes full schedule; 23 unit tests all pass |
| 4 | When an unrecognized vehicle is connected, fallback charging activates during off-peak hours | VERIFIED | `_detect_fallback_needed()` reads `charger_status_entity`; returns `True` when charger reports connected state AND `_soc_last_updated is None` or elapsed > `FALLBACK_STALE_THRESHOLD_MINUTES * 60`; `fallback_mode=True` passed to scheduler selects cheapest `len(available) // 2` slots |

**Score: 4/4 truths verified**

---

## Required Artifacts

### Plan 01 Artifacts

| Artifact | Provides | Exists | Substantive | Wired | Status |
|----------|----------|--------|-------------|-------|--------|
| `custom_components/energy_manager/car_charging_scheduler.py` | Pure Python scheduling algorithm | Yes | 263 lines, `build_car_charging_schedule()`, `CarScheduleSlot`, `CarScheduleResult`, zero HA imports | Imported by coordinator.py via `from .car_charging_scheduler import CarScheduleResult, build_car_charging_schedule` | VERIFIED |
| `tests/test_car_charging_scheduler.py` | Unit tests for scheduler | Yes | 671 lines, 23 tests across 12 test classes, all pass | Direct import of scheduler module | VERIFIED |

### Plan 02 Artifacts

| Artifact | Provides | Exists | Substantive | Wired | Status |
|----------|----------|--------|-------------|-------|--------|
| `custom_components/energy_manager/coordinator.py` | `CarChargingCoordinator`, `CarChargingData` | Yes | `CarChargingData` frozen dataclass (10 fields); `CarChargingCoordinator` class with `_async_setup`, `_async_update_data`, `_read_car_soc`, `_departure_to_utc`, `_detect_fallback_needed` | Instantiated in `__init__.py`; consumed by entity files | VERIFIED |
| `custom_components/energy_manager/entity.py` | `CarEntity` base class | Yes | `CarEntity(CoordinatorEntity)` with `device_info` returning subentry-based `DeviceInfo` with `via_device` link | Inherited by `CarScheduleSensor`, `CarDepartureTime`, `CarTargetSOC`, `CarMaxChargePower` | VERIFIED |
| `custom_components/energy_manager/const.py` | Car charging constants | Yes | `CONF_MAX_CHARGE_POWER_KW`, `DEFAULT_CAR_MAX_CHARGE_POWER_KW = 7.4`, `MIN/MAX_CAR_MAX_CHARGE_POWER_KW`, `DEFAULT/MIN/MAX_TARGET_SOC_PCT`, `CAR_SCHEDULE_UPDATE_INTERVAL_MINUTES = 5`, `FALLBACK_STALE_THRESHOLD_MINUTES = 60` | Imported by coordinator.py and number.py | VERIFIED |
| `custom_components/energy_manager/__init__.py` | Per-subentry coordinator loop | Yes | Loop over `entry.subentries`, filters by `SUBENTRY_TYPE_CAR`, creates `CarChargingCoordinator` per match, stores in `EnergyManagerData.car_coordinators`; `_get_enabled_platforms` adds `Platform.TIME` and `Platform.NUMBER` when EV enabled | Entry point for all Phase 4 coordinator creation | VERIFIED |

### Plan 03 Artifacts

| Artifact | Provides | Exists | Substantive | Wired | Status |
|----------|----------|--------|-------------|-------|--------|
| `custom_components/energy_manager/sensor.py` | `CarScheduleSensor` | Yes | `class CarScheduleSensor(CarEntity, SensorEntity)`; `native_value` = `data.current_action`; `extra_state_attributes` includes schedule (filtered+capped at 48), charging_slots, energy_needed_kwh, hours_needed, current_soc, target_soc, is_preliminary, last_calculated | Created per subentry in `async_setup_entry` with `config_subentry_id` | VERIFIED |
| `custom_components/energy_manager/time.py` | `CarDepartureTime` entity | Yes | `class CarDepartureTime(CarEntity, TimeEntity, RestoreEntity)`; default `time(7,0)`; `async_added_to_hass` restores via `async_get_last_state`; `async_set_value` updates `coordinator.departure_time` and calls `async_request_refresh()` | Created per subentry via `async_setup_entry` with `config_subentry_id` | VERIFIED |
| `custom_components/energy_manager/number.py` | `CarTargetSOC`, `CarMaxChargePower` | Yes | Both inherit `CarEntity, RestoreNumber`; `CarTargetSOC` range 10-100%, `CarMaxChargePower` range 1.4-22.0 kW; both restore via `async_get_last_number_data` and call `coordinator.async_request_refresh()` on change | Created per subentry in `async_setup_entry` with `config_subentry_id`; battery early-return bug fixed | VERIFIED |
| `custom_components/energy_manager/strings.json` | Entity translations | Yes | `entity.sensor.car_schedule`, `entity.time.departure_time`, `entity.number.car_target_soc`, `entity.number.car_max_charge_power` all present | Consumed by HA translation engine via `_attr_translation_key` on each entity class | VERIFIED |
| `custom_components/energy_manager/translations/en.json` | English entity translations | Yes | Identical to strings.json entity section; all 4 car entity keys present | Consumed by HA translation engine | VERIFIED |

---

## Key Link Verification

| From | To | Via | Pattern | Status |
|------|----|-----|---------|--------|
| `tests/test_car_charging_scheduler.py` | `car_charging_scheduler.py` | direct import | `from custom_components.energy_manager.car_charging_scheduler import` | WIRED |
| `coordinator.py` | `car_charging_scheduler.py` | import and call | `from .car_charging_scheduler import CarScheduleResult, build_car_charging_schedule` | WIRED |
| `coordinator.py` | `coordinator.py` (PriceCoordinator chaining) | `async_add_listener` | `self._price_coordinator.async_add_listener(self._handle_price_update)` in `_async_setup` | WIRED |
| `__init__.py` | `coordinator.py` | `CarChargingCoordinator` instantiation | `CarChargingCoordinator(hass, entry, subentry, price_coordinator)` in per-subentry loop | WIRED |
| `entity.py` | `const.py` | DOMAIN import for device identifiers | `identifiers={(DOMAIN, self._subentry_id)}` in `device_info` | WIRED |
| `coordinator.py` | `coordinator.py` (fallback detection) | `_detect_fallback_needed` reads charger_status_entity | `def _detect_fallback_needed(self) -> bool` reads `self.hass.states.get(self._charger_status_entity)` | WIRED |
| `sensor.py` | `coordinator.py` | `CarChargingCoordinator` data access | `entry.runtime_data.car_coordinators.items()` in `async_setup_entry` | WIRED |
| `time.py` | `coordinator.py` | departure_time update + refresh | `self.coordinator.departure_time = value` + `await self.coordinator.async_request_refresh()` | WIRED |
| `number.py` | `coordinator.py` | target_soc and max_charge_power update + refresh | `self.coordinator.target_soc = value` + `await self.coordinator.async_request_refresh()` | WIRED |
| `sensor.py` | `entity.py` | `CarEntity` base class inheritance | `class CarScheduleSensor(CarEntity, SensorEntity)` | WIRED |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| EV-01 | 04-01 | User can view price-optimized charging schedule per car | SATISFIED | `CarScheduleSensor` exposes `current_action` state and full schedule in attributes; `build_car_charging_schedule()` selects cheapest N slots |
| EV-02 | 04-02 | User can set departure time per car via datetime entity | SATISFIED | `CarDepartureTime(CarEntity, TimeEntity, RestoreEntity)` in time.py; persists across restarts; triggers recalculation |
| EV-03 | 04-02, 04-03 | User can set target SOC percentage per car via number entity | SATISFIED | `CarTargetSOC(CarEntity, RestoreNumber)` in number.py; range 10-100%, default 80%; triggers recalculation |
| EV-04 | 04-02 | Each car is configured as a separate device (via subentry or per-car config) | SATISFIED | `CarEntity.device_info` returns `DeviceInfo(identifiers={(DOMAIN, subentry_id)}, via_device=(DOMAIN, entry_id))`; entities registered with `config_subentry_id` |
| EV-05 | 04-02 | Integration auto-detects compatible car integrations (Skoda, VW) and offers setup | SATISFIED | `CarSubentryFlowHandler.async_step_user()` calls `find_car_integrations()` which scans HA for Skoda/VW entities and pre-fills form (Phase 1, confirmed consumed in Phase 4) |
| EV-06 | 04-02 | User can manually add cars with custom entity mappings | SATISFIED | Config subentry form provides `EntitySelector` for `battery_level_entity` and `home_plugged_entity` with no required auto-detection; all fields optional except `car_name` |
| EV-07 | 04-01 | Schedule considers car battery capacity and current SOC to calculate energy needed | SATISFIED | `energy_needed_kwh = (target_soc - current_soc) / 100.0 * battery_capacity_kwh`; `hours_needed = energy_needed_kwh / max_charge_power_kw`; 23 unit tests cover this |
| EV-08 | 04-02 | Fallback charging activates for unrecognized connected vehicles during off-peak hours | SATISFIED | `_detect_fallback_needed()` returns True when charger reports connected + SOC never updated or stale > 60 min; `fallback_mode=True` selects cheapest half of available slots |
| EV-11 | 04-03 | Schedule sensor exposes current state with full schedule in attributes | SATISFIED | `CarScheduleSensor.extra_state_attributes` returns schedule list (filtered to future slots, capped at 48), plus charging_slots, energy_needed_kwh, hours_needed, current_soc, target_soc, is_preliminary, last_calculated |

**Note on EV-09 and EV-10:** These requirements (solar-surplus EV charging, dynamic phase switching) were explicitly moved to Phase 5 in REQUIREMENTS.md (last updated 2026-02-23). They are out of scope for Phase 4. The solar_surplus_available flag exists as a pass-through stub with `False` hardcoded, ready for Phase 5 to wire.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `config_flow.py` | 474 | `# Empty form — serves as a placeholder until Phase 6` (options flow) | Info | Out of scope for Phase 4; intentionally deferred |

No blockers. No Phase 4 stubs detected in car charging files.

---

## Human Verification Required

### 1. Auto-detection Pre-fill in Config Flow

**Test:** Start a fresh HA instance with the Skoda Connect or VW We Connect integration loaded. Go to Integrations, add Energy Manager, enable EV module, then add a car subentry.
**Expected:** The "Battery level sensor" and "Car name" fields are pre-populated from detected Skoda/VW entities.
**Why human:** `find_car_integrations()` requires a live HA state machine with actual Skoda/VW integration entities; cannot verify headlessly.

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

All 23 unit tests for the car charging scheduler pass:

```
tests/test_car_charging_scheduler.py - 23 passed in 0.02s
```

Covered cases: normal scheduling (cheapest N slots), SOC at/above target (idle), no available slots, zero charge power, fallback mode (cheapest half), solar charge marking (flag pass-through), preliminary flag, current_action derivation, window filtering, data type immutability, energy calculation (Enyaq 77kWh scenario).

---

## Commit Verification

All 6 documented commits verified to exist in git history:

| Commit | Description |
|--------|-------------|
| `dc1eb5e` | test(04-01): add failing tests for car charging schedule algorithm |
| `4ad6ce5` | feat(04-01): implement car charging schedule algorithm |
| `748416d` | feat(04-02): add CarChargingData dataclass, CarChargingCoordinator, and car charging constants |
| `946629d` | feat(04-02): add CarEntity base, car_coordinators to EnergyManagerData, and __init__.py wiring |
| `ff81c5d` | feat(04-03): add CarScheduleSensor and CarDepartureTime entities |
| `3766570` | feat(04-03): add CarTargetSOC and CarMaxChargePower entities and translations |

---

## Summary

Phase 4 goal is achieved. All four observable success criteria are verified against the actual codebase:

1. The config flow (Phase 1's `CarSubentryFlowHandler`) calls `find_car_integrations()` for Skoda/VW auto-detection and provides manual entity selectors as fallback.
2. `CarEntity` base class produces per-car `DeviceInfo` with subentry-based identifiers; all four entity types (sensor, time, number x2) are created per subentry with `config_subentry_id`.
3. `build_car_charging_schedule()` is a substantive, tested algorithm (23 passing tests) that selects cheapest N slots before departure; `CarScheduleSensor` exposes this via state and attributes with full schedule.
4. `_detect_fallback_needed()` correctly implements the EV-08 trigger logic (charger connected + SOC stale); `fallback_mode=True` selects cheapest half of available slots in the pure scheduler.

No placeholder implementations, no orphaned artifacts, no broken key links found. Four human verification items are documented for UI-level and real-device behavior that cannot be verified programmatically.

---

_Verified: 2026-02-23_
_Verifier: Claude (gsd-verifier)_

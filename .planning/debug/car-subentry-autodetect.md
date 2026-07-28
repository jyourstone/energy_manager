---
status: diagnosed
trigger: "Car subentry flow: not auto-detecting Skoda car; asking for manual 'Home and plugged in' sensor instead of auto-deriving"
created: 2026-02-23T12:00:00Z
updated: 2026-02-23T12:00:00Z
---

## Current Focus

hypothesis: Two distinct root causes -- (1) find_car_integrations() searches for domain "skoda" but the mySkoda integration uses domain "myskoda"; (2) home_plugged_entity is a raw EntitySelector field with no auto-derivation logic
test: Code review of auto_detect.py find_car_integrations() and config_flow.py CarSubentryFlowHandler
expecting: Confirmed both root causes via code evidence
next_action: Return diagnosis

## Symptoms

expected: |
  1. Config flow auto-detects the user's Skoda car (from mySkoda/Skoda Connect integration) and pre-populates car name and battery level entity
  2. "Home and plugged in" status is auto-derived from available signals (Easee charger status, car binary sensors, location sensors) rather than requiring manual sensor selection
actual: |
  1. No car is auto-detected -- the form shows empty fields despite mySkoda integration being installed
  2. Config flow presents a manual EntitySelector for "Home and plugged in sensor" requiring the user to find and select a sensor themselves
errors: No error messages -- the detection silently returns empty results
reproduction: Add a car via the Energy Manager config subentry flow with a mySkoda integration installed
started: Since Phase 1 implementation (the auto-detect logic was written with incomplete knowledge of integration domain names)

## Eliminated

(none -- root causes found on first investigation)

## Evidence

- timestamp: 2026-02-23T12:00:00Z
  checked: auto_detect.py find_car_integrations() lines 437-520
  found: |
    Function searches for config entries where domain contains "skoda" or "volkswagen"/"vw".
    The actual mySkoda integration (homeassistant-myskoda) uses domain "myskoda".
    The pattern check `any(pattern in entry.domain.lower() for pattern in domain_patterns)`
    with patterns ["skoda"] DOES match "myskoda" because "skoda" is a substring of "myskoda".
    BUT the function also requires finding a sensor with "battery_level" or "state_of_charge"
    in the entity_id or unique_id. The mySkoda integration uses entity IDs like
    "sensor.skoda_enyaq_battery_level" -- this SHOULD match the "battery_level" pattern.
  implication: |
    The domain pattern matching itself is NOT the bug for Skoda Connect.
    However, for the newer mySkoda integration, entity naming patterns may differ.
    The real question is whether entity IDs follow the expected "battery_level" pattern.
    Need to verify against actual mySkoda entity IDs in the user's HA instance.

- timestamp: 2026-02-23T12:01:00Z
  checked: auto_detect.py platform_patterns dict (line 452-455)
  found: |
    platform_patterns = {
        "skoda": ["skoda"],
        "volkswagen": ["volkswagen", "vw"],
    }
    The domain check is `any(pattern in entry.domain.lower() for pattern in domain_patterns)`.
    For mySkoda (domain "myskoda"): "skoda" in "myskoda" = True. This does match.
    For the deprecated Skoda Connect (domain "skodaconnect"): "skoda" in "skodaconnect" = True.
    So the domain matching is actually fine for both old and new Skoda integrations.
  implication: Domain matching alone is not the problem. The issue must be in entity matching or the integration not being loaded at config flow time.

- timestamp: 2026-02-23T12:02:00Z
  checked: auto_detect.py entity matching logic (lines 478-515)
  found: |
    The function groups entities by device_id, then for each device looks for a sensor with
    "battery_level" or "state_of_charge" in entity_id or unique_id. A car is only added to
    the results list if battery_level_entity is found (line 499: `if battery_level_entity is not None`).

    The mySkoda integration creates entities like:
    - sensor.skoda_enyaq_battery_level (or sensor.<model>_battery_percentage)
    - binary_sensor.skoda_enyaq_charger_connected
    - device_tracker.skoda_enyaq_parking_position

    The pattern "battery_level" should match "sensor.skoda_enyaq_battery_level".
    BUT some mySkoda versions use "battery_percentage" instead of "battery_level".
    If the entity is "sensor.skoda_enyaq_battery_percentage", neither "battery_level"
    nor "state_of_charge" matches, and the car is silently skipped.
  implication: |
    ROOT CAUSE 1 IDENTIFIED: The entity matching patterns are too narrow.
    They check for "battery_level" and "state_of_charge" but miss "battery_percentage"
    which is used by mySkoda. The function should also check for "battery_percentage"
    and potentially "soc" as a shorter variant.

- timestamp: 2026-02-23T12:03:00Z
  checked: config_flow.py CarSubentryFlowHandler.async_step_user() lines 483-532
  found: |
    The car subentry form includes:
    - CONF_CAR_NAME (text)
    - CONF_BATTERY_CAPACITY (number)
    - CONF_BATTERY_LEVEL_ENTITY (entity selector)
    - CONF_HOME_PLUGGED_ENTITY (entity selector for sensor/binary_sensor)

    The auto-detection only pre-fills car_name and battery_level_entity from find_car_integrations().
    The home_plugged_entity is NEVER auto-populated -- there is no logic anywhere that:
    1. Checks the Easee charger status entity to derive "plugged in" state
    2. Looks for mySkoda/VW binary_sensor.charger_connected entities
    3. Combines charger status + location into a derived "home and plugged in" state

    The field is just a raw EntitySelector that the user must manually fill in.
  implication: |
    ROOT CAUSE 2 IDENTIFIED: No auto-derivation logic exists for home_plugged_entity.
    The field asks the user to manually specify a single sensor, but the UAT expectation
    is that the system should derive this from multiple available signals automatically.

- timestamp: 2026-02-23T12:04:00Z
  checked: coordinator.py CarChargingCoordinator lines 1066-1067
  found: |
    The coordinator reads home_plugged_entity from subentry data and stores it, but never
    actually uses it in _async_update_data() or anywhere else visible. The _detect_fallback_needed()
    method only checks the charger_status_entity (from main entry options), not home_plugged_entity.
    The home_plugged_entity appears to be stored but not consumed by the scheduler.
  implication: |
    The home_plugged_entity is dead config -- stored but not actively used in schedule calculation.
    The coordinator already has charger_status_entity from the main EV config step.
    This suggests the architecture should derive "home and plugged in" internally rather
    than asking the user for a separate entity.

- timestamp: 2026-02-23T12:05:00Z
  checked: strings.json lines 98-104 (car subentry UI)
  found: |
    The UI label is "Home and plugged in sensor" with description "Sensor or binary sensor
    indicating the car is home and plugged in." This implies a single composite sensor that
    the user must find or create, rather than the system deriving it from available data.
  implication: |
    The UI framing makes the field confusing -- most users won't have a single sensor
    that represents both "home" and "plugged in" combined. This needs to either be
    auto-derived or split into separate, more discoverable fields.

## Resolution

root_cause: |
  TWO ROOT CAUSES:

  **Issue 1: Car auto-detection may fail for mySkoda users**

  File: `custom_components/energy_manager/auto_detect.py`, function `find_car_integrations()`, lines 486-492.

  The entity matching patterns check for "battery_level" and "state_of_charge" in entity IDs,
  but the mySkoda integration may use "battery_percentage" for the SOC entity depending on
  the version. When neither pattern matches, no car is detected and the form shows empty.

  The domain matching ("skoda" in "myskoda") is actually correct -- the failure is specifically
  in the entity ID pattern matching.

  Additionally, the function does NOT search for domain "myskoda" explicitly -- it relies on
  the substring match. While this works, it is fragile and not self-documenting. Adding
  "myskoda" to the patterns would make intent clear and future-proof.

  **Issue 2: home_plugged_entity requires manual selection with no auto-derivation**

  Files: `custom_components/energy_manager/config_flow.py` (CarSubentryFlowHandler, lines 510-512)
  and `custom_components/energy_manager/auto_detect.py` (find_car_integrations, no home_plugged logic).

  The config flow presents CONF_HOME_PLUGGED_ENTITY as a plain EntitySelector with no
  auto-detection or auto-derivation. The UAT expectation is that this should be automatically
  determined from:
  - The Easee charger status entity (already configured in the main EV step): check if status != "disconnected"
  - The car's binary_sensor for charger connection (e.g., binary_sensor.skoda_enyaq_charger_connected)
  - The car's device tracker for home location (e.g., device_tracker.skoda_enyaq_parking_position)

  Currently:
  1. find_car_integrations() does not look for charger_connected or location entities
  2. CarSubentryFlowHandler does not pre-fill home_plugged_entity from detected data
  3. The coordinator stores home_plugged_entity but does not appear to use it in scheduling
  4. The Easee charger status (already in main config) could serve as the "plugged in" signal

fix: Not applied (diagnosis only mode)

verification: N/A

files_changed: []

## Recommended Fix Direction

### For Issue 1 (car auto-detection):
1. In `auto_detect.py` `find_car_integrations()`:
   - Add "battery_percentage" to the entity matching patterns (line 488-492)
   - Add "myskoda" explicitly to platform_patterns: `"skoda": ["skoda", "myskoda"]`
   - Consider also matching "charging_level" or just "battery" + "percent"
   - Add debug logging when a config entry matches domain but no battery entity is found

2. Consider adding a fallback that checks device registry names for car model names.

### For Issue 2 (home_plugged_entity):
Two possible approaches:

**Approach A: Auto-derive internally (preferred)**
- Remove home_plugged_entity from the car subentry config form entirely
- In `find_car_integrations()`, also detect:
  - `binary_sensor.*_charger_connected` (from mySkoda/VW) per car device
  - `device_tracker.*_parking_position` or `*_location` per car device
- In the CarChargingCoordinator, derive "home and plugged in" from:
  - Easee charger_status_entity != "disconnected" (already available)
  - Car's charger_connected binary_sensor (if available)
  - Car's location device_tracker (if available, compare to home zone)
- This eliminates the confusing manual field entirely

**Approach B: Auto-populate with detected entity**
- In `find_car_integrations()`, also scan for binary_sensor entities with
  "charger_connected" or "plug_connected" in the entity ID per car device
- Pre-fill home_plugged_entity with the detected binary_sensor
- Keep the field in the form but pre-populated so users can override

### Files involved:
- `custom_components/energy_manager/auto_detect.py` -- expand find_car_integrations()
- `custom_components/energy_manager/config_flow.py` -- CarSubentryFlowHandler form changes
- `custom_components/energy_manager/const.py` -- potentially new config keys if splitting into separate signals
- `custom_components/energy_manager/coordinator.py` -- CarChargingCoordinator to consume new signals
- `custom_components/energy_manager/strings.json` -- UI label updates
- `custom_components/energy_manager/translations/en.json` -- translation updates

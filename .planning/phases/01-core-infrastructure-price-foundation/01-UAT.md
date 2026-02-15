---
status: diagnosed
phase: 01-core-infrastructure-price-foundation
source: 01-01-SUMMARY.md, 01-02-SUMMARY.md, 01-03-SUMMARY.md, 01-04-SUMMARY.md
started: 2026-02-15T19:30:00Z
updated: 2026-02-15T20:30:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Add Integration via Config Flow
expected: In HA Settings > Devices & Services > Add Integration, searching "Energy Manager" shows the integration. Clicking it starts the config flow with a Nordpool sensor selection step. If you have a Nordpool integration installed, the sensor entity should be auto-detected and pre-filled.
result: pass

### 2. Module Toggle Step
expected: After selecting Nordpool sensor, the next step shows two checkboxes: "Enable Home Battery" and "Enable EV Charging". Both can be toggled independently.
result: pass

### 3. Battery Configuration Step
expected: When Home Battery is enabled, a configuration step appears with two SigenStor entity fields: Battery SOC sensor and Battery power sensor. If SigenStor is installed, fields are pre-filled via auto-detection.
result: pass

### 4. EV Configuration Step
expected: When EV Charging is enabled, a configuration step appears with Easee charger entity fields: Charger status entity and Charger power entity. If Easee is installed, fields are pre-filled via auto-detection.
result: pass

### 5. Integration Setup Completes
expected: After completing all config flow steps, the integration appears in Settings > Devices & Services. A hub device named "Energy Manager" is registered. No errors in the HA log.
result: issue
reported: "It gets created but I get 2 warnings in logs: 1) State attributes for sensor.energy_manager_electricity_price exceed maximum size of 16384 bytes - attributes will not be stored. 2) Entity sensor.energy_manager_electricity_price is using state class 'measurement' which is impossible considering device class 'monetary' - expected None or one of 'total'."
severity: major

### 6. Price Sensor Entity
expected: A sensor entity "Electricity Price" (sensor.energy_manager_electricity_price) appears showing the current electricity price in SEK/kWh. The entity attributes contain "prices_today" (list of hourly price slots) and "prices_tomorrow" (list or empty if not yet published).
result: pass

### 7. Integration Survives Restart
expected: After restarting Home Assistant, the Energy Manager integration loads without errors. The price sensor retains its state. No ghost entities appear.
result: pass

### 8. Integration Unload/Reload
expected: In Settings > Devices & Services, clicking the three-dot menu on Energy Manager and selecting "Reload" works without errors. The integration can also be removed and re-added cleanly.
result: pass

## Summary

total: 8
passed: 7
issues: 1
pending: 0
skipped: 0

## Gaps

- truth: "Integration setup completes with no errors or warnings in the HA log"
  status: failed
  reason: "User reported: Two warnings: 1) Price sensor attributes exceed 16384 bytes (recorder won't store them). 2) State class 'measurement' incompatible with device class 'monetary' (expected None or 'total')."
  severity: major
  test: 5
  root_cause: "Two bugs in sensor.py: 1) extra_state_attributes serializes 48 hourly price slots exceeding HA's 16KB recorder limit. 2) _attr_state_class = SensorStateClass.MEASUREMENT is incompatible with SensorDeviceClass.MONETARY (must be None or TOTAL)."
  artifacts:
    - path: "custom_components/energy_manager/sensor.py"
      issue: "Lines 85-106: serializes full price slot lists into attributes exceeding 16KB. Line 52: wrong state_class for monetary device class."
  missing:
    - "Remove prices_today/prices_tomorrow from extra_state_attributes (downstream modules use coordinator directly)"
    - "Delete _attr_state_class = SensorStateClass.MEASUREMENT (None is correct for spot prices)"
  debug_session: ".planning/debug/price-sensor-warnings.md"

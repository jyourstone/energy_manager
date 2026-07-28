---
status: resolved
trigger: "EMS Auto-Detection Missing Entities - investigate find_sigenstor_ems_entities() patterns"
created: 2026-02-22T00:00:00Z
updated: 2026-02-22T00:00:00Z
---

## Current Focus

hypothesis: Confirmed - entity_id patterns in find_sigenstor_ems_entities() do not match actual SigenStor entity naming convention
test: Compared patterns in code against actual entity_id values reported by user
expecting: n/a - root cause confirmed
next_action: RESOLVED - see Resolution section

## Symptoms

expected: Auto-detection finds max charging limit, max discharging limit, L-current sensor, PV power sensor
actual:
  - Max charging limit: not found (actual entity: sensor.sigen_battery_ess_rated_charging_power)
  - Max discharging limit: not found (actual entity: sensor.sigen_plant_ess_rated_discharging_power)
  - L-current sensor: not found (user has 3 phase kW sensors, no direct A sensor)
  - PV power sensor: not found (actual entities: sensor.sigen_inverter_pv_power, sensor.sigen_plant_pv_power)
  - EMS mode select: works fine
errors: none (silent detection failure - returns empty/partial dict)
reproduction: run config flow EMS step on SigenStor installation
started: unknown

## Eliminated

- hypothesis: EMS select pattern wrong
  evidence: "remote_ems_control" and "ems_control_mode" patterns work - user confirms EMS mode select IS found
  timestamp: 2026-02-22

## Evidence

- timestamp: 2026-02-22
  checked: auto_detect.py lines 164-195 - charge/discharge limit patterns
  found: |
    Code looks for domain "number" with patterns:
      - "max_charging_limit", "ess_max_charging" (charge)
      - "max_discharging_limit", "ess_max_discharging" (discharge)
    Actual entities are sensors (not numbers):
      - sensor.sigen_battery_ess_rated_charging_power
      - sensor.sigen_plant_ess_rated_discharging_power
    TWO MISMATCHES:
      1. Domain filter: code requires domain=="number", actual entities are domain=="sensor"
      2. Pattern mismatch: code looks for "max_charging_limit"/"ess_max_charging",
         actual entity_id contains "ess_rated_charging_power"
  implication: Both the domain guard and the string patterns are wrong for charging/discharging limit entities

- timestamp: 2026-02-22
  checked: auto_detect.py lines 198-215 - L-current sensor patterns
  found: |
    Code looks for domain "sensor" with patterns:
      - "highest_l_current", "phase_current", "l_current" in entity_id or unique_id
    User has 3-phase kW power sensors:
      - sigen_plant_grid_phase_a_active_power
      - sigen_plant_grid_phase_b_active_power
      - sigen_plant_grid_phase_c_active_power
    None of these match "highest_l_current", "phase_current", or "l_current".
    The word "phase" is present but "phase_current" requires BOTH words together;
    user entities use "phase_X_active_power" (X=a/b/c), which does NOT contain "phase_current".
    Fallback scan at lines 236-252 also checks only "highest_l_current" and "l_current" - same miss.
  implication: |
    Pattern mismatch - actual entities use "active_power" naming.
    Additionally these are kW power sensors, not A current sensors.
    A conversion or different matching strategy is needed.

- timestamp: 2026-02-22
  checked: auto_detect.py lines 217-234 - PV power sensor patterns
  found: |
    Code looks for domain "sensor" with patterns:
      - "pv_power", "solar_power", "pv_generation" in entity_id or unique_id
    User has:
      - sensor.sigen_inverter_pv_power  (contains "pv_power" - SHOULD MATCH)
      - sensor.sigen_plant_pv_power     (contains "pv_power" - SHOULD MATCH)
    "pv_power" IS present in both entity_ids.
  implication: |
    Pattern for PV power looks CORRECT. However detection may fail if:
    (a) SigenStor does NOT appear as a config entry with domain containing "sigen"
        (integration might use a different domain name), OR
    (b) Entities are not registered under the sigen config entry
        (could be helper/template sensors registered differently).
    This requires investigation of how SigenStor registers its config entry domain.

- timestamp: 2026-02-22
  checked: find_sigenstor_ems_entities() outer loop structure (lines 132-136)
  found: |
    The function searches config_entries where "sigen" in entry.domain.lower().
    If the SigenStor integration uses a domain like "sigenergy" or "sigen_energy"
    instead of just "sigen", this still works (substring match).
    BUT if PV entities (sigen_inverter_pv_power, sigen_plant_pv_power) are NOT
    registered under that config entry (e.g. they are child devices under a different
    config entry, or manually created sensors), they would be invisible to the loop.
    The fallback global scan at lines 236-252 only covers L-current, not PV.
  implication: |
    PV sensor failure is likely caused by either:
    (a) config entry domain not matching "sigen" substring, OR
    (b) PV entities not being registered under the sigen config entry
    A global fallback scan for "pv_power" (like the L-current fallback) would be a reliable fix.

## Resolution

root_cause: |
  Four distinct bugs in find_sigenstor_ems_entities() in auto_detect.py:

  BUG 1 - Charge limit: domain filter wrong + pattern wrong (lines 164-179)
    - Code filters domain == "number" but actual entity is a sensor
    - Code looks for "max_charging_limit"/"ess_max_charging" but actual name is
      "ess_rated_charging_power"
    - Fix: change domain to "sensor" AND add pattern "ess_rated_charging" or
      "rated_charging_power"

  BUG 2 - Discharge limit: domain filter wrong + pattern wrong (lines 181-196)
    - Same as BUG 1 for discharge side
    - Code filters domain == "number" but actual entity is a sensor
    - Code looks for "max_discharging_limit"/"ess_max_discharging" but actual name is
      "ess_rated_discharging_power"
    - Fix: change domain to "sensor" AND add pattern "ess_rated_discharging" or
      "rated_discharging_power"

  BUG 3 - L-current: pattern mismatch, no applicable entity exists (lines 198-215)
    - Code looks for "highest_l_current", "phase_current", "l_current"
    - User has "phase_a_active_power", "phase_b_active_power", "phase_c_active_power"
      which are kW power sensors, not A current sensors
    - "phase_current" pattern requires the word "current" which is absent
    - Fix: add "phase_a_active_power"/"phase_b_active_power"/"active_power" as
      fallback patterns. Note: the resulting entity will be in kW not A - the
      EMSCoordinator or the config UI may need to handle unit-of-measurement
      mismatch (W/kW vs A). Alternatively, present the 3 phase power entities as
      candidate choices and let the user pick, with a note about unit conversion.

  BUG 4 - PV power: entity not found under config entry (lines 217-234)
    - Pattern "pv_power" IS correct and WOULD match "sigen_inverter_pv_power"
    - But entity is apparently not registered under the sigen config entry, OR
      config entry domain does not contain "sigen"
    - There is no global fallback scan for PV power (unlike L-current which has one)
    - Fix: add a global fallback scan for "pv_power" / "sigen.*pv" patterns,
      mirroring the L-current fallback approach at lines 236-252.
      Prefer "sigen_plant_pv_power" over "sigen_inverter_pv_power" if both exist
      (plant-level = total after clipping, inverter = before clipping).

fix: Recommended changes to auto_detect.py find_sigenstor_ems_entities():
  1. Charge limit (lines 164-179): add domain=="sensor", add patterns "ess_rated_charging" / "rated_charging_power"
  2. Discharge limit (lines 181-196): add domain=="sensor", add patterns "ess_rated_discharging" / "rated_discharging_power"
  3. L-current (lines 198-215): add patterns "phase_a_active_power" / "phase_b_active_power" / "active_power" as last-resort fallback
  4. PV power: add global fallback scan (after sigen config entry loop) checking all sensor entities for "pv_power" with "sigen" in entity_id

verification: n/a - diagnosis only mode (find_root_cause_only)
files_changed: []

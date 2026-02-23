---
status: diagnosed
trigger: "Fuse protection uses total grid power (single sensor) instead of per-phase power sensors. One phase could be at 30A while total looks fine — safety risk."
created: 2026-02-23T10:00:00Z
updated: 2026-02-23T10:00:00Z
symptoms_prefilled: true
goal: find_root_cause_only
---

## Current Focus

hypothesis: CONFIRMED — The entire fuse protection pipeline is single-phase by design. Config stores one grid_power_entity, coordinator reads one value, divides by 3 (balanced load assumption), and passes a single current_l_amps to compute_ems_state(). Per-phase sensors exist on the inverter but are actively excluded by auto-detection.
test: Traced full chain: const.py -> config_flow.py -> auto_detect.py -> coordinator.py -> ems_controller.py
expecting: n/a — root cause confirmed
next_action: COMPLETE — root cause and required changes documented

## Symptoms

expected: Fuse protection should monitor per-phase grid power and use the worst-case (highest) phase to calculate headroom, preventing any single phase from exceeding the fuse rating.
actual: System uses a single total grid power sensor (e.g., sigen_plant_grid_active_power), divides total power by 3 assuming balanced load, producing an averaged per-phase estimate. If phase A draws 30A and phases B/C draw 5A each, total = ~9.2kW, estimated per-phase = ~13.3A — system thinks there is plenty of headroom when phase A is actually near/over fuse limit.
errors: No errors — the code works as designed, but the design is unsafe for unbalanced loads.
reproduction: Any 3-phase installation with unbalanced phase loading (e.g., EV charger on one phase, heat pump on another).
started: From initial design — never supported per-phase monitoring.

## Eliminated

(none — single hypothesis confirmed on first pass)

## Evidence

- timestamp: 2026-02-23T10:00:00Z
  checked: const.py line 90
  found: |
    Single config key: CONF_GRID_POWER_ENTITY = "grid_power_entity"
    No per-phase config keys exist (no CONF_GRID_PHASE_A_ENTITY, etc.)
  implication: The data model only supports one grid power sensor from the ground up.

- timestamp: 2026-02-23T10:00:00Z
  checked: auto_detect.py lines 210-225 (find_sigenstor_ems_entities)
  found: |
    Grid power detection explicitly EXCLUDES per-phase sensors:
    ```python
    if (
        entity_entry.domain == "sensor"
        and CONF_GRID_POWER_ENTITY not in result
        and (
            "grid_active_power" in entity_id_lower
            or "grid_active_power" in unique_id_lower
        )
        # Exclude per-phase variants (prefer total grid power)
        and "phase_" not in entity_id_lower
    ):
    ```
    The comment "Exclude per-phase variants (prefer total grid power)" and the guard
    `"phase_" not in entity_id_lower` mean the three per-phase sensors are intentionally
    filtered out. The auto-detect will match `sigen_plant_grid_active_power` (total)
    but reject `sigen_plant_grid_phase_a_active_power`, `..._phase_b_...`, `..._phase_c_...`.
  implication: Auto-detection is hardcoded to find only the total power sensor.

- timestamp: 2026-02-23T10:00:00Z
  checked: auto_detect.py lines 248-266 (fallback scan)
  found: |
    Fallback global scan has the same exclusion:
    ```python
    if (
        "grid_active_power" in entity_id_lower
        and "phase_" not in entity_id_lower
    ):
    ```
  implication: Even the fallback path excludes per-phase sensors.

- timestamp: 2026-02-23T10:00:00Z
  checked: config_flow.py lines 304-306 and 339-341 (EMS step)
  found: |
    Single grid power entity field in the EMS config step:
    ```python
    self._data[CONF_GRID_POWER_ENTITY] = user_input.get(
        CONF_GRID_POWER_ENTITY, ""
    )
    ```
    And in the schema:
    ```python
    vol.Optional(CONF_GRID_POWER_ENTITY): EntitySelector(
        EntitySelectorConfig(domain="sensor")
    ),
    ```
    Only one entity selector — no way to configure three per-phase sensors.
  implication: Config flow UI only allows selecting a single grid power sensor.

- timestamp: 2026-02-23T10:00:00Z
  checked: config_flow.py lines 424 (_create_entry)
  found: |
    Options dict stores single value:
    ```python
    CONF_GRID_POWER_ENTITY: self._data.get(CONF_GRID_POWER_ENTITY, ""),
    ```
  implication: Stored config only has one grid power entity.

- timestamp: 2026-02-23T10:00:00Z
  checked: coordinator.py lines 552-554 (EMSCoordinator.__init__)
  found: |
    Single grid power entity loaded:
    ```python
    self._grid_power_entity: str = entry.options.get(
        CONF_GRID_POWER_ENTITY, ""
    )
    ```
  implication: Coordinator only tracks one entity.

- timestamp: 2026-02-23T10:00:00Z
  checked: coordinator.py lines 586-598 (_async_setup)
  found: |
    Event-driven listener on single entity:
    ```python
    if self._grid_power_entity:
        self.config_entry.async_on_unload(
            async_track_state_change_event(
                self.hass,
                [self._grid_power_entity],
                self._handle_fuse_update,
            )
        )
    ```
  implication: Only listens to one sensor for fuse-critical updates.

- timestamp: 2026-02-23T10:00:00Z
  checked: coordinator.py lines 740-764 (_read_grid_current_amps)
  found: |
    THE CRITICAL FUNCTION — converts total power to estimated per-phase current:
    ```python
    def _read_grid_current_amps(self) -> float:
        """Read grid power and convert to estimated per-phase current in amps.

        Uses the formula: I_phase = abs(P_total) / (3 * V_phase) for a 3-phase
        system assuming balanced load. This is an approximation -- per-phase
        sensors would give more accurate results but most inverter integrations
        disable them by default.
        """
        power = self._read_float_state(self._grid_power_entity, 0.0)
        if power == 0.0:
            return 0.0

        state = self.hass.states.get(self._grid_power_entity)
        if state is not None:
            uom = state.attributes.get("unit_of_measurement", "")
            if uom == "kW":
                power = power * 1000.0

        # 3-phase balanced estimate: I = |P| / (3 * V_phase)
        return abs(power) / (3.0 * 230.0)
    ```
    This divides total power by 3 * 230V, assuming perfectly balanced phases.
    The docstring even acknowledges "per-phase sensors would give more accurate
    results" but takes the approximation path.
  implication: |
    THIS IS THE CORE SAFETY ISSUE. Example scenario:
    - Phase A: 6900W (30A), Phase B: 1150W (5A), Phase C: 1150W (5A)
    - Total: 9200W
    - Balanced estimate: 9200 / (3 * 230) = 13.3A per phase
    - Actual worst case: 30A on phase A
    - With 20A fuse: system calculates headroom = 20 - 13.3 - 2 = 4.7A (safe)
    - Reality: phase A is already 10A OVER the fuse rating

- timestamp: 2026-02-23T10:00:00Z
  checked: coordinator.py line 642 (_async_update_data)
  found: |
    Single value passed to compute_ems_state:
    ```python
    l_current = self._read_grid_current_amps()
    ...
    result = compute_ems_state(
        ...
        current_l_amps=l_current,
        ...
    )
    ```
  implication: compute_ems_state receives a single scalar, not per-phase data.

- timestamp: 2026-02-23T10:00:00Z
  checked: ems_controller.py lines 139-152 and 186-187 (compute_ems_state)
  found: |
    Function signature takes `current_l_amps: float` — a single value.
    Docstring says: "current_l_amps: Current highest phase load in amps."
    But the coordinator passes the balanced average, not the highest phase.
    Formula: `headroom = max(0.0, fuse_rating_amps - current_l_amps - safety_buffer_amps)`
  implication: |
    The ems_controller itself is designed correctly — its docstring says it expects
    "Current highest phase load in amps". The bug is in the coordinator layer which
    provides a balanced average instead of the actual highest phase value.

- timestamp: 2026-02-23T10:00:00Z
  checked: strings.json and translations/en.json (EMS step descriptions)
  found: |
    grid_power_entity description: "Sensor showing total grid active power (kW or W).
    Used to calculate fuse headroom — current is estimated from power."
    This description explicitly says "total grid active power" — reinforcing the
    single-sensor design.
  implication: UI text would need updating for per-phase support.

- timestamp: 2026-02-23T10:00:00Z
  checked: existing debug session .planning/debug/fuse-headroom-always-18a.md
  found: |
    Previous diagnosis found that fuse_headroom_amps stays at 18A because the
    grid power entity is empty string, causing 0A to be assumed. That bug is
    COMPOUNDING — even if the entity were properly configured, the balanced-load
    approximation would still be unsafe.
  implication: Two bugs compound: (1) entity often not configured, (2) even when configured, uses unsafe averaging.

## Resolution

root_cause: |
  The fuse protection system is architecturally single-phase throughout the entire stack:

  1. **const.py**: Only defines `CONF_GRID_POWER_ENTITY` (one key, one sensor)
  2. **auto_detect.py**: Actively filters OUT per-phase sensors with `"phase_" not in entity_id_lower`
  3. **config_flow.py**: Only offers one EntitySelector for grid power
  4. **coordinator.py**: `_read_grid_current_amps()` reads one sensor, divides total power by 3
     assuming balanced load: `abs(power) / (3.0 * 230.0)`
  5. **ems_controller.py**: Takes `current_l_amps: float` — designed for highest-phase current
     (docstring says "Current highest phase load in amps") but receives the balanced average

  The safety risk: on a 20A fuse, if one phase draws 30A and the other two draw 5A each,
  total power = 9.2kW, balanced estimate = 13.3A per phase. The system sees 4.7A headroom
  and allows battery charging. In reality, phase A is 10A over the fuse rating.

  The user has three per-phase sensors available:
  - sensor.sigen_plant_grid_phase_a_active_power
  - sensor.sigen_plant_grid_phase_b_active_power
  - sensor.sigen_plant_grid_phase_c_active_power

  These are EXACTLY what is needed but are currently excluded by design.

fix: |
  NOT APPLIED (goal: find_root_cause_only)

  Required changes across 6 files:

  ### 1. const.py — Add per-phase config keys
  Add three new config keys alongside the existing CONF_GRID_POWER_ENTITY:
  ```python
  CONF_GRID_PHASE_A_ENTITY = "grid_phase_a_entity"
  CONF_GRID_PHASE_B_ENTITY = "grid_phase_b_entity"
  CONF_GRID_PHASE_C_ENTITY = "grid_phase_c_entity"
  ```
  Keep CONF_GRID_POWER_ENTITY as fallback for users who only have total power.

  ### 2. auto_detect.py — Detect per-phase sensors
  In `find_sigenstor_ems_entities()`:
  - ADD detection for per-phase grid power sensors (match "phase_a_active_power",
    "phase_b_active_power", "phase_c_active_power")
  - KEEP the total grid power detection as fallback
  - Return all four keys when per-phase sensors are found, or just the total when not
  - Apply same logic in both the sigen config entry loop and fallback global scan

  ### 3. config_flow.py — Add per-phase entity selectors to EMS step
  In `async_step_ems()`:
  - Add three new EntitySelector fields for phase A/B/C grid power
  - Keep the single grid_power_entity field as a fallback option
  - Pre-fill with auto-detected per-phase entities
  - Store all three in `_create_entry()` options dict
  - Update descriptions to explain: "For accurate fuse protection, configure all
    three per-phase sensors. If unavailable, a single total power sensor can be
    used as a less accurate fallback."

  ### 4. coordinator.py — Read per-phase and compute MAX
  In `EMSCoordinator.__init__()`:
  - Load three per-phase entity IDs from options (with "" defaults)
  - Keep single grid power entity as fallback

  In `_async_setup()`:
  - Register state change listeners for all three per-phase entities (fuse-critical)
  - Fall back to single entity listener if per-phase not configured

  Replace `_read_grid_current_amps()` with new logic:
  ```python
  def _read_grid_current_amps(self) -> float:
      """Read per-phase grid power and return worst-case phase current in amps.

      If per-phase sensors are configured, reads each phase's power and converts
      to amps independently: I = abs(P_phase) / V_phase. Returns the MAX across
      all three phases (worst-case for fuse protection).

      Falls back to total-power balanced-load estimate if per-phase sensors are
      not configured.
      """
      phase_entities = [
          self._grid_phase_a_entity,
          self._grid_phase_b_entity,
          self._grid_phase_c_entity,
      ]

      if all(phase_entities):
          # Per-phase mode: convert each phase to amps, take worst case
          phase_amps = []
          for entity_id in phase_entities:
              power = self._read_float_state(entity_id, 0.0)
              state = self.hass.states.get(entity_id)
              if state is not None:
                  uom = state.attributes.get("unit_of_measurement", "")
                  if uom == "kW":
                      power = power * 1000.0
              phase_amps.append(abs(power) / 230.0)
          return max(phase_amps) if phase_amps else 0.0

      # Fallback: single total power sensor with balanced-load assumption
      power = self._read_float_state(self._grid_power_entity, 0.0)
      if power == 0.0:
          return 0.0
      state = self.hass.states.get(self._grid_power_entity)
      if state is not None:
          uom = state.attributes.get("unit_of_measurement", "")
          if uom == "kW":
              power = power * 1000.0
      return abs(power) / (3.0 * 230.0)
  ```

  ### 5. ems_controller.py — No changes needed
  The pure function already expects "current highest phase load in amps" per its
  docstring. The fix is entirely in how the coordinator computes that value.

  ### 6. strings.json + translations/en.json — Update UI text
  Add labels and descriptions for the three new per-phase entity fields:
  - "Grid phase A power sensor" / "Grid phase B power sensor" / "Grid phase C power sensor"
  - Update grid_power_entity description to say it is a fallback if per-phase not available
  - Add description noting: "For 3-phase installations, configure per-phase sensors for
    accurate fuse protection. The system will use the highest loaded phase."

  ### 7. tests/test_ems_controller.py — Add per-phase scenarios
  The pure ems_controller tests do not need changes (it already tests with scalar current).
  But integration/coordinator tests should add:
  - Test: per-phase configured, unbalanced load -> coordinator passes highest phase current
  - Test: per-phase configured, balanced load -> same result as total/3
  - Test: per-phase partially configured (only 2 of 3) -> fallback to total power
  - Test: per-phase all return 0 -> returns 0
  - Test: mixed units (some kW, some W) -> correct conversion

verification: NOT PERFORMED
files_changed: []

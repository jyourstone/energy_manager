"""Regression tests for EMS entity auto-detection in find_sigenstor_ems_entities().

Tests verify:
- Charge/discharge limit detection: number domain ONLY, with preference order
  max_charging_limit/max_discharging_limit > charging_limit/discharging_limit >
  ess_rated_charging/ess_rated_discharging. Sensor-domain entities (e.g.
  "rated_*" capability sensors) must NEVER be selected -- this is a deliberate
  semantic change from the 03-04-era tests, which asserted sensor-domain
  acceptance based on a misdiagnosis (see phase41 UAT bug 2: rated_* sensors
  are read-only capabilities, not writable setpoints).
- Grid power detection for fuse headroom (replaces L-current)
- PV power global fallback scan with plant-over-inverter preference
- Disabled entity filtering (entities disabled by integration are skipped)
- EMS select detection sanity check
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from custom_components.energy_manager.auto_detect import (
    find_house_consumption_entity,
    find_sigenstor_ems_entities,
    find_sigenstor_entities,
)
from custom_components.energy_manager.const import (
    CONF_AVAILABLE_DISCHARGE_POWER_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMS_SELECT_ENTITY,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_RATED_DISCHARGE_POWER_ENTITY,
)


@dataclass
class FakeEntityEntry:
    """Minimal entity entry mock matching homeassistant.helpers.entity_registry.RegistryEntry."""

    entity_id: str
    domain: str
    unique_id: str | None = None
    original_name: str | None = None
    device_id: str | None = None
    disabled_by: str | None = None


@dataclass
class FakeConfigEntry:
    """Minimal config entry mock."""

    entry_id: str
    domain: str


class FakeEntityRegistry:
    """Minimal entity registry mock supporting async_entries_for_config_entry."""

    def __init__(self, entries: list[FakeEntityEntry]) -> None:
        self._entries = entries
        # entities dict keyed by entity_id (used by global fallback scans)
        self.entities: dict[str, FakeEntityEntry] = {
            e.entity_id: e for e in entries
        }

    def entries_for_config_entry(self, config_entry_id: str) -> list[FakeEntityEntry]:
        """Return entities associated with the given config entry."""
        return self._entries


def _build_hass(
    config_entries: list[FakeConfigEntry],
    entity_entries: list[FakeEntityEntry],
) -> MagicMock:
    """Build a minimal mock hass object for auto-detect testing."""
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = config_entries
    return hass


def _run_detect(
    sigen_entities: list[FakeEntityEntry],
    global_entities: list[FakeEntityEntry] | None = None,
) -> dict[str, str]:
    """Run find_sigenstor_ems_entities with mocked HA objects.

    Args:
        sigen_entities: Entities that appear under the sigen config entry.
        global_entities: ALL entities in the registry (for global fallback scans).
                        If None, defaults to sigen_entities.
    """
    if global_entities is None:
        global_entities = sigen_entities

    sigen_entry = FakeConfigEntry(entry_id="sigen_entry_1", domain="sigenergy")
    hass = _build_hass(
        config_entries=[sigen_entry],
        entity_entries=global_entities,
    )
    registry = FakeEntityRegistry(global_entities)

    with (
        patch(
            "custom_components.energy_manager.auto_detect.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.er.async_entries_for_config_entry",
            return_value=sigen_entities,
        ),
    ):
        return find_sigenstor_ems_entities(hass)


def _run_all_detectors(
    sigen_entities: list[FakeEntityEntry],
    global_entities: list[FakeEntityEntry] | None = None,
) -> dict[str, str]:
    """Run the ems step's detector union against the same fake registry.

    Merges find_sigenstor_ems_entities, find_sigenstor_entities and
    find_house_consumption_entity in the same order async_step_ems builds
    its auto-detect prefill, in both the setup and the options flow.
    """
    if global_entities is None:
        global_entities = sigen_entities

    sigen_entry = FakeConfigEntry(entry_id="sigen_entry_1", domain="sigenergy")
    hass = _build_hass(
        config_entries=[sigen_entry],
        entity_entries=global_entities,
    )
    registry = FakeEntityRegistry(global_entities)

    with (
        patch(
            "custom_components.energy_manager.auto_detect.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.er.async_entries_for_config_entry",
            return_value=sigen_entities,
        ),
    ):
        detected = find_sigenstor_ems_entities(hass)
        detected.update(find_sigenstor_entities(hass))
        detected.update(find_house_consumption_entity(hass))
        return detected


# ---------------------------------------------------------------------------
# Test 1: Charge limit detected with sensor domain
# ---------------------------------------------------------------------------


class TestChargeLimit:
    """Charge limit entity: number domain ONLY, preference-ordered patterns.

    NOTE: semantic change from the 03-04-era tests. Sensor-domain
    "ess_rated_charging_power" entities are READ-ONLY capabilities, not
    writable setpoints, and must never be selected (phase41 UAT bug 2).
    """

    def test_detects_max_charging_limit_number_domain(self):
        """number.sigen_plant_ess_max_charging_limit should be detected (top tier)."""
        entity = FakeEntityEntry(
            entity_id="number.sigen_plant_ess_max_charging_limit",
            domain="number",
            unique_id="sigen_plant_ess_max_charging_limit",
        )
        result = _run_detect([entity])
        assert CONF_CHARGE_LIMIT_ENTITY in result
        assert result[CONF_CHARGE_LIMIT_ENTITY] == entity.entity_id

    def test_prefers_max_charging_limit_over_rated(self):
        """When both max_charging_limit and a rated number entity exist, prefer max."""
        rated = FakeEntityEntry(
            entity_id="number.sigen_battery_ess_rated_charging_power",
            domain="number",
            unique_id="sigen_battery_ess_rated_charging_power",
        )
        max_entity = FakeEntityEntry(
            entity_id="number.sigen_plant_ess_max_charging_limit",
            domain="number",
            unique_id="sigen_plant_ess_max_charging_limit",
        )
        result = _run_detect([rated, max_entity])
        assert result[CONF_CHARGE_LIMIT_ENTITY] == max_entity.entity_id

    def test_never_selects_sensor_domain_rated_charging(self):
        """sensor.sigen_battery_ess_rated_charging_power must NOT be selected --
        it is a read-only capability sensor, not a writable setpoint."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_battery_ess_rated_charging_power",
            domain="sensor",
            unique_id="sigen_battery_ess_rated_charging_power",
        )
        result = _run_detect([entity])
        assert CONF_CHARGE_LIMIT_ENTITY not in result


# ---------------------------------------------------------------------------
# Test 2: Discharge limit detected -- number domain only, preference order
# ---------------------------------------------------------------------------


class TestDischargeLimit:
    """Discharge limit entity: number domain ONLY, preference-ordered patterns.

    NOTE: semantic change from the 03-04-era tests. Sensor-domain
    "ess_rated_discharging_power" entities are READ-ONLY capabilities, not
    writable setpoints, and must never be selected (phase41 UAT bug 2).
    """

    def test_detects_max_discharging_limit_number_domain(self):
        """number.sigen_plant_ess_max_discharging_limit should be detected (top tier)."""
        entity = FakeEntityEntry(
            entity_id="number.sigen_plant_ess_max_discharging_limit",
            domain="number",
            unique_id="sigen_plant_ess_max_discharging_limit",
        )
        result = _run_detect([entity])
        assert CONF_DISCHARGE_LIMIT_ENTITY in result
        assert result[CONF_DISCHARGE_LIMIT_ENTITY] == entity.entity_id

    def test_never_selects_sensor_domain_rated_discharging(self):
        """sensor.sigen_plant_ess_rated_discharging_power must NOT be selected --
        it is a read-only capability sensor, not a writable setpoint."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_ess_rated_discharging_power",
            domain="sensor",
            unique_id="sigen_plant_ess_rated_discharging_power",
        )
        result = _run_detect([entity])
        assert CONF_DISCHARGE_LIMIT_ENTITY not in result

    def test_falls_back_to_rated_when_number_domain(self):
        """ess_rated_discharging as a number-domain entity (rare firmware) is
        accepted as the last-resort tier when no better pattern exists."""
        entity = FakeEntityEntry(
            entity_id="number.sigen_plant_ess_rated_discharging_power",
            domain="number",
            unique_id="sigen_plant_ess_rated_discharging_power",
        )
        result = _run_detect([entity])
        assert CONF_DISCHARGE_LIMIT_ENTITY in result
        assert result[CONF_DISCHARGE_LIMIT_ENTITY] == entity.entity_id


# ---------------------------------------------------------------------------
# Test 2b: Discharge power cap sensors (available/rated) -- hardware clamp
# ---------------------------------------------------------------------------


class TestDischargePowerCapSensors:
    """Sensor-domain capability sensors used by coordinator._send_discharge_limit
    to clamp the discharge limit write against SigenStor's live rated/available
    discharge power (avoids exception_code=2 illegal-data-address rejects)."""

    def test_detects_available_max_discharging_power_sensor(self):
        """sensor.sigen_plant_available_max_discharging_power should be detected."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_available_max_discharging_power",
            domain="sensor",
            unique_id="sigen_plant_available_max_discharging_power",
        )
        result = _run_detect([entity])
        assert CONF_AVAILABLE_DISCHARGE_POWER_ENTITY in result
        assert result[CONF_AVAILABLE_DISCHARGE_POWER_ENTITY] == entity.entity_id

    def test_detects_rated_discharging_power_sensor(self):
        """sensor.sigen_battery_ess_rated_discharging_power should be detected."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_battery_ess_rated_discharging_power",
            domain="sensor",
            unique_id="sigen_battery_ess_rated_discharging_power",
        )
        result = _run_detect([entity])
        assert CONF_RATED_DISCHARGE_POWER_ENTITY in result
        assert result[CONF_RATED_DISCHARGE_POWER_ENTITY] == entity.entity_id

    def test_number_domain_not_selected_for_cap_sensors(self):
        """A number-domain entity matching these substrings must NOT be
        selected for the cap-sensor keys -- these are sensor-domain-only
        read caps, distinct from the writable discharge limit setpoint."""
        available = FakeEntityEntry(
            entity_id="number.sigen_plant_available_max_discharging_power",
            domain="number",
            unique_id="sigen_plant_available_max_discharging_power",
        )
        rated = FakeEntityEntry(
            entity_id="number.sigen_battery_ess_rated_discharging_power",
            domain="number",
            unique_id="sigen_battery_ess_rated_discharging_power",
        )
        result = _run_detect([available, rated])
        assert CONF_AVAILABLE_DISCHARGE_POWER_ENTITY not in result
        assert CONF_RATED_DISCHARGE_POWER_ENTITY not in result

    def test_keys_absent_when_no_matching_sensors(self):
        """Neither cap-sensor key is present when no matching sensors exist."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_active_power",
            domain="sensor",
            unique_id="sigen_plant_grid_active_power",
        )
        result = _run_detect([entity])
        assert CONF_AVAILABLE_DISCHARGE_POWER_ENTITY not in result
        assert CONF_RATED_DISCHARGE_POWER_ENTITY not in result


# ---------------------------------------------------------------------------
# Test 3: Grid power detection (replaces L-current)
# ---------------------------------------------------------------------------


class TestGridPower:
    """Grid power detection for fuse headroom calculation."""

    def test_detects_grid_active_power(self):
        """sensor.sigen_plant_grid_active_power should be detected."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_active_power",
            domain="sensor",
            unique_id="sigen_plant_grid_active_power",
        )
        result = _run_detect([entity])
        assert CONF_GRID_POWER_ENTITY in result
        assert result[CONF_GRID_POWER_ENTITY] == entity.entity_id

    def test_skips_per_phase_grid_power(self):
        """Per-phase grid power sensors should NOT be selected (prefer total)."""
        phase_entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_a_active_power",
            domain="sensor",
        )
        total_entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_active_power",
            domain="sensor",
        )
        result = _run_detect([phase_entity, total_entity])
        assert CONF_GRID_POWER_ENTITY in result
        assert result[CONF_GRID_POWER_ENTITY] == total_entity.entity_id

    def test_grid_power_fallback_scan(self):
        """Grid power found via global fallback when not in sigen config entry."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_active_power",
            domain="sensor",
        )
        result = _run_detect(sigen_entities=[], global_entities=[entity])
        assert CONF_GRID_POWER_ENTITY in result
        assert result[CONF_GRID_POWER_ENTITY] == entity.entity_id


# ---------------------------------------------------------------------------
# Test 4: PV power via global fallback
# ---------------------------------------------------------------------------


class TestPVPowerFallback:
    """PV power detection via global fallback scan."""

    def test_detects_pv_power_via_global_fallback(self):
        """PV power entity NOT under sigen config entry should be found via global fallback."""
        pv_entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_pv_power",
            domain="sensor",
            unique_id="sigen_plant_pv_power",
        )
        # Pass pv_entity only in global_entities, NOT in sigen_entities
        # (simulates entity not being registered under the sigen config entry)
        result = _run_detect(sigen_entities=[], global_entities=[pv_entity])
        assert CONF_PV_POWER_ENTITY in result
        assert result[CONF_PV_POWER_ENTITY] == pv_entity.entity_id


# ---------------------------------------------------------------------------
# Test 5: PV fallback prefers plant over inverter
# ---------------------------------------------------------------------------


class TestPVPlantPreference:
    """PV fallback should prefer plant-level entity over inverter-level."""

    def test_pv_fallback_prefers_plant_over_inverter(self):
        """When both plant and inverter PV entities exist, prefer plant."""
        inverter_entity = FakeEntityEntry(
            entity_id="sensor.sigen_inverter_pv_power",
            domain="sensor",
            unique_id="sigen_inverter_pv_power",
        )
        plant_entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_pv_power",
            domain="sensor",
            unique_id="sigen_plant_pv_power",
        )
        # Both entities in global but NOT in sigen config entry
        result = _run_detect(
            sigen_entities=[],
            global_entities=[inverter_entity, plant_entity],
        )
        assert CONF_PV_POWER_ENTITY in result
        assert result[CONF_PV_POWER_ENTITY] == "sensor.sigen_plant_pv_power"


# ---------------------------------------------------------------------------
# Test 6: Disabled entities are skipped
# ---------------------------------------------------------------------------


class TestDisabledEntityFiltering:
    """Entities disabled by the integration should be skipped."""

    def test_skips_disabled_pv_power(self):
        """Disabled PV entity should be skipped in favor of enabled one."""
        disabled_entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_sigen_pv_power",
            domain="sensor",
            disabled_by="integration",
        )
        enabled_entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_pv_power",
            domain="sensor",
        )
        result = _run_detect([disabled_entity, enabled_entity])
        assert CONF_PV_POWER_ENTITY in result
        assert result[CONF_PV_POWER_ENTITY] == "sensor.sigen_plant_pv_power"

    def test_skips_disabled_grid_power_in_fallback(self):
        """Disabled grid power entity skipped in global fallback."""
        disabled_entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_active_power",
            domain="sensor",
            disabled_by="integration",
        )
        result = _run_detect(sigen_entities=[], global_entities=[disabled_entity])
        assert CONF_GRID_POWER_ENTITY not in result


# ---------------------------------------------------------------------------
# Test 7: EMS select sanity check (existing working pattern)
# ---------------------------------------------------------------------------


class TestEMSSelectSanity:
    """Sanity check: EMS select detection should still work after all changes."""

    def test_ems_select_still_detected(self):
        """select.sigen_remote_ems_control should still be detected."""
        entity = FakeEntityEntry(
            entity_id="select.sigen_remote_ems_control",
            domain="select",
            unique_id="sigen_remote_ems_control",
        )
        result = _run_detect([entity])
        assert CONF_EMS_SELECT_ENTITY in result
        assert result[CONF_EMS_SELECT_ENTITY] == entity.entity_id


# ---------------------------------------------------------------------------
# Test 8: Per-phase grid power detection
# ---------------------------------------------------------------------------


class TestPerPhaseGridPower:
    """Per-phase grid power detection for 3-phase fuse protection."""

    def test_detects_per_phase_a_power(self):
        """sensor.sigen_plant_grid_phase_a_active_power should be detected for phase A."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_a_active_power",
            domain="sensor",
            unique_id="sigen_plant_grid_phase_a_active_power",
        )
        result = _run_detect([entity])
        assert CONF_GRID_PHASE_A_ENTITY in result
        assert result[CONF_GRID_PHASE_A_ENTITY] == entity.entity_id

    def test_detects_all_three_phases(self):
        """All three per-phase grid power sensors should be detected."""
        phase_a = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_a_active_power",
            domain="sensor",
            unique_id="sigen_plant_grid_phase_a_active_power",
        )
        phase_b = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_b_active_power",
            domain="sensor",
            unique_id="sigen_plant_grid_phase_b_active_power",
        )
        phase_c = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_c_active_power",
            domain="sensor",
            unique_id="sigen_plant_grid_phase_c_active_power",
        )
        result = _run_detect([phase_a, phase_b, phase_c])
        assert CONF_GRID_PHASE_A_ENTITY in result
        assert result[CONF_GRID_PHASE_A_ENTITY] == phase_a.entity_id
        assert CONF_GRID_PHASE_B_ENTITY in result
        assert result[CONF_GRID_PHASE_B_ENTITY] == phase_b.entity_id
        assert CONF_GRID_PHASE_C_ENTITY in result
        assert result[CONF_GRID_PHASE_C_ENTITY] == phase_c.entity_id

    def test_per_phase_and_total_coexist(self):
        """Per-phase and total grid power should all be detected simultaneously."""
        phase_a = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_a_active_power",
            domain="sensor",
        )
        phase_b = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_b_active_power",
            domain="sensor",
        )
        phase_c = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_c_active_power",
            domain="sensor",
        )
        total = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_active_power",
            domain="sensor",
        )
        result = _run_detect([phase_a, phase_b, phase_c, total])
        assert CONF_GRID_PHASE_A_ENTITY in result
        assert CONF_GRID_PHASE_B_ENTITY in result
        assert CONF_GRID_PHASE_C_ENTITY in result
        assert CONF_GRID_POWER_ENTITY in result
        assert result[CONF_GRID_POWER_ENTITY] == total.entity_id

    def test_per_phase_fallback_scan(self):
        """Per-phase entities found via global fallback when not in sigen config entry."""
        phase_a = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_a_active_power",
            domain="sensor",
        )
        phase_b = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_b_active_power",
            domain="sensor",
        )
        phase_c = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_c_active_power",
            domain="sensor",
        )
        result = _run_detect(
            sigen_entities=[],
            global_entities=[phase_a, phase_b, phase_c],
        )
        assert CONF_GRID_PHASE_A_ENTITY in result
        assert result[CONF_GRID_PHASE_A_ENTITY] == phase_a.entity_id
        assert CONF_GRID_PHASE_B_ENTITY in result
        assert result[CONF_GRID_PHASE_B_ENTITY] == phase_b.entity_id
        assert CONF_GRID_PHASE_C_ENTITY in result
        assert result[CONF_GRID_PHASE_C_ENTITY] == phase_c.entity_id


# ---------------------------------------------------------------------------
# Test 9: ems step detector union
# ---------------------------------------------------------------------------


class TestEmsStepDetectorUnion:
    """Pins the ems step's auto-detect prefill: the union of
    find_sigenstor_ems_entities, find_sigenstor_entities and
    find_house_consumption_entity, called together by async_step_ems in
    both the setup and the options flow."""

    def test_union_supplies_battery_power_for_the_ems_step(self):
        """A sigen registry with a battery power sensor yields CONF_BATTERY_POWER_ENTITY."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_battery_power",
            domain="sensor",
            unique_id="sigen_battery_power",
        )
        result = _run_all_detectors([entity])
        assert CONF_BATTERY_POWER_ENTITY in result
        assert result[CONF_BATTERY_POWER_ENTITY] == entity.entity_id

    def test_union_supplies_house_consumption_for_the_ems_step(self):
        """A sigen registry with a plant consumed-power sensor yields CONF_HOUSE_CONSUMPTION_ENTITY."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_consumed_power",
            domain="sensor",
            unique_id="sigen_plant_consumed_power",
        )
        result = _run_all_detectors([entity])
        assert CONF_HOUSE_CONSUMPTION_ENTITY in result
        assert result[CONF_HOUSE_CONSUMPTION_ENTITY] == entity.entity_id

    def test_union_covers_every_auto_detectable_ems_field(self):
        """A fully-populated fake registry yields every field the ems step
        can auto-detect: battery power, house consumption, PV power, grid
        power, the three grid phases, EMS select, charge and discharge limit."""
        entities = [
            FakeEntityEntry(
                entity_id="sensor.sigen_battery_power",
                domain="sensor",
                unique_id="sigen_battery_power",
            ),
            FakeEntityEntry(
                entity_id="sensor.sigen_plant_consumed_power",
                domain="sensor",
                unique_id="sigen_plant_consumed_power",
            ),
            FakeEntityEntry(
                entity_id="sensor.sigen_plant_pv_power",
                domain="sensor",
                unique_id="sigen_plant_pv_power",
            ),
            FakeEntityEntry(
                entity_id="sensor.sigen_plant_grid_active_power",
                domain="sensor",
                unique_id="sigen_plant_grid_active_power",
            ),
            FakeEntityEntry(
                entity_id="sensor.sigen_plant_grid_phase_a_active_power",
                domain="sensor",
                unique_id="sigen_plant_grid_phase_a_active_power",
            ),
            FakeEntityEntry(
                entity_id="sensor.sigen_plant_grid_phase_b_active_power",
                domain="sensor",
                unique_id="sigen_plant_grid_phase_b_active_power",
            ),
            FakeEntityEntry(
                entity_id="sensor.sigen_plant_grid_phase_c_active_power",
                domain="sensor",
                unique_id="sigen_plant_grid_phase_c_active_power",
            ),
            FakeEntityEntry(
                entity_id="select.sigen_remote_ems_control",
                domain="select",
                unique_id="sigen_remote_ems_control",
            ),
            FakeEntityEntry(
                entity_id="number.sigen_plant_ess_max_charging_limit",
                domain="number",
                unique_id="sigen_plant_ess_max_charging_limit",
            ),
            FakeEntityEntry(
                entity_id="number.sigen_plant_ess_max_discharging_limit",
                domain="number",
                unique_id="sigen_plant_ess_max_discharging_limit",
            ),
        ]
        result = _run_all_detectors(entities)
        for key in (
            CONF_BATTERY_POWER_ENTITY,
            CONF_HOUSE_CONSUMPTION_ENTITY,
            CONF_PV_POWER_ENTITY,
            CONF_GRID_POWER_ENTITY,
            CONF_GRID_PHASE_A_ENTITY,
            CONF_GRID_PHASE_B_ENTITY,
            CONF_GRID_PHASE_C_ENTITY,
            CONF_EMS_SELECT_ENTITY,
            CONF_CHARGE_LIMIT_ENTITY,
            CONF_DISCHARGE_LIMIT_ENTITY,
        ):
            assert key in result

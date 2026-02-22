"""Regression tests for EMS entity auto-detection in find_sigenstor_ems_entities().

Tests verify fixes for 4 bugs discovered during UAT:
- BUG 1: Charge limit entities with sensor domain (not just number)
- BUG 2: Discharge limit entities with sensor domain (not just number)
- BUG 3: L-current fallback via phase_active_power patterns
- BUG 4: PV power global fallback scan with plant-over-inverter preference
Plus a sanity check for EMS select detection (which was working).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from custom_components.energy_manager.auto_detect import (
    find_sigenstor_ems_entities,
)
from custom_components.energy_manager.const import (
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMS_SELECT_ENTITY,
    CONF_L_CURRENT_ENTITY,
    CONF_PV_POWER_ENTITY,
)


@dataclass
class FakeEntityEntry:
    """Minimal entity entry mock matching homeassistant.helpers.entity_registry.RegistryEntry."""

    entity_id: str
    domain: str
    unique_id: str | None = None
    original_name: str | None = None
    device_id: str | None = None


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


# ---------------------------------------------------------------------------
# Test 1: Charge limit detected with sensor domain
# ---------------------------------------------------------------------------


class TestChargeLimit:
    """BUG 1: Charge limit entity with sensor domain and ess_rated_charging pattern."""

    def test_detects_charge_limit_sensor_domain(self):
        """sensor.sigen_battery_ess_rated_charging_power should be detected."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_battery_ess_rated_charging_power",
            domain="sensor",
            unique_id="sigen_battery_ess_rated_charging_power",
        )
        result = _run_detect([entity])
        assert CONF_CHARGE_LIMIT_ENTITY in result
        assert result[CONF_CHARGE_LIMIT_ENTITY] == entity.entity_id


# ---------------------------------------------------------------------------
# Test 2: Discharge limit detected with sensor domain
# ---------------------------------------------------------------------------


class TestDischargeLimit:
    """BUG 2: Discharge limit entity with sensor domain and ess_rated_discharging pattern."""

    def test_detects_discharge_limit_sensor_domain(self):
        """sensor.sigen_plant_ess_rated_discharging_power should be detected."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_ess_rated_discharging_power",
            domain="sensor",
            unique_id="sigen_plant_ess_rated_discharging_power",
        )
        result = _run_detect([entity])
        assert CONF_DISCHARGE_LIMIT_ENTITY in result
        assert result[CONF_DISCHARGE_LIMIT_ENTITY] == entity.entity_id


# ---------------------------------------------------------------------------
# Test 3: L-current via phase_active_power pattern
# ---------------------------------------------------------------------------


class TestLCurrent:
    """BUG 3: L-current detection via phase_a_active_power pattern."""

    def test_detects_l_current_via_phase_active_power(self):
        """sensor.sigen_plant_grid_phase_a_active_power should be detected."""
        entity = FakeEntityEntry(
            entity_id="sensor.sigen_plant_grid_phase_a_active_power",
            domain="sensor",
            unique_id="sigen_plant_grid_phase_a_active_power",
        )
        result = _run_detect([entity])
        assert CONF_L_CURRENT_ENTITY in result
        assert result[CONF_L_CURRENT_ENTITY] == entity.entity_id


# ---------------------------------------------------------------------------
# Test 4: PV power via global fallback
# ---------------------------------------------------------------------------


class TestPVPowerFallback:
    """BUG 4: PV power detection via global fallback scan."""

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
# Test 6: EMS select sanity check (existing working pattern)
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

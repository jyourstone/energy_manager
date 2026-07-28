"""Tests for Phase 5 Wave B Easee-related auto-detection helpers.

Covers:
- derive_charger_device_id(): pure decision (no HA access).
- find_easee_charger_device_id(): HA-touching lookup via entity/device registry.
- find_house_consumption_entity(): SigenStor "consumed_power" auto-detect.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from custom_components.energy_manager.auto_detect import (
    derive_charger_device_id,
    find_easee_charger_device_id,
    find_house_consumption_entity,
)
from custom_components.energy_manager.const import CONF_HOUSE_CONSUMPTION_ENTITY


@dataclass
class FakeEntityEntry:
    """Minimal entity entry mock matching homeassistant.helpers.entity_registry.RegistryEntry."""

    entity_id: str
    domain: str
    unique_id: str | None = None
    device_id: str | None = None
    disabled_by: str | None = None


@dataclass
class FakeConfigEntry:
    entry_id: str
    domain: str


@dataclass
class FakeDeviceEntry:
    id: str


# ---------------------------------------------------------------------------
# derive_charger_device_id() -- pure
# ---------------------------------------------------------------------------


def test_derive_charger_device_id_prefers_status_entity_device() -> None:
    assert derive_charger_device_id("dev-from-entity", ["dev-fallback"]) == "dev-from-entity"


def test_derive_charger_device_id_falls_back_when_no_entity_device() -> None:
    assert derive_charger_device_id(None, ["dev-fallback", "dev-other"]) == "dev-fallback"


def test_derive_charger_device_id_none_when_nothing_found() -> None:
    assert derive_charger_device_id(None, []) is None


# ---------------------------------------------------------------------------
# find_easee_charger_device_id() -- HA-touching
# ---------------------------------------------------------------------------


def test_find_easee_charger_device_id_from_status_entity() -> None:
    hass = MagicMock()
    registry = MagicMock()
    registry.async_get.return_value = FakeEntityEntry(
        entity_id="sensor.easee_status", domain="sensor", device_id="dev-123"
    )

    with patch(
        "custom_components.energy_manager.auto_detect.er.async_get",
        return_value=registry,
    ):
        result = find_easee_charger_device_id(hass, "sensor.easee_status")

    assert result == "dev-123"


def test_find_easee_charger_device_id_falls_back_to_easee_device_registry() -> None:
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [
        FakeConfigEntry(entry_id="easee_entry_1", domain="easee")
    ]
    registry = MagicMock()
    registry.async_get.return_value = None  # status entity not registered yet
    device_registry = MagicMock()

    with (
        patch(
            "custom_components.energy_manager.auto_detect.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.dr.async_get",
            return_value=device_registry,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.dr.async_entries_for_config_entry",
            return_value=[FakeDeviceEntry(id="dev-fallback")],
        ),
    ):
        result = find_easee_charger_device_id(hass, "sensor.easee_status")

    assert result == "dev-fallback"


def test_find_easee_charger_device_id_empty_status_entity_skips_lookup() -> None:
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    registry = MagicMock()

    with (
        patch(
            "custom_components.energy_manager.auto_detect.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.dr.async_get",
            return_value=MagicMock(),
        ),
    ):
        result = find_easee_charger_device_id(hass, "")

    registry.async_get.assert_not_called()
    assert result is None


def test_find_easee_charger_device_id_nothing_found_returns_none() -> None:
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    registry = MagicMock()
    registry.async_get.return_value = None

    with (
        patch(
            "custom_components.energy_manager.auto_detect.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.dr.async_get",
            return_value=MagicMock(),
        ),
    ):
        result = find_easee_charger_device_id(hass, "sensor.easee_status")

    assert result is None


# ---------------------------------------------------------------------------
# find_house_consumption_entity()
# ---------------------------------------------------------------------------


def test_find_house_consumption_entity_found() -> None:
    hass = MagicMock()
    sigen_entry = FakeConfigEntry(entry_id="sigen_1", domain="sigenergy")
    hass.config_entries.async_entries.return_value = [sigen_entry]
    registry = MagicMock()
    entities = [
        FakeEntityEntry(
            entity_id="sensor.sigen_plant_consumed_power",
            domain="sensor",
            unique_id="plant_consumed_power",
        )
    ]

    with (
        patch(
            "custom_components.energy_manager.auto_detect.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.er.async_entries_for_config_entry",
            return_value=entities,
        ),
    ):
        result = find_house_consumption_entity(hass)

    assert result == {
        CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.sigen_plant_consumed_power"
    }


def test_find_house_consumption_entity_not_found() -> None:
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    registry = MagicMock()

    with patch(
        "custom_components.energy_manager.auto_detect.er.async_get",
        return_value=registry,
    ):
        result = find_house_consumption_entity(hass)

    assert result == {}

"""Regression tests for car auto-detection in find_car_integrations().

Tests verify (phase41 UAT bug 1):
- Target/goal SOC entities (e.g. mySkoda's target_battery_percentage, or the
  localized "mal_..." entity_id form) are excluded from SOC matching so the
  ACTUAL SOC entity wins when both exist on the same device.
- The suggested car name is derived from the car DEVICE's name in the device
  registry (e.g. "Skoda Enyaq"), falling back to the previous
  entity/config-entry-derived behavior only when no device name is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from custom_components.energy_manager.auto_detect import find_car_integrations


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
    title: str | None = None


@dataclass
class FakeDeviceEntry:
    """Minimal device entry mock matching homeassistant.helpers.device_registry.DeviceEntry."""

    name: str | None = None
    name_by_user: str | None = None


class FakeDeviceRegistry:
    """Minimal device registry mock supporting async_get."""

    def __init__(self, devices: dict[str, FakeDeviceEntry]) -> None:
        self._devices = devices

    def async_get(self, device_id: str) -> FakeDeviceEntry | None:
        return self._devices.get(device_id)


def _run_detect(
    entities: list[FakeEntityEntry],
    devices: dict[str, FakeDeviceEntry] | None = None,
    config_entry: FakeConfigEntry | None = None,
) -> list[dict[str, str]]:
    """Run find_car_integrations with mocked HA registries.

    Args:
        entities: Entities registered under the (single) matching config entry.
        devices: Mapping of device_id -> FakeDeviceEntry for the device registry.
        config_entry: The config entry to match against; defaults to a
            myskoda entry with no title (forces entity/platform-name fallback).
    """
    if config_entry is None:
        config_entry = FakeConfigEntry(
            entry_id="myskoda_entry_1", domain="myskoda", title=None
        )
    if devices is None:
        devices = {}

    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [config_entry]

    with (
        patch(
            "custom_components.energy_manager.auto_detect.er.async_get",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.energy_manager.auto_detect.er.async_entries_for_config_entry",
            return_value=entities,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.dr.async_get",
            return_value=FakeDeviceRegistry(devices),
        ),
    ):
        return find_car_integrations(hass)


# ---------------------------------------------------------------------------
# Test 1: Actual SOC wins over target/goal SOC
# ---------------------------------------------------------------------------


class TestActualSocWinsOverTarget:
    """The actual SOC entity must be selected, never the target/goal SOC."""

    def test_actual_wins_when_target_appears_first(self):
        """Target entity iterated before actual -- actual must still win."""
        target = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_mal_batteriprocent",
            domain="sensor",
            unique_id="VIN123_target_battery_percentage",
            device_id="device_1",
        )
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_batteriprocent",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            device_id="device_1",
        )
        cars = _run_detect([target, actual])
        assert len(cars) == 1
        assert cars[0]["battery_level_entity"] == actual.entity_id

    def test_actual_wins_when_target_appears_last(self):
        """Target entity iterated after actual -- actual must not be overwritten."""
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_batteriprocent",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            device_id="device_1",
        )
        target = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_mal_batteriprocent",
            domain="sensor",
            unique_id="VIN123_target_battery_percentage",
            device_id="device_1",
        )
        cars = _run_detect([actual, target])
        assert len(cars) == 1
        assert cars[0]["battery_level_entity"] == actual.entity_id

    def test_excludes_target_by_unique_id_even_with_english_entity_id(self):
        """unique_id containing 'target' is excluded even if entity_id doesn't."""
        target = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_target_battery_percentage",
            domain="sensor",
            unique_id="VIN123_target_battery_percentage",
            device_id="device_1",
        )
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_battery_percentage",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            device_id="device_1",
        )
        cars = _run_detect([target, actual])
        assert len(cars) == 1
        assert cars[0]["battery_level_entity"] == actual.entity_id

    def test_no_car_when_only_target_soc_exists(self):
        """If only the target/goal SOC entity exists, no car is detected."""
        target = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_mal_batteriprocent",
            domain="sensor",
            unique_id="VIN123_target_battery_percentage",
            device_id="device_1",
        )
        cars = _run_detect([target])
        assert cars == []


# ---------------------------------------------------------------------------
# Test 2: Car name derived from device registry
# ---------------------------------------------------------------------------


class TestCarNameFromDeviceRegistry:
    """Suggested car name should come from the device registry, not the sensor."""

    def test_uses_device_name_over_sensor_friendly_name(self):
        """Device name ("Skoda Enyaq") wins over sensor original_name ("Batteriprocent")."""
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_batteriprocent",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            original_name="Batteriprocent",
            device_id="device_1",
        )
        devices = {"device_1": FakeDeviceEntry(name="Skoda Enyaq")}
        cars = _run_detect([actual], devices=devices)
        assert len(cars) == 1
        assert cars[0]["name"] == "Skoda Enyaq"

    def test_prefers_name_by_user_over_name(self):
        """User-assigned device name takes priority over the default device name."""
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_batteriprocent",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            device_id="device_1",
        )
        devices = {
            "device_1": FakeDeviceEntry(name="Skoda Enyaq", name_by_user="My Car")
        }
        cars = _run_detect([actual], devices=devices)
        assert cars[0]["name"] == "My Car"

    def test_falls_back_to_sensor_name_when_no_device(self):
        """No device registry entry -- falls back to the sensor's original_name."""
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_batteriprocent",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            original_name="Batteriprocent sensor",
            device_id="device_1",
        )
        cars = _run_detect([actual], devices={})
        assert cars[0]["name"] == "Batteriprocent"

    def test_falls_back_to_config_entry_title_when_no_device_or_sensor_name(self):
        """No device and no sensor original_name -- falls back to config entry title."""
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_batteriprocent",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            device_id="device_1",
        )
        config_entry = FakeConfigEntry(
            entry_id="myskoda_entry_1", domain="myskoda", title="My Skoda"
        )
        cars = _run_detect([actual], devices={}, config_entry=config_entry)
        assert cars[0]["name"] == "My Skoda"

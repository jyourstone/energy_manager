"""Tests for device-scoped car entity matching in match_car_entities().

The user asserts which device is their car (a DeviceSelector in the car
subentry flow), so matching only has to pick the right entities *within* that
device -- it never has to tell a car apart from a phone or a UPS.

Tests verify:
- Battery level prefers device_class "battery", falling back to name keywords
  for integrations (and template sensors) that omit the class.
- Target/goal SOC entities (e.g. mySkoda's target_battery_percentage, or the
  localized "mal_..." entity_id form) are excluded so the ACTUAL SOC entity
  wins when both exist on the same device (phase41 UAT bug 1).
- The suggested car name comes from the device registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from custom_components.energy_manager.auto_detect import match_car_entities


@dataclass
class FakeEntityEntry:
    """Minimal entity entry mock matching homeassistant.helpers.entity_registry.RegistryEntry."""

    entity_id: str
    domain: str
    unique_id: str | None = None
    original_name: str | None = None
    device_id: str | None = None
    device_class: str | None = None
    original_device_class: str | None = None
    disabled_by: str | None = None


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


def _run_match(
    entities: list[FakeEntityEntry],
    devices: dict[str, FakeDeviceEntry] | None = None,
    device_id: str = "device_1",
) -> dict[str, str]:
    """Run match_car_entities with mocked HA registries.

    Args:
        entities: Entities registered to the device being matched.
        devices: Mapping of device_id -> FakeDeviceEntry for the device registry.
        device_id: The device the user picked.
    """
    if devices is None:
        devices = {}

    hass = MagicMock()

    with (
        patch(
            "custom_components.energy_manager.auto_detect.er.async_get",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.energy_manager.auto_detect.er.async_entries_for_device",
            return_value=entities,
        ),
        patch(
            "custom_components.energy_manager.auto_detect.dr.async_get",
            return_value=FakeDeviceRegistry(devices),
        ),
    ):
        return match_car_entities(hass, device_id)


# ---------------------------------------------------------------------------
# Battery level selection
# ---------------------------------------------------------------------------


class TestBatteryLevelSelection:
    """The car's SOC sensor must be picked out of the device's sensors."""

    def test_prefers_device_class_battery(self):
        """A device_class battery sensor wins over an unrelated named sensor."""
        soc = FakeEntityEntry(
            entity_id="sensor.enyaq_batteriprocent",
            domain="sensor",
            original_device_class="battery",
        )
        other = FakeEntityEntry(entity_id="sensor.enyaq_range", domain="sensor")
        assert _run_match([other, soc])["battery_level_entity"] == soc.entity_id

    def test_device_class_overrides_original_device_class(self):
        """A user-overridden device_class is honoured over the original."""
        soc = FakeEntityEntry(
            entity_id="sensor.enyaq_soc",
            domain="sensor",
            device_class="battery",
            original_device_class="power",
        )
        assert _run_match([soc])["battery_level_entity"] == soc.entity_id

    def test_falls_back_to_keywords_when_no_device_class(self):
        """Template sensors often omit device_class -- keywords must still match."""
        soc = FakeEntityEntry(
            entity_id="sensor.enyaq_state_of_charge", domain="sensor"
        )
        assert _run_match([soc])["battery_level_entity"] == soc.entity_id

    def test_device_class_wins_over_keyword_match(self):
        """A classed sensor beats a merely keyword-matching one."""
        keyword = FakeEntityEntry(
            entity_id="sensor.enyaq_battery_level_last_updated", domain="sensor"
        )
        classed = FakeEntityEntry(
            entity_id="sensor.enyaq_soc",
            domain="sensor",
            original_device_class="battery",
        )
        assert _run_match([keyword, classed])["battery_level_entity"] == (
            classed.entity_id
        )

    def test_no_battery_key_when_device_has_none(self):
        """A device with no battery-ish sensor yields no battery suggestion."""
        other = FakeEntityEntry(entity_id="sensor.enyaq_odometer", domain="sensor")
        assert "battery_level_entity" not in _run_match([other])


# ---------------------------------------------------------------------------
# Actual SOC wins over target/goal SOC
# ---------------------------------------------------------------------------


class TestActualSocWinsOverTarget:
    """The actual SOC entity must be selected, never the target/goal SOC."""

    def test_actual_wins_when_target_appears_first(self):
        """Target entity iterated before actual -- actual must still win."""
        target = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_mal_batteriprocent",
            domain="sensor",
            unique_id="VIN123_target_battery_percentage",
            original_device_class="battery",
        )
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_batteriprocent",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            original_device_class="battery",
        )
        assert _run_match([target, actual])["battery_level_entity"] == actual.entity_id

    def test_actual_wins_when_target_appears_last(self):
        """Target entity iterated after actual -- actual must not be overwritten."""
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_batteriprocent",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            original_device_class="battery",
        )
        target = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_mal_batteriprocent",
            domain="sensor",
            unique_id="VIN123_target_battery_percentage",
            original_device_class="battery",
        )
        assert _run_match([actual, target])["battery_level_entity"] == actual.entity_id

    def test_excludes_target_by_unique_id_even_with_english_entity_id(self):
        """unique_id containing 'target' is excluded even if entity_id doesn't."""
        target = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_target_battery_percentage",
            domain="sensor",
            unique_id="VIN123_target_battery_percentage",
            original_device_class="battery",
        )
        actual = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_battery_percentage",
            domain="sensor",
            unique_id="VIN123_battery_percentage",
            original_device_class="battery",
        )
        assert _run_match([target, actual])["battery_level_entity"] == actual.entity_id

    def test_no_battery_key_when_only_target_soc_exists(self):
        """If only the target/goal SOC entity exists, nothing is suggested."""
        target = FakeEntityEntry(
            entity_id="sensor.skoda_enyaq_mal_batteriprocent",
            domain="sensor",
            unique_id="VIN123_target_battery_percentage",
            original_device_class="battery",
        )
        assert "battery_level_entity" not in _run_match([target])


# ---------------------------------------------------------------------------
# Charger connected / location
# ---------------------------------------------------------------------------


class TestChargerConnectedAndLocation:
    """Plug and location entities are matched by device_class, then keywords."""

    def test_prefers_plug_device_class(self):
        """A device_class plug binary sensor is the charger-connected signal."""
        plug = FakeEntityEntry(
            entity_id="binary_sensor.enyaq_kabel",
            domain="binary_sensor",
            original_device_class="plug",
        )
        assert _run_match([plug])["charger_connected_entity"] == plug.entity_id

    def test_accepts_battery_charging_device_class(self):
        """Integrations that expose the cable as battery_charging also match."""
        charging = FakeEntityEntry(
            entity_id="binary_sensor.enyaq_laddar",
            domain="binary_sensor",
            original_device_class="battery_charging",
        )
        assert _run_match([charging])["charger_connected_entity"] == charging.entity_id

    def test_falls_back_to_keywords_when_no_device_class(self):
        """Unclassed binary sensors match on the usual naming."""
        plug = FakeEntityEntry(
            entity_id="binary_sensor.enyaq_charger_connected", domain="binary_sensor"
        )
        assert _run_match([plug])["charger_connected_entity"] == plug.entity_id

    def test_ignores_unrelated_binary_sensors(self):
        """A door sensor on the car device must not be taken for the cable."""
        door = FakeEntityEntry(
            entity_id="binary_sensor.enyaq_doors",
            domain="binary_sensor",
            original_device_class="door",
        )
        assert "charger_connected_entity" not in _run_match([door])

    def test_picks_the_device_tracker_as_location(self):
        """Any device_tracker on the car device is the location entity."""
        tracker = FakeEntityEntry(
            entity_id="device_tracker.enyaq_position", domain="device_tracker"
        )
        assert _run_match([tracker])["location_entity"] == tracker.entity_id


# ---------------------------------------------------------------------------
# Car name
# ---------------------------------------------------------------------------


class TestCarName:
    """Suggested car name comes from the device registry."""

    def test_uses_device_name(self):
        """Device name is the suggestion."""
        devices = {"device_1": FakeDeviceEntry(name="Skoda Enyaq")}
        assert _run_match([], devices=devices)["car_name"] == "Skoda Enyaq"

    def test_prefers_name_by_user_over_name(self):
        """User-assigned device name takes priority over the default device name."""
        devices = {
            "device_1": FakeDeviceEntry(name="Skoda Enyaq", name_by_user="My Car")
        }
        assert _run_match([], devices=devices)["car_name"] == "My Car"

    def test_unknown_device_yields_empty_match(self):
        """A device_id with no registry entry suggests nothing."""
        assert _run_match([], devices={}) == {}

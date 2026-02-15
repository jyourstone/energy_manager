"""Sensor platform for the Energy Manager integration.

Provides a price sensor entity that exposes current electricity price
as state and today's/tomorrow's hourly price slots as attributes.
Data is sourced from the PriceCoordinator.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EnergyManagerConfigEntry, PriceData
from .entity import EnergyManagerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyManagerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Energy Manager sensor entities from a config entry.

    Creates the price sensor that exposes PriceCoordinator data to the UI.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    price_coordinator = entry.runtime_data.price_coordinator
    async_add_entities([EnergyManagerPriceSensor(price_coordinator, entry)])


class EnergyManagerPriceSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing current electricity price with hourly price attributes.

    State is the current electricity price in SEK/kWh. Extra attributes
    contain today's and tomorrow's hourly price slots for use by automations,
    dashboards, and downstream scheduling modules.
    """

    _attr_translation_key = "electricity_price"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the price sensor.

        Args:
            coordinator: The PriceCoordinator providing price data.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_electricity_price"

    @property
    def native_value(self) -> float | None:
        """Return the current electricity price."""
        data: PriceData | None = self.coordinator.data
        if data is None:
            return None
        return data.current_price

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return today's and tomorrow's hourly price slots as attributes."""
        data: PriceData | None = self.coordinator.data
        if data is None:
            return {"today": [], "tomorrow": [], "last_updated": None}

        today = [
            {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "price": slot.price,
            }
            for slot in data.today
        ]

        tomorrow = [
            {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "price": slot.price,
            }
            for slot in data.tomorrow
        ]

        return {
            "today": today,
            "tomorrow": tomorrow,
            "last_updated": data.last_updated.isoformat() if data.last_updated else None,
        }

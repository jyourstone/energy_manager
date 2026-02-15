"""Sensor platform for the Energy Manager integration.

Provides a price sensor entity that exposes current electricity price
as state. Downstream modules access full price slot data directly from
the PriceCoordinator via entry.runtime_data.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
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
    """Sensor showing current electricity price.

    State is the current electricity price in SEK/kWh. Full hourly price
    slot data is available to downstream modules via the PriceCoordinator.
    """

    _attr_translation_key = "electricity_price"
    _attr_device_class = SensorDeviceClass.MONETARY
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
        """Return lightweight metadata attributes.

        Full hourly price slot data is accessed by downstream modules
        directly via the PriceCoordinator (entry.runtime_data).
        """
        data: PriceData | None = self.coordinator.data
        if data is None:
            return {"last_updated": None}

        return {
            "last_updated": data.last_updated.isoformat() if data.last_updated else None,
        }

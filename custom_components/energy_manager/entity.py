"""Base entity for the Energy Manager integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import CONF_CAR_NAME, DOMAIN

if TYPE_CHECKING:
    from .coordinator import CarChargingCoordinator


class EnergyManagerEntity(CoordinatorEntity):
    """Base entity for all Energy Manager module entities.

    Provides shared device_info for the hub device and entity naming.
    Subclassed by module-specific entities (battery, EV, car).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the base entity.

        Args:
            coordinator: The data update coordinator for this entity.
            entry: The config entry this entity belongs to.
        """
        super().__init__(coordinator)
        self._entry_id = entry.entry_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the Energy Manager hub device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Energy Manager",
            manufacturer="Energy Manager",
            model="Hub",
            entry_type=DeviceEntryType.SERVICE,
        )


class CarEntity(CoordinatorEntity):
    """Base entity for per-car entities with car-specific device.

    Each car appears as a separate device in HA, linked to the hub device
    via via_device. Uses subentry_id as the device identifier for uniqueness.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarChargingCoordinator,
        entry: ConfigEntry,
        subentry,
    ) -> None:
        """Initialize the car entity.

        Args:
            coordinator: The CarChargingCoordinator for this car.
            entry: The config entry this entity belongs to.
            subentry: The car subentry with car-specific configuration.
        """
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._subentry_id = subentry.subentry_id
        self._car_name = subentry.data.get(CONF_CAR_NAME, "Unknown Car")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the car-specific device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._subentry_id)},
            name=self._car_name,
            manufacturer="Energy Manager",
            model="Car",
            via_device=(DOMAIN, self._entry_id),
        )

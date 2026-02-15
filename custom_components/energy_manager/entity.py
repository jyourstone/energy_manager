"""Base entity for the Energy Manager integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN


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

"""Time platform for the Energy Manager integration.

Provides per-car departure time entities using TimeEntity with RestoreEntity
for persistence across restarts. Changing the departure time triggers
schedule recalculation on the associated car coordinator.
"""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import EnergyManagerConfigEntry
from .entity import CarEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyManagerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up car departure time entities from config entry.

    Creates one CarDepartureTime entity per car subentry.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    for subentry_id, coordinator in entry.runtime_data.car_coordinators.items():
        subentry = entry.subentries[subentry_id]
        async_add_entities(
            [CarDepartureTime(coordinator, entry, subentry)],
            config_subentry_id=subentry_id,
        )


class CarDepartureTime(CarEntity, TimeEntity, RestoreEntity):
    """Time entity for per-car departure time.

    Allows users to set when their car should be ready. Value persists
    across restarts via RestoreEntity. Changes trigger schedule
    recalculation on the car's coordinator.
    """

    _attr_translation_key = "departure_time"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
        subentry,
    ) -> None:
        """Initialize the departure time entity.

        Args:
            coordinator: The CarChargingCoordinator for this car.
            entry: The config entry this entity belongs to.
            subentry: The car subentry with car-specific configuration.
        """
        super().__init__(coordinator, entry, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_departure_time"
        self._attr_native_value = time(7, 0)

    async def async_added_to_hass(self) -> None:
        """Restore previous departure time on startup, or use default.

        Uses RestoreEntity's async_get_last_state to recover the
        previously set departure time across HA restarts.
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                self._attr_native_value = time.fromisoformat(last_state.state)
            except (ValueError, TypeError):
                pass  # Keep default 07:00

        # Sync initial value to coordinator
        self.coordinator.departure_time = self._attr_native_value

    async def async_set_value(self, value: time) -> None:
        """Update the departure time and trigger schedule recalculation.

        Args:
            value: New departure time.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.departure_time = value
        await self.coordinator.async_request_refresh()

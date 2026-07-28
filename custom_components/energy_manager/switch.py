"""Switch platform for the Energy Manager integration.

Provides the master "Device control" switch (CORE-14): the single choke
point that gates ALL outgoing device commands sent by any coordinator.
Defaults to OFF (observe-only) -- coordinators still compute and publish
every decision, but no hass.services.async_call is made until this switch
is turned on. Restores its last state across restarts via RestoreEntity,
but fails safe to OFF whenever no previous state can be recovered.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import EnergyManagerConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyManagerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Energy Manager device control switch from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    async_add_entities([DeviceControlSwitch(entry)])


class DeviceControlSwitch(SwitchEntity, RestoreEntity):
    """Master switch gating all outgoing device commands (CORE-14).

    OFF (observe-only, the default) means every coordinator still computes
    and publishes its decisions, but suppresses the actual device command at
    the single choke point in EMSCoordinator. Not tied to any coordinator --
    the switch must remain controllable even if other coordinators fail.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "control_enabled"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, entry: EnergyManagerConfigEntry) -> None:
        """Initialize the device control switch.

        Args:
            entry: The config entry this switch belongs to.
        """
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_control_enabled"
        self._attr_is_on = False

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the Energy Manager hub device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Energy Manager",
            manufacturer="Energy Manager",
            model="Hub",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Restore previous state on startup, or default to OFF (observe-only).

        Fail-safe: no previous state, an unknown/unavailable last state, or
        anything unexpected all resolve to OFF -- control is only ever
        enabled by an explicit prior "on" state.
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        self._attr_is_on = last_state is not None and last_state.state == "on"
        self._entry.runtime_data.control_enabled = self._attr_is_on

    async def async_turn_on(self, **kwargs) -> None:
        """Enable device control (leave observe-only mode)."""
        self._attr_is_on = True
        self._entry.runtime_data.control_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable device control (return to observe-only mode)."""
        self._attr_is_on = False
        self._entry.runtime_data.control_enabled = False
        self.async_write_ha_state()

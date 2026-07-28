"""Switch platform for the Energy Manager integration.

Provides the master "Device control" switch (CORE-14): the single choke
point that gates ALL outgoing device commands sent by any coordinator.
Defaults to OFF (observe-only) -- coordinators still compute and publish
every decision, but no hass.services.async_call is made until this switch
is turned on. Restores its last state across restarts via RestoreEntity,
but fails safe to OFF whenever no previous state can be recovered.

Also provides the "Force grid charging" switch (EASE-03), which replaces
the legacy input_boolean.easee_force_charging -- when ON, the Easee charger
controller's mode arbitration treats forced charging as the highest
priority (see charger_state_machine.ChargerController.decide()).
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
    """Set up the Energy Manager switch entities from a config entry.

    The "Force grid charging" switch (EASE-03) is only created when the
    Easee charger coordinator exists.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    entities: list[SwitchEntity] = [DeviceControlSwitch(entry)]
    if entry.runtime_data.easee_coordinator is not None:
        entities.append(ForceChargingSwitch(entry))
    async_add_entities(entities)


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
        await self._async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable device control (return to observe-only mode)."""
        self._attr_is_on = False
        self._entry.runtime_data.control_enabled = False
        self.async_write_ha_state()
        await self._async_request_refresh()

    async def _async_request_refresh(self) -> None:
        """Request an immediate re-evaluation from both coordinators.

        So flipping observe-only on/off takes effect immediately instead of
        waiting for the next poll cycle.
        """
        runtime_data = self._entry.runtime_data
        if runtime_data.ems_coordinator is not None:
            await runtime_data.ems_coordinator.async_request_refresh()
        if runtime_data.easee_coordinator is not None:
            await runtime_data.easee_coordinator.async_request_refresh()


class ForceChargingSwitch(SwitchEntity, RestoreEntity):
    """Force-grid-charging switch (EASE-03), replaces input_boolean.easee_force_charging.

    OFF by default and restores its previous state across restarts. When ON,
    the Easee charger controller's mode arbitration treats "forced" as the
    highest priority (see charger_state_machine.ChargerController.decide()),
    starting the charger at the grid amp target regardless of the car's
    schedule or solar state.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "force_charging"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, entry: EnergyManagerConfigEntry) -> None:
        """Initialize the force-charging switch.

        Args:
            entry: The config entry this switch belongs to.
        """
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_force_charging"
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
        """Restore previous state on startup, or default to OFF.

        Fail-safe: no previous state, an unknown/unavailable last state, or
        anything unexpected all resolve to OFF.
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        self._attr_is_on = last_state is not None and last_state.state == "on"
        self._entry.runtime_data.force_charging = self._attr_is_on

    async def async_turn_on(self, **kwargs) -> None:
        """Enable forced grid charging."""
        self._attr_is_on = True
        self._entry.runtime_data.force_charging = True
        self.async_write_ha_state()
        await self._async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable forced grid charging."""
        self._attr_is_on = False
        self._entry.runtime_data.force_charging = False
        self.async_write_ha_state()
        await self._async_request_refresh()

    async def _async_request_refresh(self) -> None:
        """Request an immediate charger re-evaluation so the toggle takes effect now."""
        easee_coordinator = self._entry.runtime_data.easee_coordinator
        if easee_coordinator is not None:
            await easee_coordinator.async_request_refresh()

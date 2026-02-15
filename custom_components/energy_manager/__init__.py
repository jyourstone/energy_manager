"""The Energy Manager integration.

Manages the full setup/unload/reload lifecycle, hub device registration,
and typed runtime data. Creates the PriceCoordinator that provides the
price data foundation for all downstream scheduling modules.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_BATTERY_ENABLED,
    CONF_EV_ENABLED,
    CONFIG_VERSION,
    DOMAIN,
    MODULE_BATTERY,
    MODULE_EV,
)
from .coordinator import (
    EnergyManagerConfigEntry,
    EnergyManagerData,
    PriceCoordinator,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: EnergyManagerConfigEntry
) -> bool:
    """Set up Energy Manager from a config entry.

    Creates the PriceCoordinator, stores typed runtime data, registers
    the hub device, and forwards platforms for enabled modules.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.

    Returns:
        True if setup was successful.

    Raises:
        ConfigEntryNotReady: If Nordpool sensor is unavailable at startup
            (raised automatically by async_config_entry_first_refresh via
            UpdateFailed).
    """
    # Create and initialize the price coordinator
    price_coordinator = PriceCoordinator(hass, entry)
    await price_coordinator.async_config_entry_first_refresh()

    # Store typed runtime data on the config entry
    entry.runtime_data = EnergyManagerData(
        price_coordinator=price_coordinator,
        modules_enabled={
            MODULE_BATTERY: entry.options.get(CONF_BATTERY_ENABLED, False),
            MODULE_EV: entry.options.get(CONF_EV_ENABLED, False),
        },
    )

    # Register hub device in device registry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Energy Manager",
        manufacturer="Energy Manager",
        model="Hub",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    # Forward platforms for enabled modules
    platforms = _get_enabled_platforms(entry)
    if platforms:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)

    return True


def _get_enabled_platforms(entry: EnergyManagerConfigEntry) -> list[Platform]:
    """Build list of platforms to set up based on enabled modules.

    Phase 1 returns an empty list (no entity platforms yet).
    Future phases extend this as modules add entities.

    Args:
        entry: The config entry to check for enabled modules.

    Returns:
        List of Platform enums to forward.
    """
    platforms: list[Platform] = []

    if entry.options.get(CONF_BATTERY_ENABLED):
        pass  # Future: platforms.extend([Platform.SENSOR, Platform.SWITCH, ...])

    if entry.options.get(CONF_EV_ENABLED):
        pass  # Future: platforms.extend([Platform.SENSOR, Platform.SWITCH, ...])

    return platforms


async def async_unload_entry(
    hass: HomeAssistant, entry: EnergyManagerConfigEntry
) -> bool:
    """Unload an Energy Manager config entry.

    Unloads forwarded platforms. Coordinator listeners are auto-cleaned
    via entry.async_on_unload() registered during _async_setup.
    Runtime data is garbage collected.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        True if unload was successful.
    """
    platforms = _get_enabled_platforms(entry)
    if platforms:
        return await hass.config_entries.async_unload_platforms(entry, platforms)
    return True


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: EnergyManagerConfigEntry
) -> bool:
    """Migrate an Energy Manager config entry to a new version.

    Currently no migrations are needed (v1). The hook exists for
    future config version changes.

    Args:
        hass: Home Assistant instance.
        config_entry: The config entry to migrate.

    Returns:
        True if migration was successful, False if downgrade not possible.
    """
    _LOGGER.debug(
        "Migrating from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    if config_entry.version > CONFIG_VERSION:
        _LOGGER.error(
            "Cannot downgrade from version %s to %s",
            config_entry.version,
            CONFIG_VERSION,
        )
        return False

    return True

"""The Energy Manager integration.

Manages the full setup/unload/reload lifecycle, hub device registration,
and typed runtime data. Creates the PriceCoordinator for price data and
conditionally creates the BatteryScheduleCoordinator when the battery
module is enabled.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BATTERY_ENABLED,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_EV_ENABLED,
    CONFIG_VERSION,
    CONSUMPTION_STORAGE_VERSION,
    DOMAIN,
    FORECAST_ACCURACY_STORAGE_VERSION,
    MODULE_BATTERY,
    MODULE_EV,
    SUBENTRY_TYPE_CAR,
)
from .coordinator import (
    BatteryScheduleCoordinator,
    CarChargingCoordinator,
    EaseeCoordinator,
    EMSCoordinator,
    EnergyManagerConfigEntry,
    EnergyManagerData,
    PriceCoordinator,
    consumption_storage_key,
    forecast_accuracy_storage_key,
)
from .repairs import ALL_ISSUE_IDS, async_clear_issue

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

    # Phase 2: Battery schedule coordinator (if battery module enabled)
    battery_coordinator = None
    if entry.options.get(CONF_BATTERY_ENABLED):
        battery_coordinator = BatteryScheduleCoordinator(
            hass, entry, price_coordinator
        )
        await battery_coordinator.async_config_entry_first_refresh()

    # Phase 3: EMS coordinator (if battery module enabled and battery coordinator exists)
    ems_coordinator = None
    if battery_coordinator is not None:
        ems_coordinator = EMSCoordinator(hass, entry, battery_coordinator)
        await ems_coordinator.async_config_entry_first_refresh()

    # Phase 4: Car charging coordinators (one per car subentry, if EV module enabled)
    car_coordinators: dict[str, CarChargingCoordinator] = {}
    if entry.options.get(CONF_EV_ENABLED):
        for subentry_id, subentry in entry.subentries.items():
            if subentry.subentry_type == SUBENTRY_TYPE_CAR:
                car_coordinator = CarChargingCoordinator(
                    hass, entry, subentry, price_coordinator
                )
                await car_coordinator.async_config_entry_first_refresh()
                car_coordinators[subentry_id] = car_coordinator

    # Phase 5: Easee charger coordinator (EV module enabled AND a charger
    # status entity configured)
    easee_coordinator: EaseeCoordinator | None = None
    if entry.options.get(CONF_EV_ENABLED) and entry.options.get(
        CONF_CHARGER_STATUS_ENTITY
    ):
        easee_coordinator = EaseeCoordinator(hass, entry)
        await easee_coordinator.async_config_entry_first_refresh()

    # Store typed runtime data on the config entry
    entry.runtime_data = EnergyManagerData(
        price_coordinator=price_coordinator,
        battery_coordinator=battery_coordinator,
        ems_coordinator=ems_coordinator,
        car_coordinators=car_coordinators,
        easee_coordinator=easee_coordinator,
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

    # Forward platforms for enabled modules. Remember exactly which were
    # forwarded -- at unload time entry.options may already hold NEW values
    # (options are persisted before update listeners fire), so recomputing
    # from options there could unload platforms that were never set up.
    platforms = _get_enabled_platforms(entry)
    entry.runtime_data.forwarded_platforms = [p.value for p in platforms]
    if platforms:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)

    # Reload the entry whenever it (or a subentry, e.g. adding/editing/
    # removing a car) is updated -- otherwise newly added subentries create
    # no entities until HA is restarted. This listener is the SINGLE reload
    # mechanism: it also covers options saves. The options flow must remain
    # plain OptionsFlow (config_flow.py) -- HA raises ValueError on options
    # save when OptionsFlowWithReload coexists with an update listener.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: EnergyManagerConfigEntry
) -> None:
    """Reload the config entry when it or one of its subentries is updated."""
    await hass.config_entries.async_reload(entry.entry_id)


def _get_enabled_platforms(entry: EnergyManagerConfigEntry) -> list[Platform]:
    """Build list of platforms to set up based on enabled modules.

    Platform.SENSOR and Platform.SWITCH are always included -- the core
    price sensor entity and the master "Device control" switch (CORE-14)
    are present regardless of which modules are enabled. Module-specific
    platforms are added conditionally as modules are enabled.

    Args:
        entry: The config entry to check for enabled modules.

    Returns:
        List of Platform enums to forward.
    """
    # Core price sensor and master control switch are always present
    platforms: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

    if entry.options.get(CONF_BATTERY_ENABLED):
        platforms.append(Platform.NUMBER)

    if entry.options.get(CONF_EV_ENABLED):
        if Platform.NUMBER not in platforms:
            platforms.append(Platform.NUMBER)
        platforms.append(Platform.TIME)

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
    # Repairs issues are re-detected every update cycle -- clear them all
    # on unload so nothing stale survives a reload or reconfiguration.
    for issue_id in ALL_ISSUE_IDS:
        async_clear_issue(hass, issue_id)

    # Flush the pending delayed consumption save so it cannot fire after
    # async_remove_entry deletes the file, or after a reload's fresh
    # coordinator has already loaded the store.
    battery_coordinator = getattr(entry.runtime_data, "battery_coordinator", None)
    if battery_coordinator is not None:
        await battery_coordinator.async_flush_consumption_store()

    forwarded = getattr(entry.runtime_data, "forwarded_platforms", None)
    if forwarded is not None:
        platforms = [Platform(p) for p in forwarded]
    else:
        platforms = _get_enabled_platforms(entry)
    if platforms:
        return await hass.config_entries.async_unload_platforms(entry, platforms)
    return True


async def async_remove_entry(
    hass: HomeAssistant, entry: EnergyManagerConfigEntry
) -> None:
    """Delete this entry's persisted Store files when the entry is removed.

    Keeps entry removal (e.g. HACS uninstall) from orphaning .storage
    files. One (version, key) pair per Store the integration creates --
    a future second Store is a one-line append here.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being removed.
    """
    for version, key in (
        (CONSUMPTION_STORAGE_VERSION, consumption_storage_key(entry.entry_id)),
        (
            FORECAST_ACCURACY_STORAGE_VERSION,
            forecast_accuracy_storage_key(entry.entry_id),
        ),
    ):
        await Store(hass, version, key).async_remove()


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

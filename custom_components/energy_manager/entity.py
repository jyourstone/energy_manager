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
from .nordpool_adapter import DEFAULT_PRICE_UNIT

if TYPE_CHECKING:
    from .coordinator import ApplianceCoordinator, CarChargingCoordinator


# HA deprecated DeviceInfo["via_device"] in 2026.8 in favor of via_device_id
# (removal planned for 2027.8, see the device-registry single-config-entry
# migration blog post). via_device_id is only a valid DeviceInfo key on
# HA >= 2026.8 -- older HA's DeviceInfo TypedDict rejects unknown keys -- so
# support is feature-detected via DeviceInfo's TypedDict metadata rather than
# an HA version check. Drop this gate once the integration's minimum
# supported HA version (see hacs.json) reaches 2026.8.
def _supports_via_device_id(device_info_cls: type) -> bool:
    """Return True if this HA's DeviceInfo accepts the via_device_id key."""
    return "via_device_id" in getattr(device_info_cls, "__optional_keys__", ())


_VIA_DEVICE_ID_SUPPORTED = _supports_via_device_id(DeviceInfo)


def get_price_unit(entry: ConfigEntry) -> str:
    """Return the active price unit (e.g. "SEK/kWh") for price-valued entities.

    Read from the price coordinator's last update, which derives it from
    the configured Nordpool sensor -- so NOK/DKK/EUR areas show their own
    currency. Falls back to SEK/kWh before the first price refresh.

    Args:
        entry: The integration's config entry.

    Returns:
        A "<CURRENCY>/kWh" unit string.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    price_coordinator = getattr(runtime_data, "price_coordinator", None)
    data = getattr(price_coordinator, "data", None)
    return getattr(data, "price_unit", None) or DEFAULT_PRICE_UNIT


class PriceUnitEntity:
    """Mixin: dynamic price unit from the configured Nordpool sensor.

    Replaces hardcoded SEK/kWh units on price-valued sensor and number
    entities. Mix in BEFORE the platform entity base class so this
    property wins over the _attr_-based default implementation.
    """

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the price unit derived from the Nordpool sensor."""
        return get_price_unit(self.coordinator.config_entry)  # type: ignore[attr-defined]


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
    via via_device_id (via_device on HA < 2026.8). Uses subentry_id as the
    device identifier for uniqueness.
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
        self._hub_device_id = getattr(
            getattr(entry, "runtime_data", None), "hub_device_id", None
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the car-specific device."""
        if _VIA_DEVICE_ID_SUPPORTED and self._hub_device_id:
            return DeviceInfo(
                identifiers={(DOMAIN, self._subentry_id)},
                name=self._car_name,
                manufacturer="Energy Manager",
                model="Car",
                via_device_id=self._hub_device_id,
            )
        return DeviceInfo(
            identifiers={(DOMAIN, self._subentry_id)},
            name=self._car_name,
            manufacturer="Energy Manager",
            model="Car",
            via_device=(DOMAIN, self._entry_id),
        )


class ApplianceEntity(CoordinatorEntity):
    """Base entity for per-appliance entities with appliance-specific device.

    Each appliance appears as a separate device in HA, linked to the hub
    device via via_device_id (via_device on HA < 2026.8). Uses subentry_id
    as the device identifier for uniqueness.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ApplianceCoordinator,
        entry: ConfigEntry,
        subentry,
    ) -> None:
        """Initialize the appliance entity.

        Args:
            coordinator: The ApplianceCoordinator shared by all appliances.
            entry: The config entry this entity belongs to.
            subentry: The appliance subentry with appliance-specific
                configuration.
        """
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._subentry_id = subentry.subentry_id
        self._appliance_name = subentry.title
        self._hub_device_id = getattr(
            getattr(entry, "runtime_data", None), "hub_device_id", None
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the appliance-specific device."""
        if _VIA_DEVICE_ID_SUPPORTED and self._hub_device_id:
            return DeviceInfo(
                identifiers={(DOMAIN, self._subentry_id)},
                name=self._appliance_name,
                manufacturer="Energy Manager",
                model="Appliance",
                via_device_id=self._hub_device_id,
            )
        return DeviceInfo(
            identifiers={(DOMAIN, self._subentry_id)},
            name=self._appliance_name,
            manufacturer="Energy Manager",
            model="Appliance",
            via_device=(DOMAIN, self._entry_id),
        )

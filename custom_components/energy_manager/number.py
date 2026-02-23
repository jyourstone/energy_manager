"""Number platform for the Energy Manager integration.

Provides user-adjustable threshold entities for battery scheduling:
- Charge price threshold (SEK/kWh)
- Discharge price threshold (SEK/kWh)
- Max charging power (kW)

When the EV module is enabled, also provides per-car number entities:
- Target SOC (%)
- Max charge power (kW)

Values persist across restarts via RestoreNumber. Each entity updates
its corresponding attribute on the appropriate coordinator and
triggers a schedule recalculation on value change.
"""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CAR_CHARGE_POWER_STEP_KW,
    CHARGE_POWER_STEP_KW,
    DEFAULT_CAR_MAX_CHARGE_POWER_KW,
    DEFAULT_CHARGE_THRESHOLD,
    DEFAULT_DISCHARGE_THRESHOLD,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DEFAULT_TARGET_SOC_PCT,
    MAX_CAR_MAX_CHARGE_POWER_KW,
    MAX_CHARGE_POWER_KW,
    MAX_PRICE_THRESHOLD,
    MAX_TARGET_SOC_PCT,
    MIN_CAR_MAX_CHARGE_POWER_KW,
    MIN_CHARGE_POWER_KW,
    MIN_PRICE_THRESHOLD,
    MIN_TARGET_SOC_PCT,
    PRICE_THRESHOLD_STEP,
    TARGET_SOC_STEP_PCT,
)
from .coordinator import BatteryScheduleCoordinator, EnergyManagerConfigEntry
from .entity import CarEntity, EnergyManagerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyManagerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Energy Manager number entities from a config entry.

    Creates battery threshold entities if the battery module is enabled,
    and per-car number entities (target SOC, max charge power) for each
    car subentry when the EV module is enabled.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: Callback to add entities to HA.
    """
    # Battery threshold entities (when battery module enabled)
    battery_coordinator = entry.runtime_data.battery_coordinator
    if battery_coordinator is not None:
        async_add_entities([
            BatteryChargeThreshold(battery_coordinator, entry),
            BatteryDischargeThreshold(battery_coordinator, entry),
            BatteryMaxChargePower(battery_coordinator, entry),
        ])

    # Car number entities (one set per car subentry)
    for subentry_id, coordinator in entry.runtime_data.car_coordinators.items():
        subentry = entry.subentries[subentry_id]
        async_add_entities(
            [
                CarTargetSOC(coordinator, entry, subentry),
                CarMaxChargePower(coordinator, entry, subentry),
            ],
            config_subentry_id=subentry_id,
        )


class BatteryChargeThreshold(EnergyManagerEntity, RestoreNumber):
    """Number entity for the battery charge price threshold.

    When the electricity price is at or below this value, the battery
    will charge from the grid. Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "charge_price_threshold"
    _attr_native_min_value = MIN_PRICE_THRESHOLD
    _attr_native_max_value = MAX_PRICE_THRESHOLD
    _attr_native_step = PRICE_THRESHOLD_STEP
    _attr_native_unit_of_measurement = "SEK/kWh"

    _default_value = DEFAULT_CHARGE_THRESHOLD

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the charge threshold entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charge_price_threshold"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._default_value
        self.coordinator.charge_threshold = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the charge threshold and trigger schedule recalculation.

        Args:
            value: New charge price threshold in SEK/kWh.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.charge_threshold = value
        await self.coordinator.async_request_refresh()


class BatteryDischargeThreshold(EnergyManagerEntity, RestoreNumber):
    """Number entity for the battery discharge price threshold.

    When the electricity price is at or above this value, the battery
    will discharge to the home. Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "discharge_price_threshold"
    _attr_native_min_value = MIN_PRICE_THRESHOLD
    _attr_native_max_value = MAX_PRICE_THRESHOLD
    _attr_native_step = PRICE_THRESHOLD_STEP
    _attr_native_unit_of_measurement = "SEK/kWh"

    _default_value = DEFAULT_DISCHARGE_THRESHOLD

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the discharge threshold entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_discharge_price_threshold"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._default_value
        self.coordinator.discharge_threshold = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the discharge threshold and trigger schedule recalculation.

        Args:
            value: New discharge price threshold in SEK/kWh.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.discharge_threshold = value
        await self.coordinator.async_request_refresh()


class BatteryMaxChargePower(EnergyManagerEntity, RestoreNumber):
    """Number entity for the maximum battery charging power.

    Controls the maximum power used for grid charging. Value persists
    across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "max_charge_power"
    _attr_native_min_value = MIN_CHARGE_POWER_KW
    _attr_native_max_value = MAX_CHARGE_POWER_KW
    _attr_native_step = CHARGE_POWER_STEP_KW
    _attr_native_unit_of_measurement = "kW"

    _default_value = DEFAULT_MAX_CHARGE_POWER_KW

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the max charge power entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_max_charge_power"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._default_value
        self.coordinator.max_charge_power_w = self._attr_native_value * 1000

    async def async_set_native_value(self, value: float) -> None:
        """Update the max charge power and trigger schedule recalculation.

        Args:
            value: New maximum charging power in kW.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.max_charge_power_w = value * 1000
        await self.coordinator.async_request_refresh()


class CarTargetSOC(CarEntity, RestoreNumber):
    """Number entity for per-car target state of charge.

    Allows users to set the desired SOC at departure time. Value persists
    across restarts via RestoreNumber. Changes trigger schedule
    recalculation on the car's coordinator.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "car_target_soc"
    _attr_native_min_value = MIN_TARGET_SOC_PCT
    _attr_native_max_value = MAX_TARGET_SOC_PCT
    _attr_native_step = TARGET_SOC_STEP_PCT
    _attr_native_unit_of_measurement = "%"

    _default_value = DEFAULT_TARGET_SOC_PCT

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
        subentry,
    ) -> None:
        """Initialize the target SOC entity.

        Args:
            coordinator: The CarChargingCoordinator for this car.
            entry: The config entry this entity belongs to.
            subentry: The car subentry with car-specific configuration.
        """
        super().__init__(coordinator, entry, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_target_soc"

    async def async_added_to_hass(self) -> None:
        """Restore previous target SOC on startup, or use default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._default_value
        self.coordinator.target_soc = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the target SOC and trigger schedule recalculation.

        Args:
            value: New target state of charge percentage.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.target_soc = value
        await self.coordinator.async_request_refresh()


class CarMaxChargePower(CarEntity, RestoreNumber):
    """Number entity for per-car maximum charging power.

    Allows users to specify the car's maximum charge rate. Value persists
    across restarts via RestoreNumber. Changes trigger schedule
    recalculation on the car's coordinator.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "car_max_charge_power"
    _attr_native_min_value = MIN_CAR_MAX_CHARGE_POWER_KW
    _attr_native_max_value = MAX_CAR_MAX_CHARGE_POWER_KW
    _attr_native_step = CAR_CHARGE_POWER_STEP_KW
    _attr_native_unit_of_measurement = "kW"

    _default_value = DEFAULT_CAR_MAX_CHARGE_POWER_KW

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
        subentry,
    ) -> None:
        """Initialize the max charge power entity.

        Args:
            coordinator: The CarChargingCoordinator for this car.
            entry: The config entry this entity belongs to.
            subentry: The car subentry with car-specific configuration.
        """
        super().__init__(coordinator, entry, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_max_charge_power"

    async def async_added_to_hass(self) -> None:
        """Restore previous max charge power on startup, or use default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._default_value
        self.coordinator.max_charge_power_kw = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the max charge power and trigger schedule recalculation.

        Args:
            value: New maximum charging power in kW.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.max_charge_power_kw = value
        await self.coordinator.async_request_refresh()

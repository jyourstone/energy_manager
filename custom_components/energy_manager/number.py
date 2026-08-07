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
triggers a schedule recalculation both on value change and on restore
(so a restored non-default value takes effect immediately rather than
waiting for the next natural update).
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
    CONF_BATTERY_CYCLE_COST,
    CONF_ELECTRICITY_COMPANY_FEE,
    CONF_EXPORT_RESERVE_SOC_PCT,
    CONF_EXPORT_SPIKE_THRESHOLD,
    CONF_GRID_TRANSFER_FEE,
    CONF_MAX_CHARGE_POWER,
    DEFAULT_BATTERY_CYCLE_COST,
    DEFAULT_CAR_MAX_CHARGE_POWER_KW,
    DEFAULT_CAR_SOLAR_TARGET_SOC_PCT,
    DEFAULT_CHARGE_THRESHOLD,
    DEFAULT_DISCHARGE_THRESHOLD,
    DEFAULT_ELECTRICITY_COMPANY_FEE,
    DEFAULT_EXPORT_RESERVE_SOC_PCT,
    DEFAULT_GRID_TRANSFER_FEE,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DEFAULT_MAX_SOC_PCT,
    DEFAULT_TARGET_SOC_PCT,
    MAX_CAR_MAX_CHARGE_POWER_KW,
    MAX_CHARGE_POWER_KW,
    MAX_EXPORT_SPIKE_THRESHOLD,
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
from .entity import CarEntity, EnergyManagerEntity, PriceUnitEntity


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
            BatteryCycleCost(battery_coordinator, entry),
            GridTransferFee(battery_coordinator, entry),
            ElectricityCompanyFee(battery_coordinator, entry),
            ExportSpikeThreshold(battery_coordinator, entry),
            ExportReserveSoc(battery_coordinator, entry),
            BatteryMaxSocTarget(battery_coordinator, entry),
        ])

    # Car number entities (one set per car subentry)
    for subentry_id, coordinator in entry.runtime_data.car_coordinators.items():
        subentry = entry.subentries[subentry_id]
        async_add_entities(
            [
                CarTargetSOC(coordinator, entry, subentry),
                CarSolarTargetSOC(coordinator, entry, subentry),
                CarMaxChargePower(coordinator, entry, subentry),
            ],
            config_subentry_id=subentry_id,
        )


class BatteryChargeThreshold(PriceUnitEntity, EnergyManagerEntity, RestoreNumber):
    """Number entity for the battery charge price threshold.

    When the electricity price is at or below the peak's max price minus
    this spread, the battery will charge from the grid. Value persists
    across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_charge_spread_threshold"
    _attr_native_min_value = MIN_PRICE_THRESHOLD
    _attr_native_max_value = MAX_PRICE_THRESHOLD
    _attr_native_step = PRICE_THRESHOLD_STEP

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
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the charge threshold and trigger schedule recalculation.

        Args:
            value: New charge price threshold in SEK/kWh.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.charge_threshold = value
        await self.coordinator.async_request_refresh()


class BatteryDischargeThreshold(PriceUnitEntity, EnergyManagerEntity, RestoreNumber):
    """Number entity for the battery discharge price threshold.

    Price spread above the horizon's cheapest slot at or above which the
    battery will discharge to the home. Value persists across restarts.
    Overridden (entity shown unavailable) while Battery Cycle Cost > 0,
    since the scheduler then derives the effective threshold from the
    cycle-cost formula instead of this manual value.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_discharge_spread_threshold"
    _attr_native_min_value = MIN_PRICE_THRESHOLD
    _attr_native_max_value = MAX_PRICE_THRESHOLD
    _attr_native_step = PRICE_THRESHOLD_STEP

    _default_value = DEFAULT_DISCHARGE_THRESHOLD

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the discharge threshold entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_discharge_price_threshold"

    @property
    def available(self) -> bool:
        """Unavailable while battery cycle cost overrides this threshold.

        When battery_cycle_cost > 0 the scheduler derives the discharge
        threshold from the cycle-cost formula and this manual value is
        ignored -- greying the entity out makes the override visible.
        """
        return super().available and self.coordinator.battery_cycle_cost <= 0

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._default_value
        self.coordinator.discharge_threshold = self._attr_native_value
        await self.coordinator.async_request_refresh()

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
    _attr_translation_key = "battery_max_charge_power"
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
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_max_charge_power"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._entry.options.get(
                CONF_MAX_CHARGE_POWER, self._default_value
            )
        self.coordinator.max_charge_power_w = self._attr_native_value * 1000
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the max charge power and trigger schedule recalculation.

        Args:
            value: New maximum charging power in kW.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.max_charge_power_w = value * 1000
        await self.coordinator.async_request_refresh()


class BatteryCycleCost(PriceUnitEntity, EnergyManagerEntity, RestoreNumber):
    """Number entity for the battery's per-cycle wear cost (BATT-14).

    When set above 0, this overrides the manual discharge price threshold:
    the effective discharge spread threshold becomes
    battery_cycle_cost - grid_transfer_fee (parity with the live system's
    economics formula). At the default of 0, the manual discharge threshold
    entity applies unchanged. Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_cycle_cost"
    _attr_native_min_value = MIN_PRICE_THRESHOLD
    _attr_native_max_value = MAX_PRICE_THRESHOLD
    _attr_native_step = PRICE_THRESHOLD_STEP

    _default_value = DEFAULT_BATTERY_CYCLE_COST

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the battery cycle cost entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_battery_cycle_cost"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._entry.options.get(
                CONF_BATTERY_CYCLE_COST, self._default_value
            )
        self.coordinator.battery_cycle_cost = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the battery cycle cost and trigger schedule recalculation.

        Args:
            value: New battery cycle cost in SEK/kWh.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.battery_cycle_cost = value
        await self.coordinator.async_request_refresh()


class GridTransferFee(PriceUnitEntity, EnergyManagerEntity, RestoreNumber):
    """Number entity for the grid transfer fee (BATT-14).

    Used together with battery_cycle_cost to derive the effective discharge
    spread threshold, and added into the actual electricity price sensor.
    Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "grid_transfer_fee"
    _attr_native_min_value = MIN_PRICE_THRESHOLD
    _attr_native_max_value = MAX_PRICE_THRESHOLD
    _attr_native_step = PRICE_THRESHOLD_STEP

    _default_value = DEFAULT_GRID_TRANSFER_FEE

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the grid transfer fee entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_grid_transfer_fee"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._entry.options.get(
                CONF_GRID_TRANSFER_FEE, self._default_value
            )
        self.coordinator.grid_transfer_fee = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the grid transfer fee and trigger schedule recalculation.

        Args:
            value: New grid transfer fee in SEK/kWh.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.grid_transfer_fee = value
        await self.coordinator.async_request_refresh()


class ElectricityCompanyFee(PriceUnitEntity, EnergyManagerEntity, RestoreNumber):
    """Number entity for the electricity company's fee (BATT-14).

    Used only by the actual electricity price sensor (spot + grid_transfer_fee
    + electricity_company_fee); does not affect scheduling. Value persists
    across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "electricity_company_fee"
    _attr_native_min_value = MIN_PRICE_THRESHOLD
    _attr_native_max_value = MAX_PRICE_THRESHOLD
    _attr_native_step = PRICE_THRESHOLD_STEP

    _default_value = DEFAULT_ELECTRICITY_COMPANY_FEE

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the electricity company fee entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_electricity_company_fee"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._entry.options.get(
                CONF_ELECTRICITY_COMPANY_FEE, self._default_value
            )
        self.coordinator.electricity_company_fee = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the electricity company fee and trigger a sensor refresh.

        Args:
            value: New electricity company fee in SEK/kWh.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.electricity_company_fee = value
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
            self._attr_native_value = round(last_data.native_value)
        else:
            self._attr_native_value = int(self._default_value)
        self.coordinator.target_soc = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the target SOC and trigger schedule recalculation.

        Args:
            value: New target state of charge percentage (whole percent).
        """
        self._attr_native_value = round(value)
        self.async_write_ha_state()
        self.coordinator.target_soc = self._attr_native_value
        await self.coordinator.async_request_refresh()


class CarSolarTargetSOC(CarEntity, RestoreNumber):
    """Number entity for the car's solar charging target SOC.

    Solar-mode charging stops when the car reaches this level; the regular
    charging target only governs scheduled (price-based) charging. Default
    100%.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "car_solar_target_soc"
    _attr_native_min_value = MIN_TARGET_SOC_PCT
    _attr_native_max_value = MAX_TARGET_SOC_PCT
    _attr_native_step = TARGET_SOC_STEP_PCT
    _attr_native_unit_of_measurement = "%"

    _default_value = DEFAULT_CAR_SOLAR_TARGET_SOC_PCT

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
        subentry,
    ) -> None:
        """Initialize the solar target SOC entity.

        Args:
            coordinator: The CarChargingCoordinator for this car.
            entry: The config entry this entity belongs to.
            subentry: The car subentry with car-specific configuration.
        """
        super().__init__(coordinator, entry, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_solar_target_soc"

    async def async_added_to_hass(self) -> None:
        """Restore previous solar target SOC on startup, or use default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = round(last_data.native_value)
        else:
            self._attr_native_value = int(self._default_value)
        self.coordinator.solar_target_soc = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the solar target SOC and trigger schedule recalculation.

        Args:
            value: New solar charging target state of charge percentage
                (whole percent).
        """
        self._attr_native_value = round(value)
        self.async_write_ha_state()
        self.coordinator.solar_target_soc = self._attr_native_value
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
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the max charge power and trigger schedule recalculation.

        Args:
            value: New maximum charging power in kW.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.max_charge_power_kw = value
        await self.coordinator.async_request_refresh()
class ExportSpikeThreshold(PriceUnitEntity, EnergyManagerEntity, RestoreNumber):
    """Number entity for the BATT-17 export spike threshold.

    Price spread above the period's cheapest upcoming hour at or above
    which the battery may sell to the grid. 0 disables export arbitrage
    (the default). Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_export_spread_threshold"
    _attr_native_min_value = MIN_PRICE_THRESHOLD
    _attr_native_max_value = MAX_EXPORT_SPIKE_THRESHOLD
    _attr_native_step = PRICE_THRESHOLD_STEP

    _default_value = 0.0

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the export spike threshold entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_export_spike_threshold"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_EXPORT_SPIKE_THRESHOLD, self._default_value
                )
                or self._default_value
            )
        self.coordinator.export_spike_threshold = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the export spike threshold and trigger recalculation.

        Args:
            value: New spread threshold in SEK/kWh (0 disables export).
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.export_spike_threshold = value
        await self.coordinator.async_request_refresh()


class ExportReserveSoc(EnergyManagerEntity, RestoreNumber):
    """Number entity for the BATT-17 export reserve SOC floor.

    The battery never sells to the grid at or below this state of charge.
    Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_export_reserve_soc"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 95.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"

    _default_value = DEFAULT_EXPORT_RESERVE_SOC_PCT

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the export reserve SOC entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_export_reserve_soc"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_EXPORT_RESERVE_SOC_PCT, self._default_value
                )
            )
        self.coordinator.export_reserve_soc_pct = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the export reserve SOC floor and trigger recalculation.

        Args:
            value: New reserve floor in percent SOC.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.export_reserve_soc_pct = value
        await self.coordinator.async_request_refresh()


class BatteryMaxSocTarget(EnergyManagerEntity, RestoreNumber):
    """Number entity for the battery's max SOC target for PV-opportunistic charging.

    PV-opportunistic charging stops once the battery reaches this level.
    Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_max_soc_target"
    _attr_native_min_value = 50.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"

    _default_value = DEFAULT_MAX_SOC_PCT

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the max SOC target entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery_max_soc_target"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._default_value
        self.coordinator.max_soc_pct = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the max SOC target and trigger schedule recalculation.

        Args:
            value: New maximum state of charge percentage.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.max_soc_pct = value
        await self.coordinator.async_request_refresh()

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
    CONF_APPLIANCE_OFF_SUSTAIN_MINUTES,
    CONF_APPLIANCE_OFF_THRESHOLD_PCT,
    CONF_APPLIANCE_ON_SUSTAIN_MINUTES,
    CONF_APPLIANCE_ON_THRESHOLD_PCT,
    CONF_APPLIANCE_PRIORITY,
    CONF_BATTERY_CYCLE_COST,
    CONF_BATTERY_SOC_GATE_PCT,
    CONF_CHARGE_BUFFER_PCT,
    CONF_ELECTRICITY_COMPANY_FEE,
    CONF_ESTIMATED_CHARGE_POWER_KW,
    CONF_EXPORT_RESERVE_SOC_PCT,
    CONF_EXPORT_SPIKE_THRESHOLD,
    CONF_GRID_TRANSFER_FEE,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_GRID_CHARGE_POWER_KW,
    CONF_PEAK_GAP_HOURS,
    CONF_PRODUCTION_FACTOR,
    CONF_SOLAR_START_THRESHOLD_KW,
    DEFAULT_APPLIANCE_OFF_SUSTAIN_MINUTES,
    DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT,
    DEFAULT_APPLIANCE_ON_SUSTAIN_MINUTES,
    DEFAULT_APPLIANCE_ON_THRESHOLD_PCT,
    DEFAULT_APPLIANCE_PRIORITY,
    DEFAULT_BATTERY_CYCLE_COST,
    DEFAULT_BATTERY_SOC_GATE_PCT,
    DEFAULT_CAR_MAX_CHARGE_POWER_KW,
    DEFAULT_CAR_SOLAR_TARGET_SOC_PCT,
    DEFAULT_CHARGE_BUFFER_PCT,
    DEFAULT_CHARGE_THRESHOLD,
    DEFAULT_DISCHARGE_THRESHOLD,
    DEFAULT_ELECTRICITY_COMPANY_FEE,
    DEFAULT_ESTIMATED_CHARGE_POWER_KW,
    DEFAULT_EXPORT_RESERVE_SOC_PCT,
    DEFAULT_GRID_TRANSFER_FEE,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DEFAULT_MAX_GRID_CHARGE_POWER_KW,
    DEFAULT_MAX_SOC_PCT,
    DEFAULT_PEAK_GAP_HOURS,
    DEFAULT_PRODUCTION_FACTOR,
    DEFAULT_SOLAR_START_THRESHOLD_KW,
    DEFAULT_TARGET_SOC_PCT,
    MAX_BATTERY_SOC_GATE_PCT,
    MAX_CAR_MAX_CHARGE_POWER_KW,
    MAX_CHARGE_BUFFER_PCT,
    MAX_CHARGE_POWER_KW,
    MAX_ESTIMATED_CHARGE_POWER_KW,
    MAX_EXPORT_SPIKE_THRESHOLD,
    MAX_MAX_GRID_CHARGE_POWER_KW,
    MAX_PEAK_GAP_HOURS,
    MAX_PRICE_THRESHOLD,
    MAX_PRODUCTION_FACTOR,
    MAX_SOLAR_START_THRESHOLD_KW,
    MAX_TARGET_SOC_PCT,
    MIN_BATTERY_SOC_GATE_PCT,
    MIN_CAR_MAX_CHARGE_POWER_KW,
    MIN_CHARGE_BUFFER_PCT,
    MIN_CHARGE_POWER_KW,
    MIN_ESTIMATED_CHARGE_POWER_KW,
    MIN_MAX_GRID_CHARGE_POWER_KW,
    MIN_PEAK_GAP_HOURS,
    MIN_PRICE_THRESHOLD,
    MIN_PRODUCTION_FACTOR,
    MIN_SOLAR_START_THRESHOLD_KW,
    MIN_TARGET_SOC_PCT,
    PRICE_THRESHOLD_STEP,
    SUBENTRY_TYPE_APPLIANCE,
    TARGET_SOC_STEP_PCT,
)
from .coordinator import BatteryScheduleCoordinator, EnergyManagerConfigEntry
from .entity import ApplianceEntity, CarEntity, EnergyManagerEntity, PriceUnitEntity


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
            BatteryChargeBuffer(battery_coordinator, entry),
            BatteryProductionFactor(battery_coordinator, entry),
            BatteryEstimatedChargePower(battery_coordinator, entry),
            BatteryPeakGapHours(battery_coordinator, entry),
        ])

    # EV charger tuning entities (when the EV module is enabled)
    easee_coordinator = entry.runtime_data.easee_coordinator
    if easee_coordinator is not None:
        async_add_entities([
            EvMaxGridChargePower(easee_coordinator, entry),
            EvSolarStartThreshold(easee_coordinator, entry),
            EvBatterySocGate(easee_coordinator, entry),
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

    # Per-appliance tuning entities (one set per appliance subentry)
    appliance_coordinator = entry.runtime_data.appliance_coordinator
    if appliance_coordinator is not None:
        for subentry_id, subentry in entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_APPLIANCE:
                continue
            async_add_entities(
                [
                    AppliancePriority(appliance_coordinator, entry, subentry),
                    ApplianceOnThreshold(appliance_coordinator, entry, subentry),
                    ApplianceOffThreshold(appliance_coordinator, entry, subentry),
                    ApplianceOnSustain(appliance_coordinator, entry, subentry),
                    ApplianceOffSustain(appliance_coordinator, entry, subentry),
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


class ApplianceTuningNumber(ApplianceEntity, RestoreNumber):
    """Base for per-appliance tuning numbers (APPL-05 knobs).

    Restores the last value, falling back to the subentry-data seed the
    add-appliance wizard wrote. Every change is pushed onto the shared
    ApplianceCoordinator via set_appliance_tuning() and applies on the
    next 30s tick -- no config-entry reload.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False

    # Set by subclasses.
    _conf_key: str
    _tuning_field: str
    _default_value: int
    # Multiplier applied when pushing to the coordinator (minutes -> s).
    _scale: int = 1

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
        subentry,
    ) -> None:
        """Initialize the tuning entity.

        Args:
            coordinator: The ApplianceCoordinator shared by all appliances.
            entry: The config entry this entity belongs to.
            subentry: The appliance subentry with appliance-specific
                configuration (seed source).
        """
        super().__init__(coordinator, entry, subentry)
        self._seed = int(subentry.data.get(self._conf_key, self._default_value))
        self._attr_unique_id = f"{subentry.subentry_id}_{self._conf_key}"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the subentry seed."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = round(last_data.native_value)
        else:
            self._attr_native_value = self._seed
        self._push_value()
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the tuning value and apply it on the next tick.

        Args:
            value: New value in the entity's native unit (whole numbers).
        """
        self._attr_native_value = round(value)
        self.async_write_ha_state()
        self._push_value()
        await self.coordinator.async_request_refresh()

    def _push_value(self) -> None:
        self.coordinator.set_appliance_tuning(
            self._subentry_id,
            **{self._tuning_field: int(self._attr_native_value) * self._scale},
        )


class AppliancePriority(ApplianceTuningNumber):
    """Allocation priority for the surplus pool (1 = highest)."""

    _attr_translation_key = "appliance_priority"
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1
    _conf_key = CONF_APPLIANCE_PRIORITY
    _tuning_field = "priority"
    _default_value = DEFAULT_APPLIANCE_PRIORITY


class ApplianceOnThreshold(ApplianceTuningNumber):
    """Turn ON when the surplus pool reaches this share of rated power."""

    _attr_translation_key = "appliance_on_threshold"
    _attr_native_min_value = 50
    _attr_native_max_value = 300
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _conf_key = CONF_APPLIANCE_ON_THRESHOLD_PCT
    _tuning_field = "on_threshold_pct"
    _default_value = DEFAULT_APPLIANCE_ON_THRESHOLD_PCT


class ApplianceOffThreshold(ApplianceTuningNumber):
    """Turn OFF when the surplus pool drops below this share of rated power."""

    _attr_translation_key = "appliance_off_threshold"
    _attr_native_min_value = 0
    _attr_native_max_value = 150
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _conf_key = CONF_APPLIANCE_OFF_THRESHOLD_PCT
    _tuning_field = "off_threshold_pct"
    _default_value = DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT


class ApplianceOnSustain(ApplianceTuningNumber):
    """Surplus must persist this long before turning ON."""

    _attr_translation_key = "appliance_on_sustain"
    _attr_native_min_value = 0
    _attr_native_max_value = 720
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _conf_key = CONF_APPLIANCE_ON_SUSTAIN_MINUTES
    _tuning_field = "on_sustain_s"
    _default_value = DEFAULT_APPLIANCE_ON_SUSTAIN_MINUTES
    _scale = 60


class ApplianceOffSustain(ApplianceTuningNumber):
    """Deficit must persist this long before turning OFF."""

    _attr_translation_key = "appliance_off_sustain"
    _attr_native_min_value = 0
    _attr_native_max_value = 720
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _conf_key = CONF_APPLIANCE_OFF_SUSTAIN_MINUTES
    _tuning_field = "off_sustain_s"
    _default_value = DEFAULT_APPLIANCE_OFF_SUSTAIN_MINUTES
    _scale = 60


class BatteryChargeBuffer(EnergyManagerEntity, RestoreNumber):
    """Number entity for the BATT-15 charge buffer percentage.

    Extra energy planned on top of the forecast deficit when sizing
    cheap-hour grid charging. Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_charge_buffer"
    _attr_native_min_value = MIN_CHARGE_BUFFER_PCT
    _attr_native_max_value = MAX_CHARGE_BUFFER_PCT
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"

    _default_value = DEFAULT_CHARGE_BUFFER_PCT

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the charge buffer entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charge_buffer_pct"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_CHARGE_BUFFER_PCT, self._default_value
                )
            )
        self.coordinator.charge_buffer_pct = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the charge buffer and trigger schedule recalculation.

        Args:
            value: New charge buffer in percent.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.charge_buffer_pct = value
        await self.coordinator.async_request_refresh()


class BatteryProductionFactor(EnergyManagerEntity, RestoreNumber):
    """Number entity for the BATT-15 solar production factor.

    Multiplier applied to the raw solar forecast before planning. Value
    persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_production_factor"
    _attr_native_min_value = MIN_PRODUCTION_FACTOR
    _attr_native_max_value = MAX_PRODUCTION_FACTOR
    _attr_native_step = 0.05

    _default_value = DEFAULT_PRODUCTION_FACTOR

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the production factor entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_production_factor"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_PRODUCTION_FACTOR, self._default_value
                )
            )
        self.coordinator.production_factor = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the production factor and trigger schedule recalculation.

        Args:
            value: New solar production factor multiplier.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.production_factor = value
        await self.coordinator.async_request_refresh()


class BatteryEstimatedChargePower(EnergyManagerEntity, RestoreNumber):
    """Number entity for the BATT-15 estimated charge power.

    Assumed charge power when converting energy need to slot count.
    Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_estimated_charge_power"
    _attr_native_min_value = MIN_ESTIMATED_CHARGE_POWER_KW
    _attr_native_max_value = MAX_ESTIMATED_CHARGE_POWER_KW
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kW"

    _default_value = DEFAULT_ESTIMATED_CHARGE_POWER_KW

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the estimated charge power entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_estimated_charge_power_kw"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_ESTIMATED_CHARGE_POWER_KW, self._default_value
                )
            )
        self.coordinator.estimated_charge_power_kw = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the estimated charge power and trigger schedule recalculation.

        Args:
            value: New estimated charge power in kW.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.estimated_charge_power_kw = value
        await self.coordinator.async_request_refresh()


class BatteryPeakGapHours(EnergyManagerEntity, RestoreNumber):
    """Number entity for the BATT-15 minimum peak gap.

    Minimum gap between price peaks treated as separate peaks. Value
    persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_peak_gap_hours"
    _attr_native_min_value = MIN_PEAK_GAP_HOURS
    _attr_native_max_value = MAX_PEAK_GAP_HOURS
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "h"

    _default_value = DEFAULT_PEAK_GAP_HOURS

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the peak gap hours entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_peak_gap_hours"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_PEAK_GAP_HOURS, self._default_value
                )
            )
        self.coordinator.peak_gap_hours = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the peak gap hours and trigger schedule recalculation.

        Args:
            value: New minimum peak gap in hours.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.peak_gap_hours = value
        await self.coordinator.async_request_refresh()


class EvMaxGridChargePower(EnergyManagerEntity, RestoreNumber):
    """Number entity for the EV grid-charging power cap.

    Ceiling on total grid power the charger may draw during scheduled
    (price-based) charging. Value persists across restarts and applies
    on the next charger tick -- no reload, so an active session's state
    machine is never torn down.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "ev_max_grid_charge_power"
    _attr_native_min_value = MIN_MAX_GRID_CHARGE_POWER_KW
    _attr_native_max_value = MAX_MAX_GRID_CHARGE_POWER_KW
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kW"

    _default_value = DEFAULT_MAX_GRID_CHARGE_POWER_KW

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the grid charge power cap entity.

        Args:
            coordinator: The EaseeCoordinator running the charger loop.
            entry: The config entry this entity belongs to.
        """
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_max_grid_charge_power_kw"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_MAX_GRID_CHARGE_POWER_KW, self._default_value
                )
            )
        self.coordinator.grid_power_cap_kw = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the power cap; applies on the next charger tick.

        Args:
            value: New grid charging power cap in kW.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.grid_power_cap_kw = value
        await self.coordinator.async_request_refresh()


class EvSolarStartThreshold(EnergyManagerEntity, RestoreNumber):
    """Number entity for the EV solar charging start threshold.

    Minimum net solar surplus before solar charging starts. Value
    persists across restarts and applies on the next charger tick -- no
    reload, so an active session's state machine is never torn down.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "ev_solar_start_threshold"
    _attr_native_min_value = MIN_SOLAR_START_THRESHOLD_KW
    _attr_native_max_value = MAX_SOLAR_START_THRESHOLD_KW
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kW"

    _default_value = DEFAULT_SOLAR_START_THRESHOLD_KW

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the solar start threshold entity.

        Args:
            coordinator: The EaseeCoordinator running the charger loop.
            entry: The config entry this entity belongs to.
        """
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_solar_start_threshold_kw"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_SOLAR_START_THRESHOLD_KW, self._default_value
                )
            )
        self.coordinator.solar_start_threshold_kw = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the solar start threshold; applies on the next charger tick.

        Args:
            value: New minimum net solar surplus in kW.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.solar_start_threshold_kw = value
        await self.coordinator.async_request_refresh()


class EvBatterySocGate(EnergyManagerEntity, RestoreNumber):
    """Number entity for the EV battery SOC gate.

    Minimum house-battery SOC before solar EV charging starts. Value
    persists across restarts and applies on the next charger tick -- no
    reload, so an active session's state machine is never torn down.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "ev_battery_soc_gate"
    _attr_native_min_value = MIN_BATTERY_SOC_GATE_PCT
    _attr_native_max_value = MAX_BATTERY_SOC_GATE_PCT
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"

    _default_value = DEFAULT_BATTERY_SOC_GATE_PCT

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the battery SOC gate entity.

        Args:
            coordinator: The EaseeCoordinator running the charger loop.
            entry: The config entry this entity belongs to.
        """
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_battery_soc_gate_pct"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_BATTERY_SOC_GATE_PCT, self._default_value
                )
            )
        self.coordinator.battery_soc_gate_pct = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the battery SOC gate; applies on the next charger tick.

        Args:
            value: New minimum house-battery SOC in percent.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.battery_soc_gate_pct = value
        await self.coordinator.async_request_refresh()

"""Sensor platform for the Energy Manager integration.

Provides a price sensor entity that exposes current electricity price
as state. When the battery module is enabled, also provides battery
schedule sensors showing current state, next charge, and next discharge slots.
When the EV module is enabled, provides per-car schedule sensors showing
the current charging action with full schedule in attributes.
When the appliances module is enabled, provides per-appliance status
sensors explaining every surplus-control decision (APPL-08).
Downstream modules access full price slot data directly from
the PriceCoordinator via entry.runtime_data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .battery_scheduler import compute_effective_discharge_threshold
from .const import (
    CONF_APPLIANCE_OFF_THRESHOLD_PCT,
    CONF_APPLIANCE_ON_THRESHOLD_PCT,
    CONF_APPLIANCE_POWER_SENSOR_ENTITY,
    CONF_APPLIANCE_RATED_POWER_W,
    DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT,
    DEFAULT_APPLIANCE_ON_THRESHOLD_PCT,
    SUBENTRY_TYPE_APPLIANCE,
)
from .coordinator import (
    ApplianceModuleData,
    BatteryScheduleData,
    CarChargingData,
    EaseeData,
    EMSData,
    EnergyManagerConfigEntry,
    PriceData,
)
from .entity import ApplianceEntity, CarEntity, EnergyManagerEntity
from .forecast_accuracy import (
    mean_ratio,
    serialize_history,
    suggested_factor,
    valid_ratios,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyManagerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Energy Manager sensor entities from a config entry.

    Creates the price sensor and optionally battery schedule sensors
    when the battery module is enabled.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    entities: list[SensorEntity] = []

    # Core price sensor (always present)
    price_coordinator = entry.runtime_data.price_coordinator
    entities.append(EnergyManagerPriceSensor(price_coordinator, entry))

    # Battery schedule sensors (when battery module enabled)
    battery_coordinator = entry.runtime_data.battery_coordinator
    if battery_coordinator is not None:
        entities.extend([
            BatteryScheduleSensor(battery_coordinator, entry),
            NextChargeSensor(battery_coordinator, entry),
            NextDischargeSensor(battery_coordinator, entry),
            ActualPriceSensor(battery_coordinator, price_coordinator, entry),
            ForecastAccuracySensor(battery_coordinator, entry),
            EffectiveDischargeThresholdSensor(battery_coordinator, entry),
        ])

    # EMS status sensor (when EMS coordinator exists)
    ems_coordinator = entry.runtime_data.ems_coordinator
    if ems_coordinator is not None:
        entities.append(EMSStatusSensor(ems_coordinator, entry))
        entities.append(CommandedChargeLimitSensor(ems_coordinator, entry))

    # Easee charger status sensor (when the Easee coordinator exists)
    easee_coordinator = entry.runtime_data.easee_coordinator
    if easee_coordinator is not None:
        entities.append(EaseeChargerStatusSensor(easee_coordinator, entry))

        # House load + Solar surplus diagnostic sensors (CORE-11): both
        # derive from the Easee coordinator's solar-surplus inputs, so both
        # are only created when it exists.
        entities.append(
            HouseLoadSensor(easee_coordinator, battery_coordinator, entry)
        )
        entities.append(SolarSurplusSensor(easee_coordinator, entry))

    async_add_entities(entities)

    # Car schedule sensors (one per car subentry)
    for subentry_id, coordinator in entry.runtime_data.car_coordinators.items():
        subentry = entry.subentries[subentry_id]
        async_add_entities(
            [CarScheduleSensor(coordinator, entry, subentry)],
            config_subentry_id=subentry_id,
        )

    # Appliance status sensors (one per appliance subentry)
    appliance_coordinator = entry.runtime_data.appliance_coordinator
    if appliance_coordinator is not None:
        for subentry_id, subentry in entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_APPLIANCE:
                continue
            async_add_entities(
                [ApplianceStatusSensor(appliance_coordinator, entry, subentry)],
                config_subentry_id=subentry_id,
            )


class EnergyManagerPriceSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing current electricity price.

    State is the current electricity price in SEK/kWh. Full hourly price
    slot data is available to downstream modules via the PriceCoordinator.
    """

    _attr_translation_key = "electricity_price"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the price sensor.

        Args:
            coordinator: The PriceCoordinator providing price data.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_electricity_price"

    @property
    def native_value(self) -> float | None:
        """Return the current electricity price."""
        data: PriceData | None = self.coordinator.data
        if data is None:
            return None
        return data.current_price

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return lightweight metadata attributes.

        Full hourly price slot data is accessed by downstream modules
        directly via the PriceCoordinator (entry.runtime_data).
        """
        data: PriceData | None = self.coordinator.data
        if data is None:
            return {"last_updated": None}

        return {
            "last_updated": data.last_updated.isoformat() if data.last_updated else None,
        }


class BatteryScheduleSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing current battery schedule state.

    State is the current battery mode: idle, grid_charging, discharging,
    solar_charging, or exporting. Attributes expose the full schedule
    (max 48 slots), slot counts, target EMS mode, and calculation metadata.
    """

    _attr_translation_key = "battery_schedule"
    _attr_icon = "mdi:home-battery"

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the battery schedule sensor.

        Args:
            coordinator: The BatteryScheduleCoordinator providing schedule data.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery_schedule"

    @property
    def native_value(self) -> str:
        """Return the current battery state."""
        data: BatteryScheduleData | None = self.coordinator.data
        if data is None:
            return "unknown"
        return data.current_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return compact schedule attributes.

        Includes the full schedule (max 48 slots), slot counts,
        target EMS mode, and calculation metadata. Does NOT include
        full price arrays (already available on PriceCoordinator).
        """
        data: BatteryScheduleData | None = self.coordinator.data
        if data is None:
            return {}

        # Filter out past slots, then cap at 48 for compact state.
        # This ensures the visible window starts from now, so charge/discharge
        # slots later in the schedule are not cut off by early idle slots.
        now = dt_util.utcnow()
        filtered = [s for s in data.schedule if s.end > now][:48]

        schedule_list = []
        for slot in filtered:
            schedule_list.append({
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "price": round(slot.price, 4),
                "action": slot.action,
            })

        return {
            "schedule": schedule_list,
            "charging_slots": data.charging_slot_count,
            "discharging_slots": data.discharging_slot_count,
            "export_slots": data.export_slot_count,
            "next_export_slot": data.next_export_slot,
            "target_ems_mode": data.target_ems_mode,
            "last_calculated": data.last_calculated.isoformat(),
            "solar_forecast_used": data.solar_forecast_used,
            "solar_forecast_tomorrow_used": data.solar_forecast_tomorrow_used,
            "discharge_allowed": data.discharge_allowed,
            "discharge_gate_reason": data.discharge_gate_reason,
            "reserved_energy_kwh": round(data.reserved_energy_kwh, 2),
        }


class NextChargeSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing the next upcoming charging slot.

    State is the start datetime of the next charging slot (TIMESTAMP
    device class). Attributes expose the slot price and end time.
    """

    _attr_translation_key = "battery_next_charging_slot"
    _attr_icon = "mdi:battery-charging"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the next charge sensor.

        Args:
            coordinator: The BatteryScheduleCoordinator providing schedule data.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_next_charging_slot"

    @property
    def available(self) -> bool:
        """Return True when coordinator has data (distinguishes no-slots from error)."""
        return self.coordinator.data is not None

    @property
    def native_value(self) -> datetime | None:
        """Return the start time of the next charging slot."""
        data: BatteryScheduleData | None = self.coordinator.data
        if data is not None and data.next_charging_slot is not None:
            start = data.next_charging_slot["start"]
            if isinstance(start, str):
                return datetime.fromisoformat(start)
            return start
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return price and end time for the next charging slot."""
        data: BatteryScheduleData | None = self.coordinator.data
        if data is not None and data.next_charging_slot is not None:
            slot = data.next_charging_slot
            return {
                "price": slot["price"],
                "end": slot["end"],
            }
        return {}


class NextDischargeSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing the next upcoming discharging slot.

    State is the start datetime of the next discharging slot (TIMESTAMP
    device class). Attributes expose the slot price and end time.
    """

    _attr_translation_key = "battery_next_discharging_slot"
    _attr_icon = "mdi:battery-arrow-down"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the next discharge sensor.

        Args:
            coordinator: The BatteryScheduleCoordinator providing schedule data.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_next_discharging_slot"

    @property
    def available(self) -> bool:
        """Return True when coordinator has data (distinguishes no-slots from error)."""
        return self.coordinator.data is not None

    @property
    def native_value(self) -> datetime | None:
        """Return the start time of the next discharging slot."""
        data: BatteryScheduleData | None = self.coordinator.data
        if data is not None and data.next_discharging_slot is not None:
            start = data.next_discharging_slot["start"]
            if isinstance(start, str):
                return datetime.fromisoformat(start)
            return start
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return price and end time for the next discharging slot."""
        data: BatteryScheduleData | None = self.coordinator.data
        if data is not None and data.next_discharging_slot is not None:
            slot = data.next_discharging_slot
            return {
                "price": slot["price"],
                "end": slot["end"],
            }
        return {}


class ActualPriceSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing the actual electricity price incl. fees (BATT-14, CORE-11).

    State is spot price + grid_transfer_fee + electricity_company_fee
    (SEK/kWh), internalizing the live system's "Faktiskt elpris" template.
    Diagnostic-ish informational sensor -- no state_class (None is correct
    for monetary spot prices, see Phase 1 lesson).
    """

    _attr_translation_key = "actual_electricity_price"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator,
        price_coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the actual price sensor.

        Args:
            coordinator: The BatteryScheduleCoordinator providing the
                grid_transfer_fee and electricity_company_fee values.
            price_coordinator: The PriceCoordinator providing the spot price.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._price_coordinator = price_coordinator
        self._attr_unique_id = f"{entry.entry_id}_actual_electricity_price"

    @property
    def native_value(self) -> float | None:
        """Return spot price + grid transfer fee + electricity company fee."""
        price_data: PriceData | None = self._price_coordinator.data
        if price_data is None or price_data.current_price is None:
            return None
        return (
            price_data.current_price
            + self.coordinator.grid_transfer_fee
            + self.coordinator.electricity_company_fee
        )


class ForecastAccuracySensor(EnergyManagerEntity, SensorEntity):
    """Diagnostic sensor for Stage-1 solar-forecast accuracy telemetry.

    State is the suggested production factor derived from the last 14 days
    of forecast-vs-actual PV production (unknown until 7 valid days exist).
    Attributes expose the accuracy ratios and the persisted daily history.
    Observe-only: nothing here feeds the scheduler -- the configured
    production factor is applied unchanged (Stage 2 is post-cutover).
    """

    _attr_translation_key = "forecast_accuracy"
    _attr_icon = "mdi:chart-line"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the forecast accuracy sensor.

        Args:
            coordinator: The BatteryScheduleCoordinator providing the
                forecast_accuracy_history records.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_forecast_accuracy"

    @property
    def native_value(self) -> float | None:
        """Return the suggested production factor, or None under 7 valid days."""
        return suggested_factor(self.coordinator.forecast_accuracy_history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return accuracy ratios and the persisted daily history.

        ratio_yesterday is the most recent valid record's actual/forecast
        ratio; ratio_7d/ratio_14d are plain means over the newest 7/14
        valid ratios; valid_days counts the ratios in the 14-day window.
        """
        history = self.coordinator.forecast_accuracy_history
        ratios = valid_ratios(history)
        ratio_7d = mean_ratio(ratios[-7:])
        ratio_14d = mean_ratio(ratios)
        return {
            "ratio_yesterday": round(ratios[-1], 3) if ratios else None,
            "ratio_7d": round(ratio_7d, 3) if ratio_7d is not None else None,
            "ratio_14d": round(ratio_14d, 3) if ratio_14d is not None else None,
            "valid_days": len(ratios),
            "history": serialize_history(history),
        }


class EffectiveDischargeThresholdSensor(EnergyManagerEntity, SensorEntity):
    """Diagnostic sensor for the discharge spread threshold actually in use.

    State is the spread threshold the scheduler actually uses. When the
    battery cycle cost is > 0 it is derived as
    max(0, cycle_cost - grid_transfer_fee) and OVERRIDES the manual
    threshold number entity; otherwise the manual value passes through
    unchanged.
    """

    _attr_translation_key = "battery_effective_discharge_threshold"
    _attr_icon = "mdi:battery-arrow-down-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the effective discharge threshold sensor.

        Args:
            coordinator: The BatteryScheduleCoordinator providing the
                discharge_threshold, battery_cycle_cost, and
                grid_transfer_fee attributes.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery_effective_discharge_threshold"

    @property
    def native_value(self) -> float | None:
        """Return the discharge threshold actually in use by the scheduler."""
        return compute_effective_discharge_threshold(
            self.coordinator.discharge_threshold,
            self.coordinator.battery_cycle_cost,
            self.coordinator.grid_transfer_fee,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return whether the threshold is manual or cycle-cost derived."""
        source = (
            "cycle_cost_derived"
            if self.coordinator.battery_cycle_cost > 0
            else "manual"
        )
        return {
            "source": source,
            "battery_cycle_cost": self.coordinator.battery_cycle_cost,
            "grid_transfer_fee": self.coordinator.grid_transfer_fee,
            "manual_threshold": self.coordinator.discharge_threshold,
        }


class EMSStatusSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing EMS controller status.

    State is the current EMS mode, shown as "pv_charging" while the
    PV-opportunistic override is active -- the generic command_charging
    mode reads as grid charging when the battery is actually absorbing
    solar. Attributes expose fuse headroom, target mode, override reason,
    verification status, and observe-only (dry-run) status (CORE-14).
    """

    _attr_translation_key = "battery_ems_status"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the EMS status sensor.

        Args:
            coordinator: The EMSCoordinator providing EMS state data.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ems_status"

    @property
    def native_value(self) -> str:
        """Return the current EMS mode, or "pv_charging" during PV charging."""
        data: EMSData | None = self.coordinator.data
        if data is None:
            return "unknown"
        if data.pv_charging_active:
            return "pv_charging"
        return data.current_mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return EMS control attributes.

        Exposes target mode, charge limit, fuse headroom, override reason,
        command verification status, active override flags, observe-only
        (dry-run) status with the last suppressed command (CORE-14), and
        the fuse-capped export limit while an export slot is active (BATT-17).
        """
        data: EMSData | None = self.coordinator.data
        if data is None:
            return {}
        return {
            "ems_mode": data.current_mode,
            "target_mode": data.target_mode,
            "charge_limit_kw": round(data.charge_limit_kw, 2),
            "fuse_headroom_amps": round(data.fuse_headroom_amps, 1),
            "override_reason": data.override_reason,
            "command_verified": data.command_verified,
            "car_override_active": data.car_override_active,
            "pv_charging_active": data.pv_charging_active,
            "dry_run": data.dry_run,
            "last_suppressed_command": data.last_suppressed_command,
            "discharge_allowed": data.discharge_allowed,
            "discharge_gate_reason": data.discharge_gate_reason,
            "export_limit_kw": round(data.export_limit_kw, 2)
            if data.export_limit_kw is not None
            else None,
        }


class CommandedChargeLimitSensor(EnergyManagerEntity, SensorEntity):
    """Diagnostic sensor for the battery charge limit EM commands (EMS-layer).

    State is the ESS max-charging-limit value the EMS controller sends to
    the battery (number.set_value on the configured charge limit entity) --
    the fuse-limited power the battery is currently allowed to charge with,
    which during PV-opportunistic charging tracks live solar production.
    In observe-only mode (CORE-14) this is the value that WOULD be sent.
    """

    _attr_translation_key = "battery_commanded_charge_limit"
    _attr_icon = "mdi:battery-arrow-up"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "kW"
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the commanded charge limit sensor.

        Args:
            coordinator: The EMSCoordinator providing EMS state data.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery_commanded_charge_limit"

    @property
    def native_value(self) -> float | None:
        """Return the charge limit EM last computed for the battery."""
        data: EMSData | None = self.coordinator.data
        if data is None:
            return None
        return data.charge_limit_kw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return context for the commanded limit.

        Exposes whether the limit is currently tracking PV production
        (pv_charging_active) and whether it was actually sent or only
        computed (dry_run, CORE-14).
        """
        data: EMSData | None = self.coordinator.data
        if data is None:
            return {}
        return {
            "pv_charging_active": data.pv_charging_active,
            "dry_run": data.dry_run,
        }


class EaseeChargerStatusSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing Easee charger controller status.

    State is the current charger decision mode: forced, scheduled, solar,
    or idle (see charger_state_machine.ChargerController.decide()).
    Attributes expose the computed amp target, phase mode, phase-switch
    sequence state, stuck-command flag, raw charger status, measured power,
    fuse headroom, override reason, and observe-only (dry-run) status with
    the last suppressed command (CORE-14).
    """

    _attr_translation_key = "ev_charger_status"
    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the charger status sensor.

        Args:
            coordinator: The EaseeCoordinator providing charger state data.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charger_status"

    @property
    def native_value(self) -> str:
        """Return the current charger decision mode."""
        data: EaseeData | None = self.coordinator.data
        if data is None:
            return "unknown"
        return data.mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return charger control attributes.

        Exposes target amps/phase mode, phase-switch sequence state, stuck
        flag, raw charger status, measured power, fuse headroom, override
        reason, and observe-only (dry-run) status with the last suppressed
        command (CORE-14).
        """
        data: EaseeData | None = self.coordinator.data
        if data is None:
            return {}
        return {
            "target_amps": round(data.target_amps, 1),
            "target_phase_mode": data.target_phase_mode,
            "sequence_state": data.sequence_state,
            "stuck": data.stuck,
            "charger_status": data.charger_status,
            "charger_power_kw": round(data.charger_power_kw, 2),
            "fuse_headroom_amps": round(data.fuse_headroom_amps, 1),
            "override_reason": data.override_reason,
            "dry_run": data.dry_run,
            "last_suppressed_command": data.last_suppressed_command,
        }


class HouseLoadSensor(EnergyManagerEntity, SensorEntity):
    """Diagnostic sensor showing filtered house load (CORE-11).

    State is house consumption minus configured excluded-power entities --
    the same net_house_consumption_kw the EaseeCoordinator computes as the
    solar-surplus formula's consumption term (EV-09/EMS-13, see
    coordinator._read_net_house_consumption_kw()). Attribute exposes the
    BatteryScheduleCoordinator's BATT-15 rolling mean consumption, when that
    coordinator exists.
    """

    _attr_translation_key = "house_load"
    _attr_icon = "mdi:home-lightning-bolt"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "kW"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator,
        battery_coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the house load sensor.

        Args:
            coordinator: The EaseeCoordinator providing house_consumption_kw.
            battery_coordinator: The BatteryScheduleCoordinator providing the
                mean_consumption_kw attribute, or None if the battery
                module is disabled.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._battery_coordinator = battery_coordinator
        self._attr_unique_id = f"{entry.entry_id}_house_load"

    @property
    def native_value(self) -> float | None:
        """Return the filtered (net) house consumption in kW."""
        data: EaseeData | None = self.coordinator.data
        if data is None:
            return None
        return data.house_consumption_kw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the BATT-15 rolling mean consumption, when available."""
        if self._battery_coordinator is None:
            return {}
        battery_data: BatteryScheduleData | None = self._battery_coordinator.data
        if battery_data is None:
            return {}
        return {"mean_consumption_kw": round(battery_data.mean_consumption_kw, 2)}


class SolarSurplusSensor(EnergyManagerEntity, SensorEntity):
    """Diagnostic sensor showing computed live solar surplus (CORE-11, EV-09).

    State is the raw (unclamped) solar_surplus_kw the EaseeCoordinator
    computes each tick via compute_solar_surplus_kw() -- before the charger
    controller's own safety-buffer/start-threshold gating is applied.
    """

    _attr_translation_key = "solar_surplus"
    _attr_icon = "mdi:solar-power-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "kW"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
    ) -> None:
        """Initialize the solar surplus sensor.

        Args:
            coordinator: The EaseeCoordinator providing solar_surplus_kw.
            entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_solar_surplus"

    @property
    def native_value(self) -> float | None:
        """Return the raw computed solar surplus in kW."""
        data: EaseeData | None = self.coordinator.data
        if data is None:
            return None
        return data.solar_surplus_kw


class CarScheduleSensor(CarEntity, SensorEntity):
    """Sensor showing current car charging schedule.

    State is the current charging action: charge, idle, or solar_charge.
    Attributes expose the full schedule (max 48 future slots), slot counts,
    energy needed, hours needed, SOC info, and calculation metadata.
    """

    _attr_translation_key = "car_schedule"
    _attr_icon = "mdi:car-electric"

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
        subentry,
    ) -> None:
        """Initialize the car schedule sensor.

        Args:
            coordinator: The CarChargingCoordinator for this car.
            entry: The config entry this sensor belongs to.
            subentry: The car subentry with car-specific configuration.
        """
        super().__init__(coordinator, entry, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_car_schedule"

    @property
    def native_value(self) -> str:
        """Return the current car charging action."""
        data: CarChargingData | None = self.coordinator.data
        if data is None:
            return "unknown"
        return data.current_action

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return car schedule attributes.

        Includes the schedule (max 48 future slots), slot counts, energy
        needed, hours needed, SOC info, and calculation metadata.
        """
        data: CarChargingData | None = self.coordinator.data
        if data is None:
            return {}

        # Filter out past slots, then cap at 48 for compact state.
        now = dt_util.utcnow()
        filtered = [s for s in data.schedule if s.end > now][:48]

        schedule_list = []
        for slot in filtered:
            schedule_list.append({
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "price": round(slot.price, 4),
                "action": slot.action,
            })

        return {
            "schedule": schedule_list,
            "charging_slots": data.charging_slot_count,
            "energy_needed_kwh": round(data.energy_needed_kwh, 2),
            "hours_needed": round(data.hours_needed, 1),
            "current_soc": data.current_soc,
            "target_soc": data.target_soc,
            "solar_target_soc": data.solar_target_soc,
            "is_preliminary": data.is_preliminary,
            "last_calculated": data.last_calculated.isoformat(),
        }


class ApplianceStatusSensor(ApplianceEntity, SensorEntity):
    """Sensor showing the appliance surplus-control status (APPL-08).

    State is this appliance's decision status from the shared
    ApplianceCoordinator (e.g. off_no_surplus, on_surplus, blocked_fuse).
    Attributes expose the decision reason, allocated power, the surplus
    signal components, the hysteresis thresholds, the measured draw (when
    a power sensor is configured), and the last (dry-run or actual)
    command message so every decision is explainable.
    """

    _attr_translation_key = "appliance_status"
    _attr_icon = "mdi:power-plug"

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
        subentry,
    ) -> None:
        """Initialize the appliance status sensor.

        Args:
            coordinator: The ApplianceCoordinator shared by all appliances.
            entry: The config entry this sensor belongs to.
            subentry: The appliance subentry with appliance-specific
                configuration.
        """
        super().__init__(coordinator, entry, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_appliance_status"
        self._entry = entry
        rated_power_w = float(subentry.data.get(CONF_APPLIANCE_RATED_POWER_W, 0))
        self._rated_power_w = rated_power_w
        on_pct = float(
            subentry.data.get(
                CONF_APPLIANCE_ON_THRESHOLD_PCT, DEFAULT_APPLIANCE_ON_THRESHOLD_PCT
            )
        )
        off_pct = float(
            subentry.data.get(
                CONF_APPLIANCE_OFF_THRESHOLD_PCT, DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT
            )
        )
        self._threshold_on_kw = rated_power_w * on_pct / 100.0 / 1000.0
        self._threshold_off_kw = rated_power_w * off_pct / 100.0 / 1000.0
        self._power_sensor_entity = subentry.data.get(
            CONF_APPLIANCE_POWER_SENSOR_ENTITY, ""
        )

    @property
    def native_value(self) -> str | None:
        """Return this appliance's decision status."""
        data: ApplianceModuleData | None = self.coordinator.data
        if data is None:
            return None
        decision = data.decisions.get(self._subentry_id)
        if decision is None:
            return None
        return decision.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return appliance decision attributes.

        Exposes the decision reason, allocated power, surplus signal
        components (raw surplus, export, battery discharge -- BATT-17),
        hysteresis thresholds, measured draw, the observe-only flag
        (CORE-14 master switch state), the idle-while-on diagnostic
        (drawing <10% of rated while commanded on -- needs the optional
        power sensor), and the last (dry-run or actual) command message.
        """
        data: ApplianceModuleData | None = self.coordinator.data
        if data is None:
            return {}
        decision = data.decisions.get(self._subentry_id)
        measured_power_w = self._read_measured_power_w()
        idle_while_on: bool | None = None
        if measured_power_w is not None and decision is not None:
            idle_while_on = (
                decision.desired_on and measured_power_w < self._rated_power_w * 0.1
            )
        runtime_data = getattr(self._entry, "runtime_data", None)
        return {
            "observe_only": not getattr(runtime_data, "control_enabled", False),
            "reason": decision.reason if decision is not None else None,
            "allocated_kw": round(decision.allocated_kw, 2)
            if decision is not None
            else None,
            "raw_surplus_kw": round(data.raw_surplus_kw, 2),
            "export_kw": round(data.export_kw, 2),
            "battery_discharge_kw": round(data.battery_discharge_kw, 2),
            "threshold_on_kw": round(self._threshold_on_kw, 2),
            "threshold_off_kw": round(self._threshold_off_kw, 2),
            "measured_power_w": measured_power_w,
            "idle_while_on": idle_while_on,
            "last_command_message": data.messages.get(self._subentry_id),
        }

    def _read_measured_power_w(self) -> float | None:
        """Read the optional appliance power sensor and return watts.

        Unit handling mirrors coordinator._read_power_kw(): the reading is
        assumed to be in watts unless the entity's unit_of_measurement is
        exactly "kW".

        Returns:
            The measured draw in W, or None if no power sensor is
            configured or its state is unavailable/unparseable.
        """
        if not self._power_sensor_entity:
            return None
        state = self.hass.states.get(self._power_sensor_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            power = float(state.state)
        except (ValueError, TypeError):
            return None
        if state.attributes.get("unit_of_measurement", "") == "kW":
            power *= 1000.0
        return round(power, 1)

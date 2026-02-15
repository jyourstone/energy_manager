"""Sensor platform for the Energy Manager integration.

Provides a price sensor entity that exposes current electricity price
as state. When the battery module is enabled, also provides battery
schedule sensors showing current state, next charge, and next discharge slots.
Downstream modules access full price slot data directly from
the PriceCoordinator via entry.runtime_data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BatteryScheduleData, EnergyManagerConfigEntry, PriceData
from .entity import EnergyManagerEntity


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
        ])

    async_add_entities(entities)


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
    or solar_charging. Attributes expose the full schedule (max 48 slots),
    slot counts, target EMS mode, and calculation metadata.
    """

    _attr_translation_key = "battery_schedule"
    _attr_icon = "mdi:battery-clock"

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

        # Serialize schedule slots (max 48)
        schedule_list = []
        for slot in data.schedule[:48]:
            schedule_list.append({
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "price": slot.price,
                "action": slot.action,
            })

        return {
            "schedule": schedule_list,
            "charging_slots": data.charging_slot_count,
            "discharging_slots": data.discharging_slot_count,
            "target_ems_mode": data.target_ems_mode,
            "last_calculated": data.last_calculated.isoformat(),
            "solar_forecast_used": data.solar_forecast_used,
        }


class NextChargeSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing the next upcoming charging slot.

    State is the start datetime of the next charging slot (TIMESTAMP
    device class). Attributes expose the slot price and end time.
    """

    _attr_translation_key = "next_charging_slot"
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

    _attr_translation_key = "next_discharging_slot"
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

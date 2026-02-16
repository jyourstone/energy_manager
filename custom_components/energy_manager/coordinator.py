"""Coordinators and typed runtime data for the Energy Manager integration.

PriceCoordinator fetches Nordpool prices on a 5-minute interval and
immediately on Nordpool state changes. Price data (today + tomorrow hourly
slots) is available via entry.runtime_data -- both to the user-visible
price sensor entity and to downstream scheduling modules.

BatteryScheduleCoordinator chains to PriceCoordinator and wraps the pure
battery_scheduler module, producing BatteryScheduleData on every update.
It recalculates when prices change, SOC changes, or solar forecast updates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_SCHEDULE_UPDATE_INTERVAL_MINUTES,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_ENABLED,
    CONF_EV_ENABLED,
    CONF_FORECAST_SOLAR_ENTITY,
    CONF_NORDPOOL_SENSOR,
    CONF_NORDPOOL_TYPE,
    CONF_SOC_ENTITY,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_CHARGE_THRESHOLD,
    DEFAULT_DISCHARGE_THRESHOLD,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DEFAULT_MAX_SOC_PCT,
    DEFAULT_MIN_SOC_PCT,
    DEFAULT_PEAK_GAP_HOURS,
    MODULE_BATTERY,
    MODULE_EV,
    PRICE_UPDATE_INTERVAL_MINUTES,
)
from .battery_scheduler import BatteryScheduleResult, build_battery_schedule
from .nordpool_adapter import async_get_prices

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PriceSlot:
    """A single hourly price slot with UTC-aware timestamps."""

    start: datetime
    end: datetime
    price: float


@dataclass
class PriceData:
    """Price data for today and tomorrow."""

    today: list[PriceSlot]
    tomorrow: list[PriceSlot]
    current_price: float | None
    last_updated: datetime


class PriceCoordinator(DataUpdateCoordinator[PriceData]):
    """Coordinator that fetches Nordpool prices with hybrid update strategy.

    Uses 5-minute polling as a baseline, with immediate refresh triggered
    by Nordpool sensor state changes (e.g., when tomorrow's prices arrive).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the price coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
        """
        super().__init__(
            hass,
            _LOGGER,
            name="Energy Manager Prices",
            config_entry=entry,
            update_interval=timedelta(minutes=PRICE_UPDATE_INTERVAL_MINUTES),
            always_update=False,
        )
        self._nordpool_entity: str = entry.data[CONF_NORDPOOL_SENSOR]
        self._nordpool_type: str = entry.data[CONF_NORDPOOL_TYPE]

    async def _async_setup(self) -> None:
        """Register Nordpool state change listener for immediate updates.

        Called once during async_config_entry_first_refresh. The listener
        is cleaned up automatically on unload via async_on_unload.
        """
        self.config_entry.async_on_unload(
            async_track_state_change_event(
                self.hass, [self._nordpool_entity], self._on_nordpool_update
            )
        )

    @callback
    def _on_nordpool_update(self, event) -> None:
        """Handle Nordpool sensor state changes.

        Triggers an immediate refresh when the Nordpool sensor updates,
        e.g., when tomorrow's prices become available around 13:00 CET.
        """
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> PriceData:
        """Fetch price data from Nordpool adapter and convert to PriceSlots.

        Returns:
            PriceData with today's and tomorrow's hourly price slots.

        Raises:
            UpdateFailed: If no price data is available for today.
        """
        raw_today, raw_tomorrow = await async_get_prices(
            self.hass, self._nordpool_entity, self._nordpool_type
        )

        if not raw_today:
            raise UpdateFailed("No Nordpool price data available for today")

        today_slots = _convert_to_price_slots(raw_today)
        tomorrow_slots = _convert_to_price_slots(raw_tomorrow)

        # Get current price from Nordpool sensor state
        current_price = _get_current_price(self.hass, self._nordpool_entity)

        return PriceData(
            today=today_slots,
            tomorrow=tomorrow_slots,
            current_price=current_price,
            last_updated=dt_util.utcnow(),
        )


def _convert_to_price_slots(raw_entries: list[dict]) -> list[PriceSlot]:
    """Convert raw price dicts to PriceSlot objects.

    Handles both ISO string and datetime objects for start/end fields.
    Ensures all datetimes are UTC-aware.

    Args:
        raw_entries: List of dicts with start, end, and value keys.

    Returns:
        List of PriceSlot objects with UTC-aware datetimes.
    """
    slots: list[PriceSlot] = []
    for entry in raw_entries:
        try:
            start_raw = entry.get("start")
            end_raw = entry.get("end")
            value = entry.get("value")

            if start_raw is None or value is None:
                continue

            start = _parse_datetime(start_raw)
            end = _parse_datetime(end_raw) if end_raw is not None else start + timedelta(hours=1)

            slots.append(
                PriceSlot(
                    start=dt_util.as_utc(start),
                    end=dt_util.as_utc(end),
                    price=float(value),
                )
            )
        except (ValueError, TypeError) as exc:
            _LOGGER.warning("Error converting price entry: %s", exc)
            continue

    return slots


def _parse_datetime(value: str | datetime) -> datetime:
    """Parse a datetime from an ISO string or return as-is if already datetime.

    Args:
        value: An ISO format string or datetime object.

    Returns:
        A datetime object.
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _get_current_price(hass: HomeAssistant, entity_id: str) -> float | None:
    """Get the current price from the Nordpool sensor state.

    Args:
        hass: Home Assistant instance.
        entity_id: The Nordpool sensor entity ID.

    Returns:
        Current price as float, or None if unavailable.
    """
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    try:
        return float(state.state)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class BatteryScheduleData:
    """Output of the battery schedule coordinator.

    Attributes:
        current_state: Current battery mode (idle/grid_charging/discharging/solar_charging).
        schedule: Full list of ScheduleSlot objects from the pure scheduler.
        next_charging_slot: Serialized next charge slot dict or None.
        next_discharging_slot: Serialized next discharge slot dict or None.
        charging_slot_count: Number of charge/solar_charge slots.
        discharging_slot_count: Number of discharge slots.
        target_ems_mode: EMS mode string for Phase 3.
        last_calculated: UTC timestamp of last calculation.
        solar_forecast_used: Whether solar forecast was incorporated.
    """

    current_state: str
    schedule: list
    next_charging_slot: dict | None
    next_discharging_slot: dict | None
    charging_slot_count: int
    discharging_slot_count: int
    target_ems_mode: str
    last_calculated: datetime
    solar_forecast_used: bool


class BatteryScheduleCoordinator(DataUpdateCoordinator[BatteryScheduleData]):
    """Coordinator that wraps the pure battery scheduler.

    Chains to PriceCoordinator for automatic recalculation on price updates.
    Also listens for SOC entity and Forecast.Solar entity state changes.
    Mutable threshold attributes are updated by NumberEntity instances.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        price_coordinator: PriceCoordinator,
    ) -> None:
        """Initialize the battery schedule coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            price_coordinator: The PriceCoordinator to chain to.
        """
        super().__init__(
            hass,
            _LOGGER,
            name="Energy Manager Battery Schedule",
            config_entry=entry,
            update_interval=timedelta(
                minutes=BATTERY_SCHEDULE_UPDATE_INTERVAL_MINUTES
            ),
            always_update=False,
        )
        self._price_coordinator = price_coordinator

        # Mutable thresholds -- updated by NumberEntity instances after setup
        self.charge_threshold: float = DEFAULT_CHARGE_THRESHOLD
        self.discharge_threshold: float = DEFAULT_DISCHARGE_THRESHOLD
        self.max_charge_power_w: float = DEFAULT_MAX_CHARGE_POWER_KW * 1000

    async def _async_setup(self) -> None:
        """Register listeners for coordinator chaining and entity state changes.

        Called once during async_config_entry_first_refresh.
        """
        # Chain to PriceCoordinator: recalculate when prices update
        self._unsub_price = self._price_coordinator.async_add_listener(
            self._handle_price_update
        )
        self.config_entry.async_on_unload(lambda: self._unsub_price())

        # Listen for SOC entity state changes
        soc_entity = self.config_entry.options.get(CONF_SOC_ENTITY)
        if soc_entity:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, [soc_entity], self._handle_external_update
                )
            )

        # Listen for Forecast.Solar entity state changes
        solar_entity = self.config_entry.options.get(CONF_FORECAST_SOLAR_ENTITY)
        if solar_entity:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, [solar_entity], self._handle_external_update
                )
            )

    @callback
    def _handle_price_update(self) -> None:
        """Handle PriceCoordinator data updates via coordinator chaining."""
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_external_update(self, event) -> None:
        """Handle SOC or solar entity state changes."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> BatteryScheduleData:
        """Fetch inputs, run pure scheduler, and return BatteryScheduleData.

        Returns:
            BatteryScheduleData with the current schedule and derived state.

        Raises:
            UpdateFailed: If no price data is available.
        """
        # 1. Read price data from the chained PriceCoordinator
        price_data = self._price_coordinator.data
        if price_data is None or not price_data.today:
            raise UpdateFailed(
                "No price data available for battery schedule calculation"
            )

        # 2. Combine today + tomorrow price slots
        price_slots = [
            {"start": slot.start, "end": slot.end, "price": slot.price}
            for slot in price_data.today + price_data.tomorrow
        ]

        # 3. Read current SOC from HA entity state
        current_soc = self._read_soc()

        # 4. Read solar forecast if configured
        solar_forecast_wh = self._get_solar_forecast_wh()

        # 5. Read battery capacity from entry options
        battery_capacity_kwh = self.config_entry.options.get(
            CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH
        )

        # 6. Call the pure scheduler
        result: BatteryScheduleResult = build_battery_schedule(
            price_slots=price_slots,
            charge_threshold=self.charge_threshold,
            discharge_threshold=self.discharge_threshold,
            max_charge_power_w=self.max_charge_power_w,
            battery_capacity_kwh=battery_capacity_kwh,
            current_soc_pct=current_soc,
            now=dt_util.utcnow(),
            solar_forecast_wh=solar_forecast_wh,
            peak_gap_hours=DEFAULT_PEAK_GAP_HOURS,
            min_soc_pct=DEFAULT_MIN_SOC_PCT,
            max_soc_pct=DEFAULT_MAX_SOC_PCT,
        )

        # 7. Serialize next slots to dicts
        next_charging = _serialize_slot(result.next_charging_slot)
        next_discharging = _serialize_slot(result.next_discharging_slot)

        # 8. Map current_action to current_state
        state_map = {
            "charge": "grid_charging",
            "solar_charge": "solar_charging",
            "discharge": "discharging",
            "idle": "idle",
        }
        current_state = state_map.get(result.current_action, result.current_action)

        # 9. Return BatteryScheduleData
        return BatteryScheduleData(
            current_state=current_state,
            schedule=result.schedule,
            next_charging_slot=next_charging,
            next_discharging_slot=next_discharging,
            charging_slot_count=result.charging_slot_count,
            discharging_slot_count=result.discharging_slot_count,
            target_ems_mode=result.target_ems_mode,
            last_calculated=dt_util.utcnow(),
            solar_forecast_used=solar_forecast_wh is not None,
        )

    def _read_soc(self) -> float:
        """Read the current battery SOC from HA entity state.

        Returns:
            SOC as a percentage (0-100). Defaults to 50.0 if unavailable.
        """
        soc_entity_id = self.config_entry.options.get(CONF_SOC_ENTITY)
        if not soc_entity_id:
            return 50.0

        state = self.hass.states.get(soc_entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return 50.0

        try:
            return float(state.state)
        except (ValueError, TypeError):
            return 50.0

    def _get_solar_forecast_wh(self) -> float | None:
        """Read Forecast.Solar entity state and return production in Wh.

        Returns:
            Solar forecast in Wh, or None if not configured or unavailable.
            Converts from kWh if the entity uses that unit.
        """
        solar_entity_id = self.config_entry.options.get(CONF_FORECAST_SOLAR_ENTITY)
        if not solar_entity_id:
            return None

        state = self.hass.states.get(solar_entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None

        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None

        # Check unit_of_measurement -- convert kWh to Wh
        uom = state.attributes.get("unit_of_measurement", "")
        if uom.lower() in ("kwh", "kwh"):
            value *= 1000.0

        return value


def _serialize_slot(slot) -> dict | None:
    """Serialize a ScheduleSlot to a dict for BatteryScheduleData.

    Args:
        slot: A ScheduleSlot instance or None.

    Returns:
        Dict with start/end as ISO strings, price, and action, or None.
    """
    if slot is None:
        return None
    return {
        "start": slot.start.isoformat(),
        "end": slot.end.isoformat(),
        "price": round(slot.price, 4),
        "action": slot.action,
    }


@dataclass
class EnergyManagerData:
    """Runtime data stored on the config entry.

    Provides typed access to coordinators and module state.
    """

    price_coordinator: PriceCoordinator
    battery_coordinator: BatteryScheduleCoordinator | None = None
    modules_enabled: dict[str, bool] = field(default_factory=dict)


EnergyManagerConfigEntry = ConfigEntry[EnergyManagerData]

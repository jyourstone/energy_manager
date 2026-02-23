"""Coordinators and typed runtime data for the Energy Manager integration.

PriceCoordinator fetches Nordpool prices on a 5-minute interval and
immediately on Nordpool state changes. Price data (today + tomorrow hourly
slots) is available via entry.runtime_data -- both to the user-visible
price sensor entity and to downstream scheduling modules.

BatteryScheduleCoordinator chains to PriceCoordinator and wraps the pure
battery_scheduler module, producing BatteryScheduleData on every update.
It recalculates when prices change, SOC changes, or solar forecast updates.

EMSCoordinator chains to BatteryScheduleCoordinator and orchestrates
real-time EMS control. It reads schedule data, real-time sensor values,
calls compute_ems_state() for decisions, sends control commands via HA
service calls, and verifies commands took effect.

CarChargingCoordinator (one per car subentry) chains to PriceCoordinator
and wraps the pure car_charging_scheduler module, producing CarChargingData
on every update. Each car gets independent scheduling based on its SOC,
departure time, and battery capacity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_SCHEDULE_UPDATE_INTERVAL_MINUTES,
    CAR_SCHEDULE_UPDATE_INTERVAL_MINUTES,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_LEVEL_ENTITY,
    CONF_CAR_NAME,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMS_SELECT_ENTITY,
    CONF_EV_ENABLED,
    CONF_FORECAST_SOLAR_ENTITY,
    CONF_FUSE_RATING,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HOME_PLUGGED_ENTITY,
    CONF_NORDPOOL_SENSOR,
    CONF_NORDPOOL_TYPE,
    CONF_PV_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_CAR_MAX_CHARGE_POWER_KW,
    DEFAULT_CHARGE_THRESHOLD,
    DEFAULT_DISCHARGE_THRESHOLD,
    DEFAULT_FUSE_RATING,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DEFAULT_MAX_SOC_PCT,
    DEFAULT_MIN_SOC_PCT,
    DEFAULT_PEAK_GAP_HOURS,
    DEFAULT_SAFETY_BUFFER_AMPS,
    DEFAULT_TARGET_SOC_PCT,
    EMS_MODE_MAP,
    EMS_UPDATE_INTERVAL_SECONDS,
    FALLBACK_STALE_THRESHOLD_MINUTES,
    MAX_CHARGE_LIMIT_KW,
    MODULE_BATTERY,
    MODULE_EV,
    PRICE_UPDATE_INTERVAL_MINUTES,
)
from .battery_scheduler import BatteryScheduleResult, build_battery_schedule
from .car_charging_scheduler import CarScheduleResult, build_car_charging_schedule
from .ems_controller import PVHysteresisTracker, compute_ems_state
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


@dataclass(frozen=True, slots=True)
class EMSData:
    """Output of the EMS coordinator.

    Attributes:
        current_mode: Active EMS mode string.
        target_mode: What the schedule wants.
        charge_limit_kw: Current safe charging limit.
        fuse_headroom_amps: Available headroom on fuse.
        override_reason: Why mode differs from schedule, or None.
        command_verified: Whether last command was verified.
        last_command_time: When last command was sent.
        car_override_active: Whether car priority paused battery.
        pv_charging_active: Whether PV opportunistic is active.
    """

    current_mode: str
    target_mode: str
    charge_limit_kw: float
    fuse_headroom_amps: float
    override_reason: str | None
    command_verified: bool
    last_command_time: datetime | None
    car_override_active: bool
    pv_charging_active: bool


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


class EMSCoordinator(DataUpdateCoordinator[EMSData]):
    """Coordinator that orchestrates real-time EMS control.

    Chains to BatteryScheduleCoordinator and re-evaluates on schedule updates.
    Reads real-time L-current sensor and reacts to fuse-critical state changes.
    Sends EMS mode commands via hass.services.async_call and verifies them.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        battery_coordinator: BatteryScheduleCoordinator,
    ) -> None:
        """Initialize the EMS coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            battery_coordinator: The BatteryScheduleCoordinator to chain to.
        """
        super().__init__(
            hass,
            _LOGGER,
            name="Energy Manager EMS",
            config_entry=entry,
            update_interval=timedelta(seconds=EMS_UPDATE_INTERVAL_SECONDS),
            always_update=False,
        )
        self._battery_coordinator = battery_coordinator
        self._fuse_rating_amps: float = float(
            entry.options.get(CONF_FUSE_RATING, DEFAULT_FUSE_RATING)
        )
        self._ems_select_entity: str = entry.options.get(
            CONF_EMS_SELECT_ENTITY, ""
        )
        self._charge_limit_entity: str = entry.options.get(
            CONF_CHARGE_LIMIT_ENTITY, ""
        )
        self._discharge_limit_entity: str = entry.options.get(
            CONF_DISCHARGE_LIMIT_ENTITY, ""
        )
        self._grid_power_entity: str = entry.options.get(
            CONF_GRID_POWER_ENTITY, ""
        )
        self._grid_phase_a_entity: str = entry.options.get(
            CONF_GRID_PHASE_A_ENTITY, ""
        )
        self._grid_phase_b_entity: str = entry.options.get(
            CONF_GRID_PHASE_B_ENTITY, ""
        )
        self._grid_phase_c_entity: str = entry.options.get(
            CONF_GRID_PHASE_C_ENTITY, ""
        )
        self._pv_power_entity: str = entry.options.get(
            CONF_PV_POWER_ENTITY, ""
        )
        self._soc_entity: str = entry.options.get(CONF_SOC_ENTITY, "")
        self._charger_status_entity: str = entry.options.get(
            CONF_CHARGER_STATUS_ENTITY, ""
        )

        # PV hysteresis tracker for oscillation prevention
        self._pv_tracker = PVHysteresisTracker()

        # Change detection for command deduplication
        self._last_sent_mode: str | None = None
        self._last_charge_limit: float | None = None

        # Command verification tracking
        self._pending_verification: dict | None = None
        self._verification_attempts: int = 0

    async def _async_setup(self) -> None:
        """Register listeners for coordinator chaining and fuse-critical events.

        Called once during async_config_entry_first_refresh.
        """
        # Chain to BatteryScheduleCoordinator: re-evaluate when schedule updates
        unsub_battery = self._battery_coordinator.async_add_listener(
            self._handle_schedule_update
        )
        self.config_entry.async_on_unload(lambda: unsub_battery())

        # Event-driven: react immediately to grid power changes (fuse-critical)
        phase_entities = [
            e for e in [
                self._grid_phase_a_entity,
                self._grid_phase_b_entity,
                self._grid_phase_c_entity,
            ] if e
        ]
        if phase_entities:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass,
                    phase_entities,
                    self._handle_fuse_update,
                )
            )
        elif self._grid_power_entity:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass,
                    [self._grid_power_entity],
                    self._handle_fuse_update,
                )
            )
        else:
            _LOGGER.warning(
                "No grid power entities configured -- fuse headroom will assume 0A load. "
                "Configure per-phase or total grid power sensors in the EMS settings for dynamic fuse protection."
            )

        # Event-driven: react immediately to charger status changes
        if self._charger_status_entity:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass,
                    [self._charger_status_entity],
                    self._handle_fuse_update,
                )
            )

    @callback
    def _handle_schedule_update(self) -> None:
        """Handle BatteryScheduleCoordinator data updates via coordinator chaining."""
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_fuse_update(self, event) -> None:
        """Handle L-current or charger status state changes for immediate response."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> EMSData:
        """Read inputs, compute EMS state, send commands, and return EMSData.

        Returns:
            EMSData with the current EMS state and control information.
        """
        # 1. Read schedule data from the chained BatteryScheduleCoordinator
        schedule_data = self._battery_coordinator.data
        if schedule_data is None:
            return EMSData(
                current_mode="unknown",
                target_mode="unknown",
                charge_limit_kw=0.0,
                fuse_headroom_amps=0.0,
                override_reason=None,
                command_verified=True,
                last_command_time=None,
                car_override_active=False,
                pv_charging_active=False,
            )

        # 2. Read real-time sensor values
        l_current = self._read_grid_current_amps()
        pv_power_w = self._read_float_state(self._pv_power_entity, 0.0)
        battery_soc = self._read_float_state(self._soc_entity, 50.0)

        # 3. Determine car state
        car_plugged_in = self._is_car_plugged_in()
        # Simplified for Phase 3: car_scheduled = EV module enabled
        car_scheduled = self.config_entry.options.get(CONF_EV_ENABLED, False)

        # 4. Update PV hysteresis tracker
        pv_active = self._pv_tracker.update(pv_power_w)

        # 5. Call pure module for calculations
        max_charge_power_kw = DEFAULT_MAX_CHARGE_POWER_KW
        result = compute_ems_state(
            target_ems_mode=schedule_data.target_ems_mode,
            current_l_amps=l_current,
            fuse_rating_amps=self._fuse_rating_amps,
            max_charge_power_kw=max_charge_power_kw,
            battery_soc_pct=battery_soc,
            car_scheduled=car_scheduled,
            car_plugged_in=car_plugged_in,
            pv_power_w=pv_power_w,
            pv_hysteresis_active=pv_active,
            max_soc_pct=DEFAULT_MAX_SOC_PCT,
            safety_buffer_amps=DEFAULT_SAFETY_BUFFER_AMPS,
        )

        # 6. Determine if mode or limit changed
        mode_changed = result.target_mode != self._last_sent_mode
        limit_changed = result.charge_limit_kw != self._last_charge_limit

        # 7. Safe command ordering (Research Pitfall 3)
        if mode_changed or limit_changed:
            if result.target_mode == "command_charging":
                # Switching TO command_charging: send limit FIRST, then mode
                if limit_changed:
                    await self._send_charge_limit(result.charge_limit_kw)
                if mode_changed:
                    await self._send_ems_mode(result.target_mode)
            else:
                # Switching FROM command_charging (or between non-charge modes):
                # send mode FIRST, then zero limit
                if mode_changed:
                    await self._send_ems_mode(result.target_mode)
                if limit_changed:
                    await self._send_charge_limit(result.charge_limit_kw)

        # 8. Schedule verification if mode changed
        if mode_changed and self._ems_select_entity:
            mapped_option = EMS_MODE_MAP.get(result.target_mode)
            if mapped_option:
                self._schedule_verification(
                    self._ems_select_entity, mapped_option
                )

        # 9. Update change detection state
        self._last_sent_mode = result.target_mode
        self._last_charge_limit = result.charge_limit_kw

        # 10. Check pending verification
        command_verified = self._check_verification()

        # 11. Return EMSData
        return EMSData(
            current_mode=result.target_mode,
            target_mode=schedule_data.target_ems_mode,
            charge_limit_kw=result.charge_limit_kw,
            fuse_headroom_amps=result.fuse_headroom_amps,
            override_reason=result.override_reason,
            command_verified=command_verified,
            last_command_time=dt_util.utcnow() if mode_changed else None,
            car_override_active=result.override_reason == "car_charging_priority",
            pv_charging_active=result.override_reason == "pv_opportunistic",
        )

    def _read_float_state(self, entity_id: str, default: float) -> float:
        """Read a sensor state and return as float, with safe fallback.

        Args:
            entity_id: The entity ID to read.
            default: Default value if entity is unavailable or unparseable.

        Returns:
            Float value of the entity state, or default.
        """
        if not entity_id:
            return default

        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return default

        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _read_grid_current_amps(self) -> float:
        """Read per-phase grid power and return worst-case phase current in amps.

        If per-phase sensors are configured, reads each phase's power and converts
        to amps independently: I = abs(P_phase) / V_phase. Returns the MAX across
        all three phases (worst-case for fuse protection).

        Falls back to total-power balanced-load estimate if per-phase sensors are
        not configured.

        Returns:
            Highest phase current in amps, or 0.0 if unavailable.
        """
        phase_entities = [
            self._grid_phase_a_entity,
            self._grid_phase_b_entity,
            self._grid_phase_c_entity,
        ]

        if all(phase_entities):
            # Per-phase mode: convert each phase to amps, take worst case
            phase_amps = []
            for entity_id in phase_entities:
                power = self._read_float_state(entity_id, 0.0)
                state = self.hass.states.get(entity_id)
                if state is not None:
                    uom = state.attributes.get("unit_of_measurement", "")
                    if uom == "kW":
                        power = power * 1000.0
                phase_amps.append(abs(power) / 230.0)
            return max(phase_amps) if phase_amps else 0.0

        # Fallback: single total power sensor with balanced-load assumption
        power = self._read_float_state(self._grid_power_entity, 0.0)
        if power == 0.0:
            return 0.0
        state = self.hass.states.get(self._grid_power_entity)
        if state is not None:
            uom = state.attributes.get("unit_of_measurement", "")
            if uom == "kW":
                power = power * 1000.0
        return abs(power) / (3.0 * 230.0)

    def _is_car_plugged_in(self) -> bool:
        """Check if a car is currently plugged in via charger status.

        Uses charger-side detection (Easee) for fast response rather than
        car-side sensors which poll infrequently.

        Returns:
            True if charger reports a car is connected.
        """
        if not self._charger_status_entity:
            return False

        state = self.hass.states.get(self._charger_status_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return False

        car_connected_states = (
            "awaiting_start",
            "charging",
            "ready_to_charge",
            "car_connected",
        )
        return state.state.lower() in car_connected_states

    async def _send_ems_mode(self, mode: str) -> bool:
        """Send EMS mode change to SigenStor via HA service call.

        Args:
            mode: Internal EMS mode string (e.g., "command_charging").

        Returns:
            True if command was sent successfully, False otherwise.
        """
        if not self._ems_select_entity:
            _LOGGER.debug("EMS select entity not configured, skipping mode command")
            return False

        # Check entity availability (Pitfall 1)
        state = self.hass.states.get(self._ems_select_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.warning(
                "EMS entity %s is unavailable, skipping command",
                self._ems_select_entity,
            )
            return False

        # Map internal mode to SigenStor option string
        option = EMS_MODE_MAP.get(mode)
        if option is None:
            _LOGGER.error("Unknown EMS mode: %s", mode)
            return False

        # Validate option is available on the entity
        entity_options = state.attributes.get("options")
        if entity_options and option not in entity_options:
            _LOGGER.error(
                "EMS option '%s' not in entity options %s for %s",
                option,
                entity_options,
                self._ems_select_entity,
            )
            return False

        _LOGGER.info("Setting EMS mode to %s (%s)", mode, option)
        await self.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": self._ems_select_entity, "option": option},
            blocking=True,
        )
        return True

    async def _send_charge_limit(self, limit_kw: float) -> bool:
        """Send charging limit to SigenStor via HA service call.

        Args:
            limit_kw: Charging limit in kW.

        Returns:
            True if command was sent successfully, False otherwise.
        """
        if not self._charge_limit_entity:
            _LOGGER.debug(
                "Charge limit entity not configured, skipping limit command"
            )
            return False

        # Check entity availability
        state = self.hass.states.get(self._charge_limit_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.warning(
                "Charge limit entity %s is unavailable",
                self._charge_limit_entity,
            )
            return False

        # Clamp to safe range before sending (Pitfall 2)
        clamped = max(0.0, min(limit_kw, MAX_CHARGE_LIMIT_KW))
        _LOGGER.info(
            "Setting charge limit to %.1f kW (requested %.1f)",
            clamped,
            limit_kw,
        )
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": self._charge_limit_entity, "value": clamped},
            blocking=True,
        )
        return True

    def _schedule_verification(
        self, entity_id: str, expected: str
    ) -> None:
        """Store pending verification for a command that was just sent.

        Args:
            entity_id: Entity to verify.
            expected: Expected state value after command.
        """
        self._pending_verification = {
            "entity_id": entity_id,
            "expected": expected,
            "sent_at": dt_util.utcnow(),
        }
        self._verification_attempts = 0

    def _check_verification(self) -> bool:
        """Check if a pending command verification has succeeded.

        Returns:
            True if no pending verification or verification passed.
            False if verification failed after timeout.
        """
        if self._pending_verification is None:
            return True

        entity_id = self._pending_verification["entity_id"]
        expected = self._pending_verification["expected"]
        sent_at = self._pending_verification["sent_at"]

        # Read current entity state
        state = self.hass.states.get(entity_id)
        if state is not None and state.state == expected:
            _LOGGER.debug(
                "Command verified: %s = %s", entity_id, expected
            )
            self._pending_verification = None
            return True

        # Check timeout (60 seconds = 2 update cycles at 30s)
        elapsed = (dt_util.utcnow() - sent_at).total_seconds()
        if elapsed > 60:
            _LOGGER.warning(
                "Command verification failed: %s expected '%s' but got '%s' "
                "after %.0fs",
                entity_id,
                expected,
                state.state if state else "None",
                elapsed,
            )
            self._pending_verification = None
            return False

        # Still waiting
        return True


@dataclass(frozen=True, slots=True)
class CarChargingData:
    """Output of the car charging coordinator.

    Attributes:
        current_action: Current action for this car (charge/idle/solar_charge).
        schedule: List of CarScheduleSlot objects from the pure scheduler.
        charging_slot_count: Number of charge/solar_charge slots.
        energy_needed_kwh: Energy needed to reach target SOC.
        hours_needed: Hours of charging needed at max charge power.
        is_preliminary: True when tomorrow's prices not yet available.
        car_name: Display name of the car.
        current_soc: Current state of charge percentage.
        target_soc: Target state of charge percentage.
        last_calculated: UTC timestamp of last calculation.
    """

    current_action: str
    schedule: list
    charging_slot_count: int
    energy_needed_kwh: float
    hours_needed: float
    is_preliminary: bool
    car_name: str
    current_soc: float
    target_soc: float
    last_calculated: datetime


class CarChargingCoordinator(DataUpdateCoordinator[CarChargingData]):
    """Coordinator for a single car's charging schedule.

    One instance per car subentry. Chains to PriceCoordinator and recalculates
    on price updates. Reads car SOC from HA entity and converts departure time
    to UTC. Calls the pure car_charging_scheduler module for slot selection.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        price_coordinator: PriceCoordinator,
    ) -> None:
        """Initialize the car charging coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            subentry: The car subentry with car-specific configuration.
            price_coordinator: The PriceCoordinator to chain to.
        """
        car_name = subentry.data.get(CONF_CAR_NAME, "Unknown Car")
        super().__init__(
            hass,
            _LOGGER,
            name=f"Energy Manager Car: {car_name}",
            config_entry=entry,
            update_interval=timedelta(
                minutes=CAR_SCHEDULE_UPDATE_INTERVAL_MINUTES
            ),
            always_update=False,
        )
        self._subentry = subentry
        self._price_coordinator = price_coordinator

        # Car-specific configuration from subentry
        self._car_name: str = car_name
        self._battery_capacity_kwh: float = float(
            subentry.data.get(CONF_BATTERY_CAPACITY, 60.0)
        )
        self._battery_level_entity: str = subentry.data.get(
            CONF_BATTERY_LEVEL_ENTITY, ""
        )
        self._home_plugged_entity: str = subentry.data.get(
            CONF_HOME_PLUGGED_ENTITY, ""
        )

        # Charger status entity from main entry options (shared across all cars)
        self._charger_status_entity: str = entry.options.get(
            CONF_CHARGER_STATUS_ENTITY, ""
        )

        # Mutable attributes -- updated by entity instances after setup
        self.departure_time: time = time(7, 0)  # Default 07:00
        self.target_soc: float = DEFAULT_TARGET_SOC_PCT
        self.max_charge_power_kw: float = DEFAULT_CAR_MAX_CHARGE_POWER_KW

        # SOC staleness tracking for fallback detection
        self._soc_last_updated: datetime | None = None

    async def _async_setup(self) -> None:
        """Register listeners for coordinator chaining and SOC state changes.

        Called once during async_config_entry_first_refresh.
        """
        # Chain to PriceCoordinator: recalculate when prices update
        unsub_price = self._price_coordinator.async_add_listener(
            self._handle_price_update
        )
        self.config_entry.async_on_unload(lambda: unsub_price())

        # Listen for battery_level_entity state changes for immediate SOC updates
        if self._battery_level_entity:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass,
                    [self._battery_level_entity],
                    self._handle_soc_update,
                )
            )

    @callback
    def _handle_price_update(self) -> None:
        """Handle PriceCoordinator data updates via coordinator chaining."""
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_soc_update(self, event) -> None:
        """Handle car SOC entity state changes for immediate recalculation."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> CarChargingData:
        """Fetch inputs, run pure scheduler, and return CarChargingData.

        Returns:
            CarChargingData with the current schedule and derived state.

        Raises:
            UpdateFailed: If no price data is available.
        """
        # 1. Read price data from chained PriceCoordinator
        price_data = self._price_coordinator.data
        if price_data is None or not price_data.today:
            raise UpdateFailed(
                "No price data available for car charging schedule calculation"
            )

        # 2. Read car SOC from battery_level_entity
        current_soc = self._read_car_soc()

        # 3. Convert departure_time (local time-of-day) to UTC datetime
        departure_utc = self._departure_to_utc()

        # 4. Check if tomorrow's prices are available
        is_preliminary = len(price_data.tomorrow) == 0

        # 5. Combine today + tomorrow price slots into dicts
        price_slots = [
            {"start": slot.start, "end": slot.end, "price": slot.price}
            for slot in price_data.today + price_data.tomorrow
        ]

        # 6. Detect fallback mode
        fallback_mode = self._detect_fallback_needed()

        # 7. Call pure scheduler with solar_surplus_available=False
        # Phase 4 always passes False; Phase 5 wires actual PV detection
        result: CarScheduleResult = build_car_charging_schedule(
            price_slots=price_slots,
            departure_time_utc=departure_utc,
            current_soc_pct=current_soc,
            target_soc_pct=self.target_soc,
            battery_capacity_kwh=self._battery_capacity_kwh,
            max_charge_power_kw=self.max_charge_power_kw,
            now=dt_util.utcnow(),
            fallback_mode=fallback_mode,
            is_preliminary=is_preliminary,
            solar_surplus_available=False,
        )

        # 8. Return CarChargingData
        return CarChargingData(
            current_action=result.current_action,
            schedule=result.schedule,
            charging_slot_count=result.charging_slot_count,
            energy_needed_kwh=result.energy_needed_kwh,
            hours_needed=result.hours_needed,
            is_preliminary=result.is_preliminary,
            car_name=self._car_name,
            current_soc=current_soc,
            target_soc=self.target_soc,
            last_calculated=dt_util.utcnow(),
        )

    def _read_car_soc(self) -> float:
        """Read the car's battery level from HA entity state.

        Returns:
            SOC as a percentage (0-100). Defaults to 50.0 if unavailable.
        """
        if not self._battery_level_entity:
            return 50.0

        state = self.hass.states.get(self._battery_level_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return 50.0

        try:
            soc = float(state.state)
            # Update staleness tracker on successful read
            self._soc_last_updated = dt_util.utcnow()
            return soc
        except (ValueError, TypeError):
            return 50.0

    def _departure_to_utc(self) -> datetime:
        """Convert departure_time (local time-of-day) to UTC datetime.

        If departure time has already passed today, assumes tomorrow.

        Returns:
            UTC-aware datetime for the next occurrence of departure_time.
        """
        local_now = dt_util.now()
        local_departure = local_now.replace(
            hour=self.departure_time.hour,
            minute=self.departure_time.minute,
            second=0,
            microsecond=0,
        )

        # If departure is in the past, roll to tomorrow
        if local_departure <= local_now:
            local_departure += timedelta(days=1)

        return dt_util.as_utc(local_departure)

    def _detect_fallback_needed(self) -> bool:
        """Detect if fallback charging mode is needed (EV-08).

        Returns True when the Easee charger reports a car is connected but
        this coordinator's car SOC has not updated recently. This indicates
        an unrecognized vehicle that should receive off-peak charging.

        Returns:
            True if fallback mode should be used.
        """
        # 1. Read charger status entity
        if not self._charger_status_entity:
            return False

        state = self.hass.states.get(self._charger_status_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return False

        # 2. Check if charger reports a connected car
        car_connected_states = (
            "awaiting_start",
            "charging",
            "ready_to_charge",
            "car_connected",
        )
        if state.state.lower() not in car_connected_states:
            return False

        # 3. Never received a SOC reading -> likely unrecognized car
        if self._soc_last_updated is None:
            return True

        # 4. SOC reading is stale -> likely unrecognized car
        elapsed = (dt_util.utcnow() - self._soc_last_updated).total_seconds()
        if elapsed > FALLBACK_STALE_THRESHOLD_MINUTES * 60:
            return True

        # 5. Recognized car with recent SOC
        return False


@dataclass
class EnergyManagerData:
    """Runtime data stored on the config entry.

    Provides typed access to coordinators and module state.
    """

    price_coordinator: PriceCoordinator
    battery_coordinator: BatteryScheduleCoordinator | None = None
    ems_coordinator: "EMSCoordinator | None" = None
    modules_enabled: dict[str, bool] = field(default_factory=dict)


EnergyManagerConfigEntry = ConfigEntry[EnergyManagerData]

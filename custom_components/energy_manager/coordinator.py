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

from .battery_scheduler import (
    BatteryScheduleResult,
    build_battery_schedule,
    compute_effective_discharge_threshold,
)
from .car_charging_scheduler import CarScheduleResult, build_car_charging_schedule
from .charger_state_machine import (
    CarDemand,
    ChargerCommand,
    ChargerController,
    ChargerInputs,
    compute_solar_surplus_kw,
)
from .const import (
    BATTERY_SCHEDULE_UPDATE_INTERVAL_MINUTES,
    CAR_SCHEDULE_UPDATE_INTERVAL_MINUTES,
    CONF_AMP_DECREASE_DELAY,
    CONF_AMP_INCREASE_DELAY,
    CONF_ASSUMED_LOAD_AMPS,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_LEVEL_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_GATE_PCT,
    CONF_CAR_NAME,
    CONF_CHARGE_BUFFER_PCT,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGER_CONNECTED_ENTITY,
    CONF_CHARGER_DEVICE_ID,
    CONF_CHARGER_POWER_ENTITY,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMERGENCY_MARGIN_AMPS,
    CONF_EMS_SELECT_ENTITY,
    CONF_ESS_INCREASE_DELAY,
    CONF_ESTIMATED_CHARGE_POWER_KW,
    CONF_EXCLUDED_POWER_ENTITIES,
    CONF_FORECAST_SOLAR_ENTITY,
    CONF_FUSE_RATING_AMPS,
    CONF_FUSE_SAFETY_BUFFER_AMPS,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_LOCATION_ENTITY,
    CONF_MAX_CHARGE_AMPS,
    CONF_MAX_ESS_CHARGE_AMPS,
    CONF_MAX_GRID_CHARGE_POWER_KW,
    CONF_MIN_CHARGE_AMPS,
    CONF_NORDPOOL_SENSOR,
    CONF_NORDPOOL_TYPE,
    CONF_NOTIFY_SERVICE,
    CONF_PEAK_GAP_HOURS,
    CONF_PHASE_CAPABILITY,
    CONF_PHASE_SWITCH_THRESHOLD_KW,
    CONF_PRODUCTION_FACTOR,
    CONF_PV_POWER_ENTITY,
    CONF_SENSOR_FAIL_BEHAVIOR,
    CONF_SOC_ENTITY,
    CONF_SOLAR_ACTIVATION_DELAY,
    CONF_SOLAR_DEACTIVATION_DELAY,
    CONF_SOLAR_START_THRESHOLD_KW,
    DEFAULT_AMP_DECREASE_DELAY_SECONDS,
    DEFAULT_AMP_INCREASE_DELAY_SECONDS,
    DEFAULT_ASSUMED_LOAD_AMPS,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CYCLE_COST,
    DEFAULT_BATTERY_SOC_GATE_PCT,
    DEFAULT_CAR_MAX_CHARGE_POWER_KW,
    DEFAULT_CAR_SOLAR_TARGET_SOC_PCT,
    DEFAULT_CHARGE_BUFFER_PCT,
    DEFAULT_CHARGE_THRESHOLD,
    DEFAULT_CHARGER_CONVERSION_FACTOR_1PHASE,
    DEFAULT_CHARGER_CONVERSION_FACTOR_2PHASE,
    DEFAULT_CHARGER_CONVERSION_FACTOR_3PHASE,
    DEFAULT_COMMAND_STUCK_TIMEOUT_SECONDS,
    DEFAULT_DISCHARGE_THRESHOLD,
    DEFAULT_ELECTRICITY_COMPANY_FEE,
    DEFAULT_EMERGENCY_MARGIN_AMPS,
    DEFAULT_ESS_INCREASE_DELAY_SECONDS,
    DEFAULT_ESTIMATED_CHARGE_POWER_KW,
    DEFAULT_FUSE_RATING_AMPS,
    DEFAULT_GRID_POWER_SAFETY_BUFFER_KW,
    DEFAULT_GRID_TRANSFER_FEE,
    DEFAULT_MAX_CHARGE_AMPS,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DEFAULT_MAX_ESS_CHARGE_AMPS,
    DEFAULT_MAX_GRID_CHARGE_POWER_KW,
    DEFAULT_MAX_SOC_PCT,
    DEFAULT_MEAN_CONSUMPTION_KW,
    DEFAULT_MIN_CHARGE_AMPS,
    DEFAULT_MIN_SOC_PCT,
    DEFAULT_PEAK_GAP_HOURS,
    DEFAULT_PHASE_CAPABILITY,
    DEFAULT_PHASE_SEQUENCE_STEP_TIMEOUT_SECONDS,
    DEFAULT_PHASE_SWITCH_THRESHOLD_KW,
    DEFAULT_PRODUCTION_FACTOR,
    DEFAULT_SAFETY_BUFFER_AMPS,
    DEFAULT_SENSOR_FAIL_BEHAVIOR,
    DEFAULT_SOC_ROUND_UP,
    DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS,
    DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS,
    DEFAULT_SOLAR_SAFETY_BUFFER_KW,
    DEFAULT_SOLAR_START_THRESHOLD_KW,
    DEFAULT_TARGET_SOC_PCT,
    EASEE_UPDATE_INTERVAL_SECONDS,
    EMS_MODE_MAP,
    EMS_UPDATE_INTERVAL_SECONDS,
    FALLBACK_STALE_THRESHOLD_MINUTES,
    MAX_CHARGE_LIMIT_KW,
    MEAN_CONSUMPTION_WINDOW_HOURS,
    MIN_CONSUMPTION_SAMPLE_INTERVAL_MINUTES,
    PRICE_UPDATE_INTERVAL_MINUTES,
    SENSOR_FAIL_BEHAVIOR_ASSUME_LOAD,
    WATTS_TO_AMPS_3PHASE_DIVISOR,
)
from .ems_controller import (
    ESSLimitRateLimiter,
    PVHysteresisTracker,
    build_command_decision,
    car_demands_priority_charging,
    compute_available_ess_amps,
    compute_ems_state,
    resolve_current_sensor_fallback,
    worst_case_signed_amps,
)
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
        mean_consumption_kw: BATT-15 rolling-average house consumption (kW)
            used to size the schedule's energy needs.
        consumption_sample_count: Number of samples currently in the BATT-15
            rolling window (see BatteryScheduleCoordinator._consumption_samples).
        discharge_allowed: Whether the scheduler currently permits battery
            discharge.
        discharge_gate_reason: Human-readable reason discharge is blocked,
            or "" when discharge is allowed.
        reserved_energy_kwh: Energy the scheduler is holding back for a
            later, more expensive peak.
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
    mean_consumption_kw: float
    consumption_sample_count: int
    discharge_allowed: bool = True
    discharge_gate_reason: str = ""
    reserved_energy_kwh: float = 0.0


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
        dry_run: True when the master "Device control" switch is OFF
            (observe-only) -- commands are computed but not sent (CORE-14).
        last_suppressed_command: Human-readable description of the most
            recently suppressed command, or None if none has been suppressed.
        discharge_allowed: Whether the scheduler currently permits battery
            discharge.
        discharge_gate_reason: Human-readable reason discharge is blocked,
            or "" when discharge is allowed.
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
    dry_run: bool
    last_suppressed_command: str | None
    discharge_allowed: bool = True
    discharge_gate_reason: str = ""


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

        # BATT-14 economics -- updated by NumberEntity instances after setup
        self.battery_cycle_cost: float = DEFAULT_BATTERY_CYCLE_COST
        self.grid_transfer_fee: float = DEFAULT_GRID_TRANSFER_FEE
        self.electricity_company_fee: float = DEFAULT_ELECTRICITY_COMPANY_FEE

        # BATT-15 in-memory rolling house-consumption samples (~48h window)
        self._consumption_samples: list[tuple[datetime, float]] = []

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

        # Listen for Forecast.Solar entity state changes (BATT-13: one or
        # more entities -- _get_forecast_solar_entities() tolerates the
        # pre-multi-forecast plain-string config shape too)
        solar_entities = self._get_forecast_solar_entities()
        if solar_entities:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, solar_entities, self._handle_external_update
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

        # 4. Sum remaining-today production across all configured
        #    Forecast.Solar sensors (BATT-13)
        solar_forecast_remaining_wh = self._get_solar_forecast_remaining_wh()

        # 5. Read battery capacity and BATT-15 tuning options from entry options
        battery_capacity_kwh = self.config_entry.options.get(
            CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH
        )
        charge_buffer_pct = self.config_entry.options.get(
            CONF_CHARGE_BUFFER_PCT, DEFAULT_CHARGE_BUFFER_PCT
        )
        production_factor = self.config_entry.options.get(
            CONF_PRODUCTION_FACTOR, DEFAULT_PRODUCTION_FACTOR
        )
        estimated_charge_power_kw = self.config_entry.options.get(
            CONF_ESTIMATED_CHARGE_POWER_KW, DEFAULT_ESTIMATED_CHARGE_POWER_KW
        )
        peak_gap_hours = self.config_entry.options.get(
            CONF_PEAK_GAP_HOURS, DEFAULT_PEAK_GAP_HOURS
        )

        # 6. Sample house consumption into the rolling average (BATT-15)
        mean_consumption_kw = self._sample_and_get_mean_consumption_kw()
        consumption_sample_count = len(self._consumption_samples)

        # 7. Read dawn/dusk from sun.sun (BATT-15a)
        dawn, dusk = _read_sun_dawn_dusk(self.hass)

        # 8. BATT-14: derive the effective discharge threshold from economics
        #    (overrides self.discharge_threshold when battery_cycle_cost > 0)
        effective_discharge_threshold = compute_effective_discharge_threshold(
            discharge_threshold=self.discharge_threshold,
            battery_cycle_cost=self.battery_cycle_cost,
            grid_transfer_fee=self.grid_transfer_fee,
        )

        # 9. Call the pure scheduler
        result: BatteryScheduleResult = build_battery_schedule(
            price_slots=price_slots,
            charge_threshold=self.charge_threshold,
            discharge_threshold=effective_discharge_threshold,
            max_charge_power_w=self.max_charge_power_w,
            battery_capacity_kwh=battery_capacity_kwh,
            current_soc_pct=current_soc,
            now=dt_util.utcnow(),
            mean_consumption_kw=mean_consumption_kw,
            estimated_charge_power_kw=estimated_charge_power_kw,
            charge_buffer_pct=charge_buffer_pct,
            solar_forecast_remaining_wh=solar_forecast_remaining_wh,
            production_factor=production_factor,
            dawn=dawn,
            dusk=dusk,
            peak_gap_hours=peak_gap_hours,
            min_soc_pct=DEFAULT_MIN_SOC_PCT,
            max_soc_pct=DEFAULT_MAX_SOC_PCT,
        )

        # 10. Serialize next slots to dicts
        next_charging = _serialize_slot(result.next_charging_slot)
        next_discharging = _serialize_slot(result.next_discharging_slot)

        # 11. Map current_action to current_state
        state_map = {
            "charge": "grid_charging",
            "solar_charge": "solar_charging",
            "discharge": "discharging",
            "idle": "idle",
        }
        current_state = state_map.get(result.current_action, result.current_action)

        # 12. Return BatteryScheduleData
        return BatteryScheduleData(
            current_state=current_state,
            schedule=result.schedule,
            next_charging_slot=next_charging,
            next_discharging_slot=next_discharging,
            charging_slot_count=result.charging_slot_count,
            discharging_slot_count=result.discharging_slot_count,
            target_ems_mode=result.target_ems_mode,
            last_calculated=dt_util.utcnow(),
            solar_forecast_used=solar_forecast_remaining_wh is not None,
            mean_consumption_kw=mean_consumption_kw,
            consumption_sample_count=consumption_sample_count,
            discharge_allowed=result.discharge_allowed,
            discharge_gate_reason=result.discharge_gate_reason,
            reserved_energy_kwh=result.reserved_energy_kwh,
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

    def _get_forecast_solar_entities(self) -> list[str]:
        """Return the configured Forecast.Solar entity IDs as a list (BATT-13).

        Tolerates a plain string (the pre-multi-forecast config shape, e.g.
        an existing dev config entry) by wrapping it in a single-item list.

        Returns:
            List of entity IDs, possibly empty.
        """
        raw = self.config_entry.options.get(CONF_FORECAST_SOLAR_ENTITY, [])
        if isinstance(raw, str):
            return [raw] if raw else []
        if isinstance(raw, list):
            return [entity_id for entity_id in raw if entity_id]
        return []

    def _get_solar_forecast_remaining_wh(self) -> float | None:
        """Sum remaining-today production across all configured
        Forecast.Solar sensors (BATT-13).

        Returns:
            Total remaining production in Wh, or None if no sensors are
            configured or none currently have a valid numeric state.
        """
        entity_ids = self._get_forecast_solar_entities()
        if not entity_ids:
            return None

        readings: list[tuple[float, str]] = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                continue
            try:
                value = float(state.state)
            except (ValueError, TypeError):
                continue
            readings.append((value, state.attributes.get("unit_of_measurement", "")))

        if not readings:
            return None

        return sum_solar_forecast_wh(readings)

    def _sample_and_get_mean_consumption_kw(self) -> float:
        """Sample house consumption and return the rolling-average mean (BATT-15).

        Appends the current reading (if available) to an in-memory sample
        list, prunes samples older than the ~48h window, and averages what
        remains. Falls back to the instantaneous reading when the window
        has no samples yet, and to a conservative default when no reading
        is available at all.

        Returns:
            Mean house consumption in kW.
        """
        house_consumption_entity = self.config_entry.options.get(
            CONF_HOUSE_CONSUMPTION_ENTITY
        )
        now = dt_util.utcnow()

        current_kw: float | None = None
        if house_consumption_entity and _entity_has_value(
            self.hass, house_consumption_entity
        ):
            current_kw = _read_power_kw(self.hass, house_consumption_entity)
            last_sample_at = (
                self._consumption_samples[-1][0]
                if self._consumption_samples
                else None
            )
            if _should_sample_consumption(
                last_sample_at, now, MIN_CONSUMPTION_SAMPLE_INTERVAL_MINUTES
            ):
                self._consumption_samples.append((now, current_kw))

        self._consumption_samples = _prune_samples(
            self._consumption_samples, now, MEAN_CONSUMPTION_WINDOW_HOURS
        )

        if self._consumption_samples:
            return sum(v for _, v in self._consumption_samples) / len(
                self._consumption_samples
            )
        if current_kw is not None:
            return current_kw
        return DEFAULT_MEAN_CONSUMPTION_KW


def sum_solar_forecast_wh(readings: list[tuple[float, str]]) -> float:
    """Sum Forecast.Solar sensor readings into a single Wh total (BATT-13).

    Converts any kWh-unit reading to Wh before summing so multiple
    Forecast.Solar sensors (e.g. east + west arrays) can be combined
    regardless of each sensor's configured unit. Pure and HA-free so it can
    be unit tested directly.

    Args:
        readings: List of (value, unit_of_measurement) tuples, one per
            configured Forecast.Solar sensor with a valid numeric state.

    Returns:
        Total production estimate in Wh.
    """
    total = 0.0
    for value, uom in readings:
        if uom.lower() == "kwh":
            value *= 1000.0
        total += value
    return total


def _prune_samples(
    samples: list[tuple[datetime, float]], now: datetime, window_hours: float
) -> list[tuple[datetime, float]]:
    """Drop samples older than window_hours relative to now.

    Pure and HA-free so it can be unit tested directly.
    """
    cutoff = now - timedelta(hours=window_hours)
    return [(t, v) for t, v in samples if t >= cutoff]


def _should_sample_consumption(
    last_sample_at: datetime | None, now: datetime, min_interval_minutes: float
) -> bool:
    """Return True if enough time has passed to record a new consumption sample.

    Refreshes are event-driven (SOC/price/Forecast.Solar updates), not
    fixed-cadence, so without this gate the rolling mean would be skewed
    toward however often those entities happen to change (a chatty sensor
    dominates the average) and the sample list could grow far faster than
    _prune_samples()'s time-window pruning intends. Pure and HA-free so it
    can be unit tested directly.
    """
    if last_sample_at is None:
        return True
    return (now - last_sample_at).total_seconds() >= min_interval_minutes * 60


def _read_sun_dawn_dusk(hass: HomeAssistant) -> tuple[datetime | None, datetime | None]:
    """Read sun.sun's next_dawn/next_dusk attributes (BATT-15a).

    These are the NEXT occurrence of each event (see
    battery_scheduler._normalize_daylight_window for how the pure scheduler
    resolves that into a single daylight window).

    Args:
        hass: Home Assistant instance.

    Returns:
        (dawn, dusk) tuple of UTC-aware datetimes, or (None, None) if the
        sun.sun entity or its attributes are unavailable.
    """
    state = hass.states.get("sun.sun")
    if state is None:
        return None, None

    dawn = _parse_optional_iso_datetime(state.attributes.get("next_dawn"))
    dusk = _parse_optional_iso_datetime(state.attributes.get("next_dusk"))
    return dawn, dusk


def _parse_optional_iso_datetime(value: object) -> datetime | None:
    """Parse an ISO datetime string, returning None on any invalid input."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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


def _read_entity_float(hass: HomeAssistant, entity_id: str, default: float) -> float:
    """Read a sensor state and return as float, with safe fallback.

    Shared by EMSCoordinator and EaseeCoordinator (Phase 5 "shared fuse
    arbiter" -- see 05-RESEARCH.md) so the read logic exists in one place.

    Args:
        hass: Home Assistant instance.
        entity_id: The entity ID to read.
        default: Default value if entity is unavailable or unparseable.

    Returns:
        Float value of the entity state, or default.
    """
    if not entity_id:
        return default

    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return default

    try:
        return float(state.state)
    except (ValueError, TypeError):
        return default


def _entity_has_value(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True if entity_id is configured and has a valid numeric state."""
    if not entity_id:
        return False
    state = hass.states.get(entity_id)
    return state is not None and state.state not in ("unavailable", "unknown")


def _read_control_enabled(config_entry: ConfigEntry) -> bool:
    """Return the master "Device control" switch state (CORE-14).

    Shared by EMSCoordinator and EaseeCoordinator -- both gate outgoing
    device commands behind this single fail-safe read of
    runtime_data.control_enabled (defaults to False/observe-only if
    runtime_data isn't set yet or the switch hasn't initialized).

    Args:
        config_entry: The config entry to read runtime_data from.

    Returns:
        True if device control is enabled, False (observe-only) otherwise.
    """
    runtime_data = getattr(config_entry, "runtime_data", None)
    return bool(getattr(runtime_data, "control_enabled", False))


def _read_force_charging(config_entry: ConfigEntry) -> bool:
    """Return the "Force grid charging" switch state (EASE-03).

    Mirrors _read_control_enabled()'s defensive read of runtime_data --
    defaults to False (not forcing) if runtime_data isn't set yet or the
    switch hasn't initialized.

    Args:
        config_entry: The config entry to read runtime_data from.

    Returns:
        True if forced charging is requested, False otherwise.
    """
    runtime_data = getattr(config_entry, "runtime_data", None)
    return bool(getattr(runtime_data, "force_charging", False))


def _read_power_kw(hass: HomeAssistant, entity_id: str) -> float:
    """Read a power sensor and return its value in kW, defaulting to 0.0.

    Unit handling mirrors FuseSensorReader._read_signed_power_amps(): the
    reading is assumed to be in watts unless the entity's
    unit_of_measurement is exactly "kW". Used for the EV-09 solar-surplus
    inputs (pv/house consumption/battery/excluded power entities), which are
    plain sensors (unlike the Easee charger power entity, which reports kW
    natively -- see EaseeCoordinator._read_charger_power_kw()).

    Args:
        hass: Home Assistant instance.
        entity_id: The entity ID to read.

    Returns:
        The sensor's value in kW, or 0.0 if unavailable, unconfigured, or
        unparseable.
    """
    power = _read_entity_float(hass, entity_id, 0.0)
    state = hass.states.get(entity_id) if entity_id else None
    if state is not None:
        uom = state.attributes.get("unit_of_measurement", "")
        if uom == "kW":
            return power
    return power / 1000.0


def _read_net_house_consumption_kw(
    hass: HomeAssistant,
    house_consumption_entity: str,
    excluded_power_entities: list[str],
) -> float:
    """Read house consumption minus configured excluded-power entities (EMS-13).

    Shared by EaseeCoordinator._read_solar_surplus_kw() (EV-09's live
    solar-surplus formula) and the "House load" diagnostic sensor (CORE-11)
    so the filtered-consumption reads exist in one place.

    Args:
        hass: Home Assistant instance.
        house_consumption_entity: Total house power consumption entity ID.
        excluded_power_entities: Power entities to subtract (e.g. a
            separately-managed water heater).

    Returns:
        Net house consumption in kW, or 0.0 if house_consumption_entity is
        not configured.
    """
    if not house_consumption_entity:
        return 0.0

    house_consumption_kw = _read_power_kw(hass, house_consumption_entity)
    excluded_power_kw = sum(
        _read_power_kw(hass, entity_id)
        for entity_id in excluded_power_entities
        if entity_id
    )
    return house_consumption_kw - excluded_power_kw


class FuseSensorReader:
    """Shared grid-current sensor read + fallback logic.

    Both EMSCoordinator (battery) and EaseeCoordinator (charger) need the
    identical signed worst-case phase current reading and sensor-fail
    fallback behavior -- extracted here so the formula and fallback policy
    exist in exactly one place (Phase 5 "shared fuse arbiter", see
    05-RESEARCH.md: "one module, two views, no duplicated formulas"). Each
    coordinator owns its own instance (independent rate-limited warning
    state); the read/fallback logic itself is identical.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        grid_phase_a_entity: str,
        grid_phase_b_entity: str,
        grid_phase_c_entity: str,
        grid_power_entity: str,
        sensor_fail_behavior: str,
        assumed_load_amps: float,
    ) -> None:
        """Initialize the reader with its sensor configuration.

        Args:
            hass: Home Assistant instance.
            grid_phase_a_entity: Per-phase grid power sensor for phase A.
            grid_phase_b_entity: Per-phase grid power sensor for phase B.
            grid_phase_c_entity: Per-phase grid power sensor for phase C.
            grid_power_entity: Fallback total grid power sensor.
            sensor_fail_behavior: CONF_SENSOR_FAIL_BEHAVIOR value.
            assumed_load_amps: Amps to assume when fail_behavior is
                "assume_load".
        """
        self._hass = hass
        self._grid_phase_a_entity = grid_phase_a_entity
        self._grid_phase_b_entity = grid_phase_b_entity
        self._grid_phase_c_entity = grid_phase_c_entity
        self._grid_power_entity = grid_power_entity
        self._sensor_fail_behavior = sensor_fail_behavior
        self._assumed_load_amps = assumed_load_amps
        self._sensor_warned = False

    def read_grid_current_amps(self) -> tuple[float, bool]:
        """Read grid power and return (signed worst-case phase current, sensor_blocked).

        Sign convention: positive = import (load on the fuse), negative =
        export (adds headroom). If per-phase sensors are configured and all
        available, returns the worst-case (highest, signed) phase current.
        Falls back to the single total grid power sensor (signed,
        balanced-load estimate) if per-phase sensors are not configured.

        If the required sensors are unavailable, unknown, or unconfigured,
        applies the configured fail-behavior instead of silently assuming
        0A load (see resolve_current_sensor_fallback()).

        Returns:
            Tuple of (current_l_amps, sensor_blocked). sensor_blocked is
            True only when the sensors failed and the fail-behavior is
            "block".
        """
        phase_entities = [
            self._grid_phase_a_entity,
            self._grid_phase_b_entity,
            self._grid_phase_c_entity,
        ]

        if all(phase_entities):
            if all(_entity_has_value(self._hass, e) for e in phase_entities):
                amps = [self._read_signed_power_amps(e, 230.0) for e in phase_entities]
                self._sensor_warned = False
                return worst_case_signed_amps(amps), False
            return self._apply_fallback("grid phase current sensors")

        if self._grid_power_entity:
            if _entity_has_value(self._hass, self._grid_power_entity):
                amps = self._read_signed_power_amps(self._grid_power_entity, 3.0 * 230.0)
                self._sensor_warned = False
                return amps, False
            return self._apply_fallback("grid power sensor")

        return self._apply_fallback("grid power sensor (not configured)")

    def _read_signed_power_amps(self, entity_id: str, divisor: float) -> float:
        """Read a power sensor and convert to signed amps.

        Sign convention: positive = import (load on the fuse), negative =
        export (adds headroom).
        """
        power = _read_entity_float(self._hass, entity_id, 0.0)
        state = self._hass.states.get(entity_id)
        if state is not None:
            uom = state.attributes.get("unit_of_measurement", "")
            if uom == "kW":
                power *= 1000.0
        return power / divisor

    def _apply_fallback(self, sensor_description: str) -> tuple[float, bool]:
        """Apply the configured fail-behavior and log a rate-limited warning."""
        result = resolve_current_sensor_fallback(
            fail_behavior=self._sensor_fail_behavior,
            assumed_load_amps=self._assumed_load_amps,
        )
        if not self._sensor_warned:
            _LOGGER.warning(
                "Fuse protection: %s unavailable -- applying '%s' fallback (%s). "
                "Configure the grid sensors in the EMS options to restore "
                "accurate fuse protection.",
                sensor_description,
                self._sensor_fail_behavior,
                f"assuming {self._assumed_load_amps}A load"
                if self._sensor_fail_behavior == SENSOR_FAIL_BEHAVIOR_ASSUME_LOAD
                else "blocking charge authorization",
            )
            self._sensor_warned = True
        return result.effective_amps, result.force_zero_headroom


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
            entry.options.get(CONF_FUSE_RATING_AMPS, DEFAULT_FUSE_RATING_AMPS)
        )
        self._safety_buffer_amps: float = float(
            entry.options.get(CONF_FUSE_SAFETY_BUFFER_AMPS, DEFAULT_SAFETY_BUFFER_AMPS)
        )
        self._sensor_fail_behavior: str = entry.options.get(
            CONF_SENSOR_FAIL_BEHAVIOR, DEFAULT_SENSOR_FAIL_BEHAVIOR
        )
        self._assumed_load_amps: float = float(
            entry.options.get(CONF_ASSUMED_LOAD_AMPS, DEFAULT_ASSUMED_LOAD_AMPS)
        )
        self._max_ess_charge_amps: float = float(
            entry.options.get(CONF_MAX_ESS_CHARGE_AMPS, DEFAULT_MAX_ESS_CHARGE_AMPS)
        )
        self._ess_increase_delay_seconds: float = float(
            entry.options.get(
                CONF_ESS_INCREASE_DELAY, DEFAULT_ESS_INCREASE_DELAY_SECONDS
            )
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
        self._battery_power_entity: str = entry.options.get(
            CONF_BATTERY_POWER_ENTITY, ""
        )
        self._soc_entity: str = entry.options.get(CONF_SOC_ENTITY, "")
        self._charger_status_entity: str = entry.options.get(
            CONF_CHARGER_STATUS_ENTITY, ""
        )

        # Shared fuse arbiter (Phase 5): identical grid-sensor read/fallback
        # logic reused by EaseeCoordinator -- see FuseSensorReader.
        self._fuse_reader = FuseSensorReader(
            hass=hass,
            grid_phase_a_entity=self._grid_phase_a_entity,
            grid_phase_b_entity=self._grid_phase_b_entity,
            grid_phase_c_entity=self._grid_phase_c_entity,
            grid_power_entity=self._grid_power_entity,
            sensor_fail_behavior=self._sensor_fail_behavior,
            assumed_load_amps=self._assumed_load_amps,
        )

        # PV hysteresis tracker for oscillation prevention
        self._pv_tracker = PVHysteresisTracker()

        # Asymmetric ESS-limit timing: decreases apply immediately, increases
        # are delayed until stable (prevents ramp-up chasing fuse headroom)
        self._ess_limiter = ESSLimitRateLimiter(
            increase_delay_seconds=self._ess_increase_delay_seconds
        )

        # Rate-limit the charge-limit-entity-wrong-domain error (logged once)
        self._charge_limit_domain_warned: bool = False

        # Change detection for command deduplication
        self._last_sent_mode: str | None = None
        self._last_charge_limit: float | None = None
        self._last_sent_discharge_limit: float | None = None

        # Command verification tracking
        self._pending_verification: dict | None = None
        self._verification_attempts: int = 0

        # Observe-only mode (CORE-14): most recently suppressed dry-run command
        self._last_suppressed_command: str | None = None

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
                dry_run=not self._is_control_enabled(),
                last_suppressed_command=self._last_suppressed_command,
                discharge_allowed=True,
                discharge_gate_reason="",
            )

        # 2. Read real-time sensor values (signed: positive = import, negative = export)
        l_current, sensor_blocked = self._fuse_reader.read_grid_current_amps()
        pv_power_w = _read_power_kw(self.hass, self._pv_power_entity) * 1000.0
        battery_soc = self._read_float_state(self._soc_entity, 50.0)
        battery_own_amps = self._read_battery_own_amps()

        # 3. Determine car charging priority: any car with an active schedule
        # slot AND home+plugged demands priority (EMS-03). Schedules stay
        # visible regardless of plugged state -- this only gates the battery.
        car_priority = self._check_car_priority()

        # 4. Update PV hysteresis tracker
        pv_active = self._pv_tracker.update(pv_power_w)

        # 5. Compute the ESS amps ceiling: fuse headroom with the battery's
        # own charging draw added back (prevents self-ratchet), then apply
        # asymmetric rate limiting (decreases immediate, increases delayed).
        raw_ess_ceiling = (
            0.0
            if sensor_blocked
            else compute_available_ess_amps(
                fuse_rating_amps=self._fuse_rating_amps,
                safety_buffer_amps=self._safety_buffer_amps,
                worst_phase_amps=l_current,
                battery_own_amps=battery_own_amps,
                max_ess_charge_amps=self._max_ess_charge_amps,
            )
        )
        applied_ess_ceiling = self._ess_limiter.update(
            raw_ess_ceiling, dt_util.utcnow()
        )

        # 6. Call pure module for calculations
        max_charge_power_kw = DEFAULT_MAX_CHARGE_POWER_KW
        result = compute_ems_state(
            target_ems_mode=schedule_data.target_ems_mode,
            current_l_amps=l_current,
            fuse_rating_amps=self._fuse_rating_amps,
            max_charge_power_kw=max_charge_power_kw,
            battery_soc_pct=battery_soc,
            car_scheduled=car_priority,
            car_plugged_in=car_priority,
            pv_power_w=pv_power_w,
            pv_hysteresis_active=pv_active,
            max_soc_pct=DEFAULT_MAX_SOC_PCT,
            safety_buffer_amps=self._safety_buffer_amps,
            sensor_blocked=sensor_blocked,
            available_ess_amps=applied_ess_ceiling,
        )

        # 7. Determine if mode or limit changed
        mode_changed = result.target_mode != self._last_sent_mode
        limit_changed = result.charge_limit_kw != self._last_charge_limit

        # 8. Safe command ordering (Research Pitfall 3)
        mode_sent = False
        limit_sent = False
        if mode_changed or limit_changed:
            if result.target_mode == "command_charging":
                # Switching TO command_charging: send limit FIRST, then mode
                if limit_changed:
                    limit_sent = await self._send_charge_limit(result.charge_limit_kw)
                if mode_changed:
                    mode_sent = await self._send_ems_mode(result.target_mode)
            else:
                # Switching FROM command_charging (or between non-charge modes):
                # send mode FIRST, then zero limit
                if mode_changed:
                    mode_sent = await self._send_ems_mode(result.target_mode)
                if limit_changed:
                    limit_sent = await self._send_charge_limit(result.charge_limit_kw)

        # 8b. Discharge limit gate: mirror the scheduler's discharge_allowed
        # decision onto the SigenStor discharge-limit number entity, using
        # the same dedup convention as the charge limit (only advance dedup
        # state when actually sent).
        if self._discharge_limit_entity:
            if schedule_data.discharge_allowed:
                limit_state = self.hass.states.get(self._discharge_limit_entity)
                entity_max = (
                    limit_state.attributes.get("max") if limit_state else None
                )
                target_discharge_limit = (
                    min(float(entity_max), MAX_CHARGE_LIMIT_KW)
                    if entity_max is not None
                    else MAX_CHARGE_LIMIT_KW
                )
            else:
                target_discharge_limit = 0.0

            if target_discharge_limit != self._last_sent_discharge_limit:
                discharge_sent = await self._send_discharge_limit(
                    target_discharge_limit
                )
                if discharge_sent:
                    self._last_sent_discharge_limit = target_discharge_limit

        # 9. Schedule verification if mode changed and actually sent -- skip
        # when suppressed by observe-only mode (CORE-14), otherwise the
        # command would never be verified and would log a false warning.
        if mode_changed and mode_sent and self._ems_select_entity:
            mapped_option = EMS_MODE_MAP.get(result.target_mode)
            if mapped_option:
                self._schedule_verification(
                    self._ems_select_entity, mapped_option
                )

        # 10. Update change detection state -- only record what was actually
        # sent. A suppressed (observe-only) or failed command must NOT be
        # recorded as sent, otherwise it is never retried once control is
        # enabled or the entity recovers (CodeRabbit/Greptile PR #1 review).
        if mode_sent:
            self._last_sent_mode = result.target_mode
        if limit_sent:
            self._last_charge_limit = result.charge_limit_kw

        # 11. Check pending verification
        command_verified = self._check_verification()

        # 12. Return EMSData
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
            dry_run=not self._is_control_enabled(),
            last_suppressed_command=self._last_suppressed_command,
            discharge_allowed=schedule_data.discharge_allowed,
            discharge_gate_reason=schedule_data.discharge_gate_reason,
        )

    def _is_control_enabled(self) -> bool:
        """Return the master "Device control" switch state (CORE-14).

        Fail-safe: this must never default to enabled. See
        _read_control_enabled() (shared with EaseeCoordinator).
        """
        return _read_control_enabled(self.config_entry)

    def _read_float_state(self, entity_id: str, default: float) -> float:
        """Read a sensor state and return as float, with safe fallback.

        Args:
            entity_id: The entity ID to read.
            default: Default value if entity is unavailable or unparseable.

        Returns:
            Float value of the entity state, or default.
        """
        return _read_entity_float(self.hass, entity_id, default)

    def _read_battery_own_amps(self) -> float:
        """Read the battery's own charging draw in amps (self-consumption add-back).

        Grid current sensors measure the battery's own charging draw as part
        of the total load, which would otherwise cause the EMS to keep
        shrinking its own charge limit (self-ratchet). Positive readings are
        treated as charging; zero/negative (idle/discharging) contribute
        nothing. Converts W to A with the 3-phase 400V divisor since this is
        a single total-power reading, not a per-phase one.

        Returns:
            Battery's own current draw in amps, or 0.0 if not configured,
            unavailable, or not currently charging.
        """
        if not self._battery_power_entity:
            return 0.0

        power = self._read_float_state(self._battery_power_entity, 0.0)
        state = self.hass.states.get(self._battery_power_entity)
        if state is not None:
            uom = state.attributes.get("unit_of_measurement", "")
            if uom == "kW":
                power *= 1000.0

        if power <= 0.0:
            return 0.0
        return power / WATTS_TO_AMPS_3PHASE_DIVISOR

    def _check_car_priority(self) -> bool:
        """Check all car coordinators for active priority-charging demand.

        A car demands priority charging only when it has an active schedule
        slot ("charge" or "solar_charge") AND is home and plugged in
        (CarChargingData.home_and_plugged). Schedules remain visible and
        keep being computed regardless of plugged state -- this only gates
        the battery priority override (EMS-03).

        Returns:
            True if at least one car currently demands priority charging.
        """
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        car_coordinators = getattr(runtime_data, "car_coordinators", None) or {}

        cars: list[tuple[bool, bool]] = []
        for car_coordinator in car_coordinators.values():
            data = car_coordinator.data
            if data is None:
                continue
            active_slot = data.current_action in ("charge", "solar_charge")
            cars.append((active_slot, data.home_and_plugged))

        return car_demands_priority_charging(cars)

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

        # Choke point (CORE-14): suppress the command when observe-only.
        decision = build_command_decision(
            control_enabled=self._is_control_enabled(),
            service_domain="select",
            service_name="select_option",
            entity_id=self._ems_select_entity,
            value=option,
        )
        if not decision.should_send:
            _LOGGER.info(decision.dry_run_message)
            self._last_suppressed_command = decision.dry_run_message
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

        # Defense in depth: the configured entity must be a writable number.*
        # setpoint. A sensor-domain "rated_*" capability entity would fail
        # number.set_value (see phase41 UAT bug 2) -- refuse to call it.
        if not self._charge_limit_entity.startswith("number."):
            if not self._charge_limit_domain_warned:
                _LOGGER.error(
                    "Charge limit entity %s is not in the 'number' domain -- "
                    "skipping command. Reconfigure the charge limit entity to "
                    "a writable number.* setpoint.",
                    self._charge_limit_entity,
                )
                self._charge_limit_domain_warned = True
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

        # Choke point (CORE-14): suppress the command when observe-only.
        decision = build_command_decision(
            control_enabled=self._is_control_enabled(),
            service_domain="number",
            service_name="set_value",
            entity_id=self._charge_limit_entity,
            value=clamped,
        )
        if not decision.should_send:
            _LOGGER.info(decision.dry_run_message)
            self._last_suppressed_command = decision.dry_run_message
            return False

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

    async def _send_discharge_limit(self, limit_kw: float) -> bool:
        """Send discharging limit to SigenStor via HA service call.

        Args:
            limit_kw: Discharging limit in kW.

        Returns:
            True if command was sent successfully, False otherwise.
        """
        if not self._discharge_limit_entity:
            _LOGGER.debug(
                "Discharge limit entity not configured, skipping limit command"
            )
            return False

        # Defense in depth: the configured entity must be a writable number.*
        # setpoint (mirrors the charge limit entity check).
        if not self._discharge_limit_entity.startswith("number."):
            _LOGGER.error(
                "Discharge limit entity %s is not in the 'number' domain -- "
                "skipping command. Reconfigure the discharge limit entity to "
                "a writable number.* setpoint.",
                self._discharge_limit_entity,
            )
            return False

        # Check entity availability
        state = self.hass.states.get(self._discharge_limit_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.warning(
                "Discharge limit entity %s is unavailable",
                self._discharge_limit_entity,
            )
            return False

        # Clamp to safe range before sending (Pitfall 2)
        clamped = max(0.0, min(limit_kw, MAX_CHARGE_LIMIT_KW))

        # Choke point (CORE-14): suppress the command when observe-only.
        decision = build_command_decision(
            control_enabled=self._is_control_enabled(),
            service_domain="number",
            service_name="set_value",
            entity_id=self._discharge_limit_entity,
            value=clamped,
        )
        if not decision.should_send:
            _LOGGER.info(decision.dry_run_message)
            self._last_suppressed_command = decision.dry_run_message
            return False

        _LOGGER.info(
            "Setting discharge limit to %.1f kW (requested %.1f)",
            clamped,
            limit_kw,
        )
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": self._discharge_limit_entity, "value": clamped},
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
        current_soc: Current state of charge percentage, or None when the
            SOC sensor is unavailable (solar eligibility stays fail-open).
        target_soc: Target state of charge percentage.
        last_calculated: UTC timestamp of last calculation.
        home_and_plugged: Whether the car is currently home and plugged in
            (see CarChargingCoordinator._is_home_and_plugged_in()).
        phase_capability: How many phases this car draws on when the
            charger is in 3-phase mode (1, 2, or 3; EV-12). Consumed by
            EaseeCoordinator to build a CarDemand.
        max_charge_power_kw: The car's own maximum charge power in kW
            (mutable, set by the per-car number entity). Consumed by
            EaseeCoordinator to build a CarDemand.
        solar_target_soc: SOC ceiling for solar charging of this car
            (percent).
    """

    current_action: str
    schedule: list
    charging_slot_count: int
    energy_needed_kwh: float
    hours_needed: float
    is_preliminary: bool
    car_name: str
    current_soc: float | None
    target_soc: float
    last_calculated: datetime
    home_and_plugged: bool
    phase_capability: int
    max_charge_power_kw: float
    solar_target_soc: float = 100.0


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
        self._charger_connected_entity: str = subentry.data.get(
            CONF_CHARGER_CONNECTED_ENTITY, ""
        )
        self._location_entity: str = subentry.data.get(
            CONF_LOCATION_ENTITY, ""
        )
        self._phase_capability: int = int(
            subentry.data.get(CONF_PHASE_CAPABILITY, DEFAULT_PHASE_CAPABILITY)
        )

        # Charger status entity from main entry options (shared across all cars)
        self._charger_status_entity: str = entry.options.get(
            CONF_CHARGER_STATUS_ENTITY, ""
        )

        # Mutable attributes -- updated by entity instances after setup
        self.departure_time: time = time(7, 0)  # Default 07:00
        self.target_soc: float = DEFAULT_TARGET_SOC_PCT
        self.max_charge_power_kw: float = DEFAULT_CAR_MAX_CHARGE_POWER_KW
        self.solar_target_soc: float = DEFAULT_CAR_SOLAR_TARGET_SOC_PCT

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

        # 2. Read car SOC from battery_level_entity. The scheduler needs a
        # number, so unknown SOC assumes 50% for slot sizing; CarChargingData
        # keeps the raw None so solar eligibility stays fail-open.
        current_soc = self._read_car_soc()
        scheduling_soc = current_soc if current_soc is not None else 50.0

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

        # 7. Call pure scheduler with the Easee charger's live solar mode
        # (EV-09) -- schedule marking only ("charge" vs "solar_charge"), no
        # effect on the actual control loop.
        result: CarScheduleResult = build_car_charging_schedule(
            price_slots=price_slots,
            departure_time_utc=departure_utc,
            current_soc_pct=scheduling_soc,
            target_soc_pct=self.target_soc,
            battery_capacity_kwh=self._battery_capacity_kwh,
            max_charge_power_kw=self.max_charge_power_kw,
            now=dt_util.utcnow(),
            fallback_mode=fallback_mode,
            is_preliminary=is_preliminary,
            solar_surplus_available=self._read_solar_surplus_available(),
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
            home_and_plugged=self._is_home_and_plugged_in(),
            phase_capability=self._phase_capability,
            max_charge_power_kw=self.max_charge_power_kw,
            solar_target_soc=self.solar_target_soc,
        )

    def _read_car_soc(self) -> float | None:
        """Read the car's battery level from HA entity state.

        Returns:
            SOC as a percentage (0-100), or None when the sensor is not
            configured, unavailable, or non-numeric. Callers that need a
            number (the price scheduler) apply their own assumption;
            solar-mode eligibility must see None so unknown SOC stays
            fail-open (charge) regardless of the solar target level.
        """
        if not self._battery_level_entity:
            return None

        state = self.hass.states.get(self._battery_level_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return None

        try:
            soc = float(state.state)
            # Track when the sensor itself last produced a value, not when
            # we happened to read it -- a frozen sensor (integration gone
            # offline but still reporting its last state) must still look
            # stale to _detect_fallback_needed().
            self._soc_last_updated = state.last_updated
            return soc
        except (ValueError, TypeError):
            return None

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

        # 3. This car is confirmed home and plugged in -> recognized car,
        # never fallback. Car SOC integrations (e.g. mySkoda) can go silent
        # for 30-60+ min while parked, so a staleness check alone would
        # misclassify the owner's own car as an unknown guest; a stale SOC
        # here just means "schedule from the last known reading".
        if self._is_home_and_plugged_in():
            return False

        # 4. Never received a SOC reading -> likely unrecognized car
        if self._soc_last_updated is None:
            return True

        # 5. SOC reading is stale -> likely unrecognized car
        # 6. Otherwise: recognized car with recent SOC
        elapsed = (dt_util.utcnow() - self._soc_last_updated).total_seconds()
        return elapsed > FALLBACK_STALE_THRESHOLD_MINUTES * 60

    def _read_solar_surplus_available(self) -> bool:
        """Return whether the Easee charger's solar mode is currently active (EV-09).

        Feeds the pure car scheduler's solar_surplus_available flag, which
        only affects schedule marking ("charge" vs "solar_charge" slot
        labels) -- no effect on the actual charging control loop. Mirrors
        EMSCoordinator._check_car_priority()'s defensive read of
        runtime_data, which may not be assigned yet (this coordinator's
        first refresh happens before EaseeCoordinator is created, see
        __init__.py) or have no data yet.

        Returns:
            True if the Easee charger controller's mode is "solar".
        """
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        easee_coordinator = getattr(runtime_data, "easee_coordinator", None)
        if easee_coordinator is None or easee_coordinator.data is None:
            return False
        return easee_coordinator.data.mode == "solar"

    def _is_home_and_plugged_in(self) -> bool:
        """Derive whether the car is home and plugged in from available signals.

        Combines up to three signals:
        1. Easee charger status entity (from main config) -- not "disconnected"
        2. Car's charger_connected binary sensor (from car subentry) -- "on"
        3. Vehicle location device_tracker (from car subentry) -- "home"

        Returns True if the charger reports a connected state. If the car's
        own charger_connected sensor is available, it must also confirm.
        If location is available, the car must be in the "home" zone.

        Returns:
            True if the car appears to be home and plugged in.
        """
        # Signal 1: Easee charger status (shared across all cars)
        charger_connected = False
        if self._charger_status_entity:
            state = self.hass.states.get(self._charger_status_entity)
            if state is not None and state.state not in ("unavailable", "unknown"):
                charger_connected = state.state.lower() not in (
                    "disconnected",
                    "offline",
                )

        if not charger_connected:
            return False

        # Signal 2: Car's own charger_connected binary sensor (if available)
        if self._charger_connected_entity:
            state = self.hass.states.get(self._charger_connected_entity)
            if (
                state is not None
                and state.state not in ("unavailable", "unknown")
                and state.state.lower() != "on"
            ):
                return False

        # Signal 3: Vehicle location (if available)
        if self._location_entity:
            state = self.hass.states.get(self._location_entity)
            if (
                state is not None
                and state.state not in ("unavailable", "unknown")
                and state.state.lower() != "home"
            ):
                return False

        return True


# ---------------------------------------------------------------------------
# Easee charger control (Phase 5 Wave B)
# ---------------------------------------------------------------------------

#: ChargerCommand actions sent via the easee.action_command service, whose
#: "action_command" field takes exactly these values (see
#: dev/config/custom_components/easee/services.py ACTIONS).
EASEE_ACTION_COMMANDS = frozenset({"start", "pause", "resume", "stop"})

#: Maps ChargerInputs/ChargerDecision phase mode vocabulary ("single"/
#: "three" -- deliberately the same strings the Easee "phase_mode" sensor
#: reports, see dev/config/custom_components/easee/const.py PM_SINGLE/
#: PM_THREE) to the easee.set_charger_phase_mode service's phase_mode enum.
EASEE_PHASE_MODE_MAP = {"single": "1_phase", "three": "3_phase"}

#: Easee's config.phaseMode raw values (1/2/3), see
#: dev/config/custom_components/easee/const.py PHASE_MODE_STATUS.
_EASEE_RAW_PHASE_MODE_SINGLE = 1


def build_easee_service_call(
    command: ChargerCommand, device_id: str
) -> tuple[str, str, dict]:
    """Map one ChargerCommand to an easee.* service call.

    Pure translation, no I/O -- exact service names/fields verified against
    dev/config/custom_components/easee/services.yaml and services.py:
        - start/pause/resume/stop -> easee.action_command
          (fields: device_id, action_command=<action>)
        - set_dynamic_limit -> easee.set_charger_dynamic_limit
          (fields: device_id, current=<amps>)
        - set_phase_mode -> easee.set_charger_phase_mode
          (fields: device_id, phase_mode="1_phase"|"3_phase")

    Args:
        command: The command to translate.
        device_id: The Easee charger's HA device_id (CONF_CHARGER_DEVICE_ID).

    Returns:
        Tuple of (service_domain, service_name, service_data).

    Raises:
        ValueError: If command.action is not a known action.
    """
    if command.action in EASEE_ACTION_COMMANDS:
        return (
            "easee",
            "action_command",
            {"device_id": device_id, "action_command": command.action},
        )
    if command.action == "set_dynamic_limit":
        return (
            "easee",
            "set_charger_dynamic_limit",
            {"device_id": device_id, "current": command.value},
        )
    if command.action == "set_phase_mode":
        phase_mode = EASEE_PHASE_MODE_MAP.get(str(command.value), command.value)
        return (
            "easee",
            "set_charger_phase_mode",
            {"device_id": device_id, "phase_mode": phase_mode},
        )
    raise ValueError(f"Unknown charger command action: {command.action!r}")


def _derive_phase_mode(raw_config_phase_mode: object) -> str:
    """Map the charger status entity's raw config_phaseMode attribute to single/three.

    The Easee "status" sensor exposes "config.phaseMode" as an extra_state_
    attribute ("config_phaseMode", raw int 1/2/3 -- NOT yet translated to
    the "single"/"auto"/"three" strings the dedicated "phase_mode" sensor
    reports, since attribute values bypass convert_units_func). 2 ("auto")
    is treated as "three" -- Energy Manager itself only ever requests
    "1_phase" or "3_phase", never "auto_phase", so an observed auto reading
    only occurs from external configuration; three-phase is the safe
    default since a 1-phase-only installation reports 1, not 2. Missing or
    unparseable values also default to "three".

    Args:
        raw_config_phase_mode: The raw attribute value (expected int-like).

    Returns:
        "single" or "three".
    """
    try:
        value = int(raw_config_phase_mode)
    except (TypeError, ValueError):
        return "three"
    return "single" if value == _EASEE_RAW_PHASE_MODE_SINGLE else "three"


def _estimate_charger_current_amps(
    charger_power_kw: float,
    current_phase_mode: str,
    conversion_factor_1phase: float,
    conversion_factor_3phase: float,
) -> float:
    """Estimate the charger's own current draw in amps from its measured power.

    Used as the ChargerInputs.current_dynamic_limit_amps add-back proxy --
    the configured Easee entities do not include a ground-truth dynamic
    limit sensor, so the charger's actual measured power is converted to an
    equivalent amps figure using the conversion factor for whichever phase
    mode the charger currently reports (per Wave B instructions: "The
    charger's own current for the add-back comes from the charger
    power/current reading").

    Args:
        charger_power_kw: Measured charger power draw in kW.
        current_phase_mode: The charger's actual reported phase mode,
            "single" or "three".
        conversion_factor_1phase: A/kW conversion factor for 1-phase.
        conversion_factor_3phase: A/kW conversion factor for 3-phase.

    Returns:
        Estimated amps, never negative.
    """
    factor = (
        conversion_factor_1phase
        if current_phase_mode == "single"
        else conversion_factor_3phase
    )
    return max(0.0, charger_power_kw) * factor


@dataclass(frozen=True, slots=True)
class EaseeData:
    """Output of the Easee charger coordinator.

    Attributes:
        mode: Active charger mode -- "forced", "scheduled", "solar", or "idle".
        target_amps: This tick's computed amp target.
        target_phase_mode: Desired charger phase mode ("single"/"three").
        sequence_state: Phase-switch sequence state ("idle", "pausing",
            "set_phase", "resuming", or "set_limit").
        stuck: True when a command was issued but showed no observable
            effect within its timeout.
        dry_run: True when the master "Device control" switch is OFF
            (observe-only) -- commands are computed but not sent (CORE-14).
        last_suppressed_command: Human-readable description of the most
            recently suppressed command, or None if none has been suppressed.
        notification_count: Number of safety notifications generated this
            tick (see ChargerDecision.notifications).
        override_reason: Why behavior is notable this tick, or None.
        charger_status: Raw Easee charger status string.
        charger_power_kw: Measured charger power draw in kW.
        fuse_headroom_amps: Available fuse headroom in amps from the
            charger's point of view (own draw added back). Informational.
        house_consumption_kw: Net house consumption (house consumption minus
            excluded-power entities) -- the "House load" diagnostic sensor's
            value (CORE-11, see _read_net_house_consumption_kw()).
        solar_surplus_kw: Raw (unclamped) solar surplus computed this tick
            via compute_solar_surplus_kw() -- the "Solar surplus" diagnostic
            sensor's value (CORE-11, EV-09).
    """

    mode: str
    target_amps: float
    target_phase_mode: str
    sequence_state: str
    stuck: bool
    dry_run: bool
    last_suppressed_command: str | None
    notification_count: int
    override_reason: str | None
    charger_status: str
    charger_power_kw: float
    fuse_headroom_amps: float
    house_consumption_kw: float
    solar_surplus_kw: float


class EaseeCoordinator(DataUpdateCoordinator[EaseeData]):
    """Coordinator that orchestrates real-time Easee charger control.

    ~30s poll + state-change listeners on the charger status/power
    entities. Builds a ChargerInputs snapshot each tick (reusing the shared
    FuseSensorReader -- same grid-sensor formula as EMSCoordinator, see
    05-RESEARCH.md "shared fuse arbiter"), calls the pure ChargerController,
    and executes the returned commands via the easee.* services through the
    same build_command_decision observe-only choke point used by
    EMSCoordinator. Force-charging reads the "Force grid charging" switch
    (EASE-03) from runtime_data; solar-surplus is computed live from the
    configured pv/house-consumption/battery/charger power entities (EV-09,
    see _read_solar_surplus_kw()).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the Easee coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
        """
        super().__init__(
            hass,
            _LOGGER,
            name="Energy Manager Easee Charger",
            config_entry=entry,
            update_interval=timedelta(seconds=EASEE_UPDATE_INTERVAL_SECONDS),
            always_update=False,
        )
        self._controller = ChargerController()

        # -- Entities --
        self._charger_status_entity: str = entry.options.get(
            CONF_CHARGER_STATUS_ENTITY, ""
        )
        self._charger_power_entity: str = entry.options.get(
            CONF_CHARGER_POWER_ENTITY, ""
        )
        self._charger_device_id: str = entry.options.get(
            CONF_CHARGER_DEVICE_ID, ""
        )
        self._soc_entity: str = entry.options.get(CONF_SOC_ENTITY, "")
        self._notify_service: str = entry.options.get(CONF_NOTIFY_SERVICE, "")

        # -- Solar-surplus inputs (EV-09/EMS-13) --
        self._pv_power_entity: str = entry.options.get(CONF_PV_POWER_ENTITY, "")
        self._house_consumption_entity: str = entry.options.get(
            CONF_HOUSE_CONSUMPTION_ENTITY, ""
        )
        self._battery_power_entity: str = entry.options.get(
            CONF_BATTERY_POWER_ENTITY, ""
        )
        self._excluded_power_entities: list[str] = list(
            entry.options.get(CONF_EXCLUDED_POWER_ENTITIES, []) or []
        )

        # -- Fuse config (shared top-level EMS options) --
        self._fuse_rating_amps: float = float(
            entry.options.get(CONF_FUSE_RATING_AMPS, DEFAULT_FUSE_RATING_AMPS)
        )
        self._safety_buffer_amps: float = float(
            entry.options.get(CONF_FUSE_SAFETY_BUFFER_AMPS, DEFAULT_SAFETY_BUFFER_AMPS)
        )
        self._fuse_reader = FuseSensorReader(
            hass=hass,
            grid_phase_a_entity=entry.options.get(CONF_GRID_PHASE_A_ENTITY, ""),
            grid_phase_b_entity=entry.options.get(CONF_GRID_PHASE_B_ENTITY, ""),
            grid_phase_c_entity=entry.options.get(CONF_GRID_PHASE_C_ENTITY, ""),
            grid_power_entity=entry.options.get(CONF_GRID_POWER_ENTITY, ""),
            sensor_fail_behavior=entry.options.get(
                CONF_SENSOR_FAIL_BEHAVIOR, DEFAULT_SENSOR_FAIL_BEHAVIOR
            ),
            assumed_load_amps=float(
                entry.options.get(CONF_ASSUMED_LOAD_AMPS, DEFAULT_ASSUMED_LOAD_AMPS)
            ),
        )

        # -- Charger tuning options (Phase-5 defaults, ALL explicit -- never
        # rely on the ChargerInputs dataclass fallbacks) --
        self._min_amps: float = float(
            entry.options.get(CONF_MIN_CHARGE_AMPS, DEFAULT_MIN_CHARGE_AMPS)
        )
        self._max_amps: float = float(
            entry.options.get(CONF_MAX_CHARGE_AMPS, DEFAULT_MAX_CHARGE_AMPS)
        )
        self._grid_power_cap_kw: float = float(
            entry.options.get(
                CONF_MAX_GRID_CHARGE_POWER_KW, DEFAULT_MAX_GRID_CHARGE_POWER_KW
            )
        )
        self._amp_increase_delay_s: float = float(
            entry.options.get(
                CONF_AMP_INCREASE_DELAY, DEFAULT_AMP_INCREASE_DELAY_SECONDS
            )
        )
        self._amp_decrease_delay_s: float = float(
            entry.options.get(
                CONF_AMP_DECREASE_DELAY, DEFAULT_AMP_DECREASE_DELAY_SECONDS
            )
        )
        # Safety invariant: decreases must never be slower than increases.
        # Nothing in the config flow cross-validates the two delays, so
        # enforce it here rather than trusting configuration.
        if self._amp_decrease_delay_s > self._amp_increase_delay_s:
            _LOGGER.warning(
                "amp_decrease_delay (%ss) exceeds amp_increase_delay (%ss); "
                "clamping decrease delay to %ss -- limit decreases are a "
                "safety property and must stay fast",
                self._amp_decrease_delay_s,
                self._amp_increase_delay_s,
                self._amp_increase_delay_s,
            )
            self._amp_decrease_delay_s = self._amp_increase_delay_s
        self._phase_switch_threshold_kw: float = float(
            entry.options.get(
                CONF_PHASE_SWITCH_THRESHOLD_KW, DEFAULT_PHASE_SWITCH_THRESHOLD_KW
            )
        )
        self._solar_start_threshold_kw: float = float(
            entry.options.get(
                CONF_SOLAR_START_THRESHOLD_KW, DEFAULT_SOLAR_START_THRESHOLD_KW
            )
        )
        self._solar_activation_delay_s: float = float(
            entry.options.get(
                CONF_SOLAR_ACTIVATION_DELAY, DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS
            )
        )
        self._solar_deactivation_delay_s: float = float(
            entry.options.get(
                CONF_SOLAR_DEACTIVATION_DELAY, DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS
            )
        )
        self._battery_soc_gate_pct: float = float(
            entry.options.get(
                CONF_BATTERY_SOC_GATE_PCT, DEFAULT_BATTERY_SOC_GATE_PCT
            )
        )
        self._emergency_margin_amps: float = float(
            entry.options.get(
                CONF_EMERGENCY_MARGIN_AMPS, DEFAULT_EMERGENCY_MARGIN_AMPS
            )
        )

        # Observe-only mode (CORE-14): most recently suppressed dry-run command
        self._last_suppressed_command: str | None = None

    async def _async_setup(self) -> None:
        """Register listeners for immediate response to charger state changes.

        Called once during async_config_entry_first_refresh. Force-charging
        and master-switch toggles cannot be listened for here (they are
        plain flags in runtime_data, not entities) -- Wave C's switch
        entities call async_request_refresh() directly instead.
        """
        entities = [e for e in (self._charger_status_entity, self._charger_power_entity) if e]
        if entities:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, entities, self._handle_charger_update
                )
            )

    @callback
    def _handle_charger_update(self, event) -> None:
        """Handle charger status/power state changes for immediate response."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> EaseeData:
        """Build ChargerInputs, call the pure controller, execute, and return EaseeData.

        Returns:
            EaseeData with the current charger state and control information.
        """
        now = dt_util.utcnow()
        charger_status = self._read_charger_status()
        charger_power_kw = self._read_charger_power_kw()
        current_phase_mode = self._read_current_phase_mode()
        l_current, sensor_blocked = self._fuse_reader.read_grid_current_amps()
        current_dynamic_limit_amps = _estimate_charger_current_amps(
            charger_power_kw,
            current_phase_mode,
            DEFAULT_CHARGER_CONVERSION_FACTOR_1PHASE,
            DEFAULT_CHARGER_CONVERSION_FACTOR_3PHASE,
        )
        battery_soc = (
            _read_entity_float(self.hass, self._soc_entity, 100.0)
            if self._soc_entity
            else 100.0
        )
        net_house_consumption_kw = _read_net_house_consumption_kw(
            self.hass, self._house_consumption_entity, self._excluded_power_entities
        )
        solar_surplus_kw = self._read_solar_surplus_kw(
            charger_power_kw, net_house_consumption_kw
        )

        inputs = ChargerInputs(
            charger_status=charger_status,
            charger_power_kw=charger_power_kw,
            measured_worst_case_signed_amps=0.0 if sensor_blocked else l_current,
            current_dynamic_limit_amps=current_dynamic_limit_amps,
            force_charging=self._is_force_charging(),
            solar_surplus_kw=solar_surplus_kw,
            battery_soc_pct=battery_soc,
            current_phase_mode=current_phase_mode,
            now=now,
            fuse_rating_amps=self._fuse_rating_amps,
            cars=self._build_car_demands(),
            safety_buffer_amps=self._safety_buffer_amps,
            min_amps=self._min_amps,
            max_amps=self._max_amps,
            conversion_factor_1phase=DEFAULT_CHARGER_CONVERSION_FACTOR_1PHASE,
            conversion_factor_2phase=DEFAULT_CHARGER_CONVERSION_FACTOR_2PHASE,
            conversion_factor_3phase=DEFAULT_CHARGER_CONVERSION_FACTOR_3PHASE,
            grid_power_cap_kw=self._grid_power_cap_kw,
            grid_power_safety_buffer_kw=DEFAULT_GRID_POWER_SAFETY_BUFFER_KW,
            phase_switch_threshold_kw=self._phase_switch_threshold_kw,
            solar_start_threshold_kw=self._solar_start_threshold_kw,
            solar_safety_buffer_kw=DEFAULT_SOLAR_SAFETY_BUFFER_KW,
            solar_activation_delay_s=self._solar_activation_delay_s,
            solar_deactivation_delay_s=self._solar_deactivation_delay_s,
            battery_soc_gate_pct=self._battery_soc_gate_pct,
            soc_round_up=DEFAULT_SOC_ROUND_UP,
            emergency_margin_amps=self._emergency_margin_amps,
            amp_increase_delay_s=self._amp_increase_delay_s,
            amp_decrease_delay_s=self._amp_decrease_delay_s,
            phase_sequence_step_timeout_s=DEFAULT_PHASE_SEQUENCE_STEP_TIMEOUT_SECONDS,
            command_stuck_timeout_s=DEFAULT_COMMAND_STUCK_TIMEOUT_SECONDS,
        )

        decision = self._controller.decide(inputs)
        await self._execute_commands(decision.commands)
        await self._send_notifications(decision.notifications)

        fuse_headroom_amps = (
            0.0
            if sensor_blocked
            else compute_available_ess_amps(
                fuse_rating_amps=self._fuse_rating_amps,
                safety_buffer_amps=self._safety_buffer_amps,
                worst_phase_amps=l_current,
                battery_own_amps=current_dynamic_limit_amps,
                max_ess_charge_amps=None,
            )
        )

        return EaseeData(
            mode=decision.mode,
            target_amps=decision.target_amps,
            target_phase_mode=decision.target_phase_mode,
            sequence_state=decision.sequence_state,
            stuck=decision.stuck,
            dry_run=not self._is_control_enabled(),
            last_suppressed_command=self._last_suppressed_command,
            notification_count=len(decision.notifications),
            override_reason=decision.override_reason,
            charger_status=charger_status,
            charger_power_kw=charger_power_kw,
            fuse_headroom_amps=fuse_headroom_amps,
            house_consumption_kw=net_house_consumption_kw,
            solar_surplus_kw=solar_surplus_kw,
        )

    def _is_control_enabled(self) -> bool:
        """Return the master "Device control" switch state (CORE-14)."""
        return _read_control_enabled(self.config_entry)

    def _is_force_charging(self) -> bool:
        """Return the "Force grid charging" switch state (EASE-03)."""
        return _read_force_charging(self.config_entry)

    def _read_solar_surplus_kw(
        self, charger_power_kw: float, net_house_consumption_kw: float
    ) -> float:
        """Compute the live solar surplus available for the charger (EV-09).

        Reads the configured pv/battery power entities and calls the pure
        compute_solar_surplus_kw(). Requires both the PV and house
        consumption entities to be configured -- otherwise there is no
        meaningful surplus to compute and solar mode simply never activates
        (matches the pre-Wave-C default of solar_surplus_kw=0.0).

        The raw result is passed straight through to
        ChargerInputs.solar_surplus_kw -- it must NOT be pre-gated here, as
        the ChargerController's own SolarActivationTracker + safety buffer +
        start threshold already do that (see charger_state_machine.py
        compute_solar_net_kw()).

        Args:
            charger_power_kw: This tick's measured charger power draw,
                already read by the caller -- added back since the house
                consumption reading already includes the charger's draw.
            net_house_consumption_kw: House consumption minus excluded-power
                entities, already read by the caller via the shared
                _read_net_house_consumption_kw() helper (CORE-11: also the
                "House load" diagnostic sensor's value).

        Returns:
            Signed solar surplus in kW (0.0 if pv/house consumption entities
            are not configured).
        """
        if not self._pv_power_entity or not self._house_consumption_entity:
            return 0.0

        pv_power_kw = _read_power_kw(self.hass, self._pv_power_entity)
        battery_power_kw = _read_power_kw(self.hass, self._battery_power_entity)

        return compute_solar_surplus_kw(
            pv_power_kw=pv_power_kw,
            house_consumption_kw=net_house_consumption_kw,
            battery_power_kw=battery_power_kw,
            charger_power_kw=charger_power_kw,
            excluded_power_kw=0.0,
        )

    def _read_charger_status(self) -> str:
        """Read the charger status entity, defaulting to "disconnected"."""
        if not self._charger_status_entity:
            return "disconnected"
        state = self.hass.states.get(self._charger_status_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return "disconnected"
        return state.state

    def _read_charger_power_kw(self) -> float:
        """Read the charger power entity in kW.

        Uses the same unit convention as every other power reader (assume W
        unless unit_of_measurement says kW) -- Easee's power sensor carries a
        kW unit attribute, so it is converted correctly via the shared helper.
        """
        if not self._charger_power_entity:
            return 0.0
        return _read_power_kw(self.hass, self._charger_power_entity)

    def _read_current_phase_mode(self) -> str:
        """Read the charger's actual phase mode from the status entity's attribute."""
        if not self._charger_status_entity:
            return "three"
        state = self.hass.states.get(self._charger_status_entity)
        if state is None:
            return "three"
        return _derive_phase_mode(state.attributes.get("config_phaseMode"))

    def _build_car_demands(self) -> tuple[CarDemand, ...]:
        """Build CarDemand snapshots from the car charging coordinators.

        Mirrors EMSCoordinator._check_car_priority()'s defensive read of
        runtime_data -- tolerates runtime_data not being set yet (first
        refresh, during async_setup_entry, happens before it is assigned).
        """
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        car_coordinators = getattr(runtime_data, "car_coordinators", None) or {}

        demands: list[CarDemand] = []
        for car_coordinator in car_coordinators.values():
            data = car_coordinator.data
            if data is None:
                continue
            demands.append(
                CarDemand(
                    active_slot=data.current_action in ("charge", "solar_charge"),
                    home_and_plugged=data.home_and_plugged,
                    phase_capability=data.phase_capability,
                    max_charge_kw=data.max_charge_power_kw,
                    soc_pct=data.current_soc,
                    solar_target_soc_pct=data.solar_target_soc,
                )
            )
        return tuple(demands)

    async def _execute_commands(self, commands: tuple[ChargerCommand, ...]) -> None:
        """Execute each ChargerCommand through the observe-only choke point."""
        for command in commands:
            await self._execute_one_command(command)

    async def _execute_one_command(self, command: ChargerCommand) -> None:
        """Translate and send (or suppress) one ChargerCommand."""
        if not self._charger_device_id:
            _LOGGER.warning(
                "Charger device ID not configured -- skipping Easee command %s",
                command.action,
            )
            return

        domain, service, service_data = build_easee_service_call(
            command, self._charger_device_id
        )

        # Choke point (CORE-14): suppress the command when observe-only.
        decision = build_command_decision(
            control_enabled=self._is_control_enabled(),
            service_domain=domain,
            service_name=service,
            entity_id=self._charger_device_id,
            value=command.value if command.value is not None else command.action,
        )
        if not decision.should_send:
            _LOGGER.info(decision.dry_run_message)
            self._last_suppressed_command = decision.dry_run_message
            return

        _LOGGER.info("Sending Easee command: %s.%s %s", domain, service, service_data)
        await self.hass.services.async_call(domain, service, service_data, blocking=True)

    async def _send_notifications(self, notifications: tuple[str, ...]) -> None:
        """Send safety notifications via the configured notify service.

        ALWAYS sent even in observe-only mode (they report real measured
        conditions, e.g. an emergency fuse overload) -- prefixed with
        "[observe-only] " when device control is disabled (EASE-08).
        """
        if not notifications or not self._notify_service:
            return

        domain, _, service = self._notify_service.partition(".")
        if not domain or not service:
            _LOGGER.warning(
                "Invalid notify_service '%s' -- expected 'notify.<service>'",
                self._notify_service,
            )
            return

        prefix = "" if self._is_control_enabled() else "[observe-only] "
        for message in notifications:
            await self.hass.services.async_call(
                domain,
                service,
                {"message": f"{prefix}{message}"},
                blocking=True,
            )


@dataclass
class EnergyManagerData:
    """Runtime data stored on the config entry.

    Provides typed access to coordinators and module state.
    """

    price_coordinator: PriceCoordinator
    battery_coordinator: BatteryScheduleCoordinator | None = None
    ems_coordinator: EMSCoordinator | None = None
    car_coordinators: dict[str, CarChargingCoordinator] = field(default_factory=dict)
    easee_coordinator: EaseeCoordinator | None = None
    modules_enabled: dict[str, bool] = field(default_factory=dict)
    # Master "Device control" switch state (CORE-14). False = observe-only:
    # coordinators still compute and publish decisions, but no outgoing
    # device command is ever sent. Set by the switch entity on toggle and
    # read by coordinators at command time. Defaults OFF -- fail-safe.
    control_enabled: bool = False
    # "Force grid charging" switch state (EASE-03, replaces
    # input_boolean.easee_force_charging). Set by ForceChargingSwitch on
    # toggle and read by EaseeCoordinator when building ChargerInputs.
    # Defaults OFF.
    force_charging: bool = False
    # Platform values actually forwarded at setup -- consulted at unload so
    # an options change (module toggle) between setup and unload can't make
    # us unload platforms that were never set up.
    forwarded_platforms: list[str] = field(default_factory=list)


EnergyManagerConfigEntry = ConfigEntry[EnergyManagerData]

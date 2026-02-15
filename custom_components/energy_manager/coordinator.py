"""Price data coordinator and typed runtime data for the Energy Manager integration.

The PriceCoordinator fetches Nordpool prices on a 5-minute interval and
immediately on Nordpool state changes. Price data (today + tomorrow hourly
slots) is available to downstream modules via entry.runtime_data.

No user-visible entities are created -- price data is consumed internally
by scheduling modules.
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
    CONF_BATTERY_ENABLED,
    CONF_EV_ENABLED,
    CONF_NORDPOOL_SENSOR,
    CONF_NORDPOOL_TYPE,
    MODULE_BATTERY,
    MODULE_EV,
    PRICE_UPDATE_INTERVAL_MINUTES,
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


@dataclass
class EnergyManagerData:
    """Runtime data stored on the config entry.

    Provides typed access to coordinators and module state.
    Future phases add coordinators here.
    """

    price_coordinator: PriceCoordinator
    modules_enabled: dict[str, bool] = field(default_factory=dict)


EnergyManagerConfigEntry = ConfigEntry[EnergyManagerData]

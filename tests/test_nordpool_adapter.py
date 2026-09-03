"""Tests for the native Nord Pool coordinator-cache read path.

Covers three changes to nordpool_adapter.py:
  1. Widened except tuple in _async_fetch_native_date (transport errors).
  2. Reading the native coordinator's cache before falling back to service calls.
  3. split_by_local_day() bucketing by the caller's local day, applied to both
     the cache-read path and the service-call fallback path.

Harness notes (see tests/conftest.py and the root conftest.py):
  - The homeassistant package is entirely stubbed with MagicMocks, so
    HomeAssistantError is not a real exception class -- it is patched with a
    real Exception subclass wherever the except tuple is exercised.
  - dt_util is also a stub -- nordpool_adapter.dt_util.now is patched to
    return real timezone-aware datetimes.
  - No pytest-asyncio is installed; coroutines are driven with asyncio.run().
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.energy_manager.nordpool_adapter import (
    _async_fetch_native_date,
    _async_get_native_prices,
    _covers_local_tomorrow,
    _get_native_coordinator_prices,
    split_by_local_day,
)

CET = timezone(timedelta(hours=1))
EET = timezone(timedelta(hours=3))  # Finland/Baltics in summer, ahead of CEST


def _native_slot(start: str, price_mwh: float, hours: int = 1) -> dict:
    """One slot shaped like the native get_prices_for_date response."""
    start_dt = datetime.fromisoformat(start)
    return {
        "start": start,
        "end": (start_dt + timedelta(hours=hours)).isoformat(),
        "price": price_mwh,
    }


def _entry(start: datetime, end: datetime, price_mwh: float) -> SimpleNamespace:
    """A fake pynordpool DeliveryPeriodEntry."""
    return SimpleNamespace(start=start, end=end, entry={"SE4": price_mwh})


def _delivery_period(*entries: SimpleNamespace) -> SimpleNamespace:
    """A fake pynordpool DeliveryPeriodData."""
    return SimpleNamespace(entries=list(entries))


def _config_entry(areas: list[str], delivery_periods, as_dict: bool = False):
    """A fake config entry with a native coordinator's cached data.

    ``as_dict`` mirrors pynordpool 0.4.0 (HA 2026.8), where
    DeliveryPeriodsData.entries is a dict keyed by date instead of a list.
    """
    if as_dict:
        # pynordpool 0.4.0 keys the dict by delivery date.
        entries = {
            period.entries[0].start.date(): period for period in delivery_periods
        }
    else:
        entries = list(delivery_periods)

    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(entries=entries)

    entry = MagicMock()
    entry.data = {"areas": areas}
    entry.runtime_data = coordinator
    return entry


def _cet_day(day: int, hours: int = 24) -> SimpleNamespace:
    """One CET delivery day of hourly slots, priced by hour for identification."""
    entries = [
        _entry(
            datetime(2026, 3, day, h, 0, tzinfo=CET),
            datetime(2026, 3, day, h, 0, tzinfo=CET) + timedelta(hours=1),
            float(h),
        )
        for h in range(hours)
    ]
    return _delivery_period(*entries)


class TestGetNativeCoordinatorPricesDictAndList:
    """Both entries container shapes (list and dict) must be readable."""

    def test_list_shaped_entries(self):
        """Older pynordpool: DeliveryPeriodsData.entries is a list."""
        periods = [_cet_day(12), _cet_day(13)]
        config_entry = _config_entry(["SE4"], periods, as_dict=False)
        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)

        with patch(
            "custom_components.energy_manager.nordpool_adapter.dt_util.now",
            return_value=now,
        ):
            result = _get_native_coordinator_prices(config_entry)

        assert result is not None
        today_prices, tomorrow_prices = result
        assert len(today_prices) == 24
        assert len(tomorrow_prices) == 24
        assert today_prices[0]["value"] == pytest.approx(0.0)

    def test_dict_shaped_entries(self):
        """pynordpool 0.4.0 (HA 2026.8): DeliveryPeriodsData.entries is a dict."""
        periods = [_cet_day(12), _cet_day(13)]
        config_entry = _config_entry(["SE4"], periods, as_dict=True)
        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)

        with patch(
            "custom_components.energy_manager.nordpool_adapter.dt_util.now",
            return_value=now,
        ):
            result = _get_native_coordinator_prices(config_entry)

        assert result is not None
        today_prices, tomorrow_prices = result
        assert len(today_prices) == 24
        assert len(tomorrow_prices) == 24


class TestGetNativeCoordinatorPricesFallbackCases:
    """Missing/empty/malformed cache data must return None so callers fall back."""

    def test_no_runtime_data_returns_none(self):
        entry = MagicMock(spec=[])  # No attributes at all
        assert _get_native_coordinator_prices(entry) is None

    def test_empty_entries_returns_none(self):
        coordinator = MagicMock()
        coordinator.data = SimpleNamespace(entries=[])
        entry = MagicMock()
        entry.runtime_data = coordinator
        assert _get_native_coordinator_prices(entry) is None

    def test_no_areas_returns_none(self):
        config_entry = _config_entry([], [_cet_day(12)])
        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)
        with patch(
            "custom_components.energy_manager.nordpool_adapter.dt_util.now",
            return_value=now,
        ):
            assert _get_native_coordinator_prices(config_entry) is None

    def test_today_bucket_empty_returns_none(self):
        """Cache only holds stale days -- caller must fall back to service calls."""
        config_entry = _config_entry(["SE4"], [_cet_day(1)])
        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)
        with patch(
            "custom_components.energy_manager.nordpool_adapter.dt_util.now",
            return_value=now,
        ):
            assert _get_native_coordinator_prices(config_entry) is None

    def test_malformed_entry_returns_none_without_raising(self):
        """An entry missing the expected attributes must not raise."""
        coordinator = MagicMock()
        # A delivery period whose "entries" isn't iterable list of proper
        # entries -- getattr(entry, "entry") will raise AttributeError.
        broken_period = SimpleNamespace(entries=[object()])
        coordinator.data = SimpleNamespace(entries=[broken_period])
        entry = MagicMock()
        entry.data = {"areas": ["SE4"]}
        entry.runtime_data = coordinator

        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)
        with patch(
            "custom_components.energy_manager.nordpool_adapter.dt_util.now",
            return_value=now,
        ):
            assert _get_native_coordinator_prices(entry) is None


class _FakeHomeAssistantError(Exception):
    """Stand-in for homeassistant.exceptions.HomeAssistantError.

    Under the root conftest.py's HA stubs, HomeAssistantError is a MagicMock
    attribute, not a real exception class -- an except tuple containing it
    would raise "catching classes that do not inherit from BaseException".
    nordpool_adapter.HomeAssistantError is patched with this real class
    wherever the except tuple in _async_fetch_native_date is exercised.
    """


@patch(
    "custom_components.energy_manager.nordpool_adapter.HomeAssistantError",
    _FakeHomeAssistantError,
)
class TestFetchNativeDateTransportErrors:
    """Transport failures must degrade to no prices, not escape the coordinator."""

    @pytest.mark.parametrize(
        "error",
        [
            aiohttp.ClientConnectorError(MagicMock(), OSError("Network unreachable")),
            aiohttp.ClientError("boom"),
            TimeoutError(),
            _FakeHomeAssistantError("entry not loaded"),
        ],
    )
    def test_transport_errors_return_empty(self, error):
        hass = MagicMock()
        hass.services.async_call = AsyncMock(side_effect=error)

        result = asyncio.run(
            _async_fetch_native_date(hass, "entry_id", date(2026, 3, 12))
        )

        assert result == []


class TestSplitByLocalDay:
    """Unit-level tests for the bucketing function itself."""

    def test_drops_slots_outside_today_and_tomorrow(self):
        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)
        slots = [
            {"start": "2026-03-11T23:00:00+01:00", "value": 1.0},
            {"start": "2026-03-12T10:00:00+01:00", "value": 2.0},
            {"start": "2026-03-13T10:00:00+01:00", "value": 3.0},
            {"start": "2026-03-14T00:00:00+01:00", "value": 4.0},
        ]

        today_prices, tomorrow_prices = split_by_local_day(slots, now)

        assert [s["value"] for s in today_prices] == [2.0]
        assert [s["value"] for s in tomorrow_prices] == [3.0]

    def test_sorts_by_start_time(self):
        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)
        slots = [
            {"start": "2026-03-12T02:00:00+01:00", "value": 2.0},
            {"start": "2026-03-12T00:00:00+01:00", "value": 0.0},
            {"start": "2026-03-12T01:00:00+01:00", "value": 1.0},
        ]

        today_prices, _tomorrow_prices = split_by_local_day(slots, now)

        assert [s["value"] for s in today_prices] == [0.0, 1.0, 2.0]

    def test_malformed_start_is_skipped(self):
        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)
        slots = [
            {"start": "not-a-timestamp", "value": 1.0},
            {"start": None, "value": 2.0},
            {"start": "2026-03-12T00:00:00+01:00", "value": 3.0},
        ]

        today_prices, _tomorrow_prices = split_by_local_day(slots, now)

        assert [s["value"] for s in today_prices] == [3.0]


class TestLocalDayBucketingViaCoordinatorCache:
    """"Today" must mean the caller's local day, not Nord Pool's CET delivery day.

    Exercises _get_native_coordinator_prices() with cached CET-day-shaped
    delivery periods (as the native coordinator stores them), the same way
    the fallback-free primary read path consumes them in production.
    """

    def test_eet_user_gets_full_local_day_starting_at_local_midnight(self):
        """At 14:00 Finnish time, "today" is the Finnish calendar day."""
        periods = [_cet_day(11), _cet_day(12), _cet_day(13)]
        config_entry = _config_entry(["SE4"], periods)
        now = datetime(2026, 3, 12, 14, 0, tzinfo=EET)

        with patch(
            "custom_components.energy_manager.nordpool_adapter.dt_util.now",
            return_value=now,
        ):
            result = _get_native_coordinator_prices(config_entry)

        assert result is not None
        today_prices, _tomorrow_prices = result

        local_dates = {
            datetime.fromisoformat(s["start"]).astimezone(EET).date()
            for s in today_prices
        }
        assert local_dates == {date(2026, 3, 12)}
        assert len(today_prices) == 24
        first = datetime.fromisoformat(today_prices[0]["start"]).astimezone(EET)
        assert (first.hour, first.minute) == (0, 0)

    def test_cet_user_unaffected(self):
        """No-regression proof: CET buckets match the old day-label behaviour."""
        periods = [_cet_day(12), _cet_day(13)]
        config_entry = _config_entry(["SE4"], periods)
        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)

        with patch(
            "custom_components.energy_manager.nordpool_adapter.dt_util.now",
            return_value=now,
        ):
            result = _get_native_coordinator_prices(config_entry)

        assert result is not None
        today_prices, tomorrow_prices = result
        assert len(today_prices) == 24
        assert len(tomorrow_prices) == 24
        assert today_prices[0]["start"] == "2026-03-12T00:00:00+01:00"
        assert tomorrow_prices[0]["start"] == "2026-03-13T00:00:00+01:00"


class TestAsyncGetNativePricesFallback:
    """Service-call fallback path: combined responses get bucketed by local day."""

    def test_combined_responses_bucketed_by_local_day(self):
        registry = MagicMock()
        entity_entry = MagicMock()
        entity_entry.config_entry_id = "entry_id"
        registry.async_get.return_value = entity_entry

        hass = MagicMock()
        # No native coordinator cache available.
        hass.config_entries.async_get_entry.return_value = None

        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)

        today_response = {
            "SE4": [
                {
                    "start": "2026-03-12T00:00:00+01:00",
                    "end": "2026-03-12T01:00:00+01:00",
                    "price": 500.0,
                },
            ]
        }
        tomorrow_response = {
            "SE4": [
                {
                    "start": "2026-03-13T00:00:00+01:00",
                    "end": "2026-03-13T01:00:00+01:00",
                    "price": 300.0,
                },
            ]
        }
        hass.services.async_call = AsyncMock(
            side_effect=[today_response, tomorrow_response]
        )

        with (
            patch(
                "custom_components.energy_manager.nordpool_adapter.er.async_get",
                return_value=registry,
            ),
            patch(
                "custom_components.energy_manager.nordpool_adapter.dt_util.now",
                return_value=now,
            ),
        ):
            raw_today, raw_tomorrow = asyncio.run(
                _async_get_native_prices(hass, "sensor.nordpool_se4")
            )

        assert len(raw_today) == 1
        assert raw_today[0]["value"] == pytest.approx(0.5)
        assert len(raw_tomorrow) == 1
        assert raw_tomorrow[0]["value"] == pytest.approx(0.3)

    def test_cache_hit_short_circuits_service_calls(self):
        """When the coordinator cache is usable, service calls are never made."""
        registry = MagicMock()
        entity_entry = MagicMock()
        entity_entry.config_entry_id = "entry_id"
        registry.async_get.return_value = entity_entry

        hass = MagicMock()
        config_entry = _config_entry(["SE4"], [_cet_day(12), _cet_day(13)])
        hass.config_entries.async_get_entry.return_value = config_entry
        hass.services.async_call = AsyncMock(
            side_effect=AssertionError("service call should not happen")
        )

        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)

        with (
            patch(
                "custom_components.energy_manager.nordpool_adapter.er.async_get",
                return_value=registry,
            ),
            patch(
                "custom_components.energy_manager.nordpool_adapter.dt_util.now",
                return_value=now,
            ),
        ):
            raw_today, raw_tomorrow = asyncio.run(
                _async_get_native_prices(hass, "sensor.nordpool_se4")
            )

        assert len(raw_today) == 24
        assert len(raw_tomorrow) == 24
        hass.services.async_call.assert_not_called()

    def test_cache_short_of_tomorrow_falls_back_to_service_calls(self):
        """A cache holding only today must not truncate the planning window.

        The native coordinator carries a delivery day only once its own
        refresh has picked it up, and until then answers with today complete
        and tomorrow empty. Accepting that as final ends every schedule at
        local midnight, so the service calls -- which do serve tomorrow --
        run instead.
        """
        registry = MagicMock()
        entity_entry = MagicMock()
        entity_entry.config_entry_id = "entry_id"
        registry.async_get.return_value = entity_entry

        hass = MagicMock()
        # Cache holds today only -- tomorrow has not landed in it yet.
        config_entry = _config_entry(["SE4"], [_cet_day(12)])
        hass.config_entries.async_get_entry.return_value = config_entry

        hass.services.async_call = AsyncMock(
            side_effect=[
                {"SE4": [_native_slot("2026-03-12T00:00:00+01:00", 500.0)]},
                {"SE4": [_native_slot("2026-03-13T00:00:00+01:00", 300.0)]},
            ]
        )

        now = datetime(2026, 3, 12, 14, 0, tzinfo=CET)

        with (
            patch(
                "custom_components.energy_manager.nordpool_adapter.er.async_get",
                return_value=registry,
            ),
            patch(
                "custom_components.energy_manager.nordpool_adapter.dt_util.now",
                return_value=now,
            ),
        ):
            raw_today, raw_tomorrow = asyncio.run(
                _async_get_native_prices(hass, "sensor.nordpool_se4")
            )

        assert [c.args[2]["date"] for c in hass.services.async_call.await_args_list] == [
            "2026-03-12",
            "2026-03-13",
        ]
        assert len(raw_today) == 1
        assert raw_today[0]["value"] == pytest.approx(0.5)
        assert len(raw_tomorrow) == 1
        assert raw_tomorrow[0]["value"] == pytest.approx(0.3)

    def test_eet_boundary_hour_is_not_mistaken_for_a_full_tomorrow(self):
        """One CET hour bucketed into local tomorrow is not a horizon.

        For a user ahead of CET, today's last CET hours already belong to
        their tomorrow, so a cache holding CET days up to today leaves the
        tomorrow bucket a few hours long -- non-empty, but not a horizon.
        Reading that as "tomorrow is present" would leave EET users
        (Finland, Baltics) planning against those few hours alone.
        """
        registry = MagicMock()
        entity_entry = MagicMock()
        entity_entry.config_entry_id = "entry_id"
        registry.async_get.return_value = entity_entry

        hass = MagicMock()
        config_entry = _config_entry(["SE4"], [_cet_day(11), _cet_day(12)])
        hass.config_entries.async_get_entry.return_value = config_entry
        hass.services.async_call = AsyncMock(
            side_effect=[
                {"SE4": [_native_slot("2026-03-12T00:00:00+01:00", 500.0)]},
                {"SE4": [_native_slot("2026-03-13T00:00:00+01:00", 300.0)]},
            ]
        )

        # EET is an hour ahead of CET: local tomorrow starts at 23:00 CET.
        now = datetime(2026, 3, 12, 16, 0, tzinfo=EET)

        with (
            patch(
                "custom_components.energy_manager.nordpool_adapter.er.async_get",
                return_value=registry,
            ),
            patch(
                "custom_components.energy_manager.nordpool_adapter.dt_util.now",
                return_value=now,
            ),
        ):
            # Precondition: the cache really does leave a sliver -- non-empty,
            # so an emptiness test would pass it, but nowhere near a day.
            cached = _get_native_coordinator_prices(config_entry)
            assert cached is not None
            assert 0 < len(cached[1]) < 24

            asyncio.run(_async_get_native_prices(hass, "sensor.nordpool_se4"))

        assert hass.services.async_call.await_count == 2

    def test_empty_service_response_keeps_the_cached_horizon(self):
        """Before publication the service has nothing -- the cache still stands.

        An empty today makes the price coordinator raise UpdateFailed, which
        takes every EM entity unavailable, so a short cache beats no cache.
        """
        registry = MagicMock()
        entity_entry = MagicMock()
        entity_entry.config_entry_id = "entry_id"
        registry.async_get.return_value = entity_entry

        hass = MagicMock()
        config_entry = _config_entry(["SE4"], [_cet_day(12)])
        hass.config_entries.async_get_entry.return_value = config_entry
        hass.services.async_call = AsyncMock(return_value={})

        now = datetime(2026, 3, 12, 9, 0, tzinfo=CET)

        with (
            patch(
                "custom_components.energy_manager.nordpool_adapter.er.async_get",
                return_value=registry,
            ),
            patch(
                "custom_components.energy_manager.nordpool_adapter.dt_util.now",
                return_value=now,
            ),
        ):
            raw_today, raw_tomorrow = asyncio.run(
                _async_get_native_prices(hass, "sensor.nordpool_se4")
            )

        assert len(raw_today) == 24
        assert raw_tomorrow == []


class TestCoversLocalTomorrow:
    """The coverage test that decides whether the cached horizon is enough."""

    @staticmethod
    def _slots(*ends: str) -> list[dict]:
        return [{"start": end, "end": end} for end in ends]

    def test_empty_is_not_covered(self):
        assert _covers_local_tomorrow([], datetime(2026, 3, 12, 14, 0, tzinfo=CET)) is False

    def test_reaching_local_midnight_is_covered(self):
        slots = self._slots("2026-03-14T00:00:00+01:00")
        assert _covers_local_tomorrow(slots, datetime(2026, 3, 12, 14, 0, tzinfo=CET))

    def test_one_hour_short_is_not_covered(self):
        slots = self._slots("2026-03-13T23:00:00+01:00")
        assert not _covers_local_tomorrow(slots, datetime(2026, 3, 12, 14, 0, tzinfo=CET))

    def test_naive_end_is_read_as_local(self):
        slots = self._slots("2026-03-14T00:00:00")
        assert _covers_local_tomorrow(slots, datetime(2026, 3, 12, 14, 0, tzinfo=CET))

    def test_malformed_end_is_not_covered(self):
        assert not _covers_local_tomorrow(
            [{"start": "x", "end": "not-a-date"}],
            datetime(2026, 3, 12, 14, 0, tzinfo=CET),
        )

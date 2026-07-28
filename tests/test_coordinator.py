"""Tests for coordinator.py's pure/testable helper functions (Wave E).

coordinator.py cannot be fully instantiated under the HA stubs (known
limitation, DataUpdateCoordinator subclassing breaks under the stub -- see
test_easee_coordinator_helpers.py), so this file targets only the small
pure/HA-light functions added for BATT-13 (multi-forecast summing) and
BATT-15 (house-consumption rolling average, sun.sun dawn/dusk reading):
- sum_solar_forecast_wh(): kWh/Wh-aware summing across multiple
  Forecast.Solar sensor readings.
- _prune_samples(): time-window pruning for the rolling consumption average.
- _should_sample_consumption(): minimum-interval gate for the rolling
  consumption average (event-driven refreshes must not append a sample on
  every tick).
- _read_sun_dawn_dusk(): reads sun.sun's next_dawn/next_dusk attributes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from custom_components.energy_manager.coordinator import (
    _prune_samples,
    _read_sun_dawn_dusk,
    _should_sample_consumption,
    sum_solar_forecast_wh,
)

UTC = timezone.utc


class FakeState:
    def __init__(self, state: str, attributes: dict | None = None) -> None:
        self.state = state
        self.attributes = attributes or {}


class FakeHass:
    def __init__(self, states: dict[str, FakeState]) -> None:
        self.states = MagicMock()
        self.states.get.side_effect = lambda entity_id: states.get(entity_id)


# ---------------------------------------------------------------------------
# sum_solar_forecast_wh() -- BATT-13 multi-forecast summing
# ---------------------------------------------------------------------------


def test_sum_solar_forecast_wh_single_wh_reading() -> None:
    assert sum_solar_forecast_wh([(1500.0, "Wh")]) == 1500.0


def test_sum_solar_forecast_wh_converts_kwh_to_wh() -> None:
    assert sum_solar_forecast_wh([(1.5, "kWh")]) == 1500.0


def test_sum_solar_forecast_wh_sums_mixed_units_across_sensors() -> None:
    """East array reports kWh, west array reports Wh -- both should sum."""
    result = sum_solar_forecast_wh([(1.2, "kWh"), (800.0, "Wh")])
    assert result == 2000.0


def test_sum_solar_forecast_wh_case_insensitive_unit() -> None:
    assert sum_solar_forecast_wh([(1.0, "KWH")]) == 1000.0


def test_sum_solar_forecast_wh_empty_readings_is_zero() -> None:
    assert sum_solar_forecast_wh([]) == 0.0


# ---------------------------------------------------------------------------
# _prune_samples() -- BATT-15 rolling consumption average window
# ---------------------------------------------------------------------------


def test_prune_samples_drops_entries_older_than_window() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    samples = [
        (now - timedelta(hours=49), 1.0),  # outside 48h window
        (now - timedelta(hours=47), 2.0),  # inside
        (now - timedelta(hours=1), 3.0),  # inside
    ]

    result = _prune_samples(samples, now, window_hours=48.0)

    assert result == [
        (now - timedelta(hours=47), 2.0),
        (now - timedelta(hours=1), 3.0),
    ]


def test_prune_samples_keeps_all_when_within_window() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    samples = [(now - timedelta(hours=1), 1.0), (now, 2.0)]

    assert _prune_samples(samples, now, window_hours=48.0) == samples


def test_prune_samples_empty_input() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    assert _prune_samples([], now, window_hours=48.0) == []


# ---------------------------------------------------------------------------
# _should_sample_consumption() -- minimum sample interval gate
# ---------------------------------------------------------------------------


def test_should_sample_consumption_no_prior_sample() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    assert _should_sample_consumption(None, now, min_interval_minutes=1.0) is True


def test_should_sample_consumption_rejects_within_interval() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    last_sample_at = now - timedelta(seconds=30)
    assert (
        _should_sample_consumption(last_sample_at, now, min_interval_minutes=1.0)
        is False
    )


def test_should_sample_consumption_allows_after_interval() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    last_sample_at = now - timedelta(minutes=1)
    assert (
        _should_sample_consumption(last_sample_at, now, min_interval_minutes=1.0)
        is True
    )


# ---------------------------------------------------------------------------
# _read_sun_dawn_dusk() -- BATT-15a sun.sun reader
# ---------------------------------------------------------------------------


def test_read_sun_dawn_dusk_parses_attributes() -> None:
    hass = FakeHass(
        {
            "sun.sun": FakeState(
                "above_horizon",
                {
                    "next_dawn": "2026-02-16T06:00:00+00:00",
                    "next_dusk": "2026-02-15T17:00:00+00:00",
                },
            )
        }
    )

    dawn, dusk = _read_sun_dawn_dusk(hass)

    assert dawn == datetime(2026, 2, 16, 6, 0, tzinfo=UTC)
    assert dusk == datetime(2026, 2, 15, 17, 0, tzinfo=UTC)


def test_read_sun_dawn_dusk_missing_entity_returns_none_pair() -> None:
    hass = FakeHass({})
    assert _read_sun_dawn_dusk(hass) == (None, None)


def test_read_sun_dawn_dusk_missing_attributes_returns_none_pair() -> None:
    hass = FakeHass({"sun.sun": FakeState("above_horizon", {})})
    assert _read_sun_dawn_dusk(hass) == (None, None)

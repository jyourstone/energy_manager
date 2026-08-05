"""Tests for coordinator.py's pure/testable helper functions (Wave E).

coordinator.py cannot be fully instantiated under the HA stubs (known
limitation, DataUpdateCoordinator subclassing breaks under the stub -- see
test_easee_coordinator_helpers.py), so this file targets only the small
pure/HA-light functions added for BATT-13 (multi-forecast summing),
BATT-15 (house-consumption rolling average, sun.sun dawn/dusk reading),
and BATT-16 (tomorrow forecast entity derivation):
- sum_solar_forecast_wh(): kWh/Wh-aware summing across multiple
  Forecast.Solar sensor readings.
- derive_tomorrow_forecast_entities(): BATT-16 auto-derivation of
  Forecast.Solar "tomorrow" entity ids from configured "remaining today" ids.
- _prune_samples(): time-window pruning for the rolling consumption average.
- _serialize_samples()/_restore_samples(): persistence round-trip for the
  rolling consumption window (survives HA restarts via Store).
- _should_sample_consumption(): minimum-interval gate for the rolling
  consumption average (event-driven refreshes must not append a sample on
  every tick).
- _read_sun_dawn_dusk(): reads sun.sun's next_dawn/next_dusk attributes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from custom_components.energy_manager.coordinator import (
    EMSData,
    _prune_samples,
    _read_sun_dawn_dusk,
    _restore_samples,
    _serialize_samples,
    _should_sample_consumption,
    derive_tomorrow_forecast_entities,
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
# derive_tomorrow_forecast_entities() -- BATT-16 tomorrow-entity derivation
# ---------------------------------------------------------------------------


def test_derive_tomorrow_forecast_entities_maps_both_prod_entities() -> None:
    """Both Forecast.Solar naming variants map, incl. the _2 array suffix."""
    result = derive_tomorrow_forecast_entities(
        [
            "sensor.energy_production_today_remaining",
            "sensor.energy_production_today_remaining_2",
        ]
    )

    assert result == [
        "sensor.energy_production_tomorrow",
        "sensor.energy_production_tomorrow_2",
    ]


def test_derive_tomorrow_forecast_entities_drops_non_matching() -> None:
    result = derive_tomorrow_forecast_entities(
        [
            "sensor.energy_production_today_remaining",
            "sensor.my_custom_solar_forecast",
        ]
    )

    assert result == ["sensor.energy_production_tomorrow"]


def test_derive_tomorrow_forecast_entities_empty_input() -> None:
    assert derive_tomorrow_forecast_entities([]) == []


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
# _serialize_samples() / _restore_samples() -- BATT-15 persistence
# ---------------------------------------------------------------------------


def test_serialize_restore_round_trip() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    samples = [
        (now - timedelta(hours=2), 1.5),
        (now - timedelta(minutes=5), 0.8),
    ]

    restored = _restore_samples(
        _serialize_samples(samples), now, window_hours=48.0
    )

    assert restored == samples


def test_restore_samples_prunes_outside_window() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    samples = [
        (now - timedelta(hours=49), 1.0),  # outside 48h window
        (now - timedelta(hours=1), 2.0),  # inside
    ]

    restored = _restore_samples(
        _serialize_samples(samples), now, window_hours=48.0
    )

    assert restored == [(now - timedelta(hours=1), 2.0)]


def test_restore_samples_tolerates_none_and_garbage() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)

    assert _restore_samples(None, now, window_hours=48.0) == []
    assert _restore_samples("garbage", now, window_hours=48.0) == []
    assert _restore_samples(42, now, window_hours=48.0) == []
    assert _restore_samples({"a": 1}, now, window_hours=48.0) == []


def test_restore_samples_skips_malformed_entries() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    valid_ts = (now - timedelta(hours=1)).isoformat()
    raw = [
        [valid_ts, 1.5],  # valid
        ["not-a-timestamp", 2.0],  # unparseable timestamp
        [123, 2.0],  # non-string timestamp
        [valid_ts, "not-a-number"],  # unparseable value
        [valid_ts],  # wrong arity
        "junk",
        None,
    ]

    restored = _restore_samples(raw, now, window_hours=48.0)

    assert restored == [(now - timedelta(hours=1), 1.5)]


def test_restore_samples_drops_future_timestamps() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    raw = _serialize_samples(
        [
            (now + timedelta(hours=1), 9.9),  # future (e.g. clock skew)
            (now - timedelta(hours=1), 1.0),
        ]
    )

    restored = _restore_samples(raw, now, window_hours=48.0)

    assert restored == [(now - timedelta(hours=1), 1.0)]


def test_restore_samples_coerces_timestamps_to_utc() -> None:
    now = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    raw = [
        ["2026-02-15T11:00:00+01:00", 1.0],  # offset-aware -> 10:00 UTC
        ["2026-02-15T09:00:00", 2.0],  # naive -> treated as UTC
    ]

    restored = _restore_samples(raw, now, window_hours=48.0)

    assert restored == [
        (datetime(2026, 2, 15, 10, 0, tzinfo=UTC), 1.0),
        (datetime(2026, 2, 15, 9, 0, tzinfo=UTC), 2.0),
    ]


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


# ---------------------------------------------------------------------------
# EMSData -- GEN-02 discharge-limit command fields
# ---------------------------------------------------------------------------


def _minimal_ems_data(**overrides) -> EMSData:
    """EMSData with only the required (non-default) fields filled."""
    kwargs = {
        "current_mode": "standby",
        "target_mode": "standby",
        "charge_limit_kw": 0.0,
        "fuse_headroom_amps": 18.0,
        "override_reason": None,
        "command_verified": True,
        "last_command_time": None,
        "car_override_active": False,
        "pv_charging_active": False,
        "dry_run": True,
        "last_suppressed_command": None,
    }
    kwargs.update(overrides)
    return EMSData(**kwargs)


def test_ems_data_discharge_limit_defaults() -> None:
    """New GEN-02 fields default to not-computed / not-delivered.

    The schedule-less early return constructs EMSData without them, so
    the defaults must be honest: no commanded limit, nothing delivered.
    """
    data = _minimal_ems_data()

    assert data.discharge_limit_kw is None
    assert data.discharge_limit_delivered is False


def test_ems_data_discharge_limit_fields_carried() -> None:
    """The commanded discharge limit and delivered flag pass through."""
    data = _minimal_ems_data(
        discharge_limit_kw=4.2, discharge_limit_delivered=True
    )

    assert data.discharge_limit_kw == 4.2
    assert data.discharge_limit_delivered is True

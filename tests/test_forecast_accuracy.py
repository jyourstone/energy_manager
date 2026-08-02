"""Tests for the pure forecast_accuracy module (Stage 1, observe-only).

Covers the daily-record guards (skip near-zero forecasts, cap actuals),
the recency-weighted suggested factor (clamping, minimum valid days,
14-day window), the serialize/restore persistence round-trip, the
trapezoidal PV energy accumulator, and the pre-dawn snapshot gate.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.energy_manager.forecast_accuracy import (
    FACTOR_WINDOW_DAYS,
    MAX_HISTORY_DAYS,
    MIN_VALID_DAYS,
    DailyAccuracyRecord,
    accumulate_energy_kwh,
    append_day,
    is_before_dawn,
    mean_ratio,
    restore_history,
    serialize_history,
    suggested_factor,
    valid_ratios,
)

UTC = timezone.utc


def _record(day: int, forecast: float, actual: float) -> DailyAccuracyRecord:
    return DailyAccuracyRecord(date(2026, 3, 1) + timedelta(days=day), forecast, actual)


def _history(ratios: list[float], forecast: float = 10.0) -> list[DailyAccuracyRecord]:
    """Build a history whose valid ratios are exactly `ratios` (oldest first)."""
    return [_record(i, forecast, forecast * r) for i, r in enumerate(ratios)]


# ---------------------------------------------------------------------------
# append_day() -- guards
# ---------------------------------------------------------------------------


def test_append_day_appends_valid_record() -> None:
    record = _record(0, 10.0, 8.0)

    result = append_day([], record)

    assert result == [record]


def test_append_day_skips_forecast_below_half_kwh() -> None:
    history = [_record(0, 10.0, 8.0)]

    result = append_day(history, _record(1, 0.4, 5.0))

    assert result == history
    assert result is not history  # always a new list, input untouched


def test_append_day_forecast_exactly_at_threshold_is_kept() -> None:
    result = append_day([], _record(0, 0.5, 0.4))

    assert len(result) == 1


def test_append_day_caps_actual_at_twice_forecast() -> None:
    result = append_day([], _record(0, 5.0, 15.0))

    assert result == [_record(0, 5.0, 10.0)]


def test_append_day_caps_history_length() -> None:
    history: list[DailyAccuracyRecord] = []
    for day in range(MAX_HISTORY_DAYS + 5):
        history = append_day(history, _record(day, 10.0, 8.0))

    assert len(history) == MAX_HISTORY_DAYS
    assert history[0].date == _record(5, 10.0, 8.0).date  # oldest dropped


# ---------------------------------------------------------------------------
# valid_ratios() / mean_ratio()
# ---------------------------------------------------------------------------


def test_valid_ratios_excludes_invalid_restored_records() -> None:
    """Records below the forecast floor (e.g. old storage) are excluded."""
    history = [
        _record(0, 10.0, 8.0),
        _record(1, 0.3, 0.3),  # invalid: below MIN_FORECAST_KWH
        _record(2, 10.0, 6.0),
    ]

    assert valid_ratios(history) == [0.8, 0.6]


def test_valid_ratios_keeps_only_last_window() -> None:
    history = _history([0.5] * 3 + [0.9] * FACTOR_WINDOW_DAYS)

    ratios = valid_ratios(history)

    assert len(ratios) == FACTOR_WINDOW_DAYS
    assert ratios == [0.9] * FACTOR_WINDOW_DAYS


def test_mean_ratio_empty_is_none() -> None:
    assert mean_ratio([]) is None


def test_mean_ratio_plain_mean() -> None:
    assert mean_ratio([1.0, 0.5]) == 0.75


# ---------------------------------------------------------------------------
# suggested_factor() -- recency weighting, clamping, minimum valid days
# ---------------------------------------------------------------------------


def test_suggested_factor_none_under_min_valid_days() -> None:
    assert suggested_factor(_history([0.8] * (MIN_VALID_DAYS - 1))) is None


def test_suggested_factor_available_at_min_valid_days() -> None:
    result = suggested_factor(_history([0.8] * MIN_VALID_DAYS))

    assert result == pytest.approx(0.8)


def test_suggested_factor_weighs_recent_days_heavier() -> None:
    """Six days at 0.6, newest day 0.9: linear weights 1..7 give
    (0.6*21 + 0.9*7) / 28 = 0.675, above the unweighted mean 0.643."""
    result = suggested_factor(_history([0.6] * 6 + [0.9]))

    assert result == pytest.approx(0.675)


def test_suggested_factor_recent_low_day_pulls_down() -> None:
    """Mirror case: newest day low means weighted < unweighted mean."""
    result = suggested_factor(_history([0.9] * 6 + [0.6]))

    assert result == pytest.approx(0.825)


def test_suggested_factor_clamps_low() -> None:
    assert suggested_factor(_history([0.2] * 10)) == 0.5


def test_suggested_factor_clamps_high() -> None:
    assert suggested_factor(_history([1.5] * 10)) == 1.0


def test_suggested_factor_uses_only_last_14_valid_ratios() -> None:
    """Old out-of-window low ratios must not drag the factor down."""
    result = suggested_factor(_history([0.2] * 6 + [0.8] * FACTOR_WINDOW_DAYS))

    assert result == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# serialize_history() / restore_history() -- persistence
# ---------------------------------------------------------------------------


def test_serialize_restore_round_trip() -> None:
    history = [_record(0, 12.5, 9.75), _record(1, 8.0, 8.2)]

    assert restore_history(serialize_history(history)) == history


def test_serialize_history_shape() -> None:
    assert serialize_history([_record(0, 12.5, 9.75)]) == [
        {"date": "2026-03-01", "forecast_kwh": 12.5, "actual_kwh": 9.75}
    ]


def test_restore_history_tolerates_none_and_garbage() -> None:
    assert restore_history(None) == []
    assert restore_history("garbage") == []
    assert restore_history(42) == []
    assert restore_history({"a": 1}) == []


def test_restore_history_skips_malformed_entries() -> None:
    raw = [
        {"date": "2026-03-01", "forecast_kwh": 10.0, "actual_kwh": 8.0},  # valid
        {"date": "not-a-date", "forecast_kwh": 10.0, "actual_kwh": 8.0},
        {"date": "2026-03-02", "forecast_kwh": "junk", "actual_kwh": 8.0},
        {"forecast_kwh": 10.0, "actual_kwh": 8.0},  # missing date
        ["2026-03-03", 10.0, 8.0],  # wrong shape
        "junk",
        None,
    ]

    assert restore_history(raw) == [_record(0, 10.0, 8.0)]


def test_restore_history_caps_length_keeping_newest() -> None:
    raw = serialize_history([_record(d, 10.0, 8.0) for d in range(MAX_HISTORY_DAYS + 10)])

    restored = restore_history(raw)

    assert len(restored) == MAX_HISTORY_DAYS
    assert restored[-1].date == _record(MAX_HISTORY_DAYS + 9, 10.0, 8.0).date


# ---------------------------------------------------------------------------
# accumulate_energy_kwh() -- trapezoidal PV energy accumulator
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def test_accumulate_first_sample_only_anchors() -> None:
    assert accumulate_energy_kwh(None, NOW, 3.0, 15.0) == (0.0, (NOW, 3.0))


def test_accumulate_unavailable_reading_keeps_anchor() -> None:
    last = (NOW - timedelta(minutes=5), 2.0)

    assert accumulate_energy_kwh(last, NOW, None, 15.0) == (0.0, last)


def test_accumulate_unavailable_with_no_anchor() -> None:
    assert accumulate_energy_kwh(None, NOW, None, 15.0) == (0.0, None)


def test_accumulate_trapezoid_between_samples() -> None:
    """6 min at mean of 2 and 4 kW: 0.1 h * 3 kW = 0.3 kWh."""
    last = (NOW - timedelta(minutes=6), 2.0)

    delta, new_last = accumulate_energy_kwh(last, NOW, 4.0, 15.0)

    assert delta == pytest.approx(0.3)
    assert new_last == (NOW, 4.0)


def test_accumulate_gap_over_max_reanchors_without_energy() -> None:
    last = (NOW - timedelta(minutes=16), 2.0)

    assert accumulate_energy_kwh(last, NOW, 4.0, 15.0) == (0.0, (NOW, 4.0))


def test_accumulate_non_positive_gap_reanchors_without_energy() -> None:
    assert accumulate_energy_kwh((NOW, 2.0), NOW, 4.0, 15.0) == (0.0, (NOW, 4.0))


def test_accumulate_clamps_negative_reading_to_zero() -> None:
    """Night-time sensor drift must never subtract energy."""
    last = (NOW - timedelta(minutes=6), 0.0)

    assert accumulate_energy_kwh(last, NOW, -0.05, 15.0) == (0.0, (NOW, 0.0))


# ---------------------------------------------------------------------------
# is_before_dawn() -- pre-dawn snapshot gate
# ---------------------------------------------------------------------------


def test_is_before_dawn_next_dawn_today() -> None:
    assert is_before_dawn(date(2026, 3, 1), date(2026, 3, 1)) is True


def test_is_before_dawn_next_dawn_tomorrow() -> None:
    assert is_before_dawn(date(2026, 3, 1), date(2026, 3, 2)) is False


def test_is_before_dawn_unknown_dawn() -> None:
    assert is_before_dawn(date(2026, 3, 1), None) is False

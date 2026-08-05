"""Pure solar-forecast accuracy telemetry (Stage 1, observe-only).

The BatteryScheduleCoordinator snapshots the Forecast.Solar day total
before dawn, trapezoid-integrates the PV power entity into a daily
actual-kWh accumulator, and appends one DailyAccuracyRecord per day at
the local-midnight rollover. A diagnostic sensor exposes the suggested
production factor derived here. Nothing in this module feeds the
scheduler -- the configured production factor is applied unchanged
(Stage 2 is post-cutover).

All functions are pure and HA-free so they can be unit tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# Days with a forecast below this are skipped entirely -- a ratio against
# a near-zero forecast is meaningless and would swing the suggested factor.
MIN_FORECAST_KWH = 0.5
# Actuals are capped at this multiple of the forecast so one bad PV-power
# reading (or a wildly low forecast) cannot dominate the history.
ACTUAL_CAP_MULTIPLE = 2.0
# suggested_factor() ratio window, validity floor, and clamp range (the
# clamp mirrors production_factor's plausible correction range).
FACTOR_WINDOW_DAYS = 14
MIN_VALID_DAYS = 7
FACTOR_MIN = 0.5
FACTOR_MAX = 1.0
# Records kept in history (and the Store): covers the 14-day factor window
# plus context in the sensor's history attribute, without unbounded growth.
MAX_HISTORY_DAYS = 30
# PV samples further apart than this are not integrated -- bridges short
# unavailable blips but never invents production across long outages or
# restarts.
MAX_SAMPLE_GAP_MINUTES = 15.0


@dataclass(frozen=True, slots=True)
class DailyAccuracyRecord:
    """One day's forecast-vs-actual PV production, both in kWh."""

    date: date
    forecast_kwh: float
    actual_kwh: float


@dataclass(frozen=True, slots=True)
class InFlightDay:
    """The current (not yet rolled-over) day's accuracy state.

    Persisted alongside the history so a config-entry reload or HA restart
    mid-day does not lose the pre-dawn snapshot and the kWh accumulated so
    far. snapshot_kwh is None until the pre-dawn snapshot succeeds. The PV
    integration anchor is deliberately NOT part of this -- on restore the
    first PV sample re-anchors via accumulate_energy_kwh's gap guard, so at
    most ~one refresh interval of production is lost per reload.
    """

    day: date
    snapshot_kwh: float | None
    accumulated_kwh: float


def append_day(
    history: list[DailyAccuracyRecord], record: DailyAccuracyRecord
) -> list[DailyAccuracyRecord]:
    """Append a day's record to the history, applying the validity guards.

    Days with a forecast below MIN_FORECAST_KWH are skipped entirely, and
    the actual is capped at ACTUAL_CAP_MULTIPLE x forecast. Returns a new
    list capped to the newest MAX_HISTORY_DAYS records.
    """
    if record.forecast_kwh < MIN_FORECAST_KWH:
        return list(history)
    actual_cap = record.forecast_kwh * ACTUAL_CAP_MULTIPLE
    if record.actual_kwh > actual_cap:
        record = DailyAccuracyRecord(record.date, record.forecast_kwh, actual_cap)
    return [*history, record][-MAX_HISTORY_DAYS:]


def valid_ratios(history: list[DailyAccuracyRecord]) -> list[float]:
    """Actual/forecast ratios of the last FACTOR_WINDOW_DAYS valid records.

    Oldest first. Validity re-checks forecast >= MIN_FORECAST_KWH
    defensively -- restored storage may predate the append_day() guards.
    """
    return [
        record.actual_kwh / record.forecast_kwh
        for record in history
        if record.forecast_kwh >= MIN_FORECAST_KWH
    ][-FACTOR_WINDOW_DAYS:]


def mean_ratio(ratios: list[float]) -> float | None:
    """Plain (unweighted) mean of the given ratios, or None when empty."""
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def suggested_factor(history: list[DailyAccuracyRecord]) -> float | None:
    """Recency-weighted mean ratio, clamped to [FACTOR_MIN, FACTOR_MAX].

    Linear recency weights (1..n, newest heaviest) over the last
    FACTOR_WINDOW_DAYS valid ratios. Returns None until MIN_VALID_DAYS
    valid days exist so a few early records cannot steer the suggestion.
    """
    ratios = valid_ratios(history)
    if len(ratios) < MIN_VALID_DAYS:
        return None
    weights = range(1, len(ratios) + 1)
    factor = sum(r * w for r, w in zip(ratios, weights, strict=True)) / sum(weights)
    return min(max(factor, FACTOR_MIN), FACTOR_MAX)


def serialize_history(history: list[DailyAccuracyRecord]) -> list[dict]:
    """Serialize accuracy records to a JSON-storable shape."""
    return [
        {
            "date": record.date.isoformat(),
            "forecast_kwh": record.forecast_kwh,
            "actual_kwh": record.actual_kwh,
        }
        for record in history
    ]


def restore_history(raw: object) -> list[DailyAccuracyRecord]:
    """Restore accuracy records persisted by serialize_history().

    Tolerates None/garbage (returns []), skips malformed entries, and caps
    the result to the newest MAX_HISTORY_DAYS records.
    """
    if not isinstance(raw, list):
        return []
    history: list[DailyAccuracyRecord] = []
    for entry in raw:
        try:
            record = DailyAccuracyRecord(
                date.fromisoformat(entry["date"]),
                float(entry["forecast_kwh"]),
                float(entry["actual_kwh"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        history.append(record)
    return history[-MAX_HISTORY_DAYS:]


def serialize_fa_store(
    history: list[DailyAccuracyRecord], in_flight: InFlightDay | None
) -> dict:
    """Serialize the forecast-accuracy Store payload (history + in-flight day).

    Wraps serialize_history() in a dict so the current day's state can ride
    along; restore_fa_store() still accepts the pre-wrapper bare-list shape.
    snapshot_kwh None round-trips as null, never 0.0 -- a 0.0 snapshot would
    count as "captured" and append a bogus zero-forecast day at rollover.
    """
    return {
        "history": serialize_history(history),
        "in_flight": None
        if in_flight is None
        else {
            "day": in_flight.day.isoformat(),
            "snapshot_kwh": in_flight.snapshot_kwh,
            "accumulated_kwh": in_flight.accumulated_kwh,
        },
    }


def restore_fa_store(
    raw: object, today: date
) -> tuple[list[DailyAccuracyRecord], InFlightDay | None]:
    """Restore the payload persisted by serialize_fa_store().

    Accepts the legacy bare-list payload (history only, no in-flight day)
    and the current dict shape; anything else restores to ([], None). A
    malformed in-flight block (bad date, non-numeric kWh, missing key)
    drops only the in-flight day, never the history.

    Stale guard: the in-flight day is accepted only when it is today or
    yesterday (relative to the caller's local date). Yesterday is fine --
    the coordinator's rollover appends it on the first refresh. A future
    day means clock skew or garbage; anything older means a multi-day
    outage whose truncated actual would bias the suggested factor low.
    Accepted tradeoff: a yesterday-day truncated by an outage that began
    mid-production is still appended and biases that one ratio low; the
    common cases (quick reload, overnight restart) lose nothing, and one
    low day out of the 14-day window is bounded by the factor clamp.
    """
    if isinstance(raw, list):
        return restore_history(raw), None
    if not isinstance(raw, dict):
        return [], None
    history = restore_history(raw.get("history"))
    in_flight: InFlightDay | None = None
    raw_in_flight = raw.get("in_flight")
    if raw_in_flight is not None:
        try:
            snapshot = raw_in_flight["snapshot_kwh"]
            in_flight = InFlightDay(
                date.fromisoformat(raw_in_flight["day"]),
                None if snapshot is None else float(snapshot),
                float(raw_in_flight["accumulated_kwh"]),
            )
        except (KeyError, TypeError, ValueError):
            in_flight = None
    if in_flight is not None and in_flight.day not in (
        today,
        today - timedelta(days=1),
    ):
        in_flight = None
    return history, in_flight


def accumulate_energy_kwh(
    last_sample: tuple[datetime, float] | None,
    now: datetime,
    power_kw: float | None,
    max_gap_minutes: float,
) -> tuple[float, tuple[datetime, float] | None]:
    """One trapezoidal-integration step of the daily PV energy accumulator.

    Returns (kwh_delta, new_last_sample). An unavailable reading
    (power_kw None) contributes nothing and keeps the previous anchor so a
    short blip still integrates across it; gaps longer than
    max_gap_minutes (or non-positive) contribute nothing and re-anchor, so
    production is never invented across long outages or restarts.
    Negative readings (night-time sensor drift) clamp to zero.
    """
    if power_kw is None:
        return 0.0, last_sample
    power_kw = max(power_kw, 0.0)
    if last_sample is None:
        return 0.0, (now, power_kw)
    gap_hours = (now - last_sample[0]).total_seconds() / 3600.0
    if gap_hours <= 0 or gap_hours > max_gap_minutes / 60.0:
        return 0.0, (now, power_kw)
    return gap_hours * (power_kw + last_sample[1]) / 2.0, (now, power_kw)


def is_before_dawn(today: date, next_dawn_date: date | None) -> bool:
    """True when now (whose local date is today) precedes today's dawn.

    sun.sun exposes only the NEXT dawn: before today's dawn it falls on
    today's local date, after it on tomorrow's -- so date equality is the
    before-dawn test. False when dawn is unknown (skipping the snapshot
    beats mistiming it).
    """
    return next_dawn_date == today

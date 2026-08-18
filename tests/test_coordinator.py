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
- _cancel_unsub(): call-if-set helper shared by PriceCoordinator's
  event-driven refresh listeners.
- _is_recent_nordpool_refresh(): pure suppression-window predicate behind
  PriceCoordinator._recent_nordpool_refresh_request().
- _find_native_coordinator(): entity registry -> config entry -> runtime_data
  lookup behind PriceCoordinator._subscribe_native_coordinator().
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.energy_manager.const import FALLBACK_STALE_THRESHOLD_MINUTES
from custom_components.energy_manager.coordinator import (
    CarChargingData,
    EMSData,
    _apply_power_caps,
    _cancel_unsub,
    _find_native_coordinator,
    _is_recent_nordpool_refresh,
    _prune_samples,
    _read_soc_timestamp,
    _read_sun_dawn_dusk,
    _restore_samples,
    _serialize_samples,
    _should_sample_consumption,
    derive_tomorrow_forecast_entities,
    sum_solar_forecast_wh,
)
from custom_components.energy_manager import coordinator as coordinator_module

UTC = timezone.utc


class FakeState:
    def __init__(
        self,
        state: str,
        attributes: dict | None = None,
        last_updated: datetime | None = None,
        last_reported: datetime | None = None,
    ) -> None:
        self.state = state
        self.attributes = attributes or {}
        self.last_updated = last_updated
        self.last_reported = last_reported


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


# ---------------------------------------------------------------------------
# _read_soc_timestamp() -- EV-08 guest-fallback staleness fix
#
# CarChargingCoordinator itself cannot be instantiated (or even
# introspected for its methods) under the homeassistant stub -- see the
# module docstring above -- so this covers the extracted staleness-field
# helper directly, and composes it with the same threshold constant
# _detect_fallback_needed() compares against to prove the actual
# incident scenario no longer misfires.
# ---------------------------------------------------------------------------


def test_read_soc_timestamp_uses_last_reported_not_last_updated() -> None:
    """last_updated only advances on a state VALUE change -- a parked car
    with a constant SOC must not be tracked by that field, or it would
    look permanently stale. last_reported advances on every write."""
    state = FakeState(
        "55",
        last_updated=datetime(2026, 8, 12, 9, 0, tzinfo=UTC),
        last_reported=datetime(2026, 8, 12, 11, 55, tzinfo=UTC),
    )

    assert _read_soc_timestamp(state) == datetime(2026, 8, 12, 11, 55, tzinfo=UTC)


def test_fallback_not_stale_when_last_reported_is_fresh() -> None:
    """Regression test for the guest-fallback misfire: a parked car whose
    SOC value hasn't changed in 2h (stale last_updated) but whose
    integration keeps polling (fresh last_reported) must not trip
    _detect_fallback_needed()'s staleness threshold."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    state = FakeState(
        "55",
        last_updated=now - timedelta(hours=2),
        last_reported=now - timedelta(minutes=5),
    )

    soc_last_updated = _read_soc_timestamp(state)
    elapsed = (now - soc_last_updated).total_seconds()

    assert elapsed <= FALLBACK_STALE_THRESHOLD_MINUTES * 60


def test_fallback_stale_when_last_reported_is_also_old() -> None:
    """When last_reported itself is stale (integration truly silent, or
    an unrecognized guest car), the staleness threshold must still
    trip."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    state = FakeState(
        "55",
        last_updated=now - timedelta(hours=2),
        last_reported=now - timedelta(hours=2),
    )

    soc_last_updated = _read_soc_timestamp(state)
    elapsed = (now - soc_last_updated).total_seconds()

    assert elapsed > FALLBACK_STALE_THRESHOLD_MINUTES * 60


# ---------------------------------------------------------------------------
# CarChargingData.fallback_mode -- EV-08 guest-fallback surfacing
# ---------------------------------------------------------------------------


def _minimal_car_charging_data(**overrides) -> CarChargingData:
    """CarChargingData with only the required (non-default) fields filled."""
    kwargs = {
        "current_action": "idle",
        "schedule": [],
        "charging_slot_count": 0,
        "energy_needed_kwh": 0.0,
        "hours_needed": 0.0,
        "is_preliminary": False,
        "car_name": "Test Car",
        "current_soc": 50.0,
        "target_soc": 80.0,
        "last_calculated": datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        "home_and_plugged": False,
        "phase_capability": 3,
        "max_charge_power_kw": 7.4,
    }
    kwargs.update(overrides)
    return CarChargingData(**kwargs)


def test_car_charging_data_fallback_mode_defaults_false() -> None:
    """A normal (non-guest) schedule must not be flagged as fallback."""
    assert _minimal_car_charging_data().fallback_mode is False


def test_car_charging_data_fallback_mode_carried() -> None:
    """The EV-08 guest-fallback flag passes through when set."""
    assert _minimal_car_charging_data(fallback_mode=True).fallback_mode is True


# ---------------------------------------------------------------------------
# _apply_power_caps() -- hardware discharge-power caps
#
# SigenStor rejects limit-register writes above the plant's rated discharge
# power with exception_code=2 (incident 2026-08-15), and the available
# power drops below rated at low SOC -- _send_discharge_limit() clamps the
# requested kW against both optional cap sensors via this helper before
# sending.
# ---------------------------------------------------------------------------


def test_apply_power_caps_clamps_to_tighter_of_both_caps() -> None:
    hass = FakeHass(
        {
            "sensor.available_discharge_power": FakeState("21.6"),
            "sensor.rated_discharge_power": FakeState("14.4"),
        }
    )
    caps = ("sensor.available_discharge_power", "sensor.rated_discharge_power")
    assert _apply_power_caps(hass, 15.0, caps) == 14.4


def test_apply_power_caps_unavailable_caps_fall_back_to_old_behavior() -> None:
    hass = FakeHass(
        {
            "sensor.available_discharge_power": FakeState("unavailable"),
            "sensor.rated_discharge_power": FakeState("unavailable"),
        }
    )
    caps = ("sensor.available_discharge_power", "sensor.rated_discharge_power")
    assert _apply_power_caps(hass, 15.0, caps) == 15.0


def test_apply_power_caps_clamps_to_available_cap_alone() -> None:
    hass = FakeHass({"sensor.available_discharge_power": FakeState("3.2")})
    caps = ("sensor.available_discharge_power", "")
    assert _apply_power_caps(hass, 15.0, caps) == 3.2


def test_apply_power_caps_non_numeric_cap_skipped_falls_back_to_other_cap() -> None:
    hass = FakeHass(
        {
            "sensor.available_discharge_power": FakeState("garbage"),
            "sensor.rated_discharge_power": FakeState("14.4"),
        }
    )
    caps = ("sensor.available_discharge_power", "sensor.rated_discharge_power")
    assert _apply_power_caps(hass, 15.0, caps) == 14.4


def test_apply_power_caps_unconfigured_returns_requested_value() -> None:
    assert _apply_power_caps(FakeHass({}), 15.0, ("", "")) == 15.0


def test_apply_power_caps_negative_read_treated_as_unreadable() -> None:
    hass = FakeHass({"sensor.available_discharge_power": FakeState("-1.0")})
    caps = ("sensor.available_discharge_power", "")
    assert _apply_power_caps(hass, 15.0, caps) == 15.0


def test_apply_power_caps_watt_unit_normalized_to_kw() -> None:
    """A W-unit cap entity (e.g. 5000 W) must clamp as 5.0 kW, not 5000 kW."""
    hass = FakeHass(
        {
            "sensor.rated_discharge_power": FakeState(
                "5000", {"unit_of_measurement": "W"}
            )
        }
    )
    caps = ("sensor.rated_discharge_power", "")
    assert _apply_power_caps(hass, 15.0, caps) == 5.0


def test_apply_power_caps_unitless_helper_assumed_kw() -> None:
    """A unitless input_number holding 14.4 means 14.4 kW -- never 14.4 W."""
    hass = FakeHass({"input_number.rated_discharge_power": FakeState("14.4")})
    caps = ("input_number.rated_discharge_power", "")
    assert _apply_power_caps(hass, 15.0, caps) == 14.4


# ---------------------------------------------------------------------------
# PriceCoordinator event-driven refresh -- _cancel_unsub(),
# _is_recent_nordpool_refresh(), _find_native_coordinator()
#
# PriceCoordinator itself cannot be instantiated under the HA stub (see
# module docstring), so these test the pure/HA-light functions its
# refresh-tracking instance methods (_schedule_clock_refresh,
# _request_refresh_from_nordpool, _subscribe_native_coordinator,
# async_shutdown) delegate to.
# ---------------------------------------------------------------------------


class TestCancelUnsub:
    """_cancel_unsub() -- shared call-if-set helper used by every listener
    PriceCoordinator owns (clock refresh, delayed clock refresh, Nord Pool
    state listener, native coordinator listener)."""

    def test_calls_unsub_when_set(self) -> None:
        unsub = MagicMock()
        _cancel_unsub(unsub)
        unsub.assert_called_once()

    def test_noop_when_none(self) -> None:
        _cancel_unsub(None)  # must not raise


class TestIsRecentNordpoolRefresh:
    """Pure suppression-window predicate behind
    PriceCoordinator._recent_nordpool_refresh_request() -- used by
    _schedule_clock_refresh() to skip the clock-aligned fallback when Nord
    Pool already requested a refresh near the same boundary."""

    def test_false_when_never_requested(self) -> None:
        now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
        assert _is_recent_nordpool_refresh(None, now, 20) is False

    def test_true_within_suppression_window(self) -> None:
        now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
        last_request = now - timedelta(seconds=19)
        assert _is_recent_nordpool_refresh(last_request, now, 20) is True

    def test_true_exactly_at_boundary(self) -> None:
        now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
        last_request = now - timedelta(seconds=20)
        assert _is_recent_nordpool_refresh(last_request, now, 20) is True

    def test_false_after_suppression_window(self) -> None:
        now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
        last_request = now - timedelta(seconds=21)
        assert _is_recent_nordpool_refresh(last_request, now, 20) is False


def _hass_with_registry(
    entity_entry: object | None, config_entry: object | None = None
) -> tuple[MagicMock, MagicMock]:
    """Build a hass double whose entity registry returns entity_entry."""
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = config_entry
    registry = MagicMock()
    registry.async_get.return_value = entity_entry
    return hass, registry


class TestFindNativeCoordinator:
    """Entity registry -> config entry -> runtime_data lookup behind
    PriceCoordinator._subscribe_native_coordinator()."""

    def test_returns_none_when_entity_not_registered(self) -> None:
        hass, registry = _hass_with_registry(entity_entry=None)
        with patch.object(coordinator_module.er, "async_get", return_value=registry):
            assert _find_native_coordinator(hass, "sensor.nordpool") is None

    def test_returns_none_when_entity_has_no_config_entry_id(self) -> None:
        entity_entry = SimpleNamespace(config_entry_id=None)
        hass, registry = _hass_with_registry(entity_entry)
        with patch.object(coordinator_module.er, "async_get", return_value=registry):
            assert _find_native_coordinator(hass, "sensor.nordpool") is None

    def test_returns_none_when_config_entry_missing(self) -> None:
        entity_entry = SimpleNamespace(config_entry_id="abc123")
        hass, registry = _hass_with_registry(entity_entry, config_entry=None)
        with patch.object(coordinator_module.er, "async_get", return_value=registry):
            assert _find_native_coordinator(hass, "sensor.nordpool") is None

    def test_returns_none_when_runtime_data_missing(self) -> None:
        entity_entry = SimpleNamespace(config_entry_id="abc123")
        config_entry = SimpleNamespace(runtime_data=None)
        hass, registry = _hass_with_registry(entity_entry, config_entry)
        with patch.object(coordinator_module.er, "async_get", return_value=registry):
            assert _find_native_coordinator(hass, "sensor.nordpool") is None

    def test_returns_none_when_runtime_data_lacks_async_add_listener(self) -> None:
        entity_entry = SimpleNamespace(config_entry_id="abc123")
        config_entry = SimpleNamespace(runtime_data=object())
        hass, registry = _hass_with_registry(entity_entry, config_entry)
        with patch.object(coordinator_module.er, "async_get", return_value=registry):
            assert _find_native_coordinator(hass, "sensor.nordpool") is None

    def test_returns_coordinator_when_fully_available(self) -> None:
        native_coordinator = MagicMock()
        entity_entry = SimpleNamespace(config_entry_id="abc123")
        config_entry = SimpleNamespace(runtime_data=native_coordinator)
        hass, registry = _hass_with_registry(entity_entry, config_entry)
        with patch.object(coordinator_module.er, "async_get", return_value=registry):
            result = _find_native_coordinator(hass, "sensor.nordpool")
        assert result is native_coordinator


class TestPriceCoordinatorHasNoPollingInterval:
    """PriceCoordinator must not use a fixed polling interval -- refreshes
    are event-driven (Nord Pool updates + clock-aligned fallback).

    PriceCoordinator cannot be instantiated under the HA stub (see module
    docstring: DataUpdateCoordinator subclassing breaks under the stub), so
    this checks the source directly rather than introspecting a live
    instance's update_interval attribute.
    """

    def test_init_passes_update_interval_none(self) -> None:
        source_path = inspect.getsourcefile(coordinator_module)
        text = Path(source_path).read_text()
        match = re.search(
            r"class PriceCoordinator\(.*?(?=\nclass \w|\Z)", text, re.DOTALL
        )
        assert match is not None, "PriceCoordinator class not found in coordinator.py"
        class_source = match.group(0)
        assert "update_interval=None" in class_source
        assert "update_interval=timedelta" not in class_source

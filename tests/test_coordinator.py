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
- car_throughput_storage_key(): Store key for the measured per-car charge
  throughput payload, shared with __init__.async_remove_entry.

- _restored_number(): entity registry -> restore-state store lookup that
  lets a coordinator seed an attribute with the value its RestoreNumber
  entity will assign seconds later, instead of a bare DEFAULT_* constant.

Plus source-text guards (the same escape hatch
TestPriceCoordinatorHasNoPollingInterval uses) over the learned-power /
ceiling split inside CarChargingCoordinator and over the four restore
seeds, neither of which a pure test can reach.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.energy_manager import coordinator as coordinator_module
from custom_components.energy_manager.battery_scheduler import (
    compute_effective_discharge_threshold,
)
from custom_components.energy_manager.const import (
    DEFAULT_CHARGE_THRESHOLD,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DEFAULT_TARGET_SOC_PCT,
    FALLBACK_STALE_THRESHOLD_MINUTES,
)
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
    _restored_number,
    _serialize_samples,
    _should_sample_consumption,
    car_throughput_storage_key,
    consumption_storage_key,
    derive_tomorrow_forecast_entities,
    forecast_accuracy_storage_key,
    solar_tracker_storage_key,
    sum_solar_forecast_wh,
)

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


def test_car_throughput_storage_key() -> None:
    """The fourth Store key -- distinct from the other three.

    __init__.async_remove_entry deletes .storage files by these keys, so a
    collision would make one coordinator's payload delete another's, and a
    silent rename would strand the old file on disk forever.
    """
    assert (
        car_throughput_storage_key("abc123") == "energy_manager.abc123-car-throughput"
    )

    entry_id = "abc123"
    keys = {
        consumption_storage_key(entry_id),
        forecast_accuracy_storage_key(entry_id),
        solar_tracker_storage_key(entry_id),
        car_throughput_storage_key(entry_id),
    }
    assert len(keys) == 4


class TestCarPlannerUsesLearnedPower:
    """The learned/ceiling split inside CarChargingCoordinator.

    The measured estimate must reach the price-slot planner but must NOT
    reach CarChargingData.max_charge_power_kw, which flows on to
    CarDemand.max_charge_kw and from there into the live amp target. Point
    that field at the learned value and the estimate becomes
    self-fulfilling: a throttled 4 kW measurement caps the charger at 4 kW,
    which re-measures 4 kW forever, while every schedule it produces still
    looks perfectly valid.

    CarChargingCoordinator cannot be instantiated under the HA stub (see the
    module docstring), so this reads the source the same way
    TestPriceCoordinatorHasNoPollingInterval does. It is a text guard, not a
    behavioural one -- a creative enough refactor could satisfy it and still
    break the invariant.
    """

    @staticmethod
    def _class_source() -> str:
        source_path = inspect.getsourcefile(coordinator_module)
        text = Path(source_path).read_text()
        match = re.search(
            r"class CarChargingCoordinator\(.*?(?=\nclass \w|\Z)", text, re.DOTALL
        )
        assert match is not None, "CarChargingCoordinator not found in coordinator.py"
        return match.group(0)

    def test_scheduler_gets_planning_kw_and_car_data_keeps_the_ceiling(self) -> None:
        before_data, sep, after_data = self._class_source().partition(
            "return CarChargingData("
        )
        assert sep, "CarChargingData construction not found -- guard is blind"

        # Planner side: the pure scheduler is sized from the learned figure.
        assert "max_charge_power_kw=planning_kw" in before_data
        # Control side: the field that becomes CarDemand.max_charge_kw keeps
        # the number entity's own value.
        assert "max_charge_power_kw=self.max_charge_power_kw" in after_data
        assert "max_charge_power_kw=planning_kw" not in after_data

    def test_both_learned_and_planning_are_reported(self) -> None:
        """Two fields, not one: planning == ceiling cannot distinguish
        "learned above the ceiling" from "never learned"."""
        _, _, after_data = self._class_source().partition("return CarChargingData(")
        assert "learned_power_kw=learned" in after_data
        assert "planning_power_kw=planning_kw" in after_data


class TestThroughputSamplingSkipsBlindTicks:
    """A tick EM cannot attribute for a STRUCTURAL reason must not be fed in.

    On the first Easee refresh after a restart or a config-entry reload,
    entry.runtime_data has not been assigned yet (__init__ awaits
    async_config_entry_first_refresh() before assigning it), so
    _build_car_demands() returns an empty tuple. Feeding the learner that
    tick classifies it "reject", and the reject path CLOSES the just-restored
    in-flight segment -- making _restore_segment's short-gap "keep
    accumulating across a reload" branch unreachable in production.

    "We cannot see the cars yet" is not evidence that the segment ended, so
    the sampler returns before observe() when the snapshot is empty.

    EaseeCoordinator cannot be instantiated under the HA stub (see the module
    docstring), so this reads the source the same way
    TestCarPlannerUsesLearnedPower does.
    """

    @staticmethod
    def _method_source() -> str:
        source_path = inspect.getsourcefile(coordinator_module)
        text = Path(source_path).read_text()
        match = re.search(
            r"\n    def _sample_throughput\(.*?(?=\n    def \w|\Z)", text, re.DOTALL
        )
        assert match is not None, "_sample_throughput not found in coordinator.py"
        return match.group(0)

    def test_an_empty_car_snapshot_returns_before_the_learner_sees_it(self) -> None:
        before, sep, after = self._method_source().partition("if not cars:")
        assert sep, "no structural guard on an empty car snapshot"
        assert "observe(" not in before, "the learner is fed before the guard"
        assert after.lstrip().startswith("return"), (
            "the guard must return, not merely log"
        )

    def test_the_learner_is_still_fed_every_other_tick(self) -> None:
        """The guard is narrow: an ordinary unattributable tick (car
        unplugged, two cars demanding, solar mode) must still reach observe(),
        because a rejected tick is exactly what closes an open segment."""
        source = self._method_source()
        assert "self._throughput_learner.observe(tick)" in source
        assert source.count("if not cars:") == 1


# --- Restore-seeded coordinator attributes ---------------------------------
#
# Incident 2026-09-02 (08:05:53, 09:54:07 -> 09:54:17, 10:05:12 -> 10:05:22):
# number.energy_manager_battery_discharge_spread_threshold published
# 0.5 SEK/kWh and went Unavailable ~10 s later on every reload/restart.
# Root cause: BatteryScheduleCoordinator seeds battery_cycle_cost from
# DEFAULT_BATTERY_CYCLE_COST (0.0) and only the RestoreNumber entity's
# async_added_to_hass assigns the user's real 1.0 -- but the coordinator's
# FIRST refresh runs before the platforms are even forwarded
# (__init__.py:87-128 constructs and first-refreshes every coordinator
# before entry.runtime_data is assigned; platform forwarding follows).
# During that window the scheduler used the manual 0.50 instead of the
# derived cycle_cost - transfer_fee = 1.00 - 0.78 = 0.22.


@dataclass(frozen=True)
class _RestoredExtraData:
    """Stand-in for homeassistant.helpers.restore_state.RestoredExtraData.

    This is what StoredState.extra_data holds after a COLD RESTART: the raw
    JSON dict rewrapped in a frozen dataclass, reachable only through
    as_dict(). It is deliberately NOT subscriptable -- a seed helper written
    as extra_data["native_value"] raises TypeError here, the helper's
    never-block-setup guard swallows it, and the seed silently falls back to
    the default. That is a fix that ships and does nothing, so the fake has
    to be able to catch it.
    """

    json_dict: dict

    def as_dict(self) -> dict:
        return self.json_dict


@dataclass(frozen=True)
class _NumberExtraStoredData:
    """Stand-in for homeassistant.components.number.NumberExtraStoredData.

    This is what StoredState.extra_data holds after a config-entry RELOAD:
    async_restore_entity_removed stores the live entity's own
    extra_restore_state_data object, never a dict. Same as_dict() contract,
    same non-subscriptability. Both storage paths must be covered, because
    the reported symptom fires on reloads and on cold restarts alike.
    """

    native_value: float | None
    native_unit_of_measurement: str | None = "SEK/kWh"
    native_max_value: float | None = 5.0
    native_min_value: float | None = 0.0
    native_step: float | None = 0.01

    def as_dict(self) -> dict:
        return {
            "native_max_value": self.native_max_value,
            "native_min_value": self.native_min_value,
            "native_step": self.native_step,
            "native_unit_of_measurement": self.native_unit_of_measurement,
            "native_value": self.native_value,
        }


def _hass_with_stored_number(
    entity_id: str | None, stored: object | None
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build hass + entity-registry + restore-store doubles for one lookup."""
    hass = MagicMock()
    registry = MagicMock()
    registry.async_get_entity_id.return_value = entity_id
    store = MagicMock()
    store.last_states = {} if entity_id is None else {entity_id: stored}
    return hass, registry, store


def _seed(
    registry: MagicMock,
    store: MagicMock,
    hass: MagicMock,
    unique_id: str = "entry1_battery_cycle_cost",
    default: float = 0.0,
) -> float:
    with (
        patch.object(coordinator_module.er, "async_get", return_value=registry),
        patch.object(
            coordinator_module.restore_state, "async_get", return_value=store
        ),
    ):
        return _restored_number(hass, unique_id, default)


class TestRestoredNumber:
    """The value a RestoreNumber will restore, read at construction time.

    One shared primitive behind all four seed sites. Every miss path must
    degrade to the caller's existing seed expression -- never raise, never
    return None, never block config-entry setup.
    """

    def test_cold_restart_extra_data_yields_the_stored_value(self) -> None:
        stored = SimpleNamespace(
            extra_data=_RestoredExtraData(
                {"native_value": 1.0, "native_unit_of_measurement": "SEK/kWh"}
            )
        )
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", stored)
        assert _seed(registry, store, hass, default=0.0) == 1.0

    def test_reload_extra_data_yields_the_stored_value(self) -> None:
        stored = SimpleNamespace(extra_data=_NumberExtraStoredData(native_value=1.0))
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", stored)
        assert _seed(registry, store, hass, default=0.0) == 1.0

    def test_a_stored_zero_wins_over_the_default(self) -> None:
        """The inverse flap. A user whose cycle cost really IS 0 must keep 0.

        "Nothing stored" and "stored 0.0" are different answers: confuse them
        and the discharge-threshold entity flips the other way, Unavailable
        -> 0.5, at every startup. This is the single most important case in
        the suite.
        """
        stored = SimpleNamespace(extra_data=_NumberExtraStoredData(native_value=0.0))
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", stored)
        assert _seed(registry, store, hass, default=0.9) == 0.0

    def test_a_decimal_native_value_is_read_not_dropped(self) -> None:
        """HA serialises a Decimal native_value as its encoded form.

        The entity's own NumberExtraStoredData.from_dict rebuilds the
        Decimal, so dropping it here and falling back to the default would
        put the seed and the entity back out of step -- reintroducing the
        divergence this helper exists to remove, just more rarely.
        """
        stored = SimpleNamespace(
            extra_data=_RestoredExtraData(
                {
                    "native_value": {
                        "__type": "<class 'decimal.Decimal'>",
                        "decimal": "0.22",
                    }
                }
            )
        )
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", stored)
        assert _seed(registry, store, hass, default=0.0) == pytest.approx(0.22)

    def test_an_unrecognised_dict_native_value_falls_back(self) -> None:
        stored = SimpleNamespace(
            extra_data=_RestoredExtraData({"native_value": {"unexpected": 1}})
        )
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", stored)
        assert _seed(registry, store, hass, default=0.9) == 0.9

    def test_the_lookup_is_scoped_to_the_number_platform(self) -> None:
        stored = SimpleNamespace(extra_data=_NumberExtraStoredData(native_value=7.4))
        hass, registry, store = _hass_with_stored_number("number.max_power", stored)
        _seed(registry, store, hass, unique_id="sub1_max_charge_power", default=11.0)
        registry.async_get_entity_id.assert_called_once_with(
            "number", coordinator_module.DOMAIN, "sub1_max_charge_power"
        )

    def test_default_when_the_entity_was_never_registered(self) -> None:
        """Fresh install, or a car subentry added this session."""
        hass, registry, store = _hass_with_stored_number(None, None)
        assert _seed(registry, store, hass, default=0.42) == 0.42

    def test_default_when_no_stored_state_survives(self) -> None:
        """Restore records expire (STATE_EXPIRATION, 7 days)."""
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", None)
        assert _seed(registry, store, hass, default=0.42) == 0.42

    def test_default_when_extra_data_is_missing(self) -> None:
        stored = SimpleNamespace(extra_data=None)
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", stored)
        assert _seed(registry, store, hass, default=0.42) == 0.42

    def test_default_when_native_value_is_none(self) -> None:
        stored = SimpleNamespace(extra_data=_NumberExtraStoredData(native_value=None))
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", stored)
        assert _seed(registry, store, hass, default=0.42) == 0.42

    def test_default_when_native_value_is_not_a_number(self) -> None:
        """HA serialises a Decimal as {"__type": ..., "decimal_str": ...}."""
        stored = SimpleNamespace(
            extra_data=_RestoredExtraData(
                {"native_value": {"__type": "<class 'decimal.Decimal'>"}}
            )
        )
        hass, registry, store = _hass_with_stored_number("number.cycle_cost", stored)
        assert _seed(registry, store, hass, default=0.42) == 0.42

    def test_default_when_the_registry_raises(self) -> None:
        """Helper drift in a future HA must not block config-entry setup."""
        hass = MagicMock()
        registry = MagicMock()
        registry.async_get_entity_id.side_effect = RuntimeError("registry gone")
        store = MagicMock()
        store.last_states = {}
        assert _seed(registry, store, hass, default=0.42) == 0.42

    def test_an_integer_stored_value_is_coerced_to_float(self) -> None:
        stored = SimpleNamespace(
            extra_data=_RestoredExtraData({"native_value": 11})
        )
        hass, registry, store = _hass_with_stored_number("number.max_power", stored)
        seeded = _seed(registry, store, hass, default=7.4)
        assert seeded == 11.0
        assert isinstance(seeded, float)


class TestSymptomADischargeThresholdSeed:
    """Both halves of the 2026-09-02 flap die at the same place.

    BatteryScheduleCoordinator cannot be instantiated under the HA stub (see
    the module docstring), so the wiring is checked the same way
    TestCarPlannerUsesLearnedPower checks its invariant, and the arithmetic
    it protects is checked directly.
    """

    @staticmethod
    def _init_source() -> str:
        source_path = inspect.getsourcefile(coordinator_module)
        text = Path(source_path).read_text()
        cls = re.search(
            r"class BatteryScheduleCoordinator\(.*?(?=\nclass \w|\Z)", text, re.DOTALL
        )
        assert cls is not None, "BatteryScheduleCoordinator not found"
        init = re.search(
            r"\n    def __init__\(.*?(?=\n    async def \w|\n    def \w|\Z)",
            cls.group(0),
            re.DOTALL,
        )
        assert init is not None, "BatteryScheduleCoordinator.__init__ not found"
        return init.group(0)

    def test_both_economics_seeds_are_needed_for_the_right_number(self) -> None:
        """Seeding the cycle cost alone gives 1.00, not 0.22 -- wrong the
        other way. The live values are cycle cost 1.00, transfer fee 0.78,
        manual threshold 0.50."""
        assert compute_effective_discharge_threshold(0.5, 1.0, 0.78) == pytest.approx(
            0.22
        )
        assert compute_effective_discharge_threshold(0.5, 1.0, 0.0) == pytest.approx(
            1.0
        )
        # Cycle cost 0 (the shipped default) means the MANUAL threshold is
        # live, which is why discharge_threshold has to be seeded too.
        assert compute_effective_discharge_threshold(0.35, 0.0, 0.78) == 0.35

    def test_the_four_battery_seeds_read_the_restore_store(self) -> None:
        # Whitespace-collapsed: a fallback expression that ruff decides to
        # wrap differently is the same expression, and this test must not
        # fail on a reformat that changes no behaviour.
        init_source = re.sub(r"\s+", " ", self._init_source())
        for unique_id, fallback in (
            (
                'f"{entry.entry_id}_battery_cycle_cost"',
                "entry.options.get(CONF_BATTERY_CYCLE_COST, DEFAULT_BATTERY_CYCLE_COST)",
            ),
            (
                'f"{entry.entry_id}_grid_transfer_fee"',
                "entry.options.get(CONF_GRID_TRANSFER_FEE, DEFAULT_GRID_TRANSFER_FEE)",
            ),
            (
                'f"{entry.entry_id}_discharge_price_threshold"',
                "DEFAULT_DISCHARGE_THRESHOLD",
            ),
            (
                'f"{entry.entry_id}_electricity_company_fee"',
                (
                    "entry.options.get( CONF_ELECTRICITY_COMPANY_FEE,"
                    " DEFAULT_ELECTRICITY_COMPANY_FEE )"
                ),
            ),
        ):
            assert unique_id in init_source, f"{unique_id} is not seeded"
            assert fallback in init_source, (
                f"{unique_id} must keep the entity's own fallback expression"
            )

    def test_both_halves_of_the_fee_sum_are_seeded_together(self) -> None:
        """grid_transfer_fee and electricity_company_fee are SUMMED by
        battery_scheduler._mark_export_slots and by
        ActualElectricityPriceSensor. Seeding one without the other produces
        a total that is neither the stored one nor the default one -- a third
        wrong answer. They move together or not at all."""
        init_source = self._init_source()
        assert ('f"{entry.entry_id}_grid_transfer_fee"' in init_source) == (
            'f"{entry.entry_id}_electricity_company_fee"' in init_source
        )

    def test_the_seed_lands_before_the_first_refresh(self) -> None:
        """Only __init__ (and _async_setup) run before the first refresh; a
        seed read inside _async_update_data would re-apply a stale stored
        value over every user edit, forever."""
        source_path = inspect.getsourcefile(coordinator_module)
        text = Path(source_path).read_text()
        cls = re.search(
            r"class BatteryScheduleCoordinator\(.*?(?=\nclass \w|\Z)", text, re.DOTALL
        )
        assert cls is not None
        assert cls.group(0).count("= _restored_number(") == self._init_source().count(
            "= _restored_number("
        ), "a battery seed escaped __init__"


class TestSymptomBCarCeilingSeed:
    """The per-car live amp ceiling must never exceed the user's stored kW.

    max_charge_power_kw is both the planning seed and the live ceiling:
    CarChargingData.max_charge_power_kw -> CarDemand.max_charge_kw ->
    compute_charger_capacity_amps(car_max_charge_kw=...). Seeding it from
    the phase derivation alone can put 11.0 kW on the wire for a car the
    user throttled to 7.4 kW, until the number entity restores.
    """

    @staticmethod
    def _init_source() -> str:
        source_path = inspect.getsourcefile(coordinator_module)
        text = Path(source_path).read_text()
        cls = re.search(
            r"class CarChargingCoordinator\(.*?(?=\nclass \w|\Z)", text, re.DOTALL
        )
        assert cls is not None, "CarChargingCoordinator not found"
        init = re.search(
            r"\n    def __init__\(.*?(?=\n    async def \w|\n    def \w|\Z)",
            cls.group(0),
            re.DOTALL,
        )
        assert init is not None, "CarChargingCoordinator.__init__ not found"
        return init.group(0)

    def test_the_ceiling_seeds_from_the_stored_value(self) -> None:
        """The ceiling seeds from the stored value. target_soc seeds too:
        the entity rounds before assigning (number.py:562), so its seed
        wraps the same _restored_number call in round() -- a naive
        "= _restored_number(" count would stay 1 and miss it, so this
        counts the bare call instead."""
        init_source = self._init_source()
        assert 'f"{subentry.subentry_id}_max_charge_power"' in init_source
        assert 'f"{subentry.subentry_id}_target_soc"' in init_source
        assert init_source.count("_restored_number(") == 2

    def test_the_phase_derivation_survives_as_the_fallback(self) -> None:
        """A NEWLY created car has nothing stored, and the just-landed
        planning fix must still size its schedule from phase capability
        rather than a flat constant."""
        init_source = self._init_source()
        assert "derive_car_max_charge_power_kw(" in init_source
        assert init_source.index("= _restored_number(") < init_source.index(
            "derive_car_max_charge_power_kw("
        ), "the derivation must be the fallback argument, not the seed"

    def test_the_seed_is_always_a_real_number(self) -> None:
        """None or a sub-minimum floor would drop the car from
        _build_car_demands / push the controller below 6 A. The helper
        returns the caller's float on every miss path -- pinned here by the
        signature, and behaviourally by TestRestoredNumber."""
        signature = inspect.signature(_restored_number)
        assert list(signature.parameters) == ["hass", "unique_id", "default"]
        assert signature.parameters["default"].annotation == "float"
        assert signature.return_annotation == "float"


class TestSeedUniqueIdsMatchTheEntities:
    """The seed duplicates each entity's unique_id. Drift re-opens the race.

    Asserted in the FULL prefixed form, not on the bare suffix:
    "_max_charge_power" is worn by BatteryMaxChargePower (entry-prefixed) as
    well as CarMaxChargePower (subentry-prefixed), so a suffix-only check
    would stay green after the car entity's id was renamed -- guarding
    nothing at exactly the site it exists for.
    """

    def test_every_seeded_unique_id_exists_verbatim_in_number_py(self) -> None:
        coordinator_path = Path(inspect.getsourcefile(coordinator_module))
        coordinator_source = coordinator_path.read_text()
        number_source = (coordinator_path.parent / "number.py").read_text()
        for unique_id in (
            'f"{entry.entry_id}_battery_cycle_cost"',
            'f"{entry.entry_id}_grid_transfer_fee"',
            'f"{entry.entry_id}_electricity_company_fee"',
            'f"{entry.entry_id}_discharge_price_threshold"',
            'f"{subentry.subentry_id}_max_charge_power"',
            'f"{entry.entry_id}_charge_price_threshold"',
            'f"{entry.entry_id}_max_charge_power"',
            'f"{subentry.subentry_id}_target_soc"',
        ):
            assert unique_id in coordinator_source, f"{unique_id} not seeded"
            assert unique_id in number_source, (
                f"{unique_id} no longer matches any number entity -- "
                "the seed silently misses and the flap returns"
            )


class TestTheThreeNewlySeededAttributes:
    """Behaviour tests for charge_threshold, max_charge_power_w, and
    target_soc -- the three attributes newly seeded alongside the four
    covered by TestRestoredNumber/TestSymptomA/TestSymptomB.

    BatteryScheduleCoordinator and CarChargingCoordinator cannot be
    instantiated under the HA stubs (see the module docstring), so these
    exercise _restored_number directly via the file's _seed helper,
    composed exactly the way each constructor composes it
    (`_seed(...) * 1000` for the watts conversion, `round(_seed(...))` for
    the SOC rounding) -- not full coordinator construction.
    """

    def test_charge_threshold_seed_returns_the_stored_value(self) -> None:
        stored = SimpleNamespace(extra_data=_NumberExtraStoredData(native_value=0.65))
        hass, registry, store = _hass_with_stored_number(
            "number.charge_threshold", stored
        )
        seeded = _seed(
            registry,
            store,
            hass,
            unique_id="entry1_charge_price_threshold",
            default=DEFAULT_CHARGE_THRESHOLD,
        )
        assert seeded == 0.65
        assert seeded != DEFAULT_CHARGE_THRESHOLD

    def test_max_charge_power_w_seed_converts_stored_kw_to_watts(self) -> None:
        """The unit trap: the entity stores kW, but max_charge_power_w is
        WATTS (number.py:347 multiplies by 1000). A stored 7.4 kW must
        become 7400.0 W here, not 7.4 -- the ~11 W cap this change exists
        to fix."""
        stored = SimpleNamespace(extra_data=_NumberExtraStoredData(native_value=7.4))
        hass, registry, store = _hass_with_stored_number(
            "number.max_charge_power", stored
        )
        seeded_w = (
            _seed(
                registry,
                store,
                hass,
                unique_id="entry1_max_charge_power",
                default=DEFAULT_MAX_CHARGE_POWER_KW,
            )
            * 1000
        )
        assert seeded_w == 7400.0

    def test_the_watts_conversion_survives_in_the_constructor(self) -> None:
        """The composed assertion above is self-fulfilling -- it multiplies
        by 1000 itself, so it stays green even if the constructor stops
        doing so. This one reads the production source: the seed for
        max_charge_power_w must still carry its * 1000, or the battery is
        capped at ~11 W on the first refresh of every restart."""
        init_source = TestSymptomADischargeThresholdSeed._init_source()
        seed_start = init_source.index('f"{entry.entry_id}_max_charge_power"')
        tail = init_source[seed_start : seed_start + 400]
        assert "* 1000" in tail, (
            "max_charge_power_w seeds kW into a WATTS attribute -- the "
            "* 1000 is the conversion, not decoration"
        )

    def test_the_rounding_survives_in_the_constructor(self) -> None:
        """Same trap as the watts conversion: round() applied in the test
        proves nothing about the constructor. The entity rounds before
        assigning (number.py:562), so the seed must round too or the two
        disagree the moment the entity restores."""
        init_source = TestSymptomBCarCeilingSeed._init_source()
        seed_start = init_source.index("self.target_soc")
        tail = init_source[seed_start : seed_start + 300]
        assert "round(" in tail, "the target_soc seed must mirror the entity's round()"

    def test_target_soc_seed_rounds_like_the_entity(self) -> None:
        """The entity rounds before assigning (number.py:562), so a stored
        80.4 must seed 80 here too, matching what the entity itself would
        assign a moment afterwards."""
        stored = SimpleNamespace(extra_data=_NumberExtraStoredData(native_value=80.4))
        hass, registry, store = _hass_with_stored_number(
            "number.target_soc", stored
        )
        seeded = round(
            _seed(
                registry,
                store,
                hass,
                unique_id="sub1_target_soc",
                default=DEFAULT_TARGET_SOC_PCT,
            )
        )
        assert seeded == 80

"""Comprehensive tests for the pure-Python EMS controller calculation module.

Tests cover all EMS decision paths:
- EMS-01: Mode selection (schedule-driven)
- EMS-02: Fuse protection (dynamic limiting)
- EMS-03: Car priority override
- EMS-04: Safety guards (clamp_amps)
- EMS-08: PV opportunistic charging
- PV hysteresis state machine
- CORE-14: Observe-only command gating
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.energy_manager.const import EMS_MODE_MAP
from custom_components.energy_manager.ems_controller import (
    CommandDecision,
    EMSDecision,
    ESSLimitRateLimiter,
    PVHysteresisTracker,
    build_command_decision,
    car_actively_charging,
    car_demands_priority_charging,
    clamp_amps,
    compute_available_ess_amps,
    compute_ems_state,
    compute_export_limit_kw,
    resolve_current_sensor_fallback,
    worst_case_signed_amps,
)

# ---------------------------------------------------------------------------
# EMS-01: Mode Selection (schedule-driven)
# ---------------------------------------------------------------------------


class TestModeSelection:
    """Tests for schedule-driven EMS mode selection."""

    def test_charge_mode_when_schedule_says_charge(self):
        """target_ems_mode='command_charging' with sufficient fuse headroom
        returns command_charging."""
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=10.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert isinstance(result, EMSDecision)
        assert result.target_mode == "command_charging"
        assert result.override_reason is None

    def test_discharge_mode_passes_through(self):
        """target_ems_mode='max_self_consumption' passes through."""
        result = compute_ems_state(
            target_ems_mode="max_self_consumption",
            current_l_amps=10.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "max_self_consumption"
        assert result.override_reason is None

    def test_standby_mode_passes_through(self):
        """target_ems_mode='standby' passes through."""
        result = compute_ems_state(
            target_ems_mode="standby",
            current_l_amps=10.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "standby"
        assert result.override_reason is None

    def test_idle_maps_to_max_self_consumption(self):
        """target_ems_mode='idle' maps to 'max_self_consumption'
        (idle = let battery do its own optimization)."""
        result = compute_ems_state(
            target_ems_mode="idle",
            current_l_amps=10.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "max_self_consumption"


# ---------------------------------------------------------------------------
# EMS-02: Fuse Protection (dynamic limiting)
# ---------------------------------------------------------------------------


class TestFuseProtection:
    """Tests for fuse headroom calculation and charge limiting."""

    def test_fuse_headroom_basic(self):
        """fuse_rating=20A, current_load=12A, safety_buffer=2A => headroom=6A."""
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=12.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=2.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.fuse_headroom_amps == pytest.approx(6.0)

    def test_fuse_headroom_never_negative(self):
        """fuse_rating=20A, current_load=25A => headroom=0A (clamped)."""
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=25.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=2.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.fuse_headroom_amps == 0.0

    def test_charge_limit_reduced_by_fuse(self):
        """max_charge=5kW but only 3A headroom => limit capped to ~0.69kW."""
        # headroom = 20 - 15 - 2 = 3A => 3*230/1000 = 0.69kW
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=15.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=2.0,
            voltage=230.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        expected_kw = (3.0 * 230.0) / 1000.0  # 0.69 kW
        assert result.charge_limit_kw == pytest.approx(expected_kw)

    def test_charge_limit_zero_when_no_headroom(self):
        """current_load exceeds fuse => charge_limit=0.0kW."""
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=25.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=2.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.charge_limit_kw == 0.0

    def test_charge_limit_uses_max_when_ample_headroom(self):
        """Plenty of headroom => charge_limit equals max_charge_power."""
        # headroom = 63 - 5 - 2 = 56A => 56*230/1000 = 12.88kW >> max 5kW
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=5.0,
            fuse_rating_amps=63.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=2.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.charge_limit_kw == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# EMS-03: Car Priority Override
# ---------------------------------------------------------------------------


class TestCarPriorityOverride:
    """Tests for car charging priority logic."""

    def test_car_priority_pauses_battery_charging(self):
        """car_scheduled=True, car_plugged_in=True, target='command_charging'
        => returns standby with override_reason='car_charging_priority'."""
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=10.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=True,
            car_plugged_in=True,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "standby"
        assert result.override_reason == "car_charging_priority"

    def test_car_priority_no_effect_when_not_scheduled(self):
        """car_scheduled=False, car_plugged_in=True => no override."""
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=10.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=True,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason is None

    def test_car_priority_no_effect_when_not_plugged(self):
        """car_scheduled=True, car_plugged_in=False => no override."""
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=10.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=True,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason is None

    def test_car_priority_no_effect_on_discharge(self):
        """car_scheduled=True, car_plugged_in=True, target='max_self_consumption'
        => no override (only affects charging)."""
        result = compute_ems_state(
            target_ems_mode="max_self_consumption",
            current_l_amps=10.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=True,
            car_plugged_in=True,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "max_self_consumption"
        assert result.override_reason is None


# ---------------------------------------------------------------------------
# EMS-04: Safety Guards (clamp_amps)
# ---------------------------------------------------------------------------


class TestClampAmps:
    """Tests for the clamp_amps safety function."""

    def test_clamp_amps_positive_range(self):
        """clamp_amps(15.0) with max=32 => 15.0."""
        assert clamp_amps(15.0, max_amps=32.0) == 15.0

    def test_clamp_amps_negative_clamped(self):
        """clamp_amps(-5.0) => 0.0."""
        assert clamp_amps(-5.0) == 0.0

    def test_clamp_amps_exceeds_max(self):
        """clamp_amps(50.0, max=32) => 32.0."""
        assert clamp_amps(50.0, max_amps=32.0) == 32.0

    def test_clamp_amps_zero_input(self):
        """clamp_amps(0.0) => 0.0."""
        assert clamp_amps(0.0) == 0.0

    def test_clamp_amps_custom_min(self):
        """clamp_amps(3.0, min_amps=6.0) => 6.0."""
        assert clamp_amps(3.0, min_amps=6.0) == 6.0


# ---------------------------------------------------------------------------
# EMS-08: PV Opportunistic Charging
# ---------------------------------------------------------------------------


class TestPVOpportunisticCharging:
    """Tests for PV opportunistic charging activation."""

    def test_pv_charging_activates_above_threshold(self):
        """standby mode, pv_power=800W, soc=50%, pv_hysteresis_active=True
        => command_charging with override_reason='pv_opportunistic'."""
        result = compute_ems_state(
            target_ems_mode="standby",
            current_l_amps=5.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=800.0,
            pv_hysteresis_active=True,
            max_soc_pct=95.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason == "pv_opportunistic"

    def test_pv_charging_skipped_when_battery_full(self):
        """pv_power=800W, soc=96% (above max_soc_pct=95) => stays standby."""
        result = compute_ems_state(
            target_ems_mode="standby",
            current_l_amps=5.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=96.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=800.0,
            pv_hysteresis_active=True,
            max_soc_pct=95.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "standby"
        assert result.override_reason is None

    def test_pv_charging_eligible_at_95_with_default_max_soc(self):
        """soc=95%, max_soc_pct=100 (new default) => 95 is no longer a
        ceiling; charging is still eligible, unlike the old 95 default."""
        result = compute_ems_state(
            target_ems_mode="standby",
            current_l_amps=5.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=95.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=800.0,
            pv_hysteresis_active=True,
            max_soc_pct=100.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason == "pv_opportunistic"

    def test_pv_charging_limited_to_available_solar(self):
        """pv_power=2000W, max_charge=5kW => charge_limit=min(2.0, safe_charge)."""
        result = compute_ems_state(
            target_ems_mode="standby",
            current_l_amps=5.0,
            fuse_rating_amps=63.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=2000.0,
            pv_hysteresis_active=True,
            max_soc_pct=95.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_charging"
        assert result.charge_limit_kw == pytest.approx(2.0)

    def test_pv_charging_no_effect_during_scheduled_charge(self):
        """target='command_charging' already => PV check skipped."""
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=5.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=2000.0,
            pv_hysteresis_active=True,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_charging"
        # Should NOT have pv_opportunistic override -- it was already charging
        assert result.override_reason is None


# ---------------------------------------------------------------------------
# PV Hysteresis State Machine (EMS-08 sub-requirement)
# ---------------------------------------------------------------------------


class TestMaxSocCeiling:
    """Scheduled charging must stop at the max-SoC target -- the schedule
    only sizes charge slots at recalculation time, so a slot can outlive
    the target being reached."""

    def _charge_state(self, **overrides: object) -> EMSDecision:
        defaults: dict[str, object] = {
            "target_ems_mode": "command_charging",
            "current_l_amps": 5.0,
            "fuse_rating_amps": 25.0,
            "max_charge_power_kw": 5.0,
            "battery_soc_pct": 95.0,
            "car_scheduled": False,
            "car_plugged_in": False,
            "pv_power_w": 0.0,
            "pv_hysteresis_active": False,
            "max_soc_pct": 95.0,
            "discharge_allowed": True,
            "discharge_gate_reason": "charging_slot",
            "car_charging_active": False,
        }
        defaults.update(overrides)
        return compute_ems_state(**defaults)  # type: ignore[arg-type]

    def test_charge_slot_stops_at_target(self):
        """soc == target during a charge slot => standby, not MSC (MSC
        would cycle the just-stored energy into house load)."""
        result = self._charge_state()
        assert result.target_mode == "standby"
        assert result.override_reason == "max_soc_reached"
        assert result.charge_limit_kw == 0.0

    def test_charge_slot_continues_below_target(self):
        result = self._charge_state(battery_soc_pct=94.9)
        assert result.target_mode == "command_charging"
        assert result.override_reason is None
        assert result.charge_limit_kw > 0.0

    def test_car_priority_reason_wins_over_max_soc(self):
        """Both overrides yield standby -- the EMS-03 car reason is the
        one surfaced (checked first)."""
        result = self._charge_state(car_scheduled=True, car_plugged_in=True)
        assert result.target_mode == "standby"
        assert result.override_reason == "car_charging_priority"


class TestPVHysteresisTracker:
    """Tests for PV hysteresis state machine preventing oscillation."""

    def test_pv_hysteresis_off_to_pending_on(self):
        """Starts 'off', receives power > activate_threshold
        => transitions to 'pending_on'."""
        tracker = PVHysteresisTracker(
            activate_threshold_w=500,
            deactivate_threshold_w=300,
            required_consecutive=2,
        )
        assert tracker.state == "off"
        result = tracker.update(600.0)
        assert tracker.state == "pending_on"
        # Not yet active -- need consecutive checks
        assert result is False

    def test_pv_hysteresis_pending_on_to_on(self):
        """In 'pending_on', receives power > activate_threshold for
        required consecutive checks => transitions to 'on'."""
        tracker = PVHysteresisTracker(
            activate_threshold_w=500,
            deactivate_threshold_w=300,
            required_consecutive=2,
        )
        tracker.update(600.0)  # off -> pending_on (count=1)
        assert tracker.state == "pending_on"
        result = tracker.update(700.0)  # pending_on -> on (count=2 >= required)
        assert tracker.state == "on"
        assert result is True

    def test_pv_hysteresis_on_to_pending_off(self):
        """In 'on', receives power < deactivate_threshold
        => transitions to 'pending_off'."""
        tracker = PVHysteresisTracker(
            activate_threshold_w=500,
            deactivate_threshold_w=300,
            required_consecutive=2,
        )
        # Get to "on" state first
        tracker.update(600.0)
        tracker.update(700.0)
        assert tracker.state == "on"
        result = tracker.update(200.0)  # on -> pending_off
        assert tracker.state == "pending_off"
        # Still active during pending_off
        assert result is True

    def test_pv_hysteresis_pending_off_back_to_on(self):
        """In 'pending_off', receives power > activate_threshold
        => back to 'on'."""
        tracker = PVHysteresisTracker(
            activate_threshold_w=500,
            deactivate_threshold_w=300,
            required_consecutive=2,
        )
        # Get to "on" state
        tracker.update(600.0)
        tracker.update(700.0)
        assert tracker.state == "on"
        # Trigger pending_off
        tracker.update(200.0)
        assert tracker.state == "pending_off"
        # Solar recovers -- back to "on"
        result = tracker.update(600.0)
        assert tracker.state == "on"
        assert result is True

    def test_pv_hysteresis_pending_off_to_off(self):
        """In 'pending_off' for required consecutive checks
        => transitions to 'off'."""
        tracker = PVHysteresisTracker(
            activate_threshold_w=500,
            deactivate_threshold_w=300,
            required_consecutive=2,
        )
        # Get to "on" state
        tracker.update(600.0)
        tracker.update(700.0)
        assert tracker.state == "on"
        # First below deactivate: pending_off (count=1)
        tracker.update(200.0)
        assert tracker.state == "pending_off"
        # Second below deactivate: off (count=2 >= required)
        result = tracker.update(100.0)
        assert tracker.state == "off"
        assert result is False


# ---------------------------------------------------------------------------
# Signed Fuse Current Math (import positive / export negative)
# ---------------------------------------------------------------------------


class TestSignedFuseCurrent:
    """Tests for signed grid current math -- PV export must increase headroom."""

    def test_export_increases_headroom_vs_zero(self):
        """current_l_amps=-10.0 (export) yields more headroom than 0.0 load."""
        result_export = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=-10.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=1.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        result_zero = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=0.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=1.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result_export.fuse_headroom_amps > result_zero.fuse_headroom_amps
        assert result_export.fuse_headroom_amps == pytest.approx(29.0)  # 20 -(-10) -1
        assert result_zero.fuse_headroom_amps == pytest.approx(19.0)

    def test_worst_case_uses_highest_import_not_average(self):
        """One phase importing 30A with low others -- worst case is 30A, not
        an averaged ~13.3A estimate."""
        worst = worst_case_signed_amps([30.0, 5.0, 5.0])
        assert worst == 30.0

        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=worst,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=1.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        # 30A already exceeds the 20A fuse rating -- headroom must be 0, not
        # the ~4.7A a balanced-average estimate (13.3A) would have produced.
        assert result.fuse_headroom_amps == 0.0

    def test_worst_case_ignores_exporting_phase(self):
        """An exporting (negative) phase must never mask an importing one."""
        worst = worst_case_signed_amps([15.0, -8.1, 2.0])
        assert worst == 15.0


# ---------------------------------------------------------------------------
# Sensor-Unavailable Fallback Behavior
# ---------------------------------------------------------------------------


class TestSensorFallback:
    """Tests for the sensor-unavailable fallback (assume_load vs block)."""

    def test_assume_load_returns_assumed_amps(self):
        result = resolve_current_sensor_fallback(
            fail_behavior="assume_load", assumed_load_amps=10.0
        )
        assert result.effective_amps == 10.0
        assert result.force_zero_headroom is False

    def test_block_forces_zero_headroom_flag(self):
        result = resolve_current_sensor_fallback(
            fail_behavior="block", assumed_load_amps=10.0
        )
        assert result.force_zero_headroom is True

    def test_assume_load_feeds_normal_headroom_math(self):
        """assume_load fallback still lets compute_ems_state calculate
        headroom normally using the assumed load as current_l_amps."""
        fallback = resolve_current_sensor_fallback(
            fail_behavior="assume_load", assumed_load_amps=10.0
        )
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=fallback.effective_amps,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=1.0,
            sensor_blocked=fallback.force_zero_headroom,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.fuse_headroom_amps == pytest.approx(9.0)  # 20 - 10 - 1

    def test_block_forces_charge_limit_to_zero(self):
        """block fallback -- sensor_blocked=True means no charge authorization
        regardless of the (unused) current reading."""
        fallback = resolve_current_sensor_fallback(
            fail_behavior="block", assumed_load_amps=10.0
        )
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=fallback.effective_amps,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            safety_buffer_amps=1.0,
            sensor_blocked=fallback.force_zero_headroom,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.fuse_headroom_amps == 0.0
        assert result.charge_limit_kw == 0.0


# ---------------------------------------------------------------------------
# Battery Self-Consumption Add-Back (prevents ESS-limit self-ratchet)
# ---------------------------------------------------------------------------


class TestBatterySelfConsumptionAddBack:
    """Tests for compute_available_ess_amps() battery add-back."""

    def test_battery_charging_at_limit_does_not_reduce_own_headroom(self):
        """Grid current includes the battery's own charging draw. Without the
        add-back, the battery's own charging looks like external load and
        shrinks its own computed headroom (self-ratchet). With the add-back,
        the true (unaffected) headroom is restored."""
        # Household load excluding battery = 2A; battery itself charges at
        # 16A, so the grid meter reads 18A total.
        without_addback = compute_available_ess_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            worst_phase_amps=18.0,
            battery_own_amps=0.0,
        )
        with_addback = compute_available_ess_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            worst_phase_amps=18.0,
            battery_own_amps=16.0,
        )
        assert without_addback == pytest.approx(1.0)  # 20 - 1 - 18 (ratcheted down)
        assert with_addback == pytest.approx(17.0)  # 20 - 1 - 18 + 16 (true headroom)
        assert with_addback > without_addback

    def test_available_ess_amps_clamped_to_max_ess_charge_amps(self):
        """Hard hardware cap (max_ess_charge_amps) limits the result even
        when the fuse math alone would allow more."""
        result = compute_available_ess_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            worst_phase_amps=0.0,
            battery_own_amps=0.0,
            max_ess_charge_amps=16.0,
        )
        assert result == 16.0  # would be 19.0 without the hardware cap

    def test_available_ess_amps_never_negative(self):
        result = compute_available_ess_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            worst_phase_amps=30.0,
            battery_own_amps=0.0,
        )
        assert result == 0.0


# ---------------------------------------------------------------------------
# Asymmetric ESS-Limit Timing (decrease immediate, increase delayed)
# ---------------------------------------------------------------------------


class TestESSLimitRateLimiter:
    """Tests for ESSLimitRateLimiter -- decreases apply immediately, increases
    only apply after CONF_ESS_INCREASE_DELAY seconds of continuous stability."""

    def test_first_reading_applies_immediately(self):
        limiter = ESSLimitRateLimiter(increase_delay_seconds=180.0)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert limiter.update(10.0, now) == 10.0

    def test_increase_not_applied_before_delay_elapses(self):
        """Computed limit rising is not applied until 180s elapsed."""
        limiter = ESSLimitRateLimiter(increase_delay_seconds=180.0)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        limiter.update(5.0, t0)
        applied = limiter.update(10.0, t0 + timedelta(seconds=60))
        assert applied == 5.0

    def test_increase_applied_after_delay_elapses(self):
        """Once the higher value has been observed continuously for the full
        delay, it is applied."""
        limiter = ESSLimitRateLimiter(increase_delay_seconds=180.0)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        limiter.update(5.0, t0)
        limiter.update(10.0, t0 + timedelta(seconds=60))
        applied = limiter.update(10.0, t0 + timedelta(seconds=241))  # 60 + 181
        assert applied == 10.0

    def test_decrease_applies_immediately(self):
        """Falling applies immediately -- no delay for reducing the limit."""
        limiter = ESSLimitRateLimiter(increase_delay_seconds=180.0)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        limiter.update(10.0, t0)
        applied = limiter.update(3.0, t0 + timedelta(seconds=1))
        assert applied == 3.0

    def test_decrease_cancels_pending_increase(self):
        """A decrease while an increase is pending cancels it -- a later
        higher reading must restart the delay from scratch."""
        limiter = ESSLimitRateLimiter(increase_delay_seconds=180.0)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        limiter.update(5.0, t0)
        limiter.update(10.0, t0 + timedelta(seconds=60))  # pending increase starts
        limiter.update(2.0, t0 + timedelta(seconds=90))  # decrease cancels it
        # 151s after the cancel -- nowhere near a fresh 180s window
        applied = limiter.update(10.0, t0 + timedelta(seconds=241))
        assert applied == 2.0


# ---------------------------------------------------------------------------
# Car-Priority Wiring (real active-slot + home/plugged signals)
# ---------------------------------------------------------------------------


class TestCarPriorityWiring:
    """Tests for car_demands_priority_charging() -- the aggregated signal
    EMSCoordinator now derives from its car coordinators, replacing the old
    'EV module enabled' flag."""

    def test_module_enabled_but_car_unplugged_not_paused(self):
        """Active schedule slot but car not home+plugged -- must NOT demand
        priority (battery is not paused)."""
        cars = [(True, False)]
        assert car_demands_priority_charging(cars) is False

    def test_car_plugged_and_active_slot_demands_priority(self):
        """Active schedule slot AND home+plugged -- demands priority
        charging (battery is paused)."""
        cars = [(True, True)]
        assert car_demands_priority_charging(cars) is True

    def test_plugged_but_no_active_slot_not_paused(self):
        """Car home+plugged but its schedule is currently idle -- no
        priority demand."""
        cars = [(False, True)]
        assert car_demands_priority_charging(cars) is False

    def test_multiple_cars_any_active_triggers_priority(self):
        """If any car among several demands priority, the aggregate is True."""
        cars = [(True, False), (False, True), (True, True)]
        assert car_demands_priority_charging(cars) is True

    def test_no_cars_no_priority(self):
        assert car_demands_priority_charging([]) is False

    def test_end_to_end_battery_not_paused_when_unplugged(self):
        """Module enabled but car unplugged -- battery NOT paused, full
        compute_ems_state pipeline."""
        car_priority = car_demands_priority_charging([(True, False)])
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=5.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=car_priority,
            car_plugged_in=car_priority,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason is None

    def test_end_to_end_battery_paused_when_plugged_and_active(self):
        """Car plugged in with an active slot -- battery IS paused, full
        compute_ems_state pipeline."""
        car_priority = car_demands_priority_charging([(True, True)])
        result = compute_ems_state(
            target_ems_mode="command_charging",
            current_l_amps=5.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=car_priority,
            car_plugged_in=car_priority,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "standby"
        assert result.override_reason == "car_charging_priority"


# ---------------------------------------------------------------------------
# Standby hold: closed discharge gate / active car charge (step 6)
# ---------------------------------------------------------------------------


def _hold_state(**overrides: object) -> EMSDecision:
    """compute_ems_state with quiet-house MSC defaults for the hold tests."""
    defaults: dict[str, object] = {
        "target_ems_mode": "max_self_consumption",
        "current_l_amps": 5.0,
        "fuse_rating_amps": 25.0,
        "max_charge_power_kw": 5.0,
        "battery_soc_pct": 50.0,
        "car_scheduled": False,
        "car_plugged_in": False,
        "pv_power_w": 0.0,
        "pv_hysteresis_active": False,
        "discharge_allowed": True,
        "discharge_gate_reason": "scheduled_discharge",
        "car_charging_active": False,
    }
    defaults.update(overrides)
    return compute_ems_state(**defaults)  # type: ignore[arg-type]


class TestDischargeGateHold:
    """A closed discharge gate must command standby, not MSC + limit 0 --
    the SigenStor ignores the max-discharging-limit register in MSC
    (verified on hardware: limit 0.0, mode MSC, battery discharging)."""

    def test_gate_closed_below_threshold_holds_in_standby(self):
        result = _hold_state(
            discharge_allowed=False, discharge_gate_reason="below_threshold"
        )
        assert result.target_mode == "standby"
        assert result.override_reason == "discharge_gate_closed"
        assert result.charge_limit_kw == 0.0

    def test_gate_closed_reserved_for_peak_holds_in_standby(self):
        result = _hold_state(
            discharge_allowed=False, discharge_gate_reason="reserved_for_peak"
        )
        assert result.target_mode == "standby"
        assert result.override_reason == "discharge_gate_closed"

    def test_no_schedule_falls_back_to_msc(self):
        """Price-feed outage / cold boot must NOT freeze the battery --
        graceful degradation to self-consumption at unknown prices."""
        result = _hold_state(
            discharge_allowed=False, discharge_gate_reason="no_schedule"
        )
        assert result.target_mode == "max_self_consumption"
        assert result.override_reason is None

    def test_gate_open_stays_msc(self):
        result = _hold_state()
        assert result.target_mode == "max_self_consumption"
        assert result.override_reason is None

    def test_command_charging_untouched_by_closed_gate(self):
        """The hold only applies to MSC -- a scheduled grid charge runs
        regardless of the discharge gate."""
        result = _hold_state(
            target_ems_mode="command_charging",
            discharge_allowed=False,
            discharge_gate_reason="below_threshold",
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason is None

    def test_standby_input_passthrough_unchanged(self):
        """Scheduler-commanded standby passes through without gaining the
        override reason -- the hold step only rewrites MSC."""
        result = _hold_state(
            target_ems_mode="standby",
            discharge_allowed=False,
            discharge_gate_reason="below_threshold",
        )
        assert result.target_mode == "standby"
        assert result.override_reason is None


class TestCarChargingActiveHold:
    """MSC must never run while a car actively draws -- the battery
    discharges freely in MSC (owner rule: battery never discharges into
    the car). Export stays exempt; PV promotion precedes the hold."""

    def test_msc_with_active_car_holds_in_standby(self):
        result = _hold_state(car_charging_active=True)
        assert result.target_mode == "standby"
        assert result.override_reason == "car_charging_priority"
        assert result.charge_limit_kw == 0.0

    def test_command_charging_coexists_with_car_session(self):
        """No schedule slot (car_scheduled False) -- battery cheap-night
        grid charge legitimately coexists with a car session (charger draw
        is already inside current_l_amps and the ESS ceiling)."""
        result = _hold_state(
            target_ems_mode="command_charging", car_charging_active=True
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason is None

    def test_export_exempt_from_car_hold(self):
        """Battery export offsets car import at the meter (EMS-03) -- it
        never adds fuse load, so command_discharging passes through."""
        result = _hold_state(
            target_ems_mode="command_discharging", car_charging_active=True
        )
        assert result.target_mode == "command_discharging"
        assert result.override_reason is None

    def test_pv_promotion_precedes_car_hold(self):
        """PV surplus still promotes MSC to charging during a car session --
        the battery soaks surplus instead of freezing."""
        result = _hold_state(
            car_charging_active=True,
            pv_power_w=800.0,
            pv_hysteresis_active=True,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason == "pv_opportunistic"

    def test_pv_promotion_precedes_gate_hold(self):
        """PV surplus wins over a closed discharge gate."""
        result = _hold_state(
            discharge_allowed=False,
            discharge_gate_reason="below_threshold",
            pv_power_w=800.0,
            pv_hysteresis_active=True,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason == "pv_opportunistic"

    def test_pv_promotion_with_sensor_blocked_clamps_limit(self):
        """Regression pin of the existing shape: sensor_blocked zeroes the
        charge authorization, but the PV branch still claims the mode."""
        result = _hold_state(
            discharge_allowed=False,
            discharge_gate_reason="below_threshold",
            pv_power_w=800.0,
            pv_hysteresis_active=True,
            sensor_blocked=True,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason == "pv_opportunistic"
        assert result.charge_limit_kw == 0.0


class TestCarActivelyCharging:
    """Tests for the car_actively_charging() pure helper -- measured truth
    only, no forced-mode term."""

    def test_status_charging_is_active(self):
        assert car_actively_charging("charging", None) is True

    def test_power_above_threshold_is_active(self):
        assert car_actively_charging(None, 0.6) is True

    def test_power_at_threshold_not_active(self):
        """Strict > -- mirrors the charger state machine comparison."""
        assert car_actively_charging(None, 0.5) is False

    @pytest.mark.parametrize("status", ["paused", "awaiting_start", None])
    @pytest.mark.parametrize("power", [None, 0.0])
    def test_inactive_status_and_no_power_not_active(self, status, power):
        assert car_actively_charging(status, power) is False


# ---------------------------------------------------------------------------
# CORE-14: Observe-only command gating
# ---------------------------------------------------------------------------


class TestBuildCommandDecision:
    """Tests for the observe-only command gating choke point."""

    def test_control_enabled_should_send(self):
        """control_enabled=True means the command should actually be sent."""
        decision = build_command_decision(
            control_enabled=True,
            service_domain="select",
            service_name="select_option",
            entity_id="select.ems_mode",
            value="Command Charging (PV First)",
        )
        assert isinstance(decision, CommandDecision)
        assert decision.should_send is True

    def test_control_disabled_suppresses_command(self):
        """control_enabled=False (observe-only default) suppresses the command."""
        decision = build_command_decision(
            control_enabled=False,
            service_domain="select",
            service_name="select_option",
            entity_id="select.ems_mode",
            value="Command Charging (PV First)",
        )
        assert decision.should_send is False

    def test_dry_run_message_states_service_entity_and_value(self):
        """The dry-run message names exactly what would have been sent."""
        decision = build_command_decision(
            control_enabled=False,
            service_domain="number",
            service_name="set_value",
            entity_id="number.charge_limit",
            value=3.5,
        )
        assert decision.dry_run_message.startswith("[dry-run]")
        assert "number.set_value" in decision.dry_run_message
        assert "number.charge_limit" in decision.dry_run_message
        assert "3.5" in decision.dry_run_message

    def test_dry_run_message_present_even_when_sending(self):
        """dry_run_message is always populated, regardless of should_send."""
        decision = build_command_decision(
            control_enabled=True,
            service_domain="select",
            service_name="select_option",
            entity_id="select.ems_mode",
            value="Standby",
        )
        assert decision.dry_run_message  # non-empty regardless


# ---------------------------------------------------------------------------
# BATT-17: Export arbitrage runtime limit + mode passthrough
# ---------------------------------------------------------------------------


class TestComputeExportLimitKw:
    """Tests for the BATT-17 fuse-capped export discharge limit."""

    @pytest.mark.parametrize(
        ("fuse", "buffer"),
        [(20, 1), (25, 2), (16, 1)],
    )
    def test_fuse_cap_never_exceeded(self, fuse, buffer):
        """MANDATORY: result never exceeds (fuse-buffer)*3*0.230 -- the
        per-phase-safe ceiling with NO house-load add-back (house load may
        be single-phase, so a total-load add-back could overload an
        unloaded phase) -- nor the 15.0 kW hardware ceiling."""
        result = compute_export_limit_kw(
            fuse_rating_amps=fuse,
            safety_buffer_amps=buffer,
            battery_soc_pct=80.0,
            export_reserve_soc_pct=20.0,
            soc_available=True,
        )
        assert result is not None
        assert result <= (fuse - buffer) * 3 * 0.230 + 1e-9
        assert result <= 15.0

    def test_default_fuse_gives_13_11_kw(self):
        """20A fuse, 1A buffer: 13.11 kW -- below the 13.8 kW fuse ceiling,
        so the plant's 14.4 kW limit is never reachable."""
        result = compute_export_limit_kw(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=80.0,
            export_reserve_soc_pct=20.0,
            soc_available=True,
        )
        assert result == pytest.approx(13.11)

    def test_soc_unavailable_returns_none(self):
        """soc_available=False must never enable export, even with SOC 50."""
        result = compute_export_limit_kw(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=50.0,
            export_reserve_soc_pct=20.0,
            soc_available=False,
        )
        assert result is None

    @pytest.mark.parametrize("soc", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_soc_returns_none(self, soc):
        """NaN/inf SOC (Modbus/template glitch) must never enable export --
        'nan <= reserve' is False, so without this guard the reserve-floor
        comparison silently passes."""
        result = compute_export_limit_kw(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=soc,
            export_reserve_soc_pct=20.0,
            soc_available=True,
        )
        assert result is None

    @pytest.mark.parametrize("soc", [20.0, 15.0])
    def test_soc_at_or_below_reserve_returns_none(self, soc):
        """SOC equal to or below the reserve floor blocks export."""
        result = compute_export_limit_kw(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=soc,
            export_reserve_soc_pct=20.0,
            soc_available=True,
        )
        assert result is None

    def test_soc_just_above_reserve_returns_limit(self):
        """SOC just above the reserve floor allows export."""
        result = compute_export_limit_kw(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=20.1,
            export_reserve_soc_pct=20.0,
            soc_available=True,
        )
        assert isinstance(result, float)
        assert result == pytest.approx(13.11)

    def test_concurrent_pv_shrinks_cap(self):
        """Live PV shares the grid connection: 5 kW PV shrinks the 13.11 kW
        cap to 8.11 kW so combined injection stays fuse-safe."""
        result = compute_export_limit_kw(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=80.0,
            export_reserve_soc_pct=20.0,
            soc_available=True,
            pv_power_kw=5.0,
        )
        assert result == pytest.approx(8.11)

    def test_pv_exceeding_cap_clamps_to_zero(self):
        """PV alone above the fuse cap leaves zero battery export."""
        result = compute_export_limit_kw(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=80.0,
            export_reserve_soc_pct=20.0,
            soc_available=True,
            pv_power_kw=14.0,
        )
        assert result == 0.0

    def test_negative_pv_reading_ignored(self):
        """A negative PV reading never inflates the cap."""
        result = compute_export_limit_kw(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=80.0,
            export_reserve_soc_pct=20.0,
            soc_available=True,
            pv_power_kw=-2.0,
        )
        assert result == pytest.approx(13.11)

    def test_max_limit_kw_clamp_honored(self):
        """A 63A fuse would allow 42.8 kW -- clamped to max_limit_kw."""
        result = compute_export_limit_kw(
            fuse_rating_amps=63.0,
            safety_buffer_amps=1.0,
            battery_soc_pct=80.0,
            export_reserve_soc_pct=20.0,
            soc_available=True,
        )
        assert result == 15.0


class TestCommandDischargingPassthrough:
    """BATT-17: command_discharging passes through compute_ems_state."""

    def test_passthrough_with_zero_charge_limit(self):
        """command_discharging passes through with charge_limit_kw forced 0.0."""
        result = compute_ems_state(
            target_ems_mode="command_discharging",
            current_l_amps=5.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=8.0,
            battery_soc_pct=80.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_discharging"
        assert result.charge_limit_kw == 0.0

    def test_car_priority_does_not_override_export(self):
        """Car priority never demotes export -- battery export offsets car
        import at the meter, it never adds fuse load."""
        result = compute_ems_state(
            target_ems_mode="command_discharging",
            current_l_amps=5.0,
            fuse_rating_amps=20.0,
            max_charge_power_kw=8.0,
            battery_soc_pct=80.0,
            car_scheduled=True,
            car_plugged_in=True,
            pv_power_w=0.0,
            pv_hysteresis_active=False,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
        )
        assert result.target_mode == "command_discharging"
        assert result.override_reason is None


class TestEmsModeMap:
    """BATT-17: EMS_MODE_MAP carries the SigenStor export option string."""

    def test_command_discharging_option_string(self):
        """command_discharging maps to the exact SigenStor select option."""
        assert (
            EMS_MODE_MAP["command_discharging"]
            == "Command Discharging (ESS First)"
        )


class TestExportCommandsGated:
    """BATT-17: export hardware commands stay behind the CORE-14 gate.

    Both commands the export path sends (the command_discharging mode
    select and the fuse-capped discharge-limit number) route through
    build_command_decision, so control_enabled=False suppresses them.
    """

    @pytest.mark.parametrize(
        ("domain", "service", "entity_id", "value"),
        [
            (
                "select",
                "select_option",
                "select.ems_mode",
                "Command Discharging (ESS First)",
            ),
            ("number", "set_value", "number.discharge_limit", 13.11),
        ],
    )
    def test_control_disabled_suppresses_export_commands(
        self, domain, service, entity_id, value
    ):
        """Device control off must suppress every export command shape."""
        decision = build_command_decision(
            control_enabled=False,
            service_domain=domain,
            service_name=service,
            entity_id=entity_id,
            value=value,
        )
        assert decision.should_send is False
        assert decision.dry_run_message.startswith("[dry-run]")

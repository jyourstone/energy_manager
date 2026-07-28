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

from custom_components.energy_manager.ems_controller import (
    CommandDecision,
    EMSDecision,
    ESSLimitRateLimiter,
    PVHysteresisTracker,
    build_command_decision,
    car_demands_priority_charging,
    clamp_amps,
    compute_available_ess_amps,
    compute_ems_state,
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
        )
        assert result.target_mode == "standby"
        assert result.override_reason is None

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
        )
        assert result.target_mode == "command_charging"
        # Should NOT have pv_opportunistic override -- it was already charging
        assert result.override_reason is None


# ---------------------------------------------------------------------------
# PV Hysteresis State Machine (EMS-08 sub-requirement)
# ---------------------------------------------------------------------------


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
        )
        assert result.target_mode == "standby"
        assert result.override_reason == "car_charging_priority"


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

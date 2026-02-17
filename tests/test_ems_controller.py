"""Comprehensive tests for the pure-Python EMS controller calculation module.

Tests cover all EMS decision paths:
- EMS-01: Mode selection (schedule-driven)
- EMS-02: Fuse protection (dynamic limiting)
- EMS-03: Car priority override
- EMS-04: Safety guards (clamp_amps)
- EMS-08: PV opportunistic charging
- PV hysteresis state machine
"""

from __future__ import annotations

import pytest

from custom_components.energy_manager.ems_controller import (
    EMSDecision,
    PVHysteresisTracker,
    clamp_amps,
    compute_ems_state,
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

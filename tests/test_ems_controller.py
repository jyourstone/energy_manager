"""Comprehensive tests for the pure-Python EMS controller calculation module.

Tests cover all EMS decision paths:
- EMS-01: Mode selection (schedule-driven)
- EMS-02: Fuse protection (dynamic limiting)
- EMS-03: Car priority override
- EMS-04: Safety guards (clamp_amps)
- EMS-08: PV opportunistic charging
- PV hysteresis state machine
- CORE-14: Observe-only command gating
- Incident 2026-08-07: command-path resilience (write backoff, guarded
  writes, select reconciliation, standby-discharge alarm)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.energy_manager.charger_state_machine import (
    POWER_ACTIVE_THRESHOLD_KW,
)
from custom_components.energy_manager.const import EMS_MODE_MAP
from custom_components.energy_manager.ems_controller import (
    CommandDecision,
    EMSDecision,
    ESSLimitRateLimiter,
    PVHysteresisTracker,
    StandbyDischargeMonitor,
    WriteRejectionBackoff,
    build_command_decision,
    car_actively_charging,
    car_demands_priority_charging,
    clamp_amps,
    compute_available_ess_amps,
    compute_ems_state,
    compute_export_limit_kw,
    ems_select_mismatch,
    guarded_device_write,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
        )
        assert result.fuse_headroom_amps == 0.0

    def test_charge_limit_reduced_by_fuse(self):
        """max_charge=5kW but only 3A/phase headroom => limit capped to 2.07kW."""
        # headroom = 20 - 15 - 2 = 3A per phase; the battery charges
        # balanced across 3 phases => 3A * 3 * 230/1000 = 2.07kW
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
            house_consumption_kw=0.0,
        )
        expected_kw = (3.0 * 3 * 230.0) / 1000.0  # 2.07 kW
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
            house_consumption_kw=0.0,
        )
        assert result.charge_limit_kw == 0.0

    def test_charge_limit_uses_max_when_ample_headroom(self):
        """Plenty of headroom => charge_limit equals max_charge_power."""
        # headroom = 63 - 5 - 2 = 56A => 56*3*230/1000 = 38.64kW >> max 5kW
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
        )
        assert result.target_mode == "command_charging"
        # Should NOT have pv_opportunistic override -- it was already charging
        assert result.override_reason is None

    def test_pv_charging_capped_at_surplus_not_gross(self):
        """pv=3000W, house_consumption=1.0kW => charge_limit is the 2.0kW
        surplus (PV minus house load), not the gross 3.0kW PV figure --
        commanding gross PV would push house load onto the grid as an
        import under the SigenStor's PV-First charging behavior."""
        result = compute_ems_state(
            target_ems_mode="standby",
            current_l_amps=5.0,
            fuse_rating_amps=63.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=3000.0,
            pv_hysteresis_active=True,
            max_soc_pct=95.0,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
            house_consumption_kw=1.0,
        )
        assert result.target_mode == "command_charging"
        assert result.charge_limit_kw == pytest.approx(2.0)

    def test_pv_charging_not_promoted_when_surplus_is_zero(self):
        """pv=800W, house_consumption=1.2kW => no surplus. The branch is
        skipped entirely, so with the discharge gate closed for an
        economic reason control falls through to the standby hold exactly
        as if PV were inactive."""
        result = compute_ems_state(
            target_ems_mode="max_self_consumption",
            current_l_amps=5.0,
            fuse_rating_amps=25.0,
            max_charge_power_kw=5.0,
            battery_soc_pct=50.0,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=800.0,
            pv_hysteresis_active=True,
            max_soc_pct=95.0,
            discharge_allowed=False,
            discharge_gate_reason="below_threshold",
            car_charging_active=False,
            house_consumption_kw=1.2,
        )
        assert result.target_mode == "standby"
        assert result.override_reason == "discharge_gate_closed"

    def test_pv_charging_house_zero_matches_gross_behavior(self):
        """house_consumption=0.0 => surplus equals gross PV, matching
        behavior when no house-consumption entity is configured."""
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
            house_consumption_kw=0.0,
        )
        assert result.target_mode == "command_charging"
        assert result.charge_limit_kw == pytest.approx(0.8)

    def test_pv_charging_full_surplus_within_3phase_fuse_headroom(self):
        """Live incident 2026-08-08: PV 7.16kW, house 0.94kW, 14.8A/phase
        ESS ceiling. The per-phase amps ceiling converts to power across
        all 3 phases (14.8 * 3 * 230 = 10.2kW), so the commanded limit is
        the full 6.2kW surplus -- the old single-phase conversion capped
        it at 3.4kW and exported the rest."""
        result = compute_ems_state(
            target_ems_mode="max_self_consumption",
            current_l_amps=5.4,
            fuse_rating_amps=20.0,
            max_charge_power_kw=8.0,
            battery_soc_pct=77.5,
            car_scheduled=False,
            car_plugged_in=False,
            pv_power_w=7160.0,
            pv_hysteresis_active=True,
            safety_buffer_amps=1.0,
            available_ess_amps=14.8,
            discharge_allowed=True,
            discharge_gate_reason="scheduled_discharge",
            car_charging_active=False,
            house_consumption_kw=0.94,
        )
        assert result.target_mode == "command_charging"
        assert result.override_reason == "pv_opportunistic"
        assert result.charge_limit_kw == pytest.approx(6.22)


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
            "house_consumption_kw": 0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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
        "house_consumption_kw": 0.0,
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

    def test_active_car_reason_wins_over_closed_gate(self):
        """Both holds apply -- the car branch is checked first, so its
        reason is the one surfaced to sensors and automations."""
        result = _hold_state(
            car_charging_active=True,
            discharge_allowed=False,
            discharge_gate_reason="below_threshold",
        )
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
        assert car_actively_charging(None, POWER_ACTIVE_THRESHOLD_KW + 0.1) is True

    def test_power_at_threshold_not_active(self):
        """Strict > -- mirrors the charger state machine comparison."""
        assert car_actively_charging(None, POWER_ACTIVE_THRESHOLD_KW) is False

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
            house_consumption_kw=0.0,
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
            house_consumption_kw=0.0,
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


# ---------------------------------------------------------------------------
# Incident 2026-08-07: command-path resilience
# ---------------------------------------------------------------------------


class TestWriteRejectionBackoff:
    """Backoff for persistently rejected device writes."""

    def test_attempts_every_cycle_before_threshold(self):
        """The first failures retry at full cadence -- no backoff yet."""
        backoff = WriteRejectionBackoff()
        for _ in range(2):
            assert backoff.should_attempt() is True
            backoff.record_failure()
        assert backoff.should_attempt() is True

    def test_record_failure_signals_entry_exactly_on_third(self):
        """Only the failure that ENTERS backoff returns True (the caller's
        one-time WARNING); repeats while in backoff stay silent."""
        backoff = WriteRejectionBackoff()
        assert backoff.record_failure() is False
        assert backoff.record_failure() is False
        assert backoff.record_failure() is True
        assert backoff.record_failure() is False

    def test_backoff_retries_only_every_tenth_cycle(self):
        """In backoff, 9 wanted cycles skip, the 10th retries -- repeating."""
        backoff = WriteRejectionBackoff()
        for _ in range(3):
            backoff.record_failure()
        attempts = [backoff.should_attempt() for _ in range(20)]
        assert attempts == [False] * 9 + [True] + [False] * 9 + [True]

    def test_success_resets_to_full_cadence(self):
        backoff = WriteRejectionBackoff()
        for _ in range(3):
            backoff.record_failure()
        assert backoff.in_backoff is True
        backoff.record_success()
        assert backoff.in_backoff is False
        assert backoff.should_attempt() is True

    def test_success_mid_streak_resets_counter(self):
        """The threshold counts CONSECUTIVE failures only."""
        backoff = WriteRejectionBackoff()
        backoff.record_failure()
        backoff.record_failure()
        backoff.record_success()
        assert backoff.consecutive_failures == 0
        assert backoff.record_failure() is False


class TestEmsSelectMismatch:
    """Reconciliation comparison between mode belief and the live select."""

    def test_no_belief_is_never_a_mismatch(self):
        assert ems_select_mismatch(None, "Standby", EMS_MODE_MAP) is False

    def test_unavailable_select_is_never_a_mismatch(self):
        """live_option None (unavailable/unknown) skips silently -- normal
        for ~2 min after an HA restart."""
        assert ems_select_mismatch("standby", None, EMS_MODE_MAP) is False

    def test_matching_option_is_not_a_mismatch(self):
        assert ems_select_mismatch("standby", "Standby", EMS_MODE_MAP) is False

    def test_incident_shape_is_a_mismatch(self):
        """Belief standby, plant in MSC -- the 2026-08-07 incident shape."""
        assert (
            ems_select_mismatch(
                "standby", "Maximum Self Consumption", EMS_MODE_MAP
            )
            is True
        )

    def test_foreign_option_is_a_mismatch(self):
        """An option EM never sends (e.g. manual Remote EMS) still means the
        hardware is not in the commanded mode."""
        assert ems_select_mismatch("standby", "Remote EMS", EMS_MODE_MAP) is True

    def test_unknown_internal_mode_is_not_a_mismatch(self):
        assert ems_select_mismatch("bogus", "Standby", EMS_MODE_MAP) is False


class TestStandbyDischargeMonitor:
    """Commanded-standby vs measured-discharge alarm condition."""

    def test_fires_only_on_second_consecutive_cycle(self):
        monitor = StandbyDischargeMonitor()
        assert monitor.update(True, 12.0) is False
        assert monitor.update(True, 12.0) is True

    def test_keeps_firing_while_condition_persists(self):
        """The caller rate-limits the log line, not the condition."""
        monitor = StandbyDischargeMonitor()
        monitor.update(True, 12.0)
        assert monitor.update(True, 12.0) is True
        assert monitor.update(True, 12.0) is True

    def test_threshold_is_exclusive(self):
        """Exactly 0.5 kW is not an alarm -- discharge must exceed it."""
        monitor = StandbyDischargeMonitor()
        assert monitor.update(True, 0.5) is False
        assert monitor.update(True, 0.5) is False

    def test_non_standby_resets_the_streak(self):
        monitor = StandbyDischargeMonitor()
        monitor.update(True, 12.0)
        assert monitor.update(False, 12.0) is False
        assert monitor.update(True, 12.0) is False

    def test_unavailable_power_resets_the_streak(self):
        monitor = StandbyDischargeMonitor()
        monitor.update(True, 12.0)
        assert monitor.update(True, None) is False
        assert monitor.update(True, 12.0) is False

    def test_low_discharge_resets_the_streak(self):
        monitor = StandbyDischargeMonitor()
        monitor.update(True, 12.0)
        assert monitor.update(True, 0.2) is False
        assert monitor.update(True, 12.0) is False


class TestGuardedDeviceWrite:
    """Exception isolation + backoff wiring for device writes."""

    def test_success_passes_through(self):
        backoff = WriteRejectionBackoff()

        async def send() -> bool:
            return True

        assert asyncio.run(guarded_device_write("test", send, backoff)) is True
        assert backoff.consecutive_failures == 0

    def test_exception_is_caught_and_counted(self):
        """A raising write returns False instead of propagating -- the rest
        of the EMS cycle must keep running (incident 2026-08-07)."""
        backoff = WriteRejectionBackoff()

        async def send() -> bool:
            raise RuntimeError("Modbus register rejected")

        assert (
            asyncio.run(guarded_device_write("test", send, backoff)) is False
        )
        assert backoff.consecutive_failures == 1

    def test_skipped_send_leaves_counter_untouched(self):
        """A suppressed/unavailable send (False, no exception) is neither a
        failure nor a success."""
        backoff = WriteRejectionBackoff()
        backoff.record_failure()
        backoff.record_failure()

        async def send() -> bool:
            return False

        assert (
            asyncio.run(guarded_device_write("test", send, backoff)) is False
        )
        assert backoff.consecutive_failures == 2

    def test_backoff_skips_the_send_entirely(self):
        backoff = WriteRejectionBackoff()
        calls = 0

        async def send() -> bool:
            nonlocal calls
            calls += 1
            raise RuntimeError("rejected")

        async def run() -> None:
            nonlocal calls
            for _ in range(3):
                await guarded_device_write("test", send, backoff)
            # In backoff: the next 9 wanted cycles never invoke send().
            for _ in range(9):
                await guarded_device_write("test", send, backoff)
            assert calls == 3
            # The 10th wanted cycle retries.
            await guarded_device_write("test", send, backoff)
            assert calls == 4

        asyncio.run(run())

    def test_warning_logged_once_on_backoff_entry(self, caplog):
        backoff = WriteRejectionBackoff()

        async def send() -> bool:
            raise RuntimeError("rejected")

        async def run() -> None:
            for _ in range(3):
                await guarded_device_write("test target", send, backoff)

        with caplog.at_level(logging.WARNING):
            asyncio.run(run())
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "backing off" in warnings[0].getMessage()

    def test_success_after_backoff_recovers_full_cadence(self):
        backoff = WriteRejectionBackoff()
        sent_calls = 0

        async def failing() -> bool:
            raise RuntimeError("rejected")

        async def succeeding() -> bool:
            nonlocal sent_calls
            sent_calls += 1
            return True

        async def run() -> None:
            for _ in range(3):
                await guarded_device_write("test", failing, backoff)
            for _ in range(9):
                assert (
                    await guarded_device_write("test", succeeding, backoff)
                    is False
                )
            assert sent_calls == 0
            assert (
                await guarded_device_write("test", succeeding, backoff)
                is True
            )
            # Recovered: the very next cycle attempts immediately again.
            assert (
                await guarded_device_write("test", succeeding, backoff)
                is True
            )

        asyncio.run(run())


class TestGuardedDeviceWriteBypassBackoff:
    """bypass_backoff=True (incident 2026-08-07 follow-up): safety-critical
    limit decreases must never be delayed by another target's backoff --
    last night's incident proved rejection can be value-dependent (a
    SigenStor accepted 0.0 but rejected a nonzero value on the same
    register), so a decrease may need to succeed while the increases that
    triggered backoff keep failing. The outcome is still recorded so a
    persistently-rejected bypassed write still enters backoff for its own
    future (non-bypassed) increases."""

    def test_bypass_skips_the_gate_while_in_backoff(self):
        backoff = WriteRejectionBackoff()
        backoff.record_failure()
        backoff.record_failure()
        backoff.record_failure()
        assert backoff.in_backoff is True
        calls = 0

        async def send() -> bool:
            nonlocal calls
            calls += 1
            return True

        assert (
            asyncio.run(
                guarded_device_write("test", send, backoff, bypass_backoff=True)
            )
            is True
        )
        assert calls == 1

    def test_bypass_failure_is_still_counted(self):
        backoff = WriteRejectionBackoff()
        backoff.record_failure()
        backoff.record_failure()
        backoff.record_failure()
        assert backoff.in_backoff is True

        async def failing() -> bool:
            raise RuntimeError("rejected")

        async def run() -> None:
            await guarded_device_write(
                "test", failing, backoff, bypass_backoff=True
            )

        asyncio.run(run())
        assert backoff.consecutive_failures == 4

    def test_bypass_success_still_resets_the_counter(self):
        backoff = WriteRejectionBackoff()
        backoff.record_failure()
        backoff.record_failure()
        backoff.record_failure()
        assert backoff.in_backoff is True

        async def succeeding() -> bool:
            return True

        assert (
            asyncio.run(
                guarded_device_write(
                    "test", succeeding, backoff, bypass_backoff=True
                )
            )
            is True
        )
        assert backoff.consecutive_failures == 0

    def test_increase_without_bypass_still_gated(self):
        """bypass_backoff=False (the default, used for increases) is
        unaffected -- backoff still gates exactly as before."""
        backoff = WriteRejectionBackoff()
        backoff.record_failure()
        backoff.record_failure()
        backoff.record_failure()
        assert backoff.in_backoff is True
        calls = 0

        async def send() -> bool:
            nonlocal calls
            calls += 1
            return True

        assert asyncio.run(guarded_device_write("test", send, backoff)) is False
        assert calls == 0

    def test_none_belief_still_gated(self):
        """An unknown belief (coordinator's _last_charge_limit /
        _last_sent_discharge_limit is None) must never bypass -- the
        coordinator only computes bypass_backoff=True for a *known*,
        strictly-lower target. Explicit bypass_backoff=False reproduces
        that None-belief case."""
        backoff = WriteRejectionBackoff()
        backoff.record_failure()
        backoff.record_failure()
        backoff.record_failure()
        assert backoff.in_backoff is True
        calls = 0

        async def send() -> bool:
            nonlocal calls
            calls += 1
            return True

        assert (
            asyncio.run(
                guarded_device_write("test", send, backoff, bypass_backoff=False)
            )
            is False
        )
        assert calls == 0

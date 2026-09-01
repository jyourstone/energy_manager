"""Exhaustive tests for the pure-Python Easee charger control module.

Tests cover all Wave A behaviors from 05-EXECUTION.md:
- Mode arbitration (forced > scheduled > solar > idle), single-car selection
- Grid amp math (fuse headroom, add-back, ceil rounding, grid power ceiling,
  car max-charge ceiling)
- Solar amp math (net surplus, SOC gate with round-up, floor rounding,
  activation/deactivation hysteresis)
- Phase capability conversion factors and the phase-switch threshold decision
- Amp hysteresis (120s increase / 5s decrease asymmetry, pending revalidation)
- Phase-switch sequence state machine (PAUSING/SET_PHASE/RESUMING/SET_LIMIT),
  timeouts, and fuse re-verification (headroom collapse mid-sequence)
- The three fuse protection layers (emergency overload, headroom-based
  target, 0A-target safety stop) and the pre-start gate
- Unauthorized-charge suppression
- Terminal-state reset (disconnected/completed/error)
- Generic stuck-command detection
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.energy_manager.charger_state_machine import (
    MAX_PENDING_DOWNTIME_SECONDS,
    MAX_RESTORED_PENDING_AGE_SECONDS,
    CarDemand,
    ChargerAmpHysteresis,
    ChargerCommand,
    ChargerController,
    ChargerDecision,
    ChargerInputs,
    SolarActivationTracker,
    clamp_amps,
    compute_charger_capacity_amps,
    compute_solar_net_kw,
    compute_solar_raw_amps,
    compute_solar_surplus_kw,
    conversion_factor_for_phase_capability,
    phase_switch_target,
    soc_gate_satisfied,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _car(
    active_slot: bool = True,
    home_and_plugged: bool = True,
    phase_capability: int = 3,
    max_charge_kw: float = 22.0,
    soc_pct: float | None = None,
    solar_target_soc_pct: float = 100.0,
) -> CarDemand:
    """A CarDemand with a generous max_charge_kw so it never binds unless overridden."""
    return CarDemand(
        active_slot=active_slot,
        home_and_plugged=home_and_plugged,
        phase_capability=phase_capability,
        max_charge_kw=max_charge_kw,
        soc_pct=soc_pct,
        solar_target_soc_pct=solar_target_soc_pct,
    )


def _inputs(**overrides) -> ChargerInputs:
    """ChargerInputs with ample-headroom defaults; override per test.

    Defaults: fuse=20A, buffer=2A (explicit -- pinned here rather than
    relying on the ChargerInputs dataclass default so this fixture's math
    stays stable regardless of that default), worst=0A, current_limit=0A =>
    fuse_available=18A; grid ceiling (12-0.5)*1.45=16.675A; car cap
    22*1.45=31.9A => capacity=16.675A (ample, only max_amps=16 binds).
    """
    defaults = {
        "charger_status": "awaiting_start",
        "charger_power_kw": 0.0,
        "measured_worst_case_signed_amps": 0.0,
        "current_dynamic_limit_amps": 0.0,
        "force_charging": False,
        "solar_surplus_kw": 0.0,
        "battery_soc_pct": 50.0,
        "current_phase_mode": "three",
        "now": T0,
        "fuse_rating_amps": 20.0,
        "cars": (_car(),),
        "safety_buffer_amps": 2.0,
    }
    defaults.update(overrides)
    return ChargerInputs(**defaults)


# ---------------------------------------------------------------------------
# Pure function: compute_charger_capacity_amps (grid amp math)
# ---------------------------------------------------------------------------


class TestComputeChargerCapacityAmps:
    """Fuse headroom + add-back + ceil() + grid power ceiling + car max cap."""

    def test_basic_headroom_with_addback(self):
        """fuse=20, buffer=2, worst=5, current_limit=0 => 13A fuse-side."""
        result = compute_charger_capacity_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=2.0,
            measured_worst_case_signed_amps=5.0,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        assert result == pytest.approx(13.0)

    def test_ceil_rounds_conservatively_on_import(self):
        """worst=5.3A (import) => ceil=6, not 5 -- conservative (less headroom)."""
        result = compute_charger_capacity_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=2.0,
            measured_worst_case_signed_amps=5.3,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        assert result == pytest.approx(12.0)  # 20-2-6+0, not 20-2-5.3+0=12.7

    def test_ceil_rounds_conservatively_on_export(self):
        """worst=-10.4A (export) => ceil=-10, not -11 -- still conservative."""
        result = compute_charger_capacity_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=2.0,
            measured_worst_case_signed_amps=-10.4,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        assert result == pytest.approx(28.0)  # 20-2-(-10)+0, not 20-2-(-11)+0=29

    def test_export_increases_headroom_vs_zero(self):
        export_result = compute_charger_capacity_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=2.0,
            measured_worst_case_signed_amps=-10.0,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        zero_result = compute_charger_capacity_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=2.0,
            measured_worst_case_signed_amps=0.0,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        assert export_result > zero_result

    def test_addback_prevents_self_ratchet(self):
        """The charger's own dynamic limit is added back so its own draw
        doesn't shrink its own headroom."""
        without_addback = compute_charger_capacity_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            measured_worst_case_signed_amps=18.0,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        with_addback = compute_charger_capacity_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=1.0,
            measured_worst_case_signed_amps=18.0,
            current_dynamic_limit_amps=16.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        assert without_addback == pytest.approx(1.0)
        assert with_addback == pytest.approx(17.0)
        assert with_addback > without_addback

    def test_grid_power_ceiling_binds_when_lower(self):
        """12kW-0.5kW ceiling (converted to amps) can be the binding constraint."""
        result = compute_charger_capacity_amps(
            fuse_rating_amps=100.0,
            safety_buffer_amps=0.0,
            measured_worst_case_signed_amps=0.0,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=12.0,
            grid_power_safety_buffer_kw=0.5,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        assert result == pytest.approx(11.5 * 1.45)

    def test_car_max_charge_kw_binds_when_lower(self):
        """A car with a low max_charge_kw caps the result below fuse/grid ceilings."""
        result = compute_charger_capacity_amps(
            fuse_rating_amps=100.0,
            safety_buffer_amps=0.0,
            measured_worst_case_signed_amps=0.0,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=3.0,
        )
        assert result == pytest.approx(3.0 * 1.45)

    def test_never_negative(self):
        result = compute_charger_capacity_amps(
            fuse_rating_amps=20.0,
            safety_buffer_amps=2.0,
            measured_worst_case_signed_amps=100.0,
            current_dynamic_limit_amps=0.0,
            grid_power_cap_kw=1000.0,
            grid_power_safety_buffer_kw=0.0,
            conversion_factor=1.45,
            car_max_charge_kw=1000.0,
        )
        assert result == 0.0


# ---------------------------------------------------------------------------
# Pure functions: solar math
# ---------------------------------------------------------------------------


class TestSolarMath:
    def test_net_solar_subtracts_safety_buffer(self):
        assert compute_solar_net_kw(2.0, 0.5) == pytest.approx(1.5)

    def test_net_solar_never_negative(self):
        assert compute_solar_net_kw(0.3, 0.5) == 0.0

    def test_net_solar_exact_zero_boundary(self):
        assert compute_solar_net_kw(0.5, 0.5) == 0.0

    def test_solar_raw_amps_floors(self):
        """net=3.0kW * 1.45 = 4.35 => floor to 4."""
        assert compute_solar_raw_amps(3.0, 1.45) == 4.0


class TestComputeSolarSurplusKw:
    """Tests for the EV-09 live solar-surplus formula.

    surplus = pv - house_consumption - max(battery_power, 0) + charger_power,
    with excluded_power_kw subtracted from house_consumption first (EMS-13).
    """

    def test_basic_formula(self):
        # 5kW PV, 2kW house load, 1kW battery charging, 0.5kW charger draw
        # 5 - 2 - 1 + 0.5 = 2.5
        result = compute_solar_surplus_kw(
            pv_power_kw=5.0,
            house_consumption_kw=2.0,
            battery_power_kw=1.0,
            charger_power_kw=0.5,
        )
        assert result == pytest.approx(2.5)

    def test_battery_discharging_not_subtracted(self):
        # Negative (discharging) battery power must not add to the surplus --
        # only max(battery_power, 0) is subtracted.
        result = compute_solar_surplus_kw(
            pv_power_kw=5.0,
            house_consumption_kw=2.0,
            battery_power_kw=-3.0,
            charger_power_kw=0.0,
        )
        assert result == pytest.approx(3.0)

    def test_battery_idle_zero_contributes_nothing(self):
        result = compute_solar_surplus_kw(
            pv_power_kw=2.0,
            house_consumption_kw=1.0,
            battery_power_kw=0.0,
            charger_power_kw=0.0,
        )
        assert result == pytest.approx(1.0)

    def test_excluded_power_reduces_effective_house_consumption(self):
        # House consumption 3kW, but 1kW of that is an excluded water heater --
        # effective consumption is 2kW, so surplus = 4 - 2 + 0 = 2.
        result = compute_solar_surplus_kw(
            pv_power_kw=4.0,
            house_consumption_kw=3.0,
            battery_power_kw=0.0,
            charger_power_kw=0.0,
            excluded_power_kw=1.0,
        )
        assert result == pytest.approx(2.0)

    def test_charger_power_added_back(self):
        # House consumption already includes the charger's own draw --
        # charger_power_kw must be added back so the charger's own charging
        # doesn't count against its own solar-surplus headroom.
        without_charger = compute_solar_surplus_kw(
            pv_power_kw=3.0,
            house_consumption_kw=2.0,
            battery_power_kw=0.0,
            charger_power_kw=0.0,
        )
        with_charger = compute_solar_surplus_kw(
            pv_power_kw=3.0,
            house_consumption_kw=2.0,
            battery_power_kw=0.0,
            charger_power_kw=1.5,
        )
        assert with_charger == pytest.approx(without_charger + 1.5)

    def test_no_clamping_negative_surplus_passes_through(self):
        # A deficit (more house load than PV production) is returned as a
        # negative value -- this function must NOT clamp to 0. The
        # ChargerController's own SolarActivationTracker + solar safety
        # buffer + start threshold (compute_solar_net_kw) are responsible
        # for gating, not this formula.
        result = compute_solar_surplus_kw(
            pv_power_kw=1.0,
            house_consumption_kw=4.0,
            battery_power_kw=1.0,
            charger_power_kw=0.0,
        )
        assert result == pytest.approx(-4.0)
        assert result < 0.0

    def test_solar_raw_amps_floors_exact_integer(self):
        assert compute_solar_raw_amps(2.0, 2.5) == 5.0

    def test_solar_raw_amps_zero(self):
        assert compute_solar_raw_amps(0.0, 1.45) == 0.0

    def test_soc_gate_round_up_satisfied_just_under_100(self):
        """99.6% rounded up to 100 satisfies a 100% gate."""
        assert soc_gate_satisfied(99.6, 100.0, round_up=True) is True

    def test_soc_gate_no_round_up_not_satisfied_just_under_100(self):
        assert soc_gate_satisfied(99.6, 100.0, round_up=False) is False

    def test_soc_gate_exact_match_always_satisfied(self):
        assert soc_gate_satisfied(100.0, 100.0, round_up=False) is True

    def test_soc_gate_round_up_still_fails_when_far_below(self):
        assert soc_gate_satisfied(99.0, 100.0, round_up=True) is False

    def test_soc_gate_above_threshold_satisfied(self):
        assert soc_gate_satisfied(100.0, 95.0, round_up=False) is True


# ---------------------------------------------------------------------------
# Pure function: conversion_factor_for_phase_capability
# ---------------------------------------------------------------------------


class TestConversionFactorForPhaseCapability:
    def test_one_phase(self):
        assert conversion_factor_for_phase_capability(1, 4.3, 2.5, 1.45) == 4.3

    def test_two_phase(self):
        assert conversion_factor_for_phase_capability(2, 4.3, 2.5, 1.45) == 2.5

    def test_three_phase(self):
        assert conversion_factor_for_phase_capability(3, 4.3, 2.5, 1.45) == 1.45

    def test_defensive_zero_falls_back_to_one_phase(self):
        assert conversion_factor_for_phase_capability(0, 4.3, 2.5, 1.45) == 4.3


# ---------------------------------------------------------------------------
# Pure function: phase_switch_target
# ---------------------------------------------------------------------------


class TestPhaseSwitchTarget:
    def test_one_phase_car_always_single(self):
        """Even with huge available capacity, a 1-phase car never goes three."""
        assert phase_switch_target(1, 1000.0, 1.45, 4.1) == "single"

    def test_three_phase_at_exact_threshold_boundary_goes_three(self):
        """capacity = threshold * factor exactly => inclusive boundary, 'three'."""
        capacity = 4.1 * 1.45
        assert phase_switch_target(3, capacity, 1.45, 4.1) == "three"

    def test_three_phase_just_below_threshold_stays_single(self):
        capacity = 4.1 * 1.45 - 0.05
        assert phase_switch_target(3, capacity, 1.45, 4.1) == "single"

    def test_three_phase_ample_capacity_goes_three(self):
        assert phase_switch_target(3, 20.0, 1.45, 4.1) == "three"

    def test_two_phase_car_uses_same_three_phase_threshold(self):
        """2-phase cars still need the wallbox in 'three' mode -- same test."""
        assert phase_switch_target(2, 20.0, 1.45, 4.1) == "three"
        assert phase_switch_target(2, 1.0, 1.45, 4.1) == "single"


# ---------------------------------------------------------------------------
# Pure function: clamp_amps
# ---------------------------------------------------------------------------


class TestClampAmps:
    def test_within_range_unchanged(self):
        assert clamp_amps(10.0, 6.0, 16.0) == 10.0

    def test_below_min_clamped_up(self):
        assert clamp_amps(3.0, 6.0, 16.0) == 6.0

    def test_above_max_clamped_down(self):
        assert clamp_amps(20.0, 6.0, 16.0) == 16.0

    def test_exact_boundaries(self):
        assert clamp_amps(6.0, 6.0, 16.0) == 6.0
        assert clamp_amps(16.0, 6.0, 16.0) == 16.0


# ---------------------------------------------------------------------------
# ChargerAmpHysteresis -- 120s increase / 5s decrease asymmetry
# ---------------------------------------------------------------------------


class TestChargerAmpHysteresis:
    def test_first_reading_applies_immediately(self):
        h = ChargerAmpHysteresis()
        assert h.update(10.0, T0, 120.0, 5.0) == 10.0

    def test_increase_not_applied_before_120s(self):
        h = ChargerAmpHysteresis()
        h.update(6.0, T0, 120.0, 5.0)
        applied = h.update(10.0, T0 + timedelta(seconds=60), 120.0, 5.0)
        assert applied == 6.0

    def test_increase_applied_after_120s(self):
        h = ChargerAmpHysteresis()
        h.update(6.0, T0, 120.0, 5.0)
        h.update(10.0, T0 + timedelta(seconds=1), 120.0, 5.0)
        applied = h.update(10.0, T0 + timedelta(seconds=122), 120.0, 5.0)
        assert applied == 10.0

    def test_increase_exact_boundary_120s_applies(self):
        h = ChargerAmpHysteresis()
        h.update(6.0, T0, 120.0, 5.0)
        h.update(10.0, T0, 120.0, 5.0)
        applied = h.update(10.0, T0 + timedelta(seconds=120), 120.0, 5.0)
        assert applied == 10.0

    def test_increase_just_under_120s_boundary_not_applied(self):
        h = ChargerAmpHysteresis()
        h.update(6.0, T0, 120.0, 5.0)
        h.update(10.0, T0, 120.0, 5.0)
        applied = h.update(10.0, T0 + timedelta(seconds=119.9), 120.0, 5.0)
        assert applied == 6.0

    def test_decrease_not_applied_before_5s(self):
        h = ChargerAmpHysteresis()
        h.update(10.0, T0, 120.0, 5.0)
        applied = h.update(3.0, T0 + timedelta(seconds=2), 120.0, 5.0)
        assert applied == 10.0

    def test_decrease_applied_after_5s(self):
        h = ChargerAmpHysteresis()
        h.update(10.0, T0, 120.0, 5.0)
        h.update(3.0, T0 + timedelta(seconds=1), 120.0, 5.0)
        applied = h.update(3.0, T0 + timedelta(seconds=6), 120.0, 5.0)
        assert applied == 3.0

    def test_decrease_never_waits_the_120s_increase_delay(self):
        """Never lengthen the decrease path -- 5s only, even though the
        increase delay is configured much longer."""
        h = ChargerAmpHysteresis()
        h.update(10.0, T0, 120.0, 5.0)
        h.update(3.0, T0, 120.0, 5.0)
        applied = h.update(3.0, T0 + timedelta(seconds=5), 120.0, 5.0)
        assert applied == 3.0

    def test_decrease_cancels_pending_increase(self):
        """A decrease while an increase is pending cancels it and starts a
        fresh 5s decrease wait."""
        h = ChargerAmpHysteresis()
        h.update(5.0, T0, 120.0, 5.0)
        h.update(10.0, T0 + timedelta(seconds=60), 120.0, 5.0)  # pending increase
        h.update(2.0, T0 + timedelta(seconds=90), 120.0, 5.0)  # cancels it
        # Only 6s after the cancel -- decrease's own 5s timer, not the 120s one.
        applied = h.update(2.0, T0 + timedelta(seconds=96), 120.0, 5.0)
        assert applied == 2.0

    def test_pending_increase_revalidated_keeps_lower_of_pending_and_new(self):
        """A higher reading while an increase is pending does not raise the
        target or restart the timer -- the original (lower) target keeps
        counting down."""
        h = ChargerAmpHysteresis()
        h.update(6.0, T0, 120.0, 5.0)
        h.update(10.0, T0 + timedelta(seconds=1), 120.0, 5.0)  # pending=10
        h.update(14.0, T0 + timedelta(seconds=2), 120.0, 5.0)  # higher -- ignored
        applied = h.update(14.0, T0 + timedelta(seconds=122), 120.0, 5.0)
        # Only 121s since the ORIGINAL pending (10) was set -- enough to apply
        # the original lower target, not the later higher one.
        assert applied == 10.0

    def test_pending_increase_revalidated_lower_candidate_restarts_timer(self):
        """A lower increase candidate replaces the pending target and
        restarts the 120s timer for that lower value."""
        h = ChargerAmpHysteresis()
        h.update(6.0, T0, 120.0, 5.0)
        h.update(14.0, T0 + timedelta(seconds=1), 120.0, 5.0)  # pending=14
        h.update(10.0, T0 + timedelta(seconds=100), 120.0, 5.0)  # lower -- restarts
        # 100s after the restart (200s total) -- not yet 120s from the restart.
        applied = h.update(10.0, T0 + timedelta(seconds=200), 120.0, 5.0)
        assert applied == 6.0
        # 121s after the restart -- now applies (the lower, 10, value).
        applied = h.update(10.0, T0 + timedelta(seconds=222), 120.0, 5.0)
        assert applied == 10.0

    def test_unchanged_value_clears_pending(self):
        h = ChargerAmpHysteresis()
        h.update(6.0, T0, 120.0, 5.0)
        h.update(10.0, T0 + timedelta(seconds=1), 120.0, 5.0)  # pending
        applied = h.update(6.0, T0 + timedelta(seconds=2), 120.0, 5.0)
        assert applied == 6.0
        # A later rise must restart the 120s wait fully -- pending was cleared.
        applied = h.update(10.0, T0 + timedelta(seconds=100), 120.0, 5.0)
        assert applied == 6.0


# ---------------------------------------------------------------------------
# SolarActivationTracker -- 300s activation / 60s deactivation
# ---------------------------------------------------------------------------


class TestSolarActivationTracker:
    def test_starts_inactive(self):
        t = SolarActivationTracker()
        assert t.active is False

    def test_raw_ok_false_stays_inactive(self):
        t = SolarActivationTracker()
        assert t.update(False, T0, 300.0, 60.0) is False

    def test_activation_not_yet_before_delay(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        active = t.update(True, T0 + timedelta(seconds=100), 300.0, 60.0)
        assert active is False

    def test_activation_exact_boundary_300s(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        active = t.update(True, T0 + timedelta(seconds=300), 300.0, 60.0)
        assert active is True

    def test_activation_just_under_300s_boundary(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        active = t.update(True, T0 + timedelta(seconds=299.9), 300.0, 60.0)
        assert active is False

    def test_deactivation_not_yet_before_60s(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        t.update(True, T0 + timedelta(seconds=300), 300.0, 60.0)
        assert t.active is True
        active = t.update(False, T0 + timedelta(seconds=310), 300.0, 60.0)
        assert active is True  # still active, pending_off started

    def test_deactivation_exact_boundary_60s(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        t.update(True, T0 + timedelta(seconds=300), 300.0, 60.0)
        t.update(False, T0 + timedelta(seconds=310), 300.0, 60.0)
        active = t.update(False, T0 + timedelta(seconds=370), 300.0, 60.0)
        assert active is False

    def test_flip_back_true_during_pending_off_cancels_it(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        t.update(True, T0 + timedelta(seconds=300), 300.0, 60.0)
        t.update(False, T0 + timedelta(seconds=310), 300.0, 60.0)  # pending_off
        t.update(True, T0 + timedelta(seconds=320), 300.0, 60.0)  # recovered
        # 50s later -- would have been past the 60s deactivation window if
        # the pending hadn't been cancelled and restarted.
        active = t.update(False, T0 + timedelta(seconds=370), 300.0, 60.0)
        assert active is True

    def test_flip_back_false_during_pending_on_cancels_it(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        t.update(False, T0 + timedelta(seconds=100), 300.0, 60.0)  # cancel
        active = t.update(True, T0 + timedelta(seconds=350), 300.0, 60.0)
        # A fresh 300s wait is required from t=100's cancel, not from t=0.
        assert active is False


# ---------------------------------------------------------------------------
# SolarActivationTracker -- serialize/restore persistence
# ---------------------------------------------------------------------------


class TestSolarTrackerPersistence:
    def test_round_trip_active_with_pending_off(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        t.update(True, T0 + timedelta(seconds=300), 300.0, 60.0)  # latched on
        t.update(False, T0 + timedelta(seconds=310), 300.0, 60.0)  # pending_off
        restored = SolarActivationTracker.restore(
            t.serialize_stored(T0 + timedelta(seconds=310)),
            T0 + timedelta(seconds=320),
        )
        assert restored.active is True
        # The deactivation countdown continues from the persisted
        # pending_since (t=310), so 60s after it the latch drops.
        assert restored.update(False, T0 + timedelta(seconds=350), 300.0, 60.0) is True
        assert restored.update(False, T0 + timedelta(seconds=370), 300.0, 60.0) is False

    def test_round_trip_inactive_with_pending_on(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)  # pending_on started
        restored = SolarActivationTracker.restore(
            t.serialize_stored(T0 + timedelta(seconds=90)),
            T0 + timedelta(seconds=100),
        )
        assert restored.active is False
        # The activation countdown continues from the persisted
        # pending_since (t=0) -- NOT restarted by a quick reload.
        assert restored.update(True, T0 + timedelta(seconds=299), 300.0, 60.0) is False
        assert restored.update(True, T0 + timedelta(seconds=300), 300.0, 60.0) is True

    def test_round_trip_active_no_pending(self):
        t = SolarActivationTracker()
        t.update(True, T0, 300.0, 60.0)
        t.update(True, T0 + timedelta(seconds=300), 300.0, 60.0)
        restored = SolarActivationTracker.restore(
            t.serialize(), T0 + timedelta(seconds=400)
        )
        assert restored.active is True
        assert restored.serialize()["pending_since"] is None

    @pytest.mark.parametrize("raw", [None, "garbage", 42, [], ["active", True]])
    def test_garbage_restores_fresh_default(self, raw):
        restored = SolarActivationTracker.restore(raw, T0)
        assert restored.active is False
        assert restored.serialize() == {"active": False, "pending_since": None}

    def test_malformed_partial_dict_restores_fresh_default(self):
        restored = SolarActivationTracker.restore({"pending_since": None}, T0)
        assert restored.active is False

    def test_non_bool_active_restores_fresh_default(self):
        restored = SolarActivationTracker.restore(
            {"active": "yes", "pending_since": None}, T0
        )
        assert restored.active is False

    def test_unparseable_pending_since_restores_fresh_default(self):
        restored = SolarActivationTracker.restore(
            {"active": True, "pending_since": "not-a-timestamp"}, T0
        )
        assert restored.active is False

    def test_future_pending_since_dropped_but_active_kept(self):
        raw = {
            "active": True,
            "pending_since": (T0 + timedelta(hours=1)).isoformat(),
        }
        restored = SolarActivationTracker.restore(raw, T0)
        assert restored.active is True
        assert restored.serialize()["pending_since"] is None

    def test_stale_pending_since_dropped_but_active_kept(self):
        # Downtime must not count toward the continuously-held delay: a
        # pending timer older than MAX_RESTORED_PENDING_AGE_SECONDS is
        # discarded so one post-restart surplus spike cannot flip the latch.
        raw = {
            "active": True,
            "pending_since": (
                T0 - timedelta(seconds=MAX_RESTORED_PENDING_AGE_SECONDS + 1)
            ).isoformat(),
        }
        restored = SolarActivationTracker.restore(raw, T0)
        assert restored.active is True
        assert restored.serialize()["pending_since"] is None

    def test_pending_since_within_age_bound_kept(self):
        raw = {
            "active": False,
            "pending_since": (
                T0 - timedelta(seconds=MAX_RESTORED_PENDING_AGE_SECONDS - 1)
            ).isoformat(),
            "saved_at": T0.isoformat(),
        }
        restored = SolarActivationTracker.restore(raw, T0)
        assert restored.serialize()["pending_since"] is not None

    def test_naive_pending_since_coerced_to_utc(self):
        # Persisted by an older/foreign writer without tzinfo -- must be
        # treated as UTC, and elapsed-time math against an aware `now`
        # must not raise.
        raw = {
            "active": False,
            "pending_since": T0.replace(tzinfo=None).isoformat(),
            "saved_at": (T0 + timedelta(seconds=100)).isoformat(),
        }
        restored = SolarActivationTracker.restore(raw, T0 + timedelta(seconds=100))
        assert restored.active is False
        assert restored.update(True, T0 + timedelta(seconds=300), 300.0, 60.0) is True

    def test_serialize_stored_stamps_saved_at(self):
        t = SolarActivationTracker()
        assert t.serialize_stored(T0) == {
            "active": False,
            "pending_since": None,
            "saved_at": T0.isoformat(),
        }

    def test_missing_saved_at_drops_pending_keeps_active(self):
        # Payload without saved_at gives no proof the downtime was short,
        # so the pending timer restarts; the latch itself survives.
        raw = {
            "active": True,
            "pending_since": (T0 - timedelta(seconds=30)).isoformat(),
        }
        restored = SolarActivationTracker.restore(raw, T0)
        assert restored.active is True
        assert restored.serialize()["pending_since"] is None

    def test_long_downtime_drops_pending(self):
        # Downtime is unobserved surplus: a save-to-restore gap past
        # MAX_PENDING_DOWNTIME_SECONDS must not count toward the delay.
        saved = T0 - timedelta(seconds=MAX_PENDING_DOWNTIME_SECONDS + 5)
        raw = {
            "active": False,
            "pending_since": (saved - timedelta(seconds=30)).isoformat(),
            "saved_at": saved.isoformat(),
        }
        restored = SolarActivationTracker.restore(raw, T0)
        assert restored.serialize()["pending_since"] is None

    def test_future_saved_at_drops_pending(self):
        raw = {
            "active": False,
            "pending_since": (T0 - timedelta(seconds=30)).isoformat(),
            "saved_at": (T0 + timedelta(hours=1)).isoformat(),
        }
        restored = SolarActivationTracker.restore(raw, T0)
        assert restored.serialize()["pending_since"] is None

    def test_garbage_saved_at_drops_pending(self):
        raw = {
            "active": False,
            "pending_since": (T0 - timedelta(seconds=30)).isoformat(),
            "saved_at": "not-a-timestamp",
        }
        restored = SolarActivationTracker.restore(raw, T0)
        assert restored.serialize()["pending_since"] is None

    def test_serialize_none_pending_round_trips(self):
        t = SolarActivationTracker()
        assert t.serialize() == {"active": False, "pending_since": None}
        restored = SolarActivationTracker.restore(t.serialize_stored(T0), T0)
        assert restored.serialize() == {"active": False, "pending_since": None}


# ---------------------------------------------------------------------------
# ChargerController -- Mode arbitration
# ---------------------------------------------------------------------------


class TestModeArbitration:
    def test_no_car_present_stays_idle_even_when_forced(self):
        inputs = _inputs(cars=(), force_charging=True)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "idle"

    def test_forced_wins_when_car_present_but_not_scheduled(self):
        car = _car(active_slot=False, home_and_plugged=True)
        inputs = _inputs(cars=(car,), force_charging=True)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "forced"

    def test_scheduled_when_car_demands_and_not_forced(self):
        car = _car(active_slot=True, home_and_plugged=True)
        inputs = _inputs(cars=(car,), force_charging=False)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "scheduled"

    def test_forced_beats_scheduled(self):
        car = _car(active_slot=True, home_and_plugged=True)
        inputs = _inputs(cars=(car,), force_charging=True)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "forced"

    def test_scheduled_beats_solar(self):
        """Even once solar has activated, a scheduled slot takes priority."""
        car = _car(active_slot=True, home_and_plugged=True)
        controller = ChargerController()
        inputs = _inputs(
            cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0,
            now=T0 + timedelta(seconds=301),
        )
        controller._solar_tracker._active = True  # simulate already-active solar
        decision = controller.decide(inputs)
        assert decision.mode == "scheduled"

    def test_solar_when_nothing_else_authorizes(self):
        car = _car(active_slot=False, home_and_plugged=True)
        controller = ChargerController()
        controller._solar_tracker._active = True
        inputs = _inputs(
            cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0,
        )
        decision = controller.decide(inputs)
        assert decision.mode == "solar"

    def test_solar_skips_car_at_or_above_solar_target(self):
        """SOC 90 already >= a solar_target of 80 -- solar-satisfied, no session."""
        car = _car(active_slot=False, home_and_plugged=True, soc_pct=90.0, solar_target_soc_pct=80.0)
        controller = ChargerController()
        controller._solar_tracker._active = True
        inputs = _inputs(cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0)
        decision = controller.decide(inputs)
        assert decision.mode == "idle"

    def test_solar_enters_when_soc_unknown(self):
        """Unknown SOC fails open -- solar mode still authorizes charging."""
        car = _car(active_slot=False, home_and_plugged=True, soc_pct=None, solar_target_soc_pct=50.0)
        controller = ChargerController()
        controller._solar_tracker._active = True
        inputs = _inputs(cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0)
        decision = controller.decide(inputs)
        assert decision.mode == "solar"

    def test_solar_selects_second_car_when_first_is_solar_satisfied(self):
        """car_a is solar-satisfied (skipped); car_b is below its solar target."""
        car_a = _car(active_slot=False, home_and_plugged=True, soc_pct=85.0, solar_target_soc_pct=80.0)
        car_b = _car(
            active_slot=False, home_and_plugged=True, max_charge_kw=3.0,
            soc_pct=50.0, solar_target_soc_pct=100.0,
        )
        controller = ChargerController()
        controller._solar_tracker._active = True
        inputs = _inputs(cars=(car_a, car_b), solar_surplus_kw=5.0, battery_soc_pct=100.0)
        decision = controller.decide(inputs)
        assert decision.mode == "solar"
        # car_b's 3.0kW cap (4.35A) below min_amps proves car_b, not car_a, was selected.
        assert decision.target_amps == pytest.approx(4.35)
        assert decision.commands == ()

    def test_idle_when_nothing_authorizes(self):
        car = _car(active_slot=False, home_and_plugged=True)
        inputs = _inputs(cars=(car,), solar_surplus_kw=0.0)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "idle"

    def test_car_not_home_and_plugged_never_authorizes(self):
        """active_slot True but not home_and_plugged -- treated as absent."""
        car = _car(active_slot=True, home_and_plugged=False)
        inputs = _inputs(cars=(car,), force_charging=False)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "idle"


class TestMultipleCarsSelection:
    def test_multiple_demanding_cars_first_wins_with_note(self):
        car_a = _car(active_slot=True, home_and_plugged=True, max_charge_kw=22.0)
        car_b = _car(active_slot=True, home_and_plugged=True, max_charge_kw=3.0)
        inputs = _inputs(cars=(car_a, car_b))
        decision = ChargerController().decide(inputs)
        assert decision.mode == "scheduled"
        assert decision.override_reason == "multiple_cars_demanding_first_selected"

    def test_single_demanding_car_no_note(self):
        car = _car(active_slot=True, home_and_plugged=True)
        inputs = _inputs(cars=(car,))
        decision = ChargerController().decide(inputs)
        assert decision.override_reason is None


# ---------------------------------------------------------------------------
# Grid amp target via the full pipeline
# ---------------------------------------------------------------------------


class TestGridAmpTargetIntegration:
    def test_forced_mode_ample_headroom_caps_at_max_amps(self):
        inputs = _inputs(force_charging=True)
        decision = ChargerController().decide(inputs)
        assert decision.target_amps == 16.0

    def test_forced_mode_limited_headroom(self):
        inputs = _inputs(
            force_charging=True,
            fuse_rating_amps=20.0,
            measured_worst_case_signed_amps=8.0,
            current_dynamic_limit_amps=0.0,
        )
        # fuse_available = 20-2-8+0 = 10
        decision = ChargerController().decide(inputs)
        assert decision.target_amps == pytest.approx(10.0)

    def test_car_max_charge_kw_caps_below_fuse_and_grid(self):
        car = _car(active_slot=True, home_and_plugged=True, max_charge_kw=3.0)
        inputs = _inputs(cars=(car,))
        # car cap = 3.0 * 1.45 = 4.35A -- below min_amps, so pre-start gate
        # blocks starting (informational target_amps reported, no commands).
        decision = ChargerController().decide(inputs)
        assert decision.target_amps == pytest.approx(4.35)
        assert decision.commands == ()


# ---------------------------------------------------------------------------
# Phase-capability conversion, integration
# ---------------------------------------------------------------------------


class TestPhaseCapabilityIntegration:
    def test_one_phase_car_uses_1phase_factor(self):
        car = _car(active_slot=True, home_and_plugged=True, phase_capability=1, max_charge_kw=22.0)
        inputs = _inputs(cars=(car,), current_phase_mode="single")
        decision = ChargerController().decide(inputs)
        # grid ceiling = 11.5*4.3=49.45, fuse_available=18 -- fuse binds.
        assert decision.target_amps == pytest.approx(16.0)  # still capped at max_amps
        assert decision.target_phase_mode == "single"

    def test_two_phase_car_uses_2phase_factor_for_grid_ceiling(self):
        car = _car(active_slot=True, home_and_plugged=True, phase_capability=2, max_charge_kw=1.0)
        inputs = _inputs(cars=(car,))
        # car cap = 1.0 * 2.5 = 2.5A -- below min_amps.
        decision = ChargerController().decide(inputs)
        assert decision.target_amps == pytest.approx(2.5)

    def test_three_phase_car_uses_3phase_factor_for_grid_ceiling(self):
        car = _car(active_slot=True, home_and_plugged=True, phase_capability=3, max_charge_kw=1.0)
        inputs = _inputs(cars=(car,))
        # car cap = 1.0 * 1.45 = 1.45A -- below min_amps.
        decision = ChargerController().decide(inputs)
        assert decision.target_amps == pytest.approx(1.45)


# ---------------------------------------------------------------------------
# Solar branch integration (SOC gate + activation/deactivation delays)
# ---------------------------------------------------------------------------


class TestSolarBranchIntegration:
    def test_soc_gate_blocks_solar_even_with_ample_surplus(self):
        car = _car(active_slot=False, home_and_plugged=True)
        inputs = _inputs(cars=(car,), solar_surplus_kw=10.0, battery_soc_pct=50.0)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "idle"

    def test_surplus_below_threshold_blocks_solar(self):
        car = _car(active_slot=False, home_and_plugged=True)
        inputs = _inputs(cars=(car,), solar_surplus_kw=1.5, battery_soc_pct=100.0)
        # net = 1.5 - 0.5 = 1.0 < 1.5 threshold.
        decision = ChargerController().decide(inputs)
        assert decision.mode == "idle"

    def test_solar_activates_after_full_activation_delay(self):
        car = _car(active_slot=False, home_and_plugged=True)
        controller = ChargerController()
        inputs_t0 = _inputs(cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0, now=T0)
        decision_t0 = controller.decide(inputs_t0)
        assert decision_t0.mode == "idle"  # pending, not yet active

        inputs_t300 = _inputs(
            cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0,
            now=T0 + timedelta(seconds=300),
        )
        decision_t300 = controller.decide(inputs_t300)
        assert decision_t300.mode == "solar"

    def test_solar_not_yet_active_just_under_activation_delay(self):
        car = _car(active_slot=False, home_and_plugged=True)
        controller = ChargerController()
        controller.decide(_inputs(cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0, now=T0))
        decision = controller.decide(
            _inputs(
                cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0,
                now=T0 + timedelta(seconds=299.9),
            )
        )
        assert decision.mode == "idle"

    def test_solar_deactivates_after_full_deactivation_delay(self):
        car = _car(active_slot=False, home_and_plugged=True)
        controller = ChargerController()
        controller.decide(_inputs(cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0, now=T0))
        controller.decide(
            _inputs(
                cars=(car,), solar_surplus_kw=5.0, battery_soc_pct=100.0,
                now=T0 + timedelta(seconds=300),
            )
        )
        # Surplus drops -- pending_off starts.
        controller.decide(
            _inputs(
                cars=(car,), solar_surplus_kw=0.0, battery_soc_pct=100.0,
                now=T0 + timedelta(seconds=310),
            )
        )
        decision = controller.decide(
            _inputs(
                cars=(car,), solar_surplus_kw=0.0, battery_soc_pct=100.0,
                now=T0 + timedelta(seconds=371),
            )
        )
        assert decision.mode == "idle"

    def test_solar_amp_target_floors(self):
        """net=4.0kW * 1.45 = 5.8 => floor to 5 -- below min_amps=6, so the
        pre-start gate blocks starting (no commands, informational target)."""
        car = _car(active_slot=False, home_and_plugged=True)
        controller = ChargerController()
        controller._solar_tracker._active = True
        inputs = _inputs(cars=(car,), solar_surplus_kw=4.5, battery_soc_pct=100.0)
        # net = 4.5 - 0.5 = 4.0
        decision = controller.decide(inputs)
        assert decision.mode == "solar"
        assert decision.target_amps == pytest.approx(5.0)
        assert decision.commands == ()

    def test_solar_amp_target_floors_and_starts_when_above_min(self):
        """net=5.0kW * 1.45 = 7.25 => floor to 7 -- starts."""
        car = _car(active_slot=False, home_and_plugged=True)
        controller = ChargerController()
        controller._solar_tracker._active = True
        inputs = _inputs(cars=(car,), solar_surplus_kw=5.5, battery_soc_pct=100.0)
        decision = controller.decide(inputs)
        assert decision.mode == "solar"
        assert decision.target_amps == pytest.approx(7.0)
        assert any(c.action == "start" for c in decision.commands)
        assert any(c.action == "set_dynamic_limit" and c.value == 7.0 for c in decision.commands)


class TestSolarDipRideThrough:
    """A momentary surplus collapse while solar charging must not hard-pause.

    Fuse headroom is still fine during a cloud dip -- only the solar term
    collapsed -- so the charger rides through at min_amps and the solar
    latch's deactivation delay decides when the session actually ends
    (2026-08-08 incident: PV 8.7->2.0 kW for ~60s produced a hard pause plus
    a safety notification, twice in 14 seconds).
    """

    def _drawing_solar_inputs(self, **overrides):
        """Charging in solar mode with ample fuse headroom; surplus collapsed."""
        defaults = {
            "charger_status": "charging",
            "charger_power_kw": 6.5,
            "cars": (_car(active_slot=False, home_and_plugged=True),),
            # net = 1.0 - 0.5 = 0.5 kW => solar_raw floors to 0A => raw <= 0
            "solar_surplus_kw": 1.0,
            "battery_soc_pct": 100.0,
        }
        defaults.update(overrides)
        return _inputs(**defaults)

    def test_surplus_collapse_while_drawing_rides_at_min_amps(self):
        controller = ChargerController()
        controller._solar_tracker._active = True
        decision = controller.decide(self._drawing_solar_inputs())
        assert decision.mode == "solar"
        assert decision.notifications == ()
        assert all(c.action != "pause" for c in decision.commands)
        assert decision.target_amps == pytest.approx(6.0)
        assert any(
            c.action == "set_dynamic_limit" and c.value == 6.0
            for c in decision.commands
        )
        assert decision.override_reason == "solar_dip_ride_through"

    def test_fuse_capacity_collapse_still_pauses_with_notification(self):
        """Capacity-driven raw<=0 keeps the instant safety stop even in solar
        mode: worst=19A => fuse available = 20-2-19 = -1A => capacity 0."""
        controller = ChargerController()
        controller._solar_tracker._active = True
        decision = controller.decide(
            self._drawing_solar_inputs(
                solar_surplus_kw=5.5,
                measured_worst_case_signed_amps=19.0,
            )
        )
        assert any(c.action == "pause" for c in decision.commands)
        assert decision.notifications != ()
        assert decision.target_amps == 0.0

    def test_dip_longer_than_deactivation_delay_ends_session(self):
        """The latch bounds the ride-through: after solar_deactivation_delay_s
        of collapsed surplus the mode drops to idle and the running draw is
        suppressed (stop + 0A limit) -- no indefinite min-amp grid charging."""
        controller = ChargerController()
        controller._solar_tracker._active = True
        first = controller.decide(self._drawing_solar_inputs(now=T0))
        assert first.mode == "solar"
        later = controller.decide(
            self._drawing_solar_inputs(now=T0 + timedelta(seconds=61))
        )
        assert later.mode == "idle"
        assert any(c.action == "stop" for c in later.commands)
        assert later.target_amps == 0.0


# ---------------------------------------------------------------------------
# Fuse Layer 1: emergency overload pause
# ---------------------------------------------------------------------------


class TestFuseLayer1Emergency:
    def test_triggers_at_exact_fuse_plus_margin_boundary(self):
        inputs = _inputs(
            charger_status="charging",
            charger_power_kw=3.5,
            measured_worst_case_signed_amps=22.0,  # fuse(20) + margin(2)
            force_charging=True,
        )
        decision = ChargerController().decide(inputs)
        assert decision.override_reason == "emergency_fuse_overload"
        assert decision.commands == (decision.commands[0],)  # single command
        assert decision.commands[0].action == "pause"
        # The pause is instant; the alert is not -- a single-tick overload
        # is a spike the pause clears, not something to wake a human for.
        assert decision.notifications == ()
        assert decision.critical_notifications == ()

    def test_does_not_trigger_just_below_boundary(self):
        inputs = _inputs(
            charger_status="charging",
            charger_power_kw=3.5,
            measured_worst_case_signed_amps=21.9,
            force_charging=True,
        )
        decision = ChargerController().decide(inputs)
        assert decision.override_reason != "emergency_fuse_overload"

    def test_does_not_trigger_when_not_drawing(self):
        """High measured current but the charger isn't actually charging."""
        inputs = _inputs(
            charger_status="awaiting_start",
            charger_power_kw=0.0,
            measured_worst_case_signed_amps=50.0,
            force_charging=True,
        )
        decision = ChargerController().decide(inputs)
        assert decision.override_reason != "emergency_fuse_overload"

    def test_triggers_regardless_of_mode(self):
        """Emergency overrides even forced-mode charging."""
        car = _car(active_slot=True, home_and_plugged=True)
        inputs = _inputs(
            cars=(car,),
            charger_status="charging",
            charger_power_kw=3.5,
            measured_worst_case_signed_amps=25.0,
            force_charging=True,
        )
        decision = ChargerController().decide(inputs)
        assert decision.mode == "forced"
        assert decision.override_reason == "emergency_fuse_overload"

    def test_triggers_via_power_cross_check_even_if_status_disagrees(self):
        """Status says not-charging but power > 0.5kW -- still counts as
        drawing, so emergency still applies."""
        inputs = _inputs(
            charger_status="paused",
            charger_power_kw=3.5,
            measured_worst_case_signed_amps=25.0,
            force_charging=True,
        )
        decision = ChargerController().decide(inputs)
        assert decision.override_reason == "emergency_fuse_overload"


# ---------------------------------------------------------------------------
# Persistent-overload critical alert
# ---------------------------------------------------------------------------


class TestPersistentOverloadAlert:
    """Alerting on an overload that load balancing could not clear."""

    @staticmethod
    def _overloaded(now, **overrides):
        defaults = {
            "charger_status": "charging",
            "charger_power_kw": 3.5,
            "measured_worst_case_signed_amps": 25.0,  # >= fuse(20)+margin(2)
            "force_charging": True,
            "now": now,
        }
        defaults.update(overrides)
        return _inputs(**defaults)

    def test_transient_overload_never_alerts(self):
        controller = ChargerController()
        first = controller.decide(self._overloaded(T0))
        cleared = controller.decide(
            self._overloaded(
                T0 + timedelta(seconds=30), measured_worst_case_signed_amps=15.0
            )
        )
        assert first.critical_notifications == ()
        assert cleared.critical_notifications == ()

    def test_alerts_once_after_the_delay(self):
        controller = ChargerController()
        controller.decide(self._overloaded(T0))
        before = controller.decide(self._overloaded(T0 + timedelta(seconds=119)))
        alerting = controller.decide(self._overloaded(T0 + timedelta(seconds=120)))
        after = controller.decide(self._overloaded(T0 + timedelta(seconds=150)))
        assert before.critical_notifications == ()
        assert len(alerting.critical_notifications) == 1
        assert "25.0 A" in alerting.critical_notifications[0]
        # Once per episode -- no re-alert every tick while it persists.
        assert after.critical_notifications == ()

    def test_alerts_again_after_the_overload_clears_and_returns(self):
        controller = ChargerController()
        controller.decide(self._overloaded(T0))
        assert controller.decide(
            self._overloaded(T0 + timedelta(seconds=120))
        ).critical_notifications
        controller.decide(
            self._overloaded(
                T0 + timedelta(seconds=150), measured_worst_case_signed_amps=10.0
            )
        )
        controller.decide(self._overloaded(T0 + timedelta(seconds=180)))
        second = controller.decide(self._overloaded(T0 + timedelta(seconds=300)))
        assert len(second.critical_notifications) == 1

    def test_alerts_while_the_charger_is_already_paused(self):
        """The case Fuse Layer 1's is_drawing gate can never see: the charger
        is off and the house load alone is over the fuse rating."""
        controller = ChargerController()
        idle = {"charger_status": "paused", "charger_power_kw": 0.0}
        first = controller.decide(self._overloaded(T0, **idle))
        alerting = controller.decide(
            self._overloaded(T0 + timedelta(seconds=120), **idle)
        )
        assert first.override_reason != "emergency_fuse_overload"
        assert first.critical_notifications == ()
        assert len(alerting.critical_notifications) == 1

    def test_alerts_even_when_the_car_is_disconnected(self):
        """Terminal status returns early from _decide() -- the alert is
        attached outside it, so it still fires."""
        controller = ChargerController()
        terminal = {"charger_status": "disconnected", "charger_power_kw": 0.0}
        controller.decide(self._overloaded(T0, **terminal))
        alerting = controller.decide(
            self._overloaded(T0 + timedelta(seconds=120), **terminal)
        )
        assert len(alerting.critical_notifications) == 1

# ---------------------------------------------------------------------------
# Fuse Layer 3: 0A-target safety stop + pre-start gate
# ---------------------------------------------------------------------------


class TestFuseLayer3AndPreStartGate:
    def test_capacity_zero_while_charging_pauses_and_notifies(self):
        inputs = _inputs(
            force_charging=True,
            charger_status="charging",
            charger_power_kw=3.5,
            # fuse_available = 20-2-19+0 = -1 -> capacity 0. Kept below the
            # fuse+margin=22 emergency threshold so this exercises Fuse
            # Layer 3, not Fuse Layer 1.
            measured_worst_case_signed_amps=19.0,
        )
        decision = ChargerController().decide(inputs)
        assert decision.target_amps == 0.0
        actions = {c.action for c in decision.commands}
        assert actions == {"set_dynamic_limit", "pause"}
        assert len(decision.notifications) == 1

    def test_capacity_zero_while_not_charging_sets_zero_proactively_no_notify(self):
        inputs = _inputs(
            force_charging=True,
            charger_status="awaiting_start",
            charger_power_kw=0.0,
            measured_worst_case_signed_amps=100.0,
        )
        decision = ChargerController().decide(inputs)
        assert decision.commands == (decision.commands[0],)
        assert decision.commands[0].action == "set_dynamic_limit"
        assert decision.commands[0].value == 0.0
        assert decision.notifications == ()

    def test_capacity_in_dead_zone_does_not_start(self):
        """0 < capacity < 6A -- do not start."""
        inputs = _inputs(
            force_charging=True,
            charger_status="awaiting_start",
            charger_power_kw=0.0,
            fuse_rating_amps=20.0,
            measured_worst_case_signed_amps=15.0,  # fuse_available=20-2-15+0=3
        )
        decision = ChargerController().decide(inputs)
        assert decision.commands == ()
        assert decision.target_amps == pytest.approx(3.0)

    def test_capacity_in_dead_zone_does_not_stop_already_charging(self):
        """Avoids a start/stop churn loop -- an already-running session is
        held, not stopped, while capacity is in the (0,6) dead zone."""
        inputs = _inputs(
            force_charging=True,
            charger_status="charging",
            charger_power_kw=3.5,
            fuse_rating_amps=20.0,
            # fuse_available = 20-2-18+2 = 2 -- dead zone, and worst(18) stays
            # below the fuse+margin=22 emergency threshold.
            measured_worst_case_signed_amps=18.0,
            current_dynamic_limit_amps=2.0,
        )
        decision = ChargerController().decide(inputs)
        assert decision.commands == ()

    def test_capacity_at_exact_min_amps_boundary_starts(self):
        inputs = _inputs(
            force_charging=True,
            charger_status="awaiting_start",
            charger_power_kw=0.0,
            fuse_rating_amps=20.0,
            safety_buffer_amps=0.0,
            measured_worst_case_signed_amps=14.0,  # fuse_available=20-0-14+0=6
        )
        decision = ChargerController().decide(inputs)
        assert decision.target_amps == pytest.approx(6.0)
        assert any(c.action == "start" for c in decision.commands)


# ---------------------------------------------------------------------------
# Unauthorized-charge suppression
# ---------------------------------------------------------------------------


class TestUnauthorizedSuppression:
    def test_charging_status_with_no_authorized_mode_is_stopped(self):
        """Suppression sends both the 0 A dynamic limit and "stop" -- "stop"
        alone only deauthorizes, which an auth-free charger ignores
        (2026-08-07 incident); the 0 A limit halts the draw regardless."""
        inputs = _inputs(cars=(), charger_status="charging", charger_power_kw=3.5)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "idle"
        assert decision.commands == (
            ChargerCommand("set_dynamic_limit", 0.0),
            ChargerCommand("stop"),
        )
        assert decision.override_reason == "unauthorized_charge_suppressed"

    def test_power_cross_check_triggers_even_if_status_says_paused(self):
        inputs = _inputs(cars=(), charger_status="paused", charger_power_kw=1.0)
        decision = ChargerController().decide(inputs)
        assert [c.action for c in decision.commands] == ["set_dynamic_limit", "stop"]
        assert decision.commands[0].value == 0.0

    def test_no_suppression_when_not_drawing(self):
        inputs = _inputs(cars=(), charger_status="awaiting_start", charger_power_kw=0.0)
        decision = ChargerController().decide(inputs)
        assert decision.commands == ()
        assert decision.override_reason is None

    def test_suppressed_zero_limit_restored_by_later_scheduled_slot(self):
        """The suppression 0 A must not fight a later authorized session --
        the managed path writes the real target amps over the 0."""
        controller = ChargerController()
        suppressed = controller.decide(
            _inputs(cars=(), charger_status="charging", charger_power_kw=3.5, now=T0)
        )
        assert ChargerCommand("set_dynamic_limit", 0.0) in suppressed.commands
        # The 0 A landed and the draw stopped; a scheduled slot then starts
        # a real session -- the limit goes back up immediately (16A differs
        # from the 0.0 belief, so no heartbeat wait).
        car = _car(active_slot=True, home_and_plugged=True)
        scheduled = controller.decide(
            _inputs(
                cars=(car,),
                charger_status="awaiting_start",
                charger_power_kw=0.0,
                now=T0 + timedelta(seconds=30),
            )
        )
        assert scheduled.mode == "scheduled"
        assert [c.action for c in scheduled.commands] == ["start", "set_dynamic_limit"]
        assert scheduled.commands[1].value == 16.0

    def test_suppression_zero_limit_reasserted_on_heartbeat(self):
        """A suppression episode outliving the heartbeat interval re-writes
        the 0 A -- a limit the charger silently dropped is re-asserted."""
        controller = ChargerController()
        drawing = {"cars": (), "charger_status": "charging", "charger_power_kw": 3.5}
        first = controller.decide(_inputs(now=T0, **drawing))
        assert ChargerCommand("set_dynamic_limit", 0.0) in first.commands
        mid = controller.decide(_inputs(now=T0 + timedelta(seconds=300), **drawing))
        assert [c.action for c in mid.commands] == ["stop"]
        heartbeat = controller.decide(_inputs(now=T0 + timedelta(seconds=600), **drawing))
        assert heartbeat.commands == (
            ChargerCommand("set_dynamic_limit", 0.0),
            ChargerCommand("stop"),
        )


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------


class TestTerminalStates:
    @pytest.mark.parametrize("status", ["disconnected", "completed", "error"])
    def test_terminal_status_reports_idle_no_commands(self, status):
        inputs = _inputs(charger_status=status, force_charging=True)
        decision = ChargerController().decide(inputs)
        assert decision.mode == "idle"
        assert decision.commands == ()
        assert decision.notifications == ()
        assert decision.override_reason == f"terminal_{status}"

    def test_terminal_status_resets_amp_hysteresis(self):
        """A fresh session after a terminal state must not inherit a stale
        hysteresis-applied value from the previous session."""
        controller = ChargerController()
        controller.decide(_inputs(force_charging=True))  # applies 16A immediately
        assert controller._amp_hysteresis.applied == 16.0
        controller.decide(_inputs(charger_status="disconnected"))
        assert controller._amp_hysteresis.applied is None

    def test_terminal_status_aborts_in_flight_sequence(self):
        car = _car(active_slot=True, home_and_plugged=True)
        controller = ChargerController()
        inputs = _inputs(
            cars=(car,),
            charger_status="charging",
            charger_power_kw=3.5,
            current_phase_mode="single",
            current_dynamic_limit_amps=16.0,
        )
        started = controller.decide(inputs)
        assert started.sequence_state == "pausing"
        decision = controller.decide(_inputs(charger_status="disconnected"))
        assert decision.mode == "idle"
        assert decision.commands == ()
        assert controller.sequence_state == "idle"


# ---------------------------------------------------------------------------
# Terminal-status physics defence: power draw beats a lying status entity
# ---------------------------------------------------------------------------


class TestTerminalStatusPhysicsDefence:
    """A terminal status while the car draws power must NOT reset -- the
    status entity is lying and supervision (incl. fuse Layer 1) must
    continue until the draw actually stops."""

    @pytest.mark.parametrize("status", ["disconnected", "completed", "error"])
    def test_terminal_status_while_drawing_keeps_supervising(self, status):
        decision = ChargerController().decide(
            _inputs(charger_status=status, charger_power_kw=6.0)
        )
        assert decision.mode == "scheduled"
        assert decision.override_reason != f"terminal_{status}"
        # Supervision is live: the dynamic limit is still being managed.
        assert [c.action for c in decision.commands] == ["set_dynamic_limit"]

    def test_no_spurious_start_commands_during_lie(self):
        decision = ChargerController().decide(
            _inputs(charger_status="disconnected", charger_power_kw=6.0)
        )
        actions = [c.action for c in decision.commands]
        assert "start" not in actions
        assert "resume" not in actions

    def test_disconnected_not_drawing_resets_unchanged(self):
        decision = ChargerController().decide(
            _inputs(charger_status="disconnected", charger_power_kw=0.0)
        )
        assert decision.mode == "idle"
        assert decision.commands == ()
        assert decision.override_reason == "terminal_disconnected"

    def test_standby_draw_below_threshold_still_resets(self):
        """0.3 kW is below POWER_ACTIVE_THRESHOLD_KW (0.5) -- not drawing,
        so the terminal reset fires as before."""
        decision = ChargerController().decide(
            _inputs(charger_status="disconnected", charger_power_kw=0.3)
        )
        assert decision.mode == "idle"
        assert decision.override_reason == "terminal_disconnected"

    def test_fuse_layer1_reachable_during_status_lie(self):
        """Emergency overload pause must still fire while the status entity
        claims disconnected -- the whole point of the defence."""
        decision = ChargerController().decide(
            _inputs(
                charger_status="disconnected",
                charger_power_kw=6.0,
                measured_worst_case_signed_amps=25.0,  # >= 20A fuse + 2A margin
            )
        )
        assert decision.override_reason == "emergency_fuse_overload"
        assert [c.action for c in decision.commands] == ["pause"]

    def test_one_tick_delayed_reset_after_real_unplug(self):
        """During the lie the controller keeps supervising; the reset fires
        one tick later, once the power draw actually stops."""
        controller = ChargerController()
        controller.decide(_inputs(charger_status="charging", charger_power_kw=6.0))
        assert controller._amp_hysteresis.applied == 16.0
        lying = controller.decide(
            _inputs(charger_status="disconnected", charger_power_kw=6.0)
        )
        assert lying.override_reason != "terminal_disconnected"
        assert controller._amp_hysteresis.applied == 16.0
        unplugged = controller.decide(
            _inputs(charger_status="disconnected", charger_power_kw=0.0)
        )
        assert unplugged.override_reason == "terminal_disconnected"
        assert unplugged.mode == "idle"
        assert unplugged.commands == ()
        assert controller._amp_hysteresis.applied is None


# ---------------------------------------------------------------------------
# Phase-switch sequence: happy path (single -> three)
# ---------------------------------------------------------------------------


class TestPhaseSwitchSequenceHappyPath:
    """Full walk-through of PAUSING -> SET_PHASE -> RESUMING -> SET_LIMIT.

    Setup: currently charging single-phase at 16A; headroom is ample enough
    (capacity ~16.675A, three_phase_kw ~11.5 >= 4.1 threshold) that the
    desired phase mode is "three".
    """

    def _base(self, **overrides):
        car = _car(active_slot=True, home_and_plugged=True)
        defaults = {
            "cars": (car,),
            "charger_status": "charging",
            "charger_power_kw": 3.5,
            "current_phase_mode": "single",
            "current_dynamic_limit_amps": 16.0,
            "measured_worst_case_signed_amps": 0.0,
        }
        defaults.update(overrides)
        return _inputs(**defaults)

    def test_full_sequence(self):
        controller = ChargerController()

        # Tick 1: still charging -- desired phase differs -- start PAUSING.
        d1 = controller.decide(self._base(now=T0))
        assert d1.sequence_state == "pausing"
        assert d1.commands == (d1.commands[0],)
        assert d1.commands[0].action == "pause"
        assert d1.target_phase_mode == "three"

        # Tick 2: pause not yet confirmed (still charging) -- no new command.
        d2 = controller.decide(
            self._base(now=T0 + timedelta(seconds=5), charger_status="charging", charger_power_kw=3.5)
        )
        assert d2.sequence_state == "pausing"
        assert d2.commands == ()

        # Tick 3: pause confirmed -- issue set_phase_mode.
        d3 = controller.decide(
            self._base(now=T0 + timedelta(seconds=8), charger_status="paused", charger_power_kw=0.0)
        )
        assert d3.sequence_state == "set_phase"
        assert d3.commands == (d3.commands[0],)
        assert d3.commands[0].action == "set_phase_mode"
        assert d3.commands[0].value == "three"

        # Tick 4: phase mode not yet reflected -- wait.
        d4 = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=10), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="single",
            )
        )
        assert d4.sequence_state == "set_phase"
        assert d4.commands == ()

        # Tick 5: phase mode confirmed -- fuse re-verified sufficient -- resume.
        d5 = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=12), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="three",
            )
        )
        assert d5.sequence_state == "resuming"
        assert d5.commands == (d5.commands[0],)
        assert d5.commands[0].action == "resume"

        # Tick 6: resume not yet confirmed.
        d6 = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=14), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="three",
            )
        )
        assert d6.sequence_state == "resuming"
        assert d6.commands == ()

        # Tick 7: resumed -- fuse re-verified sufficient -- set new limit.
        d7 = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=16), charger_status="charging", charger_power_kw=3.6,
                current_phase_mode="three",
            )
        )
        assert d7.sequence_state == "set_limit"
        assert d7.commands == (d7.commands[0],)
        assert d7.commands[0].action == "set_dynamic_limit"
        assert d7.commands[0].value == 16.0
        assert controller.sequence_state == "idle"

        # Tick 8: back to normal operation, nothing more to do.
        d8 = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=18), charger_status="charging", charger_power_kw=3.6,
                current_phase_mode="three", current_dynamic_limit_amps=16.0,
            )
        )
        assert d8.sequence_state == "idle"
        assert d8.commands == ()


# ---------------------------------------------------------------------------
# Phase-switch sequence: timeouts
# ---------------------------------------------------------------------------


class TestPhaseSwitchSequenceTimeouts:
    def _base(self, **overrides):
        car = _car(active_slot=True, home_and_plugged=True)
        defaults = {
            "cars": (car,),
            "charger_status": "charging",
            "charger_power_kw": 3.5,
            "current_phase_mode": "single",
            "current_dynamic_limit_amps": 16.0,
            "measured_worst_case_signed_amps": 0.0,
        }
        defaults.update(overrides)
        return _inputs(**defaults)

    def test_pausing_timeout_aborts_with_stuck_flag(self):
        controller = ChargerController()
        controller.decide(self._base(now=T0))
        decision = controller.decide(
            self._base(now=T0 + timedelta(seconds=16), charger_status="charging", charger_power_kw=3.5)
        )
        assert decision.sequence_state == "idle"
        assert decision.stuck is True
        assert decision.override_reason == "phase_switch_pause_timeout"
        assert decision.commands[0].action == "pause"

    def test_pausing_not_yet_timed_out_at_boundary(self):
        controller = ChargerController()
        controller.decide(self._base(now=T0))
        decision = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=14.9), charger_status="charging", charger_power_kw=3.5
            )
        )
        assert decision.sequence_state == "pausing"
        assert decision.stuck is False

    def test_set_phase_timeout_aborts_with_stuck_flag(self):
        controller = ChargerController()
        controller.decide(self._base(now=T0))
        controller.decide(
            self._base(now=T0 + timedelta(seconds=5), charger_status="paused", charger_power_kw=0.0)
        )
        decision = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=21), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="single",
            )
        )
        assert decision.sequence_state == "idle"
        assert decision.stuck is True
        assert decision.override_reason == "phase_switch_set_phase_timeout"

    def test_resuming_timeout_aborts_with_stuck_flag(self):
        controller = ChargerController()
        controller.decide(self._base(now=T0))
        controller.decide(
            self._base(now=T0 + timedelta(seconds=5), charger_status="paused", charger_power_kw=0.0)
        )
        controller.decide(
            self._base(
                now=T0 + timedelta(seconds=6), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="three",
            )
        )
        decision = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=22), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="three",
            )
        )
        assert decision.sequence_state == "idle"
        assert decision.stuck is True
        assert decision.override_reason == "phase_switch_resume_timeout"


# ---------------------------------------------------------------------------
# Phase-switch sequence: fuse re-verification / headroom collapse mid-sequence
# ---------------------------------------------------------------------------


class TestPhaseSwitchSequenceInsufficientHeadroom:
    def _base(self, **overrides):
        car = _car(active_slot=True, home_and_plugged=True)
        defaults = {
            "cars": (car,),
            "charger_status": "charging",
            "charger_power_kw": 3.5,
            "current_phase_mode": "single",
            "current_dynamic_limit_amps": 16.0,
            "measured_worst_case_signed_amps": 0.0,
        }
        defaults.update(overrides)
        return _inputs(**defaults)

    def test_insufficient_headroom_before_resume_aborts_no_stuck(self):
        controller = ChargerController()
        controller.decide(self._base(now=T0))
        controller.decide(
            self._base(now=T0 + timedelta(seconds=5), charger_status="paused", charger_power_kw=0.0)
        )
        # Household load spikes right as the phase mode confirms -- headroom
        # collapses below min_amps before we'd resume.
        decision = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=6), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="three", measured_worst_case_signed_amps=30.0,
            )
        )
        assert decision.sequence_state == "idle"
        assert decision.stuck is False
        assert decision.override_reason == "phase_switch_insufficient_before_resume"
        assert decision.commands == (decision.commands[0],)
        assert decision.commands[0].action == "pause"

    def test_insufficient_headroom_before_set_limit_aborts_no_stuck(self):
        controller = ChargerController()
        controller.decide(self._base(now=T0))
        controller.decide(
            self._base(now=T0 + timedelta(seconds=5), charger_status="paused", charger_power_kw=0.0)
        )
        controller.decide(
            self._base(
                now=T0 + timedelta(seconds=6), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="three",
            )
        )
        # Resumed successfully, but headroom collapses right as we'd set
        # the new limit -- worst=15 (below the fuse+margin=22 emergency
        # threshold) with current_dynamic_limit_amps reset to 0 (the
        # previous limit no longer applies post-switch) collapses capacity
        # to 20-2-15+0=3, below min_amps, without tripping Fuse Layer 1.
        decision = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=8), charger_status="charging", charger_power_kw=3.6,
                current_phase_mode="three", measured_worst_case_signed_amps=15.0,
                current_dynamic_limit_amps=0.0,
            )
        )
        assert decision.sequence_state == "idle"
        assert decision.stuck is False
        assert decision.override_reason == "phase_switch_insufficient_before_limit"
        assert decision.commands[0].action == "pause"


# ---------------------------------------------------------------------------
# Phase-switch sequence completing in solar mode -- final target must be
# bounded by the mode-gated (solar) amps, not the full fuse/grid capacity.
# ---------------------------------------------------------------------------


class TestPhaseSwitchSequenceSolarModeBounding:
    """BUG: `raw = min(capacity, solar_raw, max_amps)` is computed for solar
    mode, but _continue_sequence's "resuming" branch derived final_target
    from bare `capacity` -- so completing a phase switch during solar mode
    set an amp limit sized to full fuse/grid capacity instead of the solar
    surplus. FIX: thread the mode-gated `raw` into _continue_sequence and
    use it for the final target in "resuming"."""

    def _base(self, **overrides):
        car = _car(active_slot=False, home_and_plugged=True, phase_capability=3, max_charge_kw=22.0)
        defaults = {
            "cars": (car,),
            "charger_status": "charging",
            "charger_power_kw": 3.5,
            "current_phase_mode": "single",
            "current_dynamic_limit_amps": 16.0,
            "measured_worst_case_signed_amps": 0.0,
            "solar_surplus_kw": 5.5,
            "battery_soc_pct": 100.0,
        }
        defaults.update(overrides)
        return _inputs(**defaults)

    def test_final_target_bounded_by_solar_amps_not_fuse_capacity(self):
        """capacity (fuse/grid) is ~16.675A, well above max_amps=16A; solar
        surplus (net=5.0kW * 1.45 A/kW = 7.25 -> floor 7A) is far lower.
        Once the phase-switch sequence completes in solar mode, the final
        set_dynamic_limit must be bounded by the 7A solar amps -- not 16A
        (the fuse/grid capacity that would apply in grid/forced/scheduled
        mode)."""
        controller = ChargerController()
        controller._solar_tracker._active = True

        d1 = controller.decide(self._base(now=T0))
        assert d1.mode == "solar"
        assert d1.sequence_state == "pausing"

        d2 = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=8), charger_status="paused", charger_power_kw=0.0
            )
        )
        assert d2.sequence_state == "set_phase"

        d3 = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=12), charger_status="paused", charger_power_kw=0.0,
                current_phase_mode="three",
            )
        )
        assert d3.sequence_state == "resuming"

        d4 = controller.decide(
            self._base(
                now=T0 + timedelta(seconds=16), charger_status="charging", charger_power_kw=3.6,
                current_phase_mode="three",
            )
        )
        assert d4.sequence_state == "set_limit"
        assert d4.mode == "solar"
        assert d4.commands == (d4.commands[0],)
        assert d4.commands[0].action == "set_dynamic_limit"
        assert d4.commands[0].value == pytest.approx(7.0)
        assert d4.target_amps == pytest.approx(7.0)

    def test_grid_mode_sequence_completion_still_uses_full_capacity(self):
        """Sanity check: in non-solar (scheduled) mode, raw == capacity
        (mode-gating is a no-op), so the final target is unaffected by the
        fix and still uses the full fuse/grid capacity."""
        car = _car(active_slot=True, home_and_plugged=True, phase_capability=3, max_charge_kw=22.0)
        controller = ChargerController()
        defaults = {
            "cars": (car,),
            "charger_status": "charging",
            "charger_power_kw": 3.5,
            "current_phase_mode": "single",
            "current_dynamic_limit_amps": 16.0,
            "measured_worst_case_signed_amps": 0.0,
        }

        d1 = controller.decide(_inputs(**defaults, now=T0))
        assert d1.mode == "scheduled"
        assert d1.sequence_state == "pausing"

        controller.decide(
            _inputs(
                **{**defaults, "charger_status": "paused", "charger_power_kw": 0.0},
                now=T0 + timedelta(seconds=8),
            )
        )
        controller.decide(
            _inputs(
                **{
                    **defaults,
                    "charger_status": "paused",
                    "charger_power_kw": 0.0,
                    "current_phase_mode": "three",
                },
                now=T0 + timedelta(seconds=12),
            )
        )
        d4 = controller.decide(
            _inputs(
                **{
                    **defaults,
                    "charger_status": "charging",
                    "charger_power_kw": 3.6,
                    "current_phase_mode": "three",
                },
                now=T0 + timedelta(seconds=16),
            )
        )
        assert d4.sequence_state == "set_limit"
        assert d4.commands[0].value == 16.0


# ---------------------------------------------------------------------------
# Phase-switch when not currently charging (no pause/resume needed)
# ---------------------------------------------------------------------------


class TestPhaseSwitchNotCharging:
    def test_direct_set_phase_mode_when_not_drawing(self):
        car = _car(active_slot=True, home_and_plugged=True)
        inputs = _inputs(
            cars=(car,),
            charger_status="awaiting_start",
            charger_power_kw=0.0,
            current_phase_mode="single",
        )
        decision = ChargerController().decide(inputs)
        assert decision.sequence_state == "idle"
        assert decision.commands == (decision.commands[0],)
        assert decision.commands[0].action == "set_phase_mode"
        assert decision.commands[0].value == "three"

    def test_starts_normally_next_tick_once_phase_confirmed(self):
        car = _car(active_slot=True, home_and_plugged=True)
        controller = ChargerController()
        controller.decide(
            _inputs(
                cars=(car,), charger_status="awaiting_start", charger_power_kw=0.0,
                current_phase_mode="single",
            )
        )
        decision = controller.decide(
            _inputs(
                cars=(car,), charger_status="awaiting_start", charger_power_kw=0.0,
                current_phase_mode="three",
            )
        )
        assert any(c.action == "start" for c in decision.commands)
        assert any(c.action == "set_dynamic_limit" for c in decision.commands)


# ---------------------------------------------------------------------------
# Generic stuck-command detection (outside phase-switch sequences)
# ---------------------------------------------------------------------------


class TestGenericStuckDetection:
    def test_start_command_not_stuck_before_timeout(self):
        car = _car(active_slot=True, home_and_plugged=True)
        controller = ChargerController()
        controller.decide(
            _inputs(cars=(car,), now=T0, charger_status="awaiting_start", charger_power_kw=0.0)
        )
        decision = controller.decide(
            _inputs(
                cars=(car,), now=T0 + timedelta(seconds=30), charger_status="awaiting_start",
                charger_power_kw=0.0,
            )
        )
        assert decision.stuck is False

    def test_start_command_stuck_after_timeout(self):
        car = _car(active_slot=True, home_and_plugged=True)
        controller = ChargerController()
        controller.decide(
            _inputs(cars=(car,), now=T0, charger_status="awaiting_start", charger_power_kw=0.0)
        )
        decision = controller.decide(
            _inputs(
                cars=(car,), now=T0 + timedelta(seconds=61), charger_status="awaiting_start",
                charger_power_kw=0.0,
            )
        )
        assert decision.stuck is True

    def test_repeated_retries_do_not_push_out_the_timeout(self):
        """Reissuing the same command every tick while waiting must not
        keep resetting the stuck clock."""
        car = _car(active_slot=True, home_and_plugged=True)
        controller = ChargerController()
        controller.decide(
            _inputs(cars=(car,), now=T0, charger_status="awaiting_start", charger_power_kw=0.0)
        )
        # Several ticks in between, still not confirmed.
        controller.decide(
            _inputs(
                cars=(car,), now=T0 + timedelta(seconds=30), charger_status="awaiting_start",
                charger_power_kw=0.0,
            )
        )
        controller.decide(
            _inputs(
                cars=(car,), now=T0 + timedelta(seconds=59), charger_status="awaiting_start",
                charger_power_kw=0.0,
            )
        )
        decision = controller.decide(
            _inputs(
                cars=(car,), now=T0 + timedelta(seconds=61), charger_status="awaiting_start",
                charger_power_kw=0.0,
            )
        )
        assert decision.stuck is True

    def test_confirmed_command_clears_stuck(self):
        car = _car(active_slot=True, home_and_plugged=True)
        controller = ChargerController()
        controller.decide(
            _inputs(cars=(car,), now=T0, charger_status="awaiting_start", charger_power_kw=0.0)
        )
        controller.decide(
            _inputs(
                cars=(car,), now=T0 + timedelta(seconds=61), charger_status="awaiting_start",
                charger_power_kw=0.0,
            )
        )
        # Now confirmed.
        decision = controller.decide(
            _inputs(
                cars=(car,), now=T0 + timedelta(seconds=62), charger_status="charging",
                charger_power_kw=3.5,
            )
        )
        assert decision.stuck is False

    def test_stop_command_stuck_after_timeout(self):
        controller = ChargerController()
        controller.decide(
            _inputs(cars=(), now=T0, charger_status="charging", charger_power_kw=3.5)
        )
        decision = controller.decide(
            _inputs(
                cars=(), now=T0 + timedelta(seconds=61), charger_status="charging",
                charger_power_kw=3.5,
            )
        )
        assert decision.stuck is True
        assert decision.commands[0].action == "stop"


class TestStuckWarningLog:
    """stuck=true logs ONE warning per stuck episode naming the charger
    status and the ignored command -- the 2026-08-07 incident set the flag
    silently while the charger ignored "stop" for 7 minutes."""

    def _drawing(self, seconds):
        return _inputs(
            cars=(),
            charger_status="charging",
            charger_power_kw=3.5,
            now=T0 + timedelta(seconds=seconds),
        )

    def test_stuck_warning_fires_once_per_episode(self, caplog):
        controller = ChargerController()
        controller.decide(self._drawing(0))
        with caplog.at_level(logging.WARNING):
            stuck = controller.decide(self._drawing(61))
            assert stuck.stuck is True
            still_stuck = controller.decide(self._drawing(91))
            assert still_stuck.stuck is True
        warnings = [
            r for r in caplog.records if r.name.endswith("charger_state_machine")
        ]
        assert len(warnings) == 1
        assert "'stop'" in warnings[0].getMessage()
        assert "'charging'" in warnings[0].getMessage()

    def test_new_episode_warns_again(self, caplog):
        controller = ChargerController()
        controller.decide(self._drawing(0))
        with caplog.at_level(logging.WARNING):
            controller.decide(self._drawing(61))  # episode 1: stop ignored
            # Confirmation (draw stopped) ends the episode.
            cleared = controller.decide(
                _inputs(
                    cars=(),
                    charger_status="awaiting_start",
                    charger_power_kw=0.0,
                    now=T0 + timedelta(seconds=120),
                )
            )
            assert cleared.stuck is False
            # Episode 2: a scheduled start goes ignored.
            car = _car(active_slot=True, home_and_plugged=True)
            waiting = {
                "cars": (car,),
                "charger_status": "awaiting_start",
                "charger_power_kw": 0.0,
            }
            controller.decide(_inputs(now=T0 + timedelta(seconds=150), **waiting))
            second = controller.decide(
                _inputs(now=T0 + timedelta(seconds=211), **waiting)
            )
            assert second.stuck is True
        warnings = [
            r for r in caplog.records if r.name.endswith("charger_state_machine")
        ]
        assert len(warnings) == 2
        assert "'start'" in warnings[1].getMessage()

    def test_stuck_warning_names_latest_command(self, caplog):
        # "stop" then "pause" both expect no draw: the action must track the
        # LATEST issued command while the timeout clock keeps its original
        # start (repeated commands must not push the stuck timeout back).
        controller = ChargerController()
        controller._note_command_expectation(False, T0, "stop")
        controller._note_command_expectation(
            False, T0 + timedelta(seconds=30), "pause"
        )
        assert controller._last_command_at == T0
        with caplog.at_level(logging.WARNING):
            controller._resolve_command_tracking(
                True, T0 + timedelta(seconds=61), 60.0, "charging"
            )
        warnings = [
            r for r in caplog.records if r.name.endswith("charger_state_machine")
        ]
        assert len(warnings) == 1
        assert "'pause'" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Belief-gated command emission (Easee reconciler)
# ---------------------------------------------------------------------------


class TestBeliefGatedLimitEmission:
    """set_dynamic_limit is emitted on value change or heartbeat -- never
    per tick just because the power-derived current_dynamic_limit_amps
    estimate (e.g. 15.95) fails to exactly match the 16.0 target."""

    def _charging(self, now, **overrides):
        defaults = {
            "charger_status": "charging",
            "charger_power_kw": 11.0,
            "current_dynamic_limit_amps": 15.95,
            "now": now,
        }
        defaults.update(overrides)
        return _inputs(**defaults)

    def test_steady_charge_emits_limit_once_then_silence(self):
        controller = ChargerController()
        d1 = controller.decide(self._charging(T0))
        assert d1.commands == (ChargerCommand("set_dynamic_limit", 16.0),)
        for seconds in (30, 60, 90):
            d = controller.decide(self._charging(T0 + timedelta(seconds=seconds)))
            assert d.commands == ()

    def test_value_change_emits_once(self):
        controller = ChargerController()
        controller.decide(self._charging(T0))
        # Headroom drops: fuse avail 20-2-8+0 = 10A -- a decrease, applied
        # after the 5s decrease delay, emitted exactly once.
        drop = {"measured_worst_case_signed_amps": 8.0, "current_dynamic_limit_amps": 0.0}
        pending = controller.decide(self._charging(T0 + timedelta(seconds=30), **drop))
        assert pending.commands == ()  # decrease still pending, applied still 16
        applied = controller.decide(self._charging(T0 + timedelta(seconds=36), **drop))
        assert applied.commands == (ChargerCommand("set_dynamic_limit", 10.0),)
        silent = controller.decide(self._charging(T0 + timedelta(seconds=66), **drop))
        assert silent.commands == ()

    def test_heartbeat_reasserts_unchanged_limit(self):
        controller = ChargerController()
        controller.decide(self._charging(T0))
        just_before = controller.decide(self._charging(T0 + timedelta(seconds=599)))
        assert just_before.commands == ()
        heartbeat = controller.decide(self._charging(T0 + timedelta(seconds=600)))
        assert heartbeat.commands == (ChargerCommand("set_dynamic_limit", 16.0),)
        after = controller.decide(self._charging(T0 + timedelta(seconds=630)))
        assert after.commands == ()

    def test_heartbeat_rewrites_applied_value_not_pending_increase(self):
        """The heartbeat re-asserts what hysteresis has APPLIED -- it must
        never leak a pending increase before amp_increase_delay_s."""
        controller = ChargerController()
        d1 = controller.decide(
            self._charging(
                T0,
                measured_worst_case_signed_amps=8.0,
                current_dynamic_limit_amps=0.0,
                amp_increase_delay_s=1200.0,
            )
        )
        assert d1.commands == (ChargerCommand("set_dynamic_limit", 10.0),)
        # Headroom recovers -- computed 16A is now increase-pending (1200s).
        controller.decide(
            self._charging(T0 + timedelta(seconds=30), amp_increase_delay_s=1200.0)
        )
        heartbeat = controller.decide(
            self._charging(T0 + timedelta(seconds=630), amp_increase_delay_s=1200.0)
        )
        assert heartbeat.commands == (ChargerCommand("set_dynamic_limit", 10.0),)
        # Once the increase delay elapses, 16A goes out as a value change.
        raised = controller.decide(
            self._charging(T0 + timedelta(seconds=1230), amp_increase_delay_s=1200.0)
        )
        assert raised.commands == (ChargerCommand("set_dynamic_limit", 16.0),)

    def test_sub_amp_jitter_does_not_resend_after_quantization(self):
        """Fuse-bound capacity jitter (15.95 -> 16.0 -> 15.9) quantizes to
        the same 15A -- no re-sends after the first emission."""
        controller = ChargerController()

        def tick(seconds, addback):
            # fuse avail = 20 - 2 - 8 + addback -- jitters with the
            # power-derived add-back estimate.
            return controller.decide(
                self._charging(
                    T0 + timedelta(seconds=seconds),
                    measured_worst_case_signed_amps=8.0,
                    current_dynamic_limit_amps=addback,
                )
            )

        first = tick(0, 5.95)  # capacity 15.95 -> emit floor 15A
        assert first.commands == (ChargerCommand("set_dynamic_limit", 15.0),)
        assert tick(30, 6.0).commands == ()  # 16.0 -- increase-pending
        assert tick(60, 5.9).commands == ()  # 15.9 -- decrease-pending
        assert tick(70, 5.9).commands == ()  # 15.9 applied -- still 15A

    def test_limit_zero_gated_but_pause_reasserted_every_tick(self):
        """The proactive 0A limit goes through the same belief (once, not
        per tick); the safety pause while still drawing stays per-tick."""
        controller = ChargerController()
        overload = {
            "force_charging": True,
            "charger_status": "charging",
            "charger_power_kw": 3.5,
            "measured_worst_case_signed_amps": 19.0,
        }
        d1 = controller.decide(_inputs(now=T0, **overload))
        assert [c.action for c in d1.commands] == ["set_dynamic_limit", "pause"]
        d2 = controller.decide(_inputs(now=T0 + timedelta(seconds=30), **overload))
        assert [c.action for c in d2.commands] == ["pause"]

    def test_limit_zero_not_drawing_emits_once(self):
        controller = ChargerController()
        blocked = {
            "force_charging": True,
            "charger_status": "awaiting_start",
            "charger_power_kw": 0.0,
            "measured_worst_case_signed_amps": 100.0,
        }
        d1 = controller.decide(_inputs(now=T0, **blocked))
        assert d1.commands == (ChargerCommand("set_dynamic_limit", 0.0),)
        d2 = controller.decide(_inputs(now=T0 + timedelta(seconds=30), **blocked))
        assert d2.commands == ()

    def test_status_lie_while_drawing_keeps_belief_gating(self):
        """Physics defence + belief gating together: while the status
        entity lies terminal but the car still draws, supervision runs the
        normal belief-gated emission path -- no per-tick re-sends, and the
        heartbeat still fires through the lie."""
        controller = ChargerController()
        d1 = controller.decide(self._charging(T0))
        assert d1.commands == (ChargerCommand("set_dynamic_limit", 16.0),)
        # Status entity flips to a lying terminal state mid-draw.
        lie = {"charger_status": "disconnected"}
        d2 = controller.decide(self._charging(T0 + timedelta(seconds=30), **lie))
        assert d2.override_reason != "terminal_disconnected"
        assert d2.commands == ()  # unchanged limit stays gated during the lie
        heartbeat = controller.decide(
            self._charging(T0 + timedelta(seconds=600), **lie)
        )
        assert heartbeat.commands == (ChargerCommand("set_dynamic_limit", 16.0),)


class TestStartResumeBeliefGating:
    """start/resume is emitted once per attempt -- not repeated every tick
    during ramp lag -- and re-issued only after command_stuck_timeout_s."""

    def _waiting(self, now, **overrides):
        defaults = {
            "charger_status": "awaiting_start",
            "charger_power_kw": 0.0,
            "now": now,
        }
        defaults.update(overrides)
        return _inputs(**defaults)

    def test_start_not_reissued_during_ramp_lag(self):
        controller = ChargerController()
        d1 = controller.decide(self._waiting(T0))
        assert [c.action for c in d1.commands] == ["start", "set_dynamic_limit"]
        for seconds in (30, 59):
            d = controller.decide(self._waiting(T0 + timedelta(seconds=seconds)))
            assert d.commands == ()

    def test_start_reissued_after_stuck_timeout(self):
        controller = ChargerController()
        controller.decide(self._waiting(T0))
        decision = controller.decide(self._waiting(T0 + timedelta(seconds=61)))
        assert [c.action for c in decision.commands] == ["start"]
        assert decision.stuck is True

    def test_resume_gated_like_start_when_paused(self):
        controller = ChargerController()
        d1 = controller.decide(self._waiting(T0, charger_status="paused"))
        assert d1.commands[0].action == "resume"
        d2 = controller.decide(
            self._waiting(T0 + timedelta(seconds=30), charger_status="paused")
        )
        assert d2.commands == ()

    def test_confirmed_draw_rearms_start_immediately(self):
        """Once the start confirms (draw observed), a later external stop
        may issue a fresh start without waiting out the stuck timeout."""
        controller = ChargerController()
        controller.decide(self._waiting(T0))
        controller.decide(
            self._waiting(
                T0 + timedelta(seconds=10),
                charger_status="charging",
                charger_power_kw=3.5,
            )
        )
        decision = controller.decide(self._waiting(T0 + timedelta(seconds=20)))
        assert any(c.action == "start" for c in decision.commands)


class TestUnauthorizedStopReassert:
    def test_stop_reasserted_every_tick(self):
        """Safety property: the unauthorized-draw stop is NOT belief-gated.

        The companion 0 A dynamic limit IS belief-gated -- emitted on the
        first suppression tick, then left to the heartbeat."""
        controller = ChargerController()
        first = controller.decide(
            _inputs(cars=(), charger_status="charging", charger_power_kw=3.5, now=T0)
        )
        assert [c.action for c in first.commands] == ["set_dynamic_limit", "stop"]
        for seconds in (30, 60):
            decision = controller.decide(
                _inputs(
                    cars=(),
                    charger_status="charging",
                    charger_power_kw=3.5,
                    now=T0 + timedelta(seconds=seconds),
                )
            )
            assert [c.action for c in decision.commands] == ["stop"]


class TestBeliefReset:
    def _charging(self, now):
        return _inputs(
            charger_status="charging",
            charger_power_kw=11.0,
            current_dynamic_limit_amps=15.95,
            now=now,
        )

    def test_terminal_reset_clears_belief(self):
        controller = ChargerController()
        controller.decide(self._charging(T0))
        assert controller.decide(self._charging(T0 + timedelta(seconds=30))).commands == ()
        terminal = controller.decide(
            _inputs(
                charger_status="disconnected",
                charger_power_kw=0.0,
                now=T0 + timedelta(seconds=60),
            )
        )
        assert terminal.override_reason == "terminal_disconnected"
        # New session: the same 16A is re-emitted well before the heartbeat.
        fresh = controller.decide(self._charging(T0 + timedelta(seconds=90)))
        assert fresh.commands == (ChargerCommand("set_dynamic_limit", 16.0),)

    def test_reset_command_belief_rearms_limit_and_start(self):
        """The coordinator calls this on the control_enabled OFF->ON edge --
        the first live tick must re-assert both the limit and the start."""
        controller = ChargerController()
        waiting = {"charger_status": "awaiting_start", "charger_power_kw": 0.0}
        controller.decide(_inputs(now=T0, **waiting))
        silent = controller.decide(_inputs(now=T0 + timedelta(seconds=30), **waiting))
        assert silent.commands == ()
        controller.reset_command_belief()
        # 45s: still inside the stuck timeout, so without the reset this
        # tick would stay silent.
        rearmed = controller.decide(_inputs(now=T0 + timedelta(seconds=45), **waiting))
        assert [c.action for c in rearmed.commands] == ["start", "set_dynamic_limit"]


# ---------------------------------------------------------------------------
# Dataclass shape sanity
# ---------------------------------------------------------------------------


class TestDataclassShapes:
    def test_decide_returns_charger_decision(self):
        decision = ChargerController().decide(_inputs())
        assert isinstance(decision, ChargerDecision)

    def test_car_demand_defaults(self):
        car = CarDemand(active_slot=True, home_and_plugged=True)
        assert car.phase_capability == 3
        assert car.max_charge_kw == 7.4

    def test_charger_inputs_default_safety_buffer_matches_canonical(self):
        """The dataclass fallback default must match DEFAULT_SAFETY_BUFFER_AMPS
        (1.0) -- production always passes safety_buffer_amps explicitly, but
        the fallback must not disagree with the canonical constant."""
        inputs = ChargerInputs(
            charger_status="awaiting_start",
            charger_power_kw=0.0,
            measured_worst_case_signed_amps=0.0,
            current_dynamic_limit_amps=0.0,
            force_charging=False,
            solar_surplus_kw=0.0,
            battery_soc_pct=50.0,
            current_phase_mode="three",
            now=T0,
            fuse_rating_amps=20.0,
        )
        assert inputs.safety_buffer_amps == 1.0

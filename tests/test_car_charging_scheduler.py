"""Tests for the pure-Python car charging schedule algorithm.

All tests use UTC-aware datetimes based on 2026-02-15. Price slots use the
dict format matching PriceSlot (start, end, price keys with datetime values).

Covers: normal scheduling, SOC at/above target, no available slots, zero
charge power, fallback mode, solar charge marking, preliminary flag,
current_action derivation, and monotonicity in the assumed charge power
(lower power never books fewer slots -- the premise the measured-throughput
planning figure rests on).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.energy_manager.car_charging_scheduler import (
    CarScheduleResult,
    CarScheduleSlot,
    build_car_charging_schedule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _make_slot(hour: int, price: float, day: int = 15) -> dict:
    """Create a price slot dict for a single hour on 2026-02-{day}."""
    start = datetime(2026, 2, day, hour, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    return {"start": start, "end": end, "price": price}


def _make_slots(prices: list[tuple[int, float]], day: int = 15) -> list[dict]:
    """Create price slot dicts from (hour, price) tuples."""
    return [_make_slot(hour, price, day) for hour, price in prices]


def _make_24h_slots(prices: list[float], day: int = 15) -> list[dict]:
    """Create 24 hour price slots from a list of 24 prices."""
    assert len(prices) == 24
    return [_make_slot(h, p, day) for h, p in enumerate(prices)]


def _make_quarter_slot(quarter_index: int, price: float, day: int = 15) -> dict:
    """Create a 15-minute price slot dict; quarter_index counts 15-min blocks
    from midnight on 2026-02-{day}."""
    start = datetime(2026, 2, day, 0, 0, 0, tzinfo=UTC) + timedelta(
        minutes=15 * quarter_index
    )
    end = start + timedelta(minutes=15)
    return {"start": start, "end": end, "price": price}


def _make_quarter_slots(prices: list[tuple[int, float]], day: int = 15) -> list[dict]:
    """Create 15-minute price slot dicts from (quarter_index, price) tuples."""
    return [_make_quarter_slot(q, price, day) for q, price in prices]


# ---------------------------------------------------------------------------
# Common test parameters
# ---------------------------------------------------------------------------

# Enyaq: 77 kWh battery, 11 kW max charge, typical scenario
DEFAULT_CAPACITY = 77.0
DEFAULT_CHARGE_POWER = 11.0


# ---------------------------------------------------------------------------
# Test 1: Normal scheduling -- cheapest N slots selected
# ---------------------------------------------------------------------------


class TestNormalScheduling:
    """Given SOC 20%, target 80%, 77kWh battery, 11kW charge: selects cheapest 5 slots."""

    def test_selects_cheapest_slots_before_departure(self):
        """SOC 20% -> 80% needs 46.2 kWh / 11 kW = 4.2h -> ceil(4.2) = 5 slots."""
        # 10 available slots with varying prices; departure at hour 10
        prices = [
            (0, 1.50),   # expensive
            (1, 0.30),   # cheap -- should be selected
            (2, 0.80),   # medium
            (3, 0.20),   # cheapest -- should be selected
            (4, 0.90),   # medium
            (5, 0.25),   # cheap -- should be selected
            (6, 1.20),   # expensive
            (7, 0.40),   # cheap -- should be selected
            (8, 0.35),   # cheap -- should be selected
            (9, 0.60),   # medium
        ]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert isinstance(result, CarScheduleResult)
        # energy_needed = (80-20)/100 * 77 = 46.2 kWh
        assert abs(result.energy_needed_kwh - 46.2) < 0.01
        # hours_needed = 46.2 / 11 = 4.2
        assert abs(result.hours_needed - 4.2) < 0.01
        # ceil(4.2) = 5 charge slots
        assert result.charging_slot_count == 5

        # Verify the 5 cheapest slots were selected (prices: 0.20, 0.25, 0.30, 0.35, 0.40)
        charge_slots = [s for s in result.schedule if s.action == "charge"]
        charge_prices = sorted(s.price for s in charge_slots)
        assert charge_prices == [0.20, 0.25, 0.30, 0.35, 0.40]

    def test_schedule_is_chronological(self):
        """Output schedule should be sorted by start time."""
        prices = [(h, 1.0 - h * 0.05) for h in range(10)]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=50.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        starts = [s.start for s in result.schedule]
        assert starts == sorted(starts), "Schedule should be in chronological order"


# ---------------------------------------------------------------------------
# Test 2: SOC already at target -- returns idle
# ---------------------------------------------------------------------------


class TestSocAtTarget:
    """When current SOC >= target SOC, all slots should be idle."""

    def test_soc_at_target_returns_idle(self):
        """SOC 80%, target 80% => 0 kWh needed, all idle."""
        slots = _make_24h_slots([0.50] * 24)
        departure = datetime(2026, 2, 16, 7, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=80.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert result.energy_needed_kwh == 0.0
        assert result.hours_needed == 0.0
        assert result.charging_slot_count == 0
        assert result.current_action == "idle"

    def test_soc_above_target_returns_idle(self):
        """SOC 80%, target 70% => already above target, no charging needed."""
        slots = _make_24h_slots([0.50] * 24)
        departure = datetime(2026, 2, 16, 7, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=80.0,
            target_soc_pct=70.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert result.energy_needed_kwh == 0.0
        assert result.charging_slot_count == 0
        assert result.current_action == "idle"


# ---------------------------------------------------------------------------
# Test 3: No available slots before departure
# ---------------------------------------------------------------------------


class TestNoAvailableSlots:
    """No slots in [now, departure] window returns empty schedule."""

    def test_no_available_slots_empty_schedule(self):
        """When all slots are outside the valid window, return idle empty result."""
        # All slots are at hour 20-23, but departure is at hour 10
        slots = _make_slots([(h, 0.50) for h in range(20, 24)])
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert result.schedule == []
        assert result.charging_slot_count == 0
        assert result.current_action == "idle"

    def test_slots_before_now_excluded(self):
        """Slots with start < now should be excluded from selection."""
        # Slots at hours 0-5, but now is at hour 4
        slots = _make_slots([(h, 0.10) for h in range(6)])
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 4, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        # Only hours 4 and 5 should be available (start >= now)
        for slot in result.schedule:
            assert slot.start >= now, f"Slot at {slot.start} should not be before now={now}"


# ---------------------------------------------------------------------------
# Test 4: Zero charge power -- division guard
# ---------------------------------------------------------------------------


class TestZeroChargePower:
    """max_charge_power_kw = 0 should return empty result (division guard)."""

    def test_zero_charge_power_returns_empty(self):
        """Division by zero guarded -- returns idle empty result."""
        slots = _make_24h_slots([0.50] * 24)
        departure = datetime(2026, 2, 16, 7, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=0.0,
            now=now,
        )

        assert result.schedule == []
        assert result.charging_slot_count == 0
        assert result.current_action == "idle"


# ---------------------------------------------------------------------------
# Test 5: Fallback mode -- cheapest half of available slots
# ---------------------------------------------------------------------------


class TestFallbackMode:
    """fallback_mode=True selects cheapest half of available slots."""

    def test_fallback_selects_cheapest_half(self):
        """In fallback mode, select cheapest len(available)//2 slots."""
        # 10 slots with varying prices
        prices = [(h, 0.10 * (h + 1)) for h in range(10)]  # 0.10, 0.20, ... 1.00
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
            fallback_mode=True,
        )

        # 10 available slots, fallback takes 10 // 2 = 5 cheapest
        assert result.charging_slot_count == 5

        charge_slots = [s for s in result.schedule if s.action == "charge"]
        charge_prices = sorted(s.price for s in charge_slots)
        # The 5 cheapest: 0.10, 0.20, 0.30, 0.40, 0.50
        expected = [0.10, 0.20, 0.30, 0.40, 0.50]
        assert charge_prices == pytest.approx(expected)

    def test_fallback_ignores_energy_calculation(self):
        """Fallback mode selects by price, not by energy need."""
        # Even with SOC already at target, fallback mode still picks cheapest half
        prices = [(h, 0.10 * (h + 1)) for h in range(8)]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 8, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=80.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
            fallback_mode=True,
        )

        # 8 available slots => 8 // 2 = 4 cheapest selected
        assert result.charging_slot_count == 4


# ---------------------------------------------------------------------------
# Test 6: Solar charge marking
# ---------------------------------------------------------------------------


class TestSolarChargeMarking:
    """solar_surplus_available=True marks charge slots as solar_charge."""

    def test_solar_surplus_marks_charge_slots(self):
        """When solar_surplus_available=True, charge slots should be solar_charge."""
        prices = [(h, 0.50) for h in range(10)]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
            solar_surplus_available=True,
        )

        # All charging slots should be marked as solar_charge
        for slot in result.schedule:
            if slot.action not in ("idle", "solar_charge"):
                pytest.fail(
                    f"With solar_surplus_available=True, expected 'solar_charge' "
                    f"or 'idle', got '{slot.action}' at {slot.start}"
                )

        solar_charge_slots = [s for s in result.schedule if s.action == "solar_charge"]
        assert len(solar_charge_slots) > 0, "Should have solar_charge slots"

    def test_no_solar_surplus_keeps_charge(self):
        """When solar_surplus_available=False (default), charge slots stay as charge."""
        prices = [(h, 0.50) for h in range(10)]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
            solar_surplus_available=False,
        )

        charge_slots = [s for s in result.schedule if s.action == "charge"]
        solar_charge_slots = [s for s in result.schedule if s.action == "solar_charge"]
        assert len(charge_slots) > 0, "Should have charge slots"
        assert len(solar_charge_slots) == 0, "Should not have solar_charge slots"


# ---------------------------------------------------------------------------
# Test 7: Preliminary flag pass-through
# ---------------------------------------------------------------------------


class TestPreliminaryFlag:
    """is_preliminary flag passed through to result."""

    def test_preliminary_true(self):
        """is_preliminary=True marks schedule as based on incomplete data."""
        slots = _make_24h_slots([0.50] * 24)
        departure = datetime(2026, 2, 16, 7, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
            is_preliminary=True,
        )

        assert result.is_preliminary is True

    def test_preliminary_false_default(self):
        """Default is_preliminary should be False."""
        slots = _make_24h_slots([0.50] * 24)
        departure = datetime(2026, 2, 16, 7, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert result.is_preliminary is False


# ---------------------------------------------------------------------------
# Test 8: Current action derivation
# ---------------------------------------------------------------------------


class TestCurrentAction:
    """current_action derived from which slot contains `now`."""

    def test_current_action_charge_when_in_charge_slot(self):
        """When now falls mid-slot (not at an exact boundary) within a
        selected charge slot, current_action='charge'.

        Regression test for the in-progress-slot exclusion bug: the old
        filter (`s.start >= now`) dropped the slot covering 'now' unless
        'now' happened to land exactly on a slot boundary, so current_action
        fell back to 'idle' almost all the time in real (non-boundary-aligned)
        ticks.
        """
        # Make slots where hour 2 is cheapest (will be selected for charging)
        prices = [(h, 1.00 if h != 2 else 0.10) for h in range(10)]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        # now is mid-slot -- 30 minutes into hour 2's slot, not at a boundary.
        now = datetime(2026, 2, 15, 2, 30, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=90.0,
            target_soc_pct=100.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        # Hour 2 is cheapest -> should be the charge slot for ~7.7 kWh needed
        assert result.current_action == "charge"

    def test_current_action_charge_7_minutes_into_quarter_hour_slot(self):
        """now = 7 minutes into a cheap 15-min slot that should charge ->
        current_action must be 'charge', not 'idle'. This is the exact
        real-world tick shape (coordinator polls mid-slot, essentially never
        exactly on a 15-minute boundary) that the in-progress-slot exclusion
        bug broke."""
        prices = [(0, 0.10)] + [(q, 1.00) for q in range(1, 8)]
        slots = _make_quarter_slots(prices)
        departure = datetime(2026, 2, 15, 2, 0, 0, tzinfo=UTC)
        slot0_start = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)
        now = slot0_start + timedelta(minutes=7)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=95.0,
            target_soc_pct=100.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert result.current_action == "charge"

    def test_current_action_idle_when_not_in_charge_slot(self):
        """When now falls mid-slot within a slot that is NOT selected for
        charging (i.e. it's the in-progress slot but it isn't among the
        cheapest), current_action='idle'.

        The in-progress slot (hour 5, price 2.00) is deliberately pricier
        than the cheap future slots (hours 6-9, price 0.10) so it's
        correctly excluded from selection on price alone -- this is
        distinct from the old bug where the in-progress slot was excluded
        unconditionally regardless of price.
        """
        prices = [(h, 2.00 if h == 5 else 1.00 if h < 5 else 0.10) for h in range(10)]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        # now is mid-slot in hour 5 (the pricier in-progress slot).
        now = datetime(2026, 2, 15, 5, 30, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=90.0,
            target_soc_pct=100.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert result.current_action == "idle"

    def test_current_action_idle_when_outside_all_slots(self):
        """When now is outside all scheduled slots, current_action='idle'."""
        slots = _make_slots([(h, 0.50) for h in range(5, 10)])
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        # now at hour 3 -- before any slot starts
        now = datetime(2026, 2, 15, 3, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert result.current_action == "idle"


# ---------------------------------------------------------------------------
# Test 9: Window filtering -- only [now, departure] slots
# ---------------------------------------------------------------------------


class TestWindowFiltering:
    """Slots not in [now, departure] window are excluded."""

    def test_slots_after_departure_excluded(self):
        """Slots with end > departure should not appear in schedule."""
        # Create slots from hour 0 to hour 23, departure at hour 10
        slots = _make_24h_slots([0.50] * 24)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        # All slots in schedule should end <= departure
        for slot in result.schedule:
            assert slot.end <= departure, (
                f"Slot ending at {slot.end} should not be in schedule "
                f"(departure={departure})"
            )

    def test_schedule_only_contains_valid_window_slots(self):
        """Schedule should only contain slots within [now, departure] window."""
        # 24h of prices, now=5, departure=15 => window is hours 5-14
        slots = _make_24h_slots([0.50] * 24)
        departure = datetime(2026, 2, 15, 15, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 5, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        for slot in result.schedule:
            assert slot.start >= now, f"Slot at {slot.start} before now={now}"
            assert slot.end <= departure, f"Slot ending {slot.end} after departure={departure}"


# ---------------------------------------------------------------------------
# Test 10: Data types -- frozen dataclasses
# ---------------------------------------------------------------------------


class TestDataTypes:
    """Verify data structure types are correct."""

    def test_car_schedule_slot_is_frozen(self):
        """CarScheduleSlot should be a frozen dataclass."""
        slot = CarScheduleSlot(
            start=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            end=datetime(2026, 2, 15, 1, 0, 0, tzinfo=UTC),
            price=0.50,
            action="charge",
        )

        with pytest.raises(AttributeError):
            slot.action = "idle"  # type: ignore[misc]

    def test_result_contains_expected_fields(self):
        """CarScheduleResult has all expected fields."""
        slots = _make_slots([(0, 0.50)])
        departure = datetime(2026, 2, 15, 2, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert hasattr(result, "schedule")
        assert hasattr(result, "charging_slot_count")
        assert hasattr(result, "energy_needed_kwh")
        assert hasattr(result, "hours_needed")
        assert hasattr(result, "current_action")
        assert hasattr(result, "is_preliminary")


# ---------------------------------------------------------------------------
# Test 11: Energy calculation accuracy
# ---------------------------------------------------------------------------


class TestEnergyCalculation:
    """Verify energy and hours calculations are correct."""

    def test_energy_calculation_enyaq(self):
        """Enyaq: SOC 20%->80%, 77kWh battery, 11kW charge."""
        slots = _make_24h_slots([0.50] * 24)
        departure = datetime(2026, 2, 16, 7, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=77.0,
            max_charge_power_kw=11.0,
            now=now,
        )

        # energy = (80-20)/100 * 77 = 46.2 kWh
        assert abs(result.energy_needed_kwh - 46.2) < 0.01
        # hours = 46.2 / 11 = 4.2 hours
        assert abs(result.hours_needed - 46.2 / 11.0) < 0.01
        # ceil(4.2) = 5 charging slots
        assert result.charging_slot_count == math.ceil(46.2 / 11.0)

    def test_energy_calculation_small_gap(self):
        """Small SOC gap: 75%->80% on 77kWh = 3.85kWh = 0.35h -> 1 slot."""
        slots = _make_slots([(h, 0.50) for h in range(5)])
        departure = datetime(2026, 2, 15, 5, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=75.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=77.0,
            max_charge_power_kw=11.0,
            now=now,
        )

        # energy = (80-75)/100 * 77 = 3.85 kWh
        assert abs(result.energy_needed_kwh - 3.85) < 0.01
        # hours = 3.85 / 11 = 0.35 -> ceil = 1
        assert result.charging_slot_count == 1


# ---------------------------------------------------------------------------
# Test 12: Empty price slots
# ---------------------------------------------------------------------------


class TestEmptyPriceSlots:
    """Empty or invalid price_slots input."""

    def test_empty_price_slots(self):
        """Empty price_slots returns idle empty result."""
        departure = datetime(2026, 2, 16, 7, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=[],
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        assert result.schedule == []
        assert result.charging_slot_count == 0
        assert result.current_action == "idle"


# ---------------------------------------------------------------------------
# Test 13: Slot-duration awareness (15-minute Nordpool slots)
#
# BUG: the scheduler used to assume hourly slots (slots_needed =
# ceil(hours_needed), then take that many *slots*). Live Nordpool now
# delivers 15-minute slots (96/day), so the old code booked ~25% of the
# needed charging time. The fix must select cheapest slots by price,
# accumulating actual deliverable energy (slot_duration_hours *
# max_charge_power_kw) until the energy needed is met -- regardless of
# slot duration.
# ---------------------------------------------------------------------------


class TestQuarterHourSlots:
    """15-minute slots must deliver the same total energy as hourly slots,
    needing proportionally more (shorter) slots."""

    def test_15_minute_slots_need_4x_hourly_slot_count_for_same_energy(self):
        """energy_needed=44kWh at 11kW = exactly 4 hours (a slot boundary
        for hourly slots, and a slot boundary for 15-min slots too since
        4h = 16 quarter-slots). Hourly needs 4 slots; 15-min needs 16 slots
        -- exactly 4x, with no overshoot in either case."""
        # Hourly: 10 available slots, distinct prices, energy needs 4 of them.
        hourly_prices = [(h, 0.10 * (h + 1)) for h in range(10)]
        hourly_slots = _make_slots(hourly_prices)
        hourly_departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        hourly_result = build_car_charging_schedule(
            price_slots=hourly_slots,
            departure_time_utc=hourly_departure,
            current_soc_pct=0.0,
            target_soc_pct=44.0,
            battery_capacity_kwh=100.0,
            max_charge_power_kw=11.0,
            now=now,
        )
        assert hourly_result.energy_needed_kwh == pytest.approx(44.0)
        assert hourly_result.charging_slot_count == 4

        # 15-minute: 40 available quarter-slots (10 hours), distinct prices,
        # same 44 kWh energy need -> should need 16 quarter-slots (4x).
        quarter_prices = [(q, 0.10 * (q + 1)) for q in range(40)]
        quarter_slots = _make_quarter_slots(quarter_prices)
        quarter_departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)

        quarter_result = build_car_charging_schedule(
            price_slots=quarter_slots,
            departure_time_utc=quarter_departure,
            current_soc_pct=0.0,
            target_soc_pct=44.0,
            battery_capacity_kwh=100.0,
            max_charge_power_kw=11.0,
            now=now,
        )
        assert quarter_result.energy_needed_kwh == pytest.approx(44.0)
        assert quarter_result.charging_slot_count == 16
        assert quarter_result.charging_slot_count == 4 * hourly_result.charging_slot_count

    def test_partial_quarter_slot_energy_rounds_up_to_whole_slot(self):
        """energy_needed just over 16 quarter-slots' worth (44.0 kWh) must
        round up to a 17th slot, not stop at 16 (which would under-deliver)."""
        # 16 quarter-slots deliver exactly 44.0 kWh (16 * 0.25h * 11kW).
        # Ask for 44.1 kWh -- must round up to 17 slots.
        quarter_prices = [(q, 0.10 * (q + 1)) for q in range(20)]
        slots = _make_quarter_slots(quarter_prices)
        departure = datetime(2026, 2, 15, 5, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=0.0,
            target_soc_pct=44.1,
            battery_capacity_kwh=100.0,
            max_charge_power_kw=11.0,
            now=now,
        )

        assert result.energy_needed_kwh == pytest.approx(44.1)
        assert result.charging_slot_count == 17


# ---------------------------------------------------------------------------
# Test 14: Mixed-duration slot lists (hourly today + 15-min tomorrow)
#
# This happens during Nordpool MTU transitions: today's already-published
# prices are hourly, tomorrow's newly-published prices are 15-minute.
# ---------------------------------------------------------------------------


class TestMixedDurationSlots:
    """Cheapest-first selection must work across a slot list mixing hourly
    and 15-minute durations, using each slot's own deliverable energy."""

    def test_mixed_hourly_and_quarter_slots_selected_by_price(self):
        """4 cheap 15-min slots (tomorrow) + 3 expensive hourly slots
        (today) + 4 very expensive 15-min slots (tomorrow, never reached).
        energy_needed=16.5 kWh (1.5h at 11kW): the 4 cheap quarter-slots
        deliver 11 kWh, leaving 5.5 kWh -- not enough for a whole quarter
        slot's worth of remaining need on their own, so the next cheapest
        slot (one of the 1.0-priced hourly slots, delivering 11 kWh) must
        be selected too. Total: 5 slots (4 quarter + 1 hourly)."""
        today = _make_slots([(0, 1.0), (1, 1.0), (2, 1.0)], day=15)
        tomorrow_cheap = _make_quarter_slots(
            [(0, 0.10), (1, 0.10), (2, 0.10), (3, 0.10)], day=16
        )
        tomorrow_expensive = _make_quarter_slots(
            [(4, 2.0), (5, 2.0), (6, 2.0), (7, 2.0)], day=16
        )
        slots = today + tomorrow_cheap + tomorrow_expensive

        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)
        departure = datetime(2026, 2, 16, 2, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=0.0,
            target_soc_pct=16.5,
            battery_capacity_kwh=100.0,
            max_charge_power_kw=11.0,
            now=now,
        )

        assert result.energy_needed_kwh == pytest.approx(16.5)
        assert result.charging_slot_count == 5

        charge_slots = [s for s in result.schedule if s.action == "charge"]
        quarter_charge = [
            s for s in charge_slots if (s.end - s.start) == timedelta(minutes=15)
        ]
        hourly_charge = [
            s for s in charge_slots if (s.end - s.start) == timedelta(hours=1)
        ]
        assert len(quarter_charge) == 4
        assert len(hourly_charge) == 1
        assert all(s.price == pytest.approx(0.10) for s in quarter_charge)
        assert all(s.price == pytest.approx(1.0) for s in hourly_charge)

        # The expensive quarter-slots (2.0 SEK/kWh) must never be selected.
        expensive_slots = [s for s in result.schedule if s.price == pytest.approx(2.0)]
        assert all(s.action == "idle" for s in expensive_slots)


# ---------------------------------------------------------------------------
# Test 15: Fallback mode with 15-minute slots
# ---------------------------------------------------------------------------


class TestFallbackModeQuarterSlots:
    """fallback_mode must be duration-aware: 'cheapest half' means half of
    the total deliverable energy, not half of the raw slot count divided
    blindly (which happens to coincide for a uniform-duration list, but the
    computation must be based on each slot's actual duration)."""

    def test_fallback_selects_half_of_quarter_slots_by_deliverable_energy(self):
        """40 quarter-slots, distinct ascending prices -> fallback should
        select the cheapest 20 (half of total deliverable energy, which
        for a uniform-duration list is also half of the slot count)."""
        quarter_prices = [(q, 0.10 * (q + 1)) for q in range(40)]
        slots = _make_quarter_slots(quarter_prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
            fallback_mode=True,
        )

        assert result.charging_slot_count == 20

        charge_slots = [s for s in result.schedule if s.action == "charge"]
        charge_prices = sorted(s.price for s in charge_slots)
        expected = [round(0.10 * (q + 1), 2) for q in range(20)]
        assert charge_prices == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Test 16: In-progress slot -- included (not just start >= now) and
# pro-rated to its remaining duration.
#
# BUG: the window filter (`s.start >= now`) excluded the slot currently in
# progress, so the output schedule never contained the slot covering 'now'
# except at an exact slot boundary -- current_action fell back to 'idle'
# almost all the time, and scheduled charging never actually triggered the
# charger. FIX: filter on `s.end > now` instead (keeps the in-progress
# slot), and pro-rate its deliverable energy to the remaining duration
# (end - max(start, now)) so selection/fallback energy math isn't
# over-credited for time that has already elapsed.
# ---------------------------------------------------------------------------


class TestInProgressSlotIncludedAndProrated:
    """The in-progress slot is included in the schedule and its deliverable
    energy is pro-rated to the time remaining, not its full duration."""

    def test_in_progress_slot_is_included_mid_slot(self):
        """now falls 30 minutes into an hourly slot -- that slot must still
        appear in the schedule (with its true, unmodified start time)."""
        slots = _make_slots([(0, 0.10), (1, 0.20), (2, 0.30)])
        departure = datetime(2026, 2, 15, 3, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 30, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=90.0,
            target_soc_pct=100.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=DEFAULT_CHARGE_POWER,
            now=now,
        )

        starts = [s.start for s in result.schedule]
        assert datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC) in starts, (
            "The in-progress slot (0:00-1:00) must be in the schedule with "
            "its true start time, not excluded or truncated."
        )

    def test_in_progress_slot_energy_is_prorated_to_remaining_duration(self):
        """A 15-min in-progress slot with only 5 of its 15 minutes left must
        only count 5 minutes' worth of energy toward the target -- not the
        full 15 minutes. This changes how many slots get selected: without
        pro-ration, the (cheap) in-progress slot alone would look like it
        delivers enough energy to stop after 1 slot; correctly pro-rated,
        it delivers less, so a 2nd (also cheap) slot must be selected too."""
        # Quarter-slots: [0:00-0:15)=0.10 (in progress), [0:15-0:30)=0.10
        # (tie price, selected 2nd), [0:30-0:45)=0.90 (must stay idle).
        slots = _make_quarter_slots([(0, 0.10), (1, 0.10), (2, 0.90)])
        departure = datetime(2026, 2, 15, 0, 45, 0, tzinfo=UTC)
        # 10 minutes into the in-progress slot -> 5 minutes (0.0833h) remain.
        now = datetime(2026, 2, 15, 0, 10, 0, tzinfo=UTC)

        # max_charge_power_kw=12kW: full 15-min slot delivers 3.0 kWh;
        # pro-rated remaining 5 min delivers 1.0 kWh. energy_needed=2.0 kWh
        # (100 kWh battery, 20%->22% SOC gap) -- achievable with 1 full slot
        # (3.0 kWh) if the in-progress slot were wrongly credited in full,
        # but requires 2 slots (1.0 + 3.0 = 4.0 kWh) once pro-rated.
        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=22.0,
            battery_capacity_kwh=100.0,
            max_charge_power_kw=12.0,
            now=now,
        )

        assert result.energy_needed_kwh == pytest.approx(2.0)
        assert result.charging_slot_count == 2

        charge_slots = [s for s in result.schedule if s.action == "charge"]
        charge_prices = sorted(s.price for s in charge_slots)
        assert charge_prices == pytest.approx([0.10, 0.10])

        # The expensive 3rd slot must remain idle.
        expensive = [s for s in result.schedule if s.price == pytest.approx(0.90)]
        assert all(s.action == "idle" for s in expensive)

    def test_fallback_mode_prorates_in_progress_slot_energy(self):
        """Fallback-mode's total-energy-halving math must also use the
        pro-rated in-progress-slot energy, not the full-duration value."""
        # In-progress slot with only 5 of 15 minutes left, plus one full
        # future slot. max_charge_power_kw=12kW.
        slots = _make_quarter_slots([(0, 0.10), (1, 0.20)])
        departure = datetime(2026, 2, 15, 0, 30, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 10, 0, tzinfo=UTC)

        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=12.0,
            now=now,
            fallback_mode=True,
        )

        # total_energy = 1.0 kWh (prorated in-progress) + 3.0 kWh (full) = 4.0
        # target_energy = 2.0 kWh -- the cheaper in-progress slot (1.0 kWh)
        # alone isn't enough, so both slots must be selected.
        assert result.charging_slot_count == 2


# ---------------------------------------------------------------------------
# Test 17: Assumed-power monotonicity -- the premise the learned-throughput
# feature rests on. Feeding the planner a measured (lower) charge power than
# the car's configured ceiling must never book FEWER slots; if it could, a
# throttled install would silently under-charge overnight, which is the one
# direction that leaves a car short at departure.
# ---------------------------------------------------------------------------


class TestAssumedPowerMonotonicity:
    """Lower assumed power => same or more slots, never fewer."""

    def test_lower_power_never_books_fewer_slots(self):
        """Identical 24h slot list and SOC gap, swept from 11.0 kW down.

        Each slot delivers duration_hours * power, so a lower power raises
        the number of cheapest slots needed to cover the fixed
        energy_needed_kwh. Priced strictly ascending so slot selection has
        no ties to break and only the power varies between runs.
        """
        slots = _make_24h_slots([0.10 * (h + 1) for h in range(24)])
        departure = datetime(2026, 2, 16, 0, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        counts = []
        for power in (11.0, 9.5, 7.4, 6.0, 3.7, 2.0, 1.4):
            result = build_car_charging_schedule(
                price_slots=slots,
                departure_time_utc=departure,
                current_soc_pct=20.0,
                target_soc_pct=80.0,
                battery_capacity_kwh=DEFAULT_CAPACITY,
                max_charge_power_kw=power,
                now=now,
            )
            counts.append(result.charging_slot_count)

        assert counts == sorted(counts), f"slot count fell as power fell: {counts}"

    def test_measured_power_books_strictly_more_than_the_ceiling(self):
        """The concrete case the feature exists for: an 11 kW ceiling that
        actually delivers 7.4 or 3.7 kW."""
        slots = _make_24h_slots([0.10 * (h + 1) for h in range(24)])
        departure = datetime(2026, 2, 16, 0, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        def _plan(power: float) -> CarScheduleResult:
            return build_car_charging_schedule(
                price_slots=slots,
                departure_time_utc=departure,
                current_soc_pct=20.0,
                target_soc_pct=80.0,
                battery_capacity_kwh=DEFAULT_CAPACITY,
                max_charge_power_kw=power,
                now=now,
            )

        # energy_needed = (80-20)/100 * 77 = 46.2 kWh, hourly slots.
        ceiling = _plan(11.0)      # 46.2 / 11.0 = 4.2h  -> 5 slots
        measured = _plan(7.4)      # 46.2 / 7.4  = 6.24h -> 7 slots
        throttled = _plan(3.7)     # 46.2 / 3.7  = 12.49h -> 13 slots

        assert ceiling.charging_slot_count == 5
        assert measured.charging_slot_count == 7
        assert throttled.charging_slot_count == 13

        assert measured.hours_needed > ceiling.hours_needed
        assert throttled.hours_needed > measured.hours_needed

    def test_extra_slots_are_the_next_cheapest_not_a_reshuffle(self):
        """Lowering the power extends the selection; it never swaps a
        cheap slot out for an expensive one."""
        slots = _make_24h_slots([0.10 * (h + 1) for h in range(24)])
        departure = datetime(2026, 2, 16, 0, 0, 0, tzinfo=UTC)
        now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

        def _charge_starts(power: float) -> set:
            result = build_car_charging_schedule(
                price_slots=slots,
                departure_time_utc=departure,
                current_soc_pct=20.0,
                target_soc_pct=80.0,
                battery_capacity_kwh=DEFAULT_CAPACITY,
                max_charge_power_kw=power,
                now=now,
            )
            return {s.start for s in result.schedule if s.action == "charge"}

        assert _charge_starts(11.0) < _charge_starts(7.4) < _charge_starts(3.7)


def test_fallback_mode_is_invariant_to_assumed_power():
    """EV-08's guest plan does not move when the assumed power changes.

    fallback_mode targets total_energy / 2.0, and both that total and every
    slot's deliverable energy scale linearly with max_charge_power_kw, so
    the factor cancels exactly. Recorded here so nobody adds a redundant
    "don't use the learned power in fallback mode" guard on the planner
    side -- it would be dead code.
    """
    slots = _make_24h_slots([0.10 * (h + 1) for h in range(24)])
    departure = datetime(2026, 2, 16, 0, 0, 0, tzinfo=UTC)
    now = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)

    def _plan(power: float) -> list[tuple]:
        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=power,
            now=now,
            fallback_mode=True,
        )
        return [(s.start, s.action) for s in result.schedule]

    ceiling_plan = _plan(11.0)
    assert len([a for _, a in ceiling_plan if a == "charge"]) == 12  # 24 // 2
    for power in (9.5, 7.4, 3.7, 1.4):
        assert _plan(power) == ceiling_plan


def test_fallback_mode_is_invariant_with_a_prorated_in_progress_slot():
    """The cancellation must survive pro-ration: the in-progress slot's
    energy is (remaining hours * power), which scales with power too."""
    slots = _make_quarter_slots([(0, 0.10), (1, 0.30), (2, 0.20), (3, 0.40)])
    departure = datetime(2026, 2, 15, 1, 0, 0, tzinfo=UTC)
    now = datetime(2026, 2, 15, 0, 10, 0, tzinfo=UTC)  # 5 of 15 min left

    def _plan(power: float) -> list[tuple]:
        result = build_car_charging_schedule(
            price_slots=slots,
            departure_time_utc=departure,
            current_soc_pct=20.0,
            target_soc_pct=80.0,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            max_charge_power_kw=power,
            now=now,
            fallback_mode=True,
        )
        return [(s.start, s.action) for s in result.schedule]

    assert _plan(3.7) == _plan(11.0)

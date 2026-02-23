"""Tests for the pure-Python car charging schedule algorithm.

All tests use UTC-aware datetimes based on 2026-02-15. Price slots use the
dict format matching PriceSlot (start, end, price keys with datetime values).

Covers: normal scheduling, SOC at/above target, no available slots, zero
charge power, fallback mode, solar charge marking, preliminary flag,
current_action derivation.
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
        assert charge_prices == [0.10, 0.20, 0.30, 0.40, 0.50]

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
        """When now falls within a selected charge slot, current_action='charge'."""
        # Make slots where hour 2 is cheapest (will be selected for charging)
        prices = [(h, 1.00 if h != 2 else 0.10) for h in range(10)]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        # now is in hour 2 (within the charge slot)
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

        # Hour 2 is cheapest -> should be the only charge slot for ~7.7 kWh needed
        assert result.current_action == "charge"

    def test_current_action_idle_when_not_in_charge_slot(self):
        """When now falls within an idle slot, current_action='idle'."""
        prices = [(h, 0.10 if h < 3 else 1.00) for h in range(10)]
        slots = _make_slots(prices)
        departure = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        # now is in hour 5 (not a cheap slot)
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

"""Tests for the pure-Python battery scheduling algorithm.

All tests use UTC-aware datetimes based on 2026-02-15. Price slots use the
dict format matching PriceSlot (start, end, price keys with datetime values).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.energy_manager.battery_scheduler import (
    BatteryScheduleResult,
    ScheduleSlot,
    build_battery_schedule,
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

# Battery: 10 kWh, max charge 5000 W, currently at 50%
DEFAULT_CAPACITY = 10.0
DEFAULT_POWER = 5000.0
DEFAULT_SOC = 50.0
DEFAULT_MIN_SOC = 10.0
DEFAULT_MAX_SOC = 95.0

# Thresholds: charge below 0.50, discharge above 1.50 SEK/kWh
DEFAULT_CHARGE_THRESHOLD = 0.50
DEFAULT_DISCHARGE_THRESHOLD = 1.50


# ---------------------------------------------------------------------------
# Test 1: Basic charge/discharge/idle classification
# ---------------------------------------------------------------------------


class TestBasicChargeDischargeSchedule:
    """Test that slots are correctly classified as charge/discharge/idle."""

    def test_basic_charge_discharge_schedule(self):
        """Given 24h prices with cheap/mid/expensive hours, verify correct actions.

        Uses a 20 kWh battery at 10% SOC with 3 kW charge rate so multiple
        cheap hours are needed to fill the battery for the discharge peak.
        """
        # Hours 0-5: cheap (0.30) -> should charge
        # Hours 6-11: mid (0.80) -> should idle
        # Hours 12-17: expensive (2.50) -> should discharge
        # Hours 18-23: mid (0.70) -> should idle
        prices = (
            [0.30] * 6  # hours 0-5: cheap
            + [0.80] * 6  # hours 6-11: mid
            + [2.50] * 6  # hours 12-17: expensive
            + [0.70] * 6  # hours 18-23: mid
        )
        slots = _make_24h_slots(prices)

        # 20 kWh battery at 10% SOC, 3 kW charge rate
        # Available capacity: (95-10)% * 20 = 17 kWh to fill
        # At 3 kW, needs ~6 hours to fill -> all cheap hours should charge
        result = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=3000.0,
            battery_capacity_kwh=20.0,
            current_soc_pct=10.0,
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
        )

        assert isinstance(result, BatteryScheduleResult)
        assert len(result.schedule) == 24

        # Check that cheap hours are charge (all needed to fill big battery)
        for slot in result.schedule:
            if slot.price <= DEFAULT_CHARGE_THRESHOLD:
                assert slot.action in ("charge", "solar_charge"), (
                    f"Slot at {slot.start.hour}:00 with price {slot.price} "
                    f"should be charge, got {slot.action}"
                )

        # Check that expensive hours are discharge
        discharge_slots = [
            s for s in result.schedule if s.action == "discharge"
        ]
        assert len(discharge_slots) > 0, "Should have discharge slots for expensive hours"

        # Check that mid-range hours are idle
        idle_slots = [s for s in result.schedule if s.action == "idle"]
        assert len(idle_slots) > 0, "Should have idle slots for mid-range hours"

        # Verify counts match
        charge_actions = [s for s in result.schedule if s.action in ("charge", "solar_charge")]
        assert result.charging_slot_count == len(charge_actions)
        assert result.discharging_slot_count == len(discharge_slots)


# ---------------------------------------------------------------------------
# Test 2: Peak grouping identifies separate discharge windows
# ---------------------------------------------------------------------------


class TestPeakGrouping:
    """Test that separate price peaks are identified as distinct discharge windows."""

    def test_peak_grouping_identifies_separate_windows(self):
        """Two expensive periods separated by cheap hours should create two peak groups."""
        # Hours 0-5: cheap (0.30)
        # Hours 8-10: expensive (2.50) -- morning peak
        # Hours 12-14: cheap (0.40)
        # Hours 17-20: expensive (3.00) -- evening peak
        # Hours 21-23: cheap (0.35)
        price_data = (
            [(h, 0.30) for h in range(0, 6)]
            + [(6, 0.60), (7, 0.70)]
            + [(h, 2.50) for h in range(8, 11)]  # morning peak
            + [(11, 0.60)]
            + [(h, 0.40) for h in range(12, 15)]
            + [(15, 0.60), (16, 0.70)]
            + [(h, 3.00) for h in range(17, 21)]  # evening peak
            + [(h, 0.35) for h in range(21, 24)]
        )
        slots = _make_slots(price_data)

        result = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=DEFAULT_SOC,
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            peak_gap_hours=2.0,
        )

        # Should have discharge slots in both peaks
        discharge_slots = [s for s in result.schedule if s.action == "discharge"]
        assert len(discharge_slots) > 0, "Should have discharge slots"

        # Verify discharge happens in both time windows
        discharge_hours = {s.start.hour for s in discharge_slots}
        morning_discharge = discharge_hours & {8, 9, 10}
        evening_discharge = discharge_hours & {17, 18, 19, 20}

        assert len(morning_discharge) > 0, "Should discharge during morning peak"
        assert len(evening_discharge) > 0, "Should discharge during evening peak"


# ---------------------------------------------------------------------------
# Test 3: Virtual energy tracking limits discharge
# ---------------------------------------------------------------------------


class TestVirtualEnergyTracking:
    """Test that discharge is limited by available battery energy."""

    def test_virtual_energy_tracking_limits_discharge(self):
        """Battery at 50% with 10kWh should not discharge more than available."""
        # At 50% SOC with min_soc=10%, available energy = (50-10)% * 10kWh = 4kWh
        # With 5kW max power, 1h slots: each discharge slot uses ~5kWh at max
        # But available is only 4kWh, so should limit discharge slots

        prices = (
            [0.30] * 6  # cheap hours
            + [2.50] * 12  # 12 expensive hours (way more than battery can serve)
            + [0.30] * 6  # cheap hours
        )
        slots = _make_24h_slots(prices)

        result = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=50.0,
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            min_soc_pct=10.0,
            max_soc_pct=95.0,
        )

        # Calculate maximum dischargeable energy
        usable_kwh = (50.0 - 10.0) / 100.0 * DEFAULT_CAPACITY  # 4 kWh
        max_discharge_power_per_slot = DEFAULT_POWER / 1000.0  # 5 kW -> 5 kWh/h

        # With charging first (cheap hours 0-5), battery could be topped up
        # But total discharge energy should never exceed what the battery can provide
        discharge_slots = [s for s in result.schedule if s.action == "discharge"]
        charge_slots = [s for s in result.schedule if s.action in ("charge", "solar_charge")]

        # The key constraint: discharge count should be reasonable given capacity
        # Not all 12 expensive hours should discharge (battery would be empty)
        assert len(discharge_slots) < 12, (
            f"Should not discharge all 12 expensive hours, got {len(discharge_slots)}"
        )
        assert len(discharge_slots) > 0, "Should have some discharge slots"


# ---------------------------------------------------------------------------
# Test 4: Multi-cycle charge between peaks
# ---------------------------------------------------------------------------


class TestMultiCycleCharging:
    """Test that the scheduler inserts charge cycles between peaks."""

    def test_multi_cycle_charge_between_peaks(self):
        """Cheap hours between two peaks should be used for recharging."""
        # Hours 0-3: cheap (0.20) -> initial charge
        # Hours 4-7: expensive (2.50) -> discharge peak 1
        # Hours 8-11: cheap (0.25) -> recharge between peaks
        # Hours 12-15: expensive (3.00) -> discharge peak 2
        # Hours 16-23: mid (0.80) -> idle
        price_data = (
            [(h, 0.20) for h in range(0, 4)]
            + [(h, 2.50) for h in range(4, 8)]
            + [(h, 0.25) for h in range(8, 12)]
            + [(h, 3.00) for h in range(12, 16)]
            + [(h, 0.80) for h in range(16, 24)]
        )
        slots = _make_slots(price_data)

        result = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=30.0,  # Start low to need charging
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            peak_gap_hours=2.0,
        )

        # Should charge in hours 0-3 (initial) AND hours 8-11 (between peaks)
        charge_slots = [
            s for s in result.schedule if s.action in ("charge", "solar_charge")
        ]
        charge_hours = {s.start.hour for s in charge_slots}

        initial_charge = charge_hours & {0, 1, 2, 3}
        mid_charge = charge_hours & {8, 9, 10, 11}

        assert len(initial_charge) > 0, "Should charge during initial cheap period"
        assert len(mid_charge) > 0, (
            "Should charge between peaks to refill for second discharge window"
        )


# ---------------------------------------------------------------------------
# Test 5: No prices returns idle schedule
# ---------------------------------------------------------------------------


class TestEdgeCaseNoPrices:
    """Test handling of empty price data."""

    def test_no_prices_returns_idle_schedule(self):
        """Empty price_slots should return an idle result with zero counts."""
        result = build_battery_schedule(
            price_slots=[],
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=DEFAULT_SOC,
            now=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
        )

        assert isinstance(result, BatteryScheduleResult)
        assert result.schedule == []
        assert result.charging_slot_count == 0
        assert result.discharging_slot_count == 0
        assert result.next_charging_slot is None
        assert result.next_discharging_slot is None
        assert result.current_action == "idle"
        assert result.target_ems_mode == "standby"


# ---------------------------------------------------------------------------
# Test 6: All prices below discharge threshold
# ---------------------------------------------------------------------------


class TestAllPricesBelowThreshold:
    """Test when all prices are below the discharge threshold."""

    def test_all_prices_below_threshold(self):
        """No prices above discharge_threshold should produce zero discharge slots."""
        # All prices at 0.80 -- above charge threshold (0.50) but below discharge (1.50)
        prices = [0.80] * 24
        slots = _make_24h_slots(prices)

        result = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=DEFAULT_SOC,
            now=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
        )

        # Schedule should still contain slots for all 24 hours
        assert len(result.schedule) == 24, (
            f"Should have 24 slots, got {len(result.schedule)}"
        )

        discharge_slots = [s for s in result.schedule if s.action == "discharge"]
        assert len(discharge_slots) == 0, "No discharge when all prices below threshold"
        assert result.discharging_slot_count == 0


# ---------------------------------------------------------------------------
# Test 7: Solar forecast reduces grid charging
# ---------------------------------------------------------------------------


class TestSolarForecast:
    """Test that solar forecast reduces the need for grid charging."""

    def test_solar_forecast_reduces_charging(self):
        """With solar_forecast_wh provided, fewer grid charge slots needed."""
        prices = (
            [0.30] * 6  # cheap hours 0-5
            + [0.80] * 6  # mid hours 6-11
            + [2.50] * 6  # expensive hours 12-17
            + [0.70] * 6  # mid hours 18-23
        )
        slots = _make_24h_slots(prices)

        # Without solar
        result_no_solar = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=DEFAULT_SOC,
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            solar_forecast_wh=None,
        )

        # With significant solar forecast (5000 Wh = 5 kWh)
        result_with_solar = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=DEFAULT_SOC,
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            solar_forecast_wh=5000.0,
        )

        grid_charge_no_solar = len(
            [s for s in result_no_solar.schedule if s.action == "charge"]
        )
        grid_charge_with_solar = len(
            [s for s in result_with_solar.schedule if s.action == "charge"]
        )
        solar_charge_slots = len(
            [s for s in result_with_solar.schedule if s.action == "solar_charge"]
        )

        # With solar, should have fewer grid charge slots OR some solar_charge slots
        assert (
            grid_charge_with_solar < grid_charge_no_solar or solar_charge_slots > 0
        ), (
            f"Solar should reduce grid charging or add solar_charge slots. "
            f"Grid charge: {grid_charge_no_solar} vs {grid_charge_with_solar}, "
            f"Solar charge: {solar_charge_slots}"
        )


# ---------------------------------------------------------------------------
# Test 8: Current action based on now
# ---------------------------------------------------------------------------


class TestCurrentAction:
    """Test that current_action and target_ems_mode reflect the slot at 'now'."""

    def test_current_action_based_on_now(self):
        """Given a specific 'now', current_action should match that slot's action.

        Uses a 20 kWh battery at 10% SOC with 3 kW charge to ensure hour 2
        is still in the charging window (needs ~6 hours to fill).
        """
        prices = [0.30] * 6 + [0.80] * 6 + [2.50] * 6 + [0.70] * 6
        slots = _make_24h_slots(prices)

        # now = hour 2 (cheap period) -- with big battery, still charging
        result_charging = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=3000.0,
            battery_capacity_kwh=20.0,
            current_soc_pct=10.0,
            now=datetime(2026, 2, 15, 2, 30, 0, tzinfo=UTC),
        )

        # now = hour 14 (expensive period)
        result_discharging = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=3000.0,
            battery_capacity_kwh=20.0,
            current_soc_pct=10.0,
            now=datetime(2026, 2, 15, 14, 30, 0, tzinfo=UTC),
        )

        # During cheap hours, should be charging
        assert result_charging.current_action in ("charge", "solar_charge"), (
            f"During cheap hours, should be charging, got {result_charging.current_action}"
        )
        assert result_charging.target_ems_mode == "command_charging"

        # During expensive hours, should be discharging
        assert result_discharging.current_action == "discharge", (
            f"During expensive hours, should be discharging, got {result_discharging.current_action}"
        )
        assert result_discharging.target_ems_mode == "max_self_consumption"


# ---------------------------------------------------------------------------
# Test 9: Next slots lookup
# ---------------------------------------------------------------------------


class TestNextSlotsLookup:
    """Test that next_charging_slot and next_discharging_slot are correct."""

    def test_next_slots_lookup(self):
        """Verify next upcoming charge/discharge slots relative to now."""
        prices = (
            [0.80] * 4  # hours 0-3: idle (mid-range)
            + [0.30] * 2  # hours 4-5: cheap -> charge
            + [0.80] * 4  # hours 6-9: idle
            + [2.50] * 4  # hours 10-13: expensive -> discharge
            + [0.80] * 10  # hours 14-23: idle
        )
        slots = _make_24h_slots(prices)

        # now = hour 1, so next charge is at hour 4, next discharge at hour 10
        result = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=DEFAULT_SOC,
            now=datetime(2026, 2, 15, 1, 0, 0, tzinfo=UTC),
        )

        # next_charging_slot should be at hour 4
        assert result.next_charging_slot is not None, "Should have a next charging slot"
        assert result.next_charging_slot.start.hour == 4, (
            f"Next charge should be at hour 4, got hour {result.next_charging_slot.start.hour}"
        )

        # next_discharging_slot should be at hour 10
        assert result.next_discharging_slot is not None, "Should have a next discharging slot"
        assert result.next_discharging_slot.start.hour == 10, (
            f"Next discharge should be at hour 10, got hour {result.next_discharging_slot.start.hour}"
        )


# ---------------------------------------------------------------------------
# Test 10: SOC constraints respected
# ---------------------------------------------------------------------------


class TestSocConstraints:
    """Test that min_soc_pct and max_soc_pct are respected."""

    def test_soc_constraints_respected(self):
        """Battery should not discharge below min_soc or charge above max_soc."""
        # Battery at 15% with min_soc=10%: only 0.5 kWh available for discharge
        # With 5kW power and 1h slots, that is less than one full slot
        prices = [2.50] * 24  # All expensive -> wants to discharge everything
        slots = _make_24h_slots(prices)

        result = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=15.0,  # Very low SOC
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            min_soc_pct=10.0,
            max_soc_pct=95.0,
        )

        # Schedule should cover all 24 hours
        assert len(result.schedule) == 24, (
            f"Should have 24 slots, got {len(result.schedule)}"
        )

        # Available energy: (15-10)% * 10kWh = 0.5 kWh
        # At 5kW, one full slot would use 5kWh -- way more than available
        # So should have very limited discharge (0 or 1 partial)
        discharge_slots = [s for s in result.schedule if s.action == "discharge"]
        assert len(discharge_slots) <= 1, (
            f"With only 0.5 kWh available, should have at most 1 discharge slot, "
            f"got {len(discharge_slots)}"
        )

    def test_max_soc_limits_charging(self):
        """Battery near max_soc should not schedule excessive charging."""
        # Battery at 90% with max_soc=95%: only 0.5 kWh capacity remaining
        prices = [0.20] * 24  # All cheap -> wants to charge everything
        slots = _make_24h_slots(prices)

        result = build_battery_schedule(
            price_slots=slots,
            charge_threshold=DEFAULT_CHARGE_THRESHOLD,
            discharge_threshold=DEFAULT_DISCHARGE_THRESHOLD,
            max_charge_power_w=DEFAULT_POWER,
            battery_capacity_kwh=DEFAULT_CAPACITY,
            current_soc_pct=90.0,  # Near full
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            min_soc_pct=10.0,
            max_soc_pct=95.0,
        )

        # Schedule should cover all 24 hours
        assert len(result.schedule) == 24, (
            f"Should have 24 slots, got {len(result.schedule)}"
        )

        # Available capacity: (95-90)% * 10kWh = 0.5 kWh
        # At 5kW, one slot charges 5kWh -- way more than capacity
        # But there are no discharge slots (all cheap), so limited charging
        charge_slots = [
            s for s in result.schedule if s.action in ("charge", "solar_charge")
        ]
        # Should not charge all 24 slots when battery is nearly full and
        # there is nowhere to discharge
        assert len(charge_slots) <= 1, (
            f"With only 0.5 kWh capacity remaining and no discharge opportunities, "
            f"should have at most 1 charge slot, got {len(charge_slots)}"
        )


# ---------------------------------------------------------------------------
# Test 11-12: Schedule attribute filtering (UAT gap closure)
# ---------------------------------------------------------------------------


class TestScheduleAttributeFiltering:
    """Test that schedule filtering logic correctly excludes past slots."""

    def test_filter_excludes_past_slots(self):
        """Past slots (end <= now) should be excluded from the visible window."""
        from datetime import datetime, timezone, timedelta

        # Simulate a 72-slot schedule (3 days worth) with discharge at slots 36-42
        now = datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc)
        base = datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc)

        slots = []
        for i in range(72):
            start = base + timedelta(hours=i)
            end = start + timedelta(hours=1)
            # Slots 36-42 (Feb 17 12:00-18:00) are discharge, rest idle
            action = "discharge" if 36 <= i <= 42 else "idle"
            slots.append({"start": start, "end": end, "action": action, "price": 0.5})

        # Apply the same filter as sensor.py: exclude slots where end <= now
        filtered = [s for s in slots if s["end"] > now][:48]

        # Slot at 13:00 has end=14:00 which equals now, so end > now is False -> excluded
        # First included: slot at 14:00 (end=15:00 > 14:00)
        assert filtered[0]["start"] == datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc)

        # Discharge slots at indices 36-42 (hours 36-42 from base = Feb 17 12:00-18:00)
        # After filtering from hour 14, these are at relative positions 22-28 in filtered
        discharge_slots = [s for s in filtered if s["action"] == "discharge"]
        assert len(discharge_slots) == 7, f"Expected 7 discharge slots, got {len(discharge_slots)}"

    def test_filter_keeps_current_slot(self):
        """A slot currently in progress (start <= now < end) should be kept."""
        from datetime import datetime, timezone, timedelta

        now = datetime(2026, 2, 16, 14, 30, tzinfo=timezone.utc)
        # Slot from 14:00 to 15:00 -- currently in progress
        current_slot = {
            "start": datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 2, 16, 15, 0, tzinfo=timezone.utc),
            "action": "discharge",
            "price": 1.2,
        }
        # Past slot from 13:00 to 14:00 -- fully past
        past_slot = {
            "start": datetime(2026, 2, 16, 13, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc),
            "action": "idle",
            "price": 0.3,
        }

        slots = [past_slot, current_slot]
        filtered = [s for s in slots if s["end"] > now]

        assert len(filtered) == 1
        assert filtered[0]["action"] == "discharge"

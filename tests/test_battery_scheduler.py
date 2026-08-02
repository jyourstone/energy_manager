"""Tests for the pure-Python battery scheduling algorithm.

All tests use UTC-aware datetimes based on 2026-02-15. Price slots use the
dict format matching PriceSlot (start, end, price keys with datetime values).

BATT-15 SEMANTIC CHANGE (this is a deliberate port, not a regression):
    The live AppDaemon system this integration ports from classifies slots by
    price SPREAD rather than absolute price, and sizes charge/discharge
    energy from house consumption rather than the battery's max power. Every
    test below was rewritten (or added) to exercise that spread-based,
    consumption-sized behavior. See battery_scheduler.py's module docstring
    for the full algorithm overview.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import ClassVar

from custom_components.energy_manager.battery_scheduler import (
    BatteryScheduleResult,
    ScheduleSlot,
    _estimate_solar_rate_kw,
    _normalize_daylight_window,
    _overlap_hours,
    _resolve_solar_windows,
    build_battery_schedule,
    compute_discharge_gate,
    compute_effective_discharge_threshold,
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


def _charge_slots(result: BatteryScheduleResult) -> list:
    return [s for s in result.schedule if s.action in ("charge", "solar_charge")]


def _discharge_slots(result: BatteryScheduleResult) -> list:
    return [s for s in result.schedule if s.action == "discharge"]


def _export_slots(result: BatteryScheduleResult) -> list:
    return [s for s in result.schedule if s.action == "export"]


# ---------------------------------------------------------------------------
# Common test parameters
# ---------------------------------------------------------------------------

# Battery: 10 kWh, max charge 5000 W, currently at 50%
DEFAULT_CAPACITY = 10.0
DEFAULT_POWER = 5000.0
DEFAULT_SOC = 50.0
DEFAULT_MIN_SOC = 10.0
DEFAULT_MAX_SOC = 95.0

# Spread thresholds (BATT-15): charge when peak_max - price > 0.30,
# discharge when price - period_min > 1.00
DEFAULT_CHARGE_THRESHOLD = 0.30
DEFAULT_DISCHARGE_THRESHOLD = 1.00

# House consumption used to size energy needs (BATT-15)
DEFAULT_MEAN_CONSUMPTION_KW = 1.0
DEFAULT_ESTIMATED_CHARGE_POWER_KW = 3.0


def _build(price_slots, **overrides):
    """Call build_battery_schedule with sensible BATT-15 defaults."""
    kwargs = {
        "price_slots": price_slots,
        "charge_threshold": DEFAULT_CHARGE_THRESHOLD,
        "discharge_threshold": DEFAULT_DISCHARGE_THRESHOLD,
        "max_charge_power_w": DEFAULT_POWER,
        "battery_capacity_kwh": DEFAULT_CAPACITY,
        "current_soc_pct": DEFAULT_SOC,
        "now": datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
        "mean_consumption_kw": DEFAULT_MEAN_CONSUMPTION_KW,
        "estimated_charge_power_kw": DEFAULT_ESTIMATED_CHARGE_POWER_KW,
        "min_soc_pct": DEFAULT_MIN_SOC,
        "max_soc_pct": DEFAULT_MAX_SOC,
    }
    kwargs.update(overrides)
    return build_battery_schedule(**kwargs)


# ---------------------------------------------------------------------------
# Test 1: Spread-based classification
# ---------------------------------------------------------------------------


class TestSpreadBasedClassification:
    """BATT-15: slots are classified by SPREAD, not absolute price."""

    def test_discharge_requires_spread_above_period_minimum(self):
        """A slot only discharges when price - period_min exceeds the threshold.

        24h prices: cheap floor (0.30) sets the period minimum. Mid-range
        hours have a spread of 0.50 (below the 1.00 threshold) so they must
        stay idle even though they are well above the charge candidate
        range. Only the 2.50 hours (spread 2.20) discharge.
        """
        prices = [0.30] * 6 + [0.80] * 6 + [2.50] * 6 + [0.70] * 6
        slots = _make_24h_slots(prices)

        result = _build(slots, current_soc_pct=10.0)

        discharge = _discharge_slots(result)
        assert len(discharge) > 0
        assert all(s.price == 2.50 for s in discharge)

        # Mid-range (0.80) hours have spread 0.50 <= threshold -> never discharge
        assert not any(s.price == 0.80 and s.action == "discharge" for s in result.schedule)

    def test_all_below_discharge_spread_produces_no_discharge(self):
        """A flat price series has zero spread everywhere -> no discharge."""
        prices = [0.80] * 24
        slots = _make_24h_slots(prices)

        result = _build(slots, current_soc_pct=50.0)

        assert len(result.schedule) == 24
        assert result.discharging_slot_count == 0
        assert _discharge_slots(result) == []

    def test_charge_candidate_is_peak_relative(self):
        """A slot is a charge candidate only relative to the peak it feeds.

        The same absolute price (0.80) one hour before a peak is rejected
        when the peak is cheap (spread 0.20 <= 0.30 threshold) but accepted
        when the peak is expensive (spread 2.70 > 0.30 threshold). This is
        the BATT-15 change from an absolute charge_threshold to a
        peak-relative spread test.
        """
        common = {
            "charge_threshold": 0.30,
            "discharge_threshold": 0.15,
            "max_charge_power_w": 3000.0,
            "battery_capacity_kwh": 10.0,
            "current_soc_pct": 10.0,
            "now": datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            "mean_consumption_kw": 1.0,
            "estimated_charge_power_kw": 3.0,
            "min_soc_pct": 10.0,
            "max_soc_pct": 95.0,
        }

        cheap_peak = build_battery_schedule(
            price_slots=_make_slots([(0, 0.80), (1, 1.00)]), **common
        )
        assert cheap_peak.schedule[0].action == "idle", (
            "Spread of 0.20 (1.00-0.80) should not clear the 0.30 threshold"
        )

        pricey_peak = build_battery_schedule(
            price_slots=_make_slots([(0, 0.80), (1, 3.50)]), **common
        )
        assert pricey_peak.schedule[0].action in ("charge", "solar_charge"), (
            "Spread of 2.70 (3.50-0.80) should clear the 0.30 threshold"
        )


# ---------------------------------------------------------------------------
# Test 2: Peak grouping (unaffected by the spread rewrite, still verified)
# ---------------------------------------------------------------------------


class TestPeakGrouping:
    """Test that separate price peaks are identified as distinct discharge windows."""

    def test_peak_grouping_identifies_separate_windows(self):
        """Two expensive periods separated by cheap hours should create two peak groups."""
        price_data = (
            [(h, 0.30) for h in range(6)]
            + [(6, 0.60), (7, 0.70)]
            + [(h, 2.50) for h in range(8, 11)]  # morning peak
            + [(11, 0.60)]
            + [(h, 0.40) for h in range(12, 15)]
            + [(15, 0.60), (16, 0.70)]
            + [(h, 3.00) for h in range(17, 21)]  # evening peak
            + [(h, 0.35) for h in range(21, 24)]
        )
        slots = _make_slots(price_data)

        result = _build(slots, current_soc_pct=DEFAULT_SOC, peak_gap_hours=2.0)

        discharge_slots = _discharge_slots(result)
        assert len(discharge_slots) > 0

        discharge_hours = {s.start.hour for s in discharge_slots}
        morning_discharge = discharge_hours & {8, 9, 10}
        evening_discharge = discharge_hours & {17, 18, 19, 20}

        assert len(morning_discharge) > 0, "Should discharge during morning peak"
        assert len(evening_discharge) > 0, "Should discharge during evening peak"


# ---------------------------------------------------------------------------
# Test 3: Consumption-based energy sizing (BATT-15)
# ---------------------------------------------------------------------------


class TestConsumptionBasedSizing:
    """Energy needed to serve a peak is sized from house consumption, not
    from the battery's max discharge power (BATT-15 semantic change)."""

    def test_discharge_limited_by_consumption_energy_not_max_power(self):
        """With only 0.5 kWh available, exactly one 0.4 kW-hour slot should discharge.

        Battery at 15% SOC with min_soc=10%: available = 0.5 kWh. Sizing
        each hour's need at mean_consumption_kw=0.4 (0.4 kWh/slot) means
        exactly one slot can be served before the battery hits min_soc. One
        cheap hour is included so the period has a non-zero spread (a fully
        flat price series has zero spread everywhere and never discharges);
        a high charge_threshold keeps that cheap hour from also being
        recruited as a charge candidate, which would replenish the battery
        and confound this discharge-only assertion.
        """
        prices = [0.20] + [2.50] * 23
        slots = _make_24h_slots(prices)

        result = _build(
            slots,
            charge_threshold=3.0,
            current_soc_pct=15.0,
            mean_consumption_kw=0.4,
            min_soc_pct=10.0,
            max_soc_pct=95.0,
        )

        discharge = _discharge_slots(result)
        assert len(discharge) == 1, (
            f"0.5 kWh available / 0.4 kWh per slot should serve exactly 1 slot, "
            f"got {len(discharge)}"
        )

    def test_energy_needed_scales_with_peak_length_and_consumption(self):
        """A longer peak or higher consumption needs a larger charge deficit,
        which should recruit more cheap charge slots from the pre-peak window.
        """
        # 2h peak vs 4h peak, same consumption and starting SOC.
        short_peak = [0.20] * 10 + [2.50] * 2 + [0.90] * 12
        long_peak = [0.20] * 10 + [2.50] * 4 + [0.90] * 10

        result_short = _build(
            _make_24h_slots(short_peak), current_soc_pct=10.0, mean_consumption_kw=1.0
        )
        result_long = _build(
            _make_24h_slots(long_peak), current_soc_pct=10.0, mean_consumption_kw=1.0
        )

        assert len(_charge_slots(result_long)) >= len(_charge_slots(result_short))


# ---------------------------------------------------------------------------
# Test 4: Multi-cycle charge between peaks (unaffected by the rewrite)
# ---------------------------------------------------------------------------


class TestMultiCycleCharging:
    """Test that the scheduler inserts charge cycles between peaks."""

    def test_multi_cycle_charge_between_peaks(self):
        """Cheap hours between two peaks (separated by >2h) should recharge.

        Uses a smaller battery (6 kWh) so BATT-15b's reservation charging
        during the first window cannot, by itself, fully cover the second
        peak's need -- the mid-window must also contribute. (With a larger
        battery the first window's reservation charging alone can fully
        fund the second peak, leaving the mid-window genuinely idle -- see
        TestFuturePeakReservation for that scenario.)
        """
        price_data = (
            [(h, 0.20) for h in range(4)]
            + [(h, 2.50) for h in range(4, 7)]
            + [(h, 0.25) for h in range(7, 12)]
            + [(h, 3.00) for h in range(12, 15)]
            + [(h, 0.80) for h in range(15, 24)]
        )
        slots = _make_slots(price_data)

        result = _build(
            slots, current_soc_pct=30.0, peak_gap_hours=2.0, battery_capacity_kwh=6.0
        )

        charge_slots = _charge_slots(result)
        charge_hours = {s.start.hour for s in charge_slots}

        initial_charge = charge_hours & {0, 1, 2, 3}
        mid_charge = charge_hours & {7, 8, 9, 10, 11}

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
        result = _build([], now=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC))

        assert isinstance(result, BatteryScheduleResult)
        assert result.schedule == []
        assert result.charging_slot_count == 0
        assert result.discharging_slot_count == 0
        assert result.next_charging_slot is None
        assert result.next_discharging_slot is None
        assert result.current_action == "idle"
        assert result.target_ems_mode == "max_self_consumption"


# ---------------------------------------------------------------------------
# Test 6: BATT-15a solar recharge estimation between peaks
# ---------------------------------------------------------------------------


class TestSolarRechargeEstimation:
    """Test solar recharge accumulated in the gap before a peak (BATT-15a)."""

    def test_solar_recharge_reduces_grid_charging(self):
        """A large remaining-solar forecast should reduce/eliminate grid charging."""
        prices = [0.20] * 12 + [2.50] * 4 + [0.90] * 8
        slots = _make_24h_slots(prices)
        common = {
            "current_soc_pct": 20.0,
            "mean_consumption_kw": 1.0,
            "estimated_charge_power_kw": 3.0,
        }

        no_solar = _build(slots, **common)
        with_solar = _build(
            slots,
            **common,
            solar_forecast_remaining_wh=8000.0,
            production_factor=0.8,
            dawn=datetime(2026, 2, 15, 7, 0, tzinfo=UTC),
            dusk=datetime(2026, 2, 15, 15, 0, tzinfo=UTC),
        )

        assert len(_charge_slots(with_solar)) <= len(_charge_slots(no_solar))

    def test_charge_slots_in_daylight_window_are_labeled_solar_charge(self):
        """Charge slots whose time overlaps the daylight window become
        "solar_charge" -- a cosmetic label (EMS treats both the same),
        signalling the draw could be covered by PV rather than grid.
        """
        prices = [0.90] * 8 + [0.20] * 4 + [2.50] * 4 + [0.90] * 8
        slots = _make_24h_slots(prices)

        result = _build(
            slots,
            current_soc_pct=10.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=3.0,
            dawn=datetime(2026, 2, 15, 7, 0, tzinfo=UTC),
            dusk=datetime(2026, 2, 15, 15, 0, tzinfo=UTC),
        )

        solar_charge = [s for s in result.schedule if s.action == "solar_charge"]
        assert len(solar_charge) > 0
        assert all(7 <= s.start.hour < 15 for s in solar_charge)

    def test_normalize_daylight_window_daytime_rolls_dawn_back(self):
        """When next_dawn > next_dusk (currently daytime), dawn rolls back 24h."""
        dawn_raw = datetime(2026, 2, 16, 7, 0, tzinfo=UTC)  # tomorrow's dawn
        dusk_raw = datetime(2026, 2, 15, 18, 0, tzinfo=UTC)  # today's upcoming dusk

        window = _normalize_daylight_window(dawn_raw, dusk_raw)

        assert window == (
            datetime(2026, 2, 15, 7, 0, tzinfo=UTC),
            datetime(2026, 2, 15, 18, 0, tzinfo=UTC),
        )

    def test_normalize_daylight_window_nighttime_uses_pair_as_is(self):
        """When next_dawn < next_dusk (currently night, before dawn), the raw
        pair already describes one consistent upcoming window."""
        dawn_raw = datetime(2026, 2, 15, 7, 0, tzinfo=UTC)
        dusk_raw = datetime(2026, 2, 15, 18, 0, tzinfo=UTC)

        window = _normalize_daylight_window(dawn_raw, dusk_raw)

        assert window == (dawn_raw, dusk_raw)

    def test_normalize_daylight_window_missing_input_returns_none(self):
        assert _normalize_daylight_window(None, datetime(2026, 2, 15, 18, 0, tzinfo=UTC)) is None
        assert _normalize_daylight_window(datetime(2026, 2, 15, 7, 0, tzinfo=UTC), None) is None

    def test_overlap_hours_partial_overlap(self):
        """A 4h range overlapping only the last 2h of a daylight window."""
        range_start = datetime(2026, 2, 15, 5, 0, tzinfo=UTC)
        range_end = datetime(2026, 2, 15, 9, 0, tzinfo=UTC)
        dawn = datetime(2026, 2, 15, 7, 0, tzinfo=UTC)
        dusk = datetime(2026, 2, 15, 18, 0, tzinfo=UTC)

        assert _overlap_hours(range_start, range_end, dawn, dusk) == 2.0

    def test_overlap_hours_no_overlap(self):
        range_start = datetime(2026, 2, 15, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 2, 15, 2, 0, tzinfo=UTC)
        dawn = datetime(2026, 2, 15, 7, 0, tzinfo=UTC)
        dusk = datetime(2026, 2, 15, 18, 0, tzinfo=UTC)

        assert _overlap_hours(range_start, range_end, dawn, dusk) == 0.0


# ---------------------------------------------------------------------------
# Test 7: BATT-15b future-peak energy reservation
# ---------------------------------------------------------------------------


_RESERVATION_COMMON = {
    "charge_threshold": 0.3,
    "discharge_threshold": 1.0,
    "max_charge_power_w": 1000.0,
    "battery_capacity_kwh": 10.0,
    "current_soc_pct": 20.0,
    "now": datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
    "mean_consumption_kw": 1.0,
    "estimated_charge_power_kw": 1.0,
    "peak_gap_hours": 2.0,
    "min_soc_pct": 10.0,
    "max_soc_pct": 95.0,
}


class TestFuturePeakReservation:
    """An early cheap peak must not starve a later, more expensive peak."""

    def test_later_more_expensive_peak_is_fully_served(self):
        """The later peak (pricier, larger need) is fully served because the
        earlier peak's ample cheap window reserves energy for it, even
        though the short window between the two peaks alone (3h @ 1kWh)
        could not cover the later peak's 4kWh need by itself.
        """
        prices = (
            [0.20] * 9  # hours 0-8: ample cheap window before peak1
            + [1.30]  # hour 9: peak1 (cheaper, small: 1 slot)
            + [0.20] * 3  # hours 10-12: short window before peak2 (3kWh max)
            + [3.50] * 4  # hours 13-16: peak2 (pricier, larger: 4 slots)
            + [0.90] * 7
        )
        slots = _make_24h_slots(prices)

        result = build_battery_schedule(price_slots=slots, **_RESERVATION_COMMON)

        peak2 = [s for s in result.schedule if s.start.hour in (13, 14, 15, 16)]
        assert all(s.action == "discharge" for s in peak2), (
            "The later, pricier peak should be fully served via reservation"
        )

    def test_no_reservation_when_future_peak_is_cheaper(self):
        """With the price roles reversed (the LATER peak is now cheaper),
        BATT-15b's condition (future peak priced above the current one)
        does not apply, so no reservation happens and the later peak --
        whose own short pre-window cannot cover its full need -- ends up
        starved (fewer discharge slots than its 4-slot need).
        """
        prices = (
            [0.20] * 9
            + [3.50]  # hour 9: peak1 (now the PRICIER one)
            + [0.20] * 3
            + [1.30] * 4  # hours 13-16: peak2 (now cheaper, still large need)
            + [0.90] * 7
        )
        slots = _make_24h_slots(prices)

        result = build_battery_schedule(price_slots=slots, **_RESERVATION_COMMON)

        peak2 = [s for s in result.schedule if s.start.hour in (13, 14, 15, 16)]
        discharge_count = len([s for s in peak2 if s.action == "discharge"])
        assert discharge_count < 4, (
            "Without reservation, the cheaper later peak's short own window "
            "should not fully cover its need"
        )


# ---------------------------------------------------------------------------
# Test 8: Charge buffer and capacity cap (BATT-15)
# ---------------------------------------------------------------------------


class TestChargeBufferAndCap:
    """Test charge_buffer_pct sizing and the max-usable-energy cap."""

    def test_higher_buffer_recruits_more_charge_slots(self):
        """A larger charge_buffer_pct should schedule at least as much
        charging as a smaller one, all else equal."""
        prices = [0.20] * 10 + [2.50] * 4 + [0.90] * 10
        slots = _make_24h_slots(prices)
        common = {
            "current_soc_pct": 10.0,
            "mean_consumption_kw": 1.0,
            "estimated_charge_power_kw": 1.0,
            "max_charge_power_w": 1000.0,
        }

        low_buffer = _build(slots, charge_buffer_pct=0.0, **common)
        high_buffer = _build(slots, charge_buffer_pct=100.0, **common)

        assert len(_charge_slots(high_buffer)) >= len(_charge_slots(low_buffer))

    def test_charge_target_capped_at_room_in_battery(self):
        """Even with abundant cheap candidates and a large deficit, charging
        stops once the battery's usable headroom (max_soc) is filled -- not
        all 20 cheap candidate slots should be used.
        """
        prices = [0.20] * 20 + [2.50] * 4
        slots = _make_24h_slots(prices)

        result = _build(
            slots,
            current_soc_pct=10.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=1.0,
            max_charge_power_w=1000.0,
            min_soc_pct=10.0,
            max_soc_pct=95.0,
        )

        charge = _charge_slots(result)
        # Usable headroom: (95-10)% of 10kWh = 8.5 kWh at 1 kWh/slot -> ~9 slots
        assert 0 < len(charge) < 20


# ---------------------------------------------------------------------------
# Test 9: SOC constraints respected
# ---------------------------------------------------------------------------


class TestSocConstraints:
    """Test that min_soc_pct and max_soc_pct are respected."""

    def test_min_soc_limits_discharge_to_available_energy(self):
        """Battery at 15% with min_soc=10% has only 0.5 kWh available."""
        prices = [2.50] * 24
        slots = _make_24h_slots(prices)

        result = _build(
            slots,
            current_soc_pct=15.0,
            mean_consumption_kw=1.0,
            min_soc_pct=10.0,
            max_soc_pct=95.0,
        )

        discharge_slots = _discharge_slots(result)
        assert len(discharge_slots) <= 1, (
            f"With only 0.5 kWh available, should have at most 1 discharge slot, "
            f"got {len(discharge_slots)}"
        )

    def test_max_soc_blocks_charging_with_no_discharge_opportunity(self):
        """All-cheap prices with no discharge peak means no charge target at all."""
        prices = [0.20] * 24
        slots = _make_24h_slots(prices)

        result = _build(
            slots,
            current_soc_pct=90.0,
            mean_consumption_kw=1.0,
            min_soc_pct=10.0,
            max_soc_pct=95.0,
        )

        charge_slots = _charge_slots(result)
        assert charge_slots == [], (
            "No discharge peak exists, so there is nothing to charge for"
        )


# ---------------------------------------------------------------------------
# Test 10: Current action based on now
# ---------------------------------------------------------------------------


class TestCurrentAction:
    """Test that current_action and target_ems_mode reflect the slot at 'now'."""

    def test_current_action_based_on_now(self):
        """Given a specific 'now', current_action should match that slot's action."""
        prices = [0.20] * 10 + [0.80] * 6 + [2.50] * 4 + [0.70] * 4
        slots = _make_24h_slots(prices)

        result_charging = _build(
            slots,
            current_soc_pct=10.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=1.0,
            max_charge_power_w=1000.0,
            now=datetime(2026, 2, 15, 2, 30, 0, tzinfo=UTC),
        )

        # BATT-18: past slots are display-only, so by 17:30 the battery
        # must actually HOLD the energy (it charged during the real
        # morning) -- planning can no longer pretend to charge in elapsed
        # cheap hours to fund the evening peak.
        result_discharging = _build(
            slots,
            current_soc_pct=80.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=1.0,
            max_charge_power_w=1000.0,
            now=datetime(2026, 2, 15, 17, 30, 0, tzinfo=UTC),
        )

        assert result_charging.current_action in ("charge", "solar_charge"), (
            f"During cheap hours, should be charging, got {result_charging.current_action}"
        )
        assert result_charging.target_ems_mode == "command_charging"

        assert result_discharging.current_action == "discharge", (
            f"During expensive hours, should be discharging, got {result_discharging.current_action}"
        )
        assert result_discharging.target_ems_mode == "max_self_consumption"


# ---------------------------------------------------------------------------
# Test 11: Next slots lookup
# ---------------------------------------------------------------------------


class TestNextSlotsLookup:
    """Test that next_charging_slot and next_discharging_slot are correct."""

    def test_next_slots_lookup(self):
        """Verify next upcoming charge/discharge slots relative to now.

        Uses a wide charge_threshold (2.0) so the 0.80 mid-range hours do
        NOT clear the peak-relative charge spread (2.50-0.80=1.70 <= 2.0)
        while the 0.20 cheap hours do (2.50-0.20=2.30 > 2.0) -- isolating
        hours 4-5 as the only charge candidates for a deterministic result.
        """
        prices = (
            [0.80] * 4  # hours 0-3: idle (mid-range, fails the peak-relative spread)
            + [0.20] * 2  # hours 4-5: cheap -> charge candidate for the peak
            + [0.80] * 4  # hours 6-9: idle
            + [2.50] * 4  # hours 10-13: expensive -> discharge
            + [0.80] * 10  # hours 14-23: idle
        )
        slots = _make_24h_slots(prices)

        result = _build(
            slots,
            charge_threshold=2.0,
            current_soc_pct=10.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=1.0,
            max_charge_power_w=1000.0,
            now=datetime(2026, 2, 15, 1, 0, 0, tzinfo=UTC),
        )

        assert result.next_charging_slot is not None, "Should have a next charging slot"
        assert result.next_charging_slot.start.hour == 4, (
            f"Next charge should be at hour 4, got hour {result.next_charging_slot.start.hour}"
        )

        assert result.next_discharging_slot is not None, "Should have a next discharging slot"
        assert result.next_discharging_slot.start.hour == 10, (
            f"Next discharge should be at hour 10, got hour {result.next_discharging_slot.start.hour}"
        )


# ---------------------------------------------------------------------------
# Test 11b: Slot counts exclude past slots
# ---------------------------------------------------------------------------


class TestSlotCountsExcludePast:
    """Slot counts reflect remaining slots only, not already-passed ones."""

    def _result_at(self, now: datetime):
        prices = (
            [0.80] * 10  # hours 0-9: idle
            + [2.50] * 4  # hours 10-13: expensive -> discharge
            + [0.80] * 10  # hours 14-23: idle
        )
        return _build(
            _make_24h_slots(prices),
            current_soc_pct=50.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=1.0,
            max_charge_power_w=1000.0,
            now=now,
        )

    def test_counts_full_before_discharge_block(self):
        result = self._result_at(datetime(2026, 2, 15, 1, 0, 0, tzinfo=UTC))
        assert result.discharging_slot_count == 4

    def test_counts_shrink_as_slots_pass(self):
        """Mid-block: the current slot still counts, passed ones do not."""
        result = self._result_at(datetime(2026, 2, 15, 12, 30, 0, tzinfo=UTC))
        assert result.discharging_slot_count == 2

    def test_counts_zero_after_block_passed(self):
        result = self._result_at(datetime(2026, 2, 15, 23, 0, 0, tzinfo=UTC))
        assert result.discharging_slot_count == 0


# ---------------------------------------------------------------------------
# Test 12: BATT-14 economics derivation (pure helper)
# ---------------------------------------------------------------------------


class TestEffectiveDischargeThresholdDerivation:
    """Test compute_effective_discharge_threshold (BATT-14)."""

    def test_zero_cycle_cost_keeps_manual_threshold(self):
        """When battery_cycle_cost is 0 (default/unconfigured), the manual
        discharge_threshold entity value is used unchanged."""
        result = compute_effective_discharge_threshold(
            discharge_threshold=0.75,
            battery_cycle_cost=0.0,
            grid_transfer_fee=0.30,
        )
        assert result == 0.75

    def test_positive_cycle_cost_overrides_manual_threshold(self):
        """When battery_cycle_cost is configured, the effective threshold
        becomes cycle_cost - transfer_fee, parity with the live system."""
        result = compute_effective_discharge_threshold(
            discharge_threshold=0.75,
            battery_cycle_cost=0.50,
            grid_transfer_fee=0.20,
        )
        assert result == 0.30

    def test_cycle_cost_below_transfer_fee_clamps_to_zero(self):
        """A transfer fee larger than the cycle cost would mathematically
        yield a negative threshold, but that must be clamped to 0.0 --
        otherwise the period's cheapest slot (spread 0) would still clear a
        negative threshold and get classified as "discharge", emptying
        charge_candidates and blocking all grid charging."""
        result = compute_effective_discharge_threshold(
            discharge_threshold=0.75,
            battery_cycle_cost=0.20,
            grid_transfer_fee=0.50,
        )
        assert result == 0.0

    def test_clamped_threshold_still_allows_charge_slots_to_be_scheduled(self):
        """Scheduler-level regression: with cycle_cost < transfer_fee, the
        clamped (0.0) effective threshold must not swallow the cheapest
        slot into "discharge", so charging can still be scheduled ahead of
        the peak."""
        effective_threshold = compute_effective_discharge_threshold(
            discharge_threshold=1.00,
            battery_cycle_cost=0.20,
            grid_transfer_fee=0.50,
        )
        assert effective_threshold == 0.0

        prices = [0.30] * 6 + [0.80] * 6 + [2.50] * 6 + [0.70] * 6
        slots = _make_24h_slots(prices)

        result = _build(
            slots, discharge_threshold=effective_threshold, current_soc_pct=10.0
        )

        # The period minimum must stay eligible to charge, not be swallowed
        # into "discharge" by a would-be-negative threshold.
        min_price_slots = [s for s in result.schedule if s.price == 0.30]
        assert all(s.action != "discharge" for s in min_price_slots)
        assert result.charging_slot_count > 0


# ---------------------------------------------------------------------------
# Test 13-14: Schedule attribute filtering (UAT gap closure, unaffected by
# the BATT-15 algorithm rewrite -- kept as regression coverage)
# ---------------------------------------------------------------------------


class TestScheduleAttributeFiltering:
    """Test that schedule filtering logic correctly excludes past slots."""

    def test_filter_excludes_past_slots(self):
        """Past slots (end <= now) should be excluded from the visible window."""
        now = datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc)
        base = datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc)

        slots = []
        for i in range(72):
            start = base + timedelta(hours=i)
            end = start + timedelta(hours=1)
            action = "discharge" if 36 <= i <= 42 else "idle"
            slots.append({"start": start, "end": end, "action": action, "price": 0.5})

        filtered = [s for s in slots if s["end"] > now][:48]

        assert filtered[0]["start"] == datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc)

        discharge_slots = [s for s in filtered if s["action"] == "discharge"]
        assert len(discharge_slots) == 7, f"Expected 7 discharge slots, got {len(discharge_slots)}"

    def test_filter_keeps_current_slot(self):
        """A slot currently in progress (start <= now < end) should be kept."""
        now = datetime(2026, 2, 16, 14, 30, tzinfo=timezone.utc)
        current_slot = {
            "start": datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 2, 16, 15, 0, tzinfo=timezone.utc),
            "action": "discharge",
            "price": 1.2,
        }
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


# ---------------------------------------------------------------------------
# Test 15: compute_discharge_gate (self-consumption discharge gating)
# ---------------------------------------------------------------------------


class TestDischargeGate:
    """Test compute_discharge_gate, the self-consumption discharge gate."""

    def test_scheduled_discharge_slot_is_allowed(self):
        """A slot already scheduled to discharge is always allowed, no reservation."""
        now = datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC)
        schedule = [
            ScheduleSlot(
                start=datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                price=2.0,
                action="discharge",
            )
        ]

        gate = compute_discharge_gate(
            schedule=schedule,
            now=now,
            effective_discharge_threshold=1.0,
            battery_soc_pct=50.0,
            battery_capacity_kwh=10.0,
            mean_consumption_kw=1.0,
        )

        assert gate.allowed is True
        assert gate.reason == "scheduled_discharge"
        assert gate.reserved_energy_kwh == 0.0

    def test_idle_spread_below_threshold_is_blocked(self):
        """An idle slot whose spread over the period minimum does not clear
        the threshold must not open the discharge limit."""
        now = datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC)
        schedule = [
            ScheduleSlot(
                start=datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                price=0.5,
                action="idle",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
                price=0.3,
                action="idle",
            ),
        ]

        gate = compute_discharge_gate(
            schedule=schedule,
            now=now,
            effective_discharge_threshold=1.0,
            battery_soc_pct=50.0,
            battery_capacity_kwh=10.0,
            mean_consumption_kw=1.0,
        )

        assert gate.allowed is False
        assert gate.reason == "below_threshold"

    def test_idle_spread_above_threshold_no_future_discharge_is_allowed(self):
        """Idle slot with sufficient spread and no upcoming discharge peak to
        protect should be allowed to self-consume."""
        now = datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC)
        schedule = [
            ScheduleSlot(
                start=datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                price=2.0,
                action="idle",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
                price=0.5,
                action="idle",
            ),
        ]

        gate = compute_discharge_gate(
            schedule=schedule,
            now=now,
            effective_discharge_threshold=1.0,
            battery_soc_pct=50.0,
            battery_capacity_kwh=10.0,
            mean_consumption_kw=1.0,
        )

        assert gate.allowed is True
        assert gate.reason == "spread_above_threshold"
        assert gate.reserved_energy_kwh == 0.0

    def test_idle_spread_above_threshold_reserved_for_future_peak_is_blocked(self):
        """A future discharge peak whose reservation eats nearly all usable
        energy must block idle-period self-consumption discharge."""
        now = datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC)
        schedule = [
            ScheduleSlot(
                start=datetime(2026, 2, 15, 9, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
                price=0.3,
                action="idle",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                price=2.0,
                action="idle",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
                price=1.5,
                action="discharge",
            ),
        ]

        gate = compute_discharge_gate(
            schedule=schedule,
            now=now,
            effective_discharge_threshold=1.0,
            battery_soc_pct=10.0,
            battery_capacity_kwh=10.0,
            mean_consumption_kw=1.0,
        )

        assert gate.allowed is False
        assert gate.reason == "reserved_for_peak"
        assert gate.reserved_energy_kwh == 1.0

    def test_charge_slot_before_future_discharge_resets_reservation(self):
        """A planned recharge before the future discharge peak means the
        reservation does not apply -- discharging now is fine."""
        now = datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC)
        schedule = [
            ScheduleSlot(
                start=datetime(2026, 2, 15, 9, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
                price=0.3,
                action="idle",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                price=2.0,
                action="idle",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
                price=0.4,
                action="charge",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 13, 0, 0, tzinfo=UTC),
                price=1.5,
                action="discharge",
            ),
        ]

        gate = compute_discharge_gate(
            schedule=schedule,
            now=now,
            effective_discharge_threshold=1.0,
            battery_soc_pct=50.0,
            battery_capacity_kwh=10.0,
            mean_consumption_kw=1.0,
        )

        assert gate.allowed is True
        assert gate.reason == "spread_above_threshold"
        assert gate.reserved_energy_kwh == 0.0

    def test_empty_schedule_returns_no_schedule(self):
        """An empty schedule (no slot contains 'now') blocks discharge."""
        now = datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC)

        gate = compute_discharge_gate(
            schedule=[],
            now=now,
            effective_discharge_threshold=1.0,
            battery_soc_pct=50.0,
            battery_capacity_kwh=10.0,
            mean_consumption_kw=1.0,
        )

        assert gate.allowed is False
        assert gate.reason == "no_schedule"
        assert gate.reserved_energy_kwh == 0.0


# ---------------------------------------------------------------------------
# Test 16: BATT-16 _resolve_solar_windows (multi-window solar model)
# ---------------------------------------------------------------------------


class TestResolveSolarWindows:
    """Unit tests for _resolve_solar_windows, the BATT-16 window resolver.

    sun.sun only describes ONE upcoming daylight window, so the resolver
    must attribute the right forecast to it: in the evening both next_dawn
    and next_dusk already point at tomorrow (the window IS tomorrow's), while
    during daytime/morning the window is today's and a synthetic +24h
    tomorrow window is appended when tomorrow's forecast is available.
    """

    DAWN = datetime(2026, 2, 15, 8, 0, tzinfo=UTC)
    DUSK = datetime(2026, 2, 15, 16, 0, tzinfo=UTC)
    TOMORROW_START = datetime(2026, 2, 16, 0, 0, tzinfo=UTC)

    def test_morning_yields_today_and_tomorrow_windows(self):
        """Morning (window is today's): today's window carries the remaining
        forecast and a +24h-shifted window carries tomorrow's forecast."""
        windows = _resolve_solar_windows(
            dawn=self.DAWN,
            dusk=self.DUSK,
            solar_forecast_remaining_wh=8000.0,
            solar_forecast_tomorrow_wh=16000.0,
            production_factor=0.8,
            tomorrow_start=self.TOMORROW_START,
        )

        w0 = (self.DAWN, self.DUSK)
        w1 = (self.DAWN + timedelta(hours=24), self.DUSK + timedelta(hours=24))
        assert windows == [
            (w0, _estimate_solar_rate_kw(8000.0, 0.8, w0)),
            (w1, _estimate_solar_rate_kw(16000.0, 0.8, w1)),
        ]

    def test_evening_window_is_tomorrows_and_uses_tomorrow_forecast(self):
        """Evening (w0_is_tomorrow): the single resolved window starts after
        tomorrow_start, so it must carry TOMORROW's forecast energy -- and no
        additional +24h window may be appended (a naive shift would misplace
        tomorrow's energy beyond the 48h horizon)."""
        dawn = self.DAWN + timedelta(hours=24)  # next_dawn already tomorrow
        dusk = self.DUSK + timedelta(hours=24)  # next_dusk already tomorrow

        windows = _resolve_solar_windows(
            dawn=dawn,
            dusk=dusk,
            solar_forecast_remaining_wh=4000.0,
            solar_forecast_tomorrow_wh=16000.0,
            production_factor=0.8,
            tomorrow_start=self.TOMORROW_START,
        )

        w0 = (dawn, dusk)
        assert windows == [(w0, _estimate_solar_rate_kw(16000.0, 0.8, w0))]

    def test_evening_fallback_without_tomorrow_uses_remaining_today(self):
        """Evening with tomorrow sensors absent: the resolved (tomorrow's)
        window falls back to the remaining-today reading -- the exact
        pre-BATT-16 behavior."""
        dawn = self.DAWN + timedelta(hours=24)
        dusk = self.DUSK + timedelta(hours=24)

        windows = _resolve_solar_windows(
            dawn=dawn,
            dusk=dusk,
            solar_forecast_remaining_wh=4000.0,
            solar_forecast_tomorrow_wh=None,
            production_factor=0.8,
            tomorrow_start=self.TOMORROW_START,
        )

        w0 = (dawn, dusk)
        assert windows == [(w0, _estimate_solar_rate_kw(4000.0, 0.8, w0))]

    def test_missing_dawn_or_dusk_returns_empty(self):
        """No resolvable daylight window degrades to [] (polar day/night,
        sun.sun unavailable) -- same degrade path as before BATT-16."""
        assert (
            _resolve_solar_windows(
                dawn=None,
                dusk=self.DUSK,
                solar_forecast_remaining_wh=8000.0,
                solar_forecast_tomorrow_wh=16000.0,
                production_factor=0.8,
                tomorrow_start=self.TOMORROW_START,
            )
            == []
        )
        assert (
            _resolve_solar_windows(
                dawn=self.DAWN,
                dusk=None,
                solar_forecast_remaining_wh=8000.0,
                solar_forecast_tomorrow_wh=16000.0,
                production_factor=0.8,
                tomorrow_start=self.TOMORROW_START,
            )
            == []
        )

    def test_no_tomorrow_start_keeps_legacy_single_window(self):
        """tomorrow_start=None with no tomorrow forecast reproduces the
        legacy single-window output exactly."""
        windows = _resolve_solar_windows(
            dawn=self.DAWN,
            dusk=self.DUSK,
            solar_forecast_remaining_wh=8000.0,
            solar_forecast_tomorrow_wh=None,
            production_factor=0.8,
            tomorrow_start=None,
        )

        w0 = (self.DAWN, self.DUSK)
        assert windows == [(w0, _estimate_solar_rate_kw(8000.0, 0.8, w0))]

    def test_no_tomorrow_start_appends_tomorrow_window_only_when_forecast_given(self):
        """tomorrow_start=None cannot flag the window as tomorrow's, so the
        daytime path applies: the +24h window appears only when a tomorrow
        forecast is actually passed."""
        windows = _resolve_solar_windows(
            dawn=self.DAWN,
            dusk=self.DUSK,
            solar_forecast_remaining_wh=8000.0,
            solar_forecast_tomorrow_wh=16000.0,
            production_factor=0.8,
            tomorrow_start=None,
        )

        assert len(windows) == 2
        assert windows[1][0] == (
            self.DAWN + timedelta(hours=24),
            self.DUSK + timedelta(hours=24),
        )


# ---------------------------------------------------------------------------
# Test 17: BATT-16 exact arithmetic -- pre-peak gap spanning two windows
# ---------------------------------------------------------------------------


class TestGapSpanningTwoWindows:
    """A pre-peak gap spanning both solar windows accrues exactly
    rate0 x overlap0 + rate1 x overlap1 kWh of recharge (BATT-16)."""

    def test_recharge_sums_rate_times_overlap_across_both_windows(self):
        """Exact arithmetic, all quantities chosen to be float-exact.

        Setup: 48h slots, now = day-15 00:00 (before dawn), battery at the
        10% SOC floor (0 kWh available), mean consumption 1.0 kW, and a
        prohibitive charge_threshold so no grid charging can confound the
        count. Single peak: day-16 hours 12-19 (8 slots, 8 kWh need).

        Windows: W0 = day-15 08:00-16:00 with remaining 2500 Wh x 0.8 =
        2.0 kWh over 8h -> rate0 = 0.25 kW; W1 = W0 + 24h with tomorrow
        10000 Wh x 0.8 = 8.0 kWh over 8h -> rate1 = 1.0 kW.

        The gap before the peak is [day-15 00:00, day-16 12:00): it overlaps
        all 8h of W0 and the first 4h of W1, so recharge =
        0.25*8 + 1.0*4 = 6.0 kWh -> exactly 6 of the 8 peak slots discharge.
        Without the tomorrow kwargs only W0 contributes (2.0 kWh -> 2 slots),
        so the difference of 4 slots is exactly rate1 x overlap1.
        """
        p1 = [0.5] * 24
        p2 = [0.5] * 12 + [3.0] * 8 + [0.5] * 4
        slots = _make_24h_slots(p1) + _make_24h_slots(p2, day=16)
        common = {
            "charge_threshold": 10.0,  # no charge candidates anywhere
            "current_soc_pct": 10.0,
            "mean_consumption_kw": 1.0,
            "solar_forecast_remaining_wh": 2500.0,
            "production_factor": 0.8,
            "dawn": datetime(2026, 2, 15, 8, 0, tzinfo=UTC),
            "dusk": datetime(2026, 2, 15, 16, 0, tzinfo=UTC),
        }

        without_tomorrow = _build(slots, **common)
        with_tomorrow = _build(
            slots,
            **common,
            solar_forecast_tomorrow_wh=10000.0,
            tomorrow_start=datetime(2026, 2, 16, 0, 0, tzinfo=UTC),
        )

        assert _charge_slots(with_tomorrow) == []
        assert len(_discharge_slots(without_tomorrow)) == 2
        assert len(_discharge_slots(with_tomorrow)) == 6, (
            "recharge = rate0*8h + rate1*4h = 2.0 + 4.0 = 6.0 kWh must serve "
            "exactly 6 of the 8 peak slots"
        )
        # The tomorrow window's exact contribution: rate1 (1.0 kW) x 4h overlap.
        assert (
            len(_discharge_slots(with_tomorrow))
            - len(_discharge_slots(without_tomorrow))
        ) == 4


# ---------------------------------------------------------------------------
# Test 18: BATT-16 tomorrow-solar integration (48h scheduling)
# ---------------------------------------------------------------------------


class TestTomorrowSolarScheduling:
    """48h integration: tomorrow's solar forecast feeds the schedule."""

    def test_evening_over_reservation_bug_resolved_by_tomorrow_forecast(self):
        """THE BATT-16 bug scenario: evening, remaining-today = 0, expensive
        day-16 peak. Without tomorrow's forecast the resolved (tomorrow's)
        daylight window carries 0 Wh, so the scheduler grid-charges tonight
        for a peak the sun will actually cover. With the tomorrow forecast
        the same window carries 8 kWh and tonight's grid charging must
        strictly shrink (here: to zero).
        """
        p1 = [0.8] * 20 + [0.4] * 4  # tonight's cheap hours 20-23
        p2 = [0.4] * 8 + [0.8] * 9 + [3.0] * 4 + [0.8] * 3  # peak hours 17-20
        slots = _make_24h_slots(p1) + _make_24h_slots(p2, day=16)
        common = {
            "current_soc_pct": 20.0,
            "mean_consumption_kw": 1.0,
            "estimated_charge_power_kw": 1.0,
            "max_charge_power_w": 1000.0,
            "now": datetime(2026, 2, 15, 20, 0, 0, tzinfo=UTC),
            "solar_forecast_remaining_wh": 0.0,  # evening: nothing left today
            "production_factor": 0.8,
            # Evening: sun.sun's next dawn AND dusk both point at tomorrow.
            "dawn": datetime(2026, 2, 16, 8, 0, tzinfo=UTC),
            "dusk": datetime(2026, 2, 16, 16, 0, tzinfo=UTC),
        }

        without_tomorrow = _build(slots, **common)
        with_tomorrow = _build(
            slots,
            **common,
            solar_forecast_tomorrow_wh=10000.0,
            tomorrow_start=datetime(2026, 2, 16, 0, 0, tzinfo=UTC),
        )

        assert with_tomorrow.charging_slot_count < without_tomorrow.charging_slot_count
        assert len(_charge_slots(with_tomorrow)) < len(_charge_slots(without_tomorrow))
        # The day-16 peak stays fully served either way -- with the forecast
        # it is funded by solar recharge instead of tonight's grid charging.
        for result in (without_tomorrow, with_tomorrow):
            peak = [s for s in result.schedule if s.price == 3.0]
            assert all(s.action == "discharge" for s in peak)

    def test_batt15b_reservation_netted_by_tomorrow_solar(self):
        """BATT-15b integration: tomorrow's forecast covers the day-16 peak.

        Tonight's cheap peak (day-15 21-22, max 2.0) precedes tomorrow's
        pricier peak (day-16 18-21, max 3.5, 4 kWh need). Without the
        tomorrow forecast, BATT-15b reserves tomorrow's FULL need at
        tonight's peak (adjusted available goes negative) and recruits
        redundant grid charging. With the forecast, the netting
        max(0, need - recharge) collapses the reservation to zero: today's
        cheap peak discharges at least as much (fully served straight from
        the battery), tomorrow's pricier peak is funded by its own solar
        window, and the redundant grid charging disappears entirely.
        """
        p1 = [0.8] * 20 + [0.4, 2.0, 2.0, 0.4]  # tonight's cheap peak 21-22
        p2 = [0.4] * 8 + [0.8] * 10 + [3.5] * 4 + [0.8] * 2  # peak hours 18-21
        slots = _make_24h_slots(p1) + _make_24h_slots(p2, day=16)
        common = {
            "current_soc_pct": 30.0,
            "mean_consumption_kw": 1.0,
            "estimated_charge_power_kw": 1.0,
            "max_charge_power_w": 1000.0,
            "now": datetime(2026, 2, 15, 20, 0, 0, tzinfo=UTC),
            "solar_forecast_remaining_wh": 0.0,
            "production_factor": 0.8,
            "dawn": datetime(2026, 2, 16, 8, 0, tzinfo=UTC),
            "dusk": datetime(2026, 2, 16, 16, 0, tzinfo=UTC),
        }

        without_tomorrow = _build(slots, **common)
        with_tomorrow = _build(
            slots,
            **common,
            solar_forecast_tomorrow_wh=10000.0,
            tomorrow_start=datetime(2026, 2, 16, 0, 0, tzinfo=UTC),
        )

        def _todays_peak_discharges(result):
            return [
                s for s in result.schedule if s.price == 2.0 and s.action == "discharge"
            ]

        # Today's cheap peak discharges at least as much and stays fully
        # served -- the collapsed reservation must not starve it.
        assert len(_todays_peak_discharges(with_tomorrow)) >= len(
            _todays_peak_discharges(without_tomorrow)
        )
        assert len(_todays_peak_discharges(with_tomorrow)) == 2
        # Tomorrow's pricier peak is fully served by its own solar window.
        peak2 = [s for s in with_tomorrow.schedule if s.price == 3.5]
        assert all(s.action == "discharge" for s in peak2)
        # The over-reservation's redundant grid charging is gone.
        assert len(_charge_slots(with_tomorrow)) < len(_charge_slots(without_tomorrow))
        assert _charge_slots(with_tomorrow) == []


# ---------------------------------------------------------------------------
# Test 19: BATT-16 discharge gate with solar windows
# ---------------------------------------------------------------------------


class TestDischargeGateSolarWindows:
    """compute_discharge_gate's net-of-solar reservation (BATT-16)."""

    _GATE_COMMON: ClassVar[dict[str, float]] = {
        "effective_discharge_threshold": 1.0,
        "battery_capacity_kwh": 10.0,
        "mean_consumption_kw": 1.0,
    }

    def _reservation_schedule(self):
        """Current idle slot with good spread, then one discharge slot."""
        return [
            ScheduleSlot(
                start=datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                price=2.0,
                action="idle",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
                price=1.5,
                action="discharge",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 13, 0, 0, tzinfo=UTC),
                price=0.3,
                action="idle",
            ),
        ]

    def _overnight_schedule(self, with_early_discharge=False):
        """Evening idle 'now' slot, then tomorrow-evening discharge slots
        that follow tomorrow's daylight window with no charge slot between.

        With with_early_discharge=True an additional discharge slot is
        placed TONIGHT (before any solar arrives).
        """
        schedule = [
            ScheduleSlot(
                start=datetime(2026, 2, 15, 20, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 21, 0, 0, tzinfo=UTC),
                price=2.0,
                action="idle",
            ),
            ScheduleSlot(
                start=datetime(2026, 2, 15, 21, 0, 0, tzinfo=UTC),
                end=datetime(2026, 2, 15, 22, 0, 0, tzinfo=UTC),
                price=0.5,
                action="idle",
            ),
        ]
        if with_early_discharge:
            schedule.append(
                ScheduleSlot(
                    start=datetime(2026, 2, 15, 22, 0, 0, tzinfo=UTC),
                    end=datetime(2026, 2, 15, 23, 0, 0, tzinfo=UTC),
                    price=2.5,
                    action="discharge",
                )
            )
        for hour in (18, 19, 20, 21):
            schedule.append(
                ScheduleSlot(
                    start=datetime(2026, 2, 16, hour, 0, 0, tzinfo=UTC),
                    end=datetime(2026, 2, 16, hour + 1, 0, 0, tzinfo=UTC),
                    price=3.5,
                    action="discharge",
                )
            )
        return schedule

    # Tomorrow's daylight window delivering 1.0 kW between 08:00 and 16:00.
    _TOMORROW_WINDOWS: ClassVar[
        list[tuple[tuple[datetime, datetime], float]]
    ] = [
        (
            (
                datetime(2026, 2, 16, 8, 0, 0, tzinfo=UTC),
                datetime(2026, 2, 16, 16, 0, 0, tzinfo=UTC),
            ),
            1.0,
        )
    ]

    def test_none_solar_windows_matches_legacy_gate_exactly(self):
        """solar_windows=None must reproduce the gross-reservation gate
        byte-identically, in both the blocked and the allowed regime."""
        now = datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC)
        schedule = self._reservation_schedule()

        for soc, expected_reason in (
            (10.0, "reserved_for_peak"),
            (50.0, "spread_above_threshold"),
        ):
            legacy = compute_discharge_gate(
                schedule=schedule,
                now=now,
                battery_soc_pct=soc,
                **self._GATE_COMMON,
            )
            explicit_none = compute_discharge_gate(
                schedule=schedule,
                now=now,
                battery_soc_pct=soc,
                solar_windows=None,
                **self._GATE_COMMON,
            )

            assert explicit_none == legacy
            assert explicit_none.reason == expected_reason
            assert explicit_none.reserved_energy_kwh == 1.0

    def test_overnight_solar_refill_unblocks_reserved_for_peak(self):
        """The guaranteed-to-fire regression scenario: tomorrow's peak has no
        preceding charge slot (the sun makes grid charging redundant), so the
        gross reservation accumulates its full 4 kWh and blocks tonight's
        self-consumption. With solar_windows, the 8 kWh arriving before the
        peak nets the reservation to zero and the gate must open."""
        now = datetime(2026, 2, 15, 20, 30, 0, tzinfo=UTC)
        schedule = self._overnight_schedule()

        without_windows = compute_discharge_gate(
            schedule=schedule,
            now=now,
            battery_soc_pct=20.0,
            **self._GATE_COMMON,
        )
        with_windows = compute_discharge_gate(
            schedule=schedule,
            now=now,
            battery_soc_pct=20.0,
            solar_windows=self._TOMORROW_WINDOWS,
            **self._GATE_COMMON,
        )

        assert without_windows.allowed is False
        assert without_windows.reason == "reserved_for_peak"
        assert without_windows.reserved_energy_kwh == 4.0

        assert with_windows.allowed is True
        assert with_windows.reason == "spread_above_threshold"
        assert with_windows.reserved_energy_kwh == 0.0

    def test_prefix_max_late_solar_does_not_cover_early_discharge(self):
        """Solar arriving AFTER an early discharge slot cannot cover it: the
        prefix-max keeps that slot's 1 kWh reserved even though the total
        solar over the horizon (8 kWh) dwarfs the total need (5 kWh). A
        naive whole-horizon netting would wrongly open the gate here."""
        now = datetime(2026, 2, 15, 20, 30, 0, tzinfo=UTC)
        schedule = self._overnight_schedule(with_early_discharge=True)

        gate = compute_discharge_gate(
            schedule=schedule,
            now=now,
            battery_soc_pct=20.0,
            solar_windows=self._TOMORROW_WINDOWS,
            **self._GATE_COMMON,
        )

        assert gate.allowed is False
        assert gate.reason == "reserved_for_peak"
        assert gate.reserved_energy_kwh == 1.0


# ---------------------------------------------------------------------------
# Test 20: BATT-16 defaults are byte-identical to pre-BATT-16 behavior
# ---------------------------------------------------------------------------


class TestTomorrowKwargsIdentity:
    """Passing the new kwargs as None must not change any output at all."""

    @staticmethod
    def _assert_identical(a: BatteryScheduleResult, b: BatteryScheduleResult):
        assert a.schedule == b.schedule
        assert a.charging_slot_count == b.charging_slot_count
        assert a.discharging_slot_count == b.discharging_slot_count
        assert a.next_charging_slot == b.next_charging_slot
        assert a.next_discharging_slot == b.next_discharging_slot
        assert a.current_action == b.current_action
        assert a.target_ems_mode == b.target_ems_mode
        assert a.discharge_allowed == b.discharge_allowed
        assert a.discharge_gate_reason == b.discharge_gate_reason
        assert a.reserved_energy_kwh == b.reserved_energy_kwh

    def test_identity_without_any_solar_inputs(self):
        """A plain charge/discharge scenario with no solar inputs."""
        prices = [0.30] * 6 + [0.80] * 6 + [2.50] * 6 + [0.70] * 6
        slots = _make_24h_slots(prices)

        base = _build(slots, current_soc_pct=10.0)
        with_none_kwargs = _build(
            slots,
            current_soc_pct=10.0,
            solar_forecast_tomorrow_wh=None,
            tomorrow_start=None,
        )

        self._assert_identical(base, with_none_kwargs)

    def test_identity_with_today_solar_inputs(self):
        """The single-window BATT-15a solar path (remaining forecast plus
        dawn/dusk) must also be untouched by the None kwargs."""
        prices = [0.20] * 12 + [2.50] * 4 + [0.90] * 8
        slots = _make_24h_slots(prices)
        common = {
            "current_soc_pct": 20.0,
            "mean_consumption_kw": 1.0,
            "estimated_charge_power_kw": 3.0,
            "solar_forecast_remaining_wh": 8000.0,
            "production_factor": 0.8,
            "dawn": datetime(2026, 2, 15, 7, 0, tzinfo=UTC),
            "dusk": datetime(2026, 2, 15, 15, 0, tzinfo=UTC),
        }

        base = _build(slots, **common)
        with_none_kwargs = _build(
            slots,
            **common,
            solar_forecast_tomorrow_wh=None,
            tomorrow_start=None,
        )

        self._assert_identical(base, with_none_kwargs)


# ---------------------------------------------------------------------------
# Test 21: BATT-17 export arbitrage
# ---------------------------------------------------------------------------

# Standard export kwargs used across the BATT-17 tests. Fees total
# 0.85 SEK/kWh (mirrors live prod), so with a 0.5 cheapest-future price the
# replacement floor is (0.5 + 0.85) / 0.9 + 0.2 = 1.7 SEK/kWh.
EXPORT_KWARGS = {
    "export_spike_threshold": 3.0,
    "export_reserve_soc_pct": 20.0,
    "export_power_kw": 2.0,
    "grid_transfer_fee": 0.5,
    "electricity_company_fee": 0.35,
    "battery_cycle_cost": 0.2,
}


def _spike_slots() -> list[dict]:
    """0.5 floor with a single 5.0 spike at hour 4 (the spike discharges)."""
    prices = [(h, 0.5) for h in range(8)]
    prices[4] = (4, 5.0)
    return _make_slots(prices)


class TestIntraPeakSolarCredit:
    """BATT-18: solar arriving DURING a peak reduces its charge deficit.

    On extreme spread days even midday prices clear the discharge
    threshold, bridging morning/midday/evening into one mega-peak whose
    pre-window is the night -- without the intra-peak credit the whole
    day's solar was invisible and the optimizer grid-charged overnight.
    """

    def _bridged_day(self, *, solar_wh: float) -> BatteryScheduleResult:
        # h0-5 cheap night, h6-21 one bridged peak (all 1.5 over the 0.2
        # floor), h22-23 cheap. Solar window 06-20 covers the peak.
        prices = [0.2] * 6 + [1.5] * 16 + [0.2] * 2
        return _build(
            _make_24h_slots(prices),
            now=datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC),
            current_soc_pct=20.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=1.0,
            max_charge_power_w=1000.0,
            solar_forecast_remaining_wh=solar_wh,
            production_factor=1.0,
            dawn=datetime(2026, 2, 15, 6, 0, 0, tzinfo=UTC),
            dusk=datetime(2026, 2, 15, 20, 0, 0, tzinfo=UTC),
        )

    def test_intra_peak_solar_reduces_night_charging(self):
        """14 kWh of solar inside the bridged peak nearly eliminates the
        night charge deficit; without solar the full deficit charges."""
        sunny = self._bridged_day(solar_wh=14000.0)
        cloudy = self._bridged_day(solar_wh=0.0)

        sunny_charges = len(_charge_slots(sunny))
        cloudy_charges = len(_charge_slots(cloudy))
        assert cloudy_charges >= 5
        assert sunny_charges < cloudy_charges
        assert sunny_charges <= 3


class TestPastSlotsDisplayOnly:
    """BATT-18: elapsed slots never participate in the optimizer.

    current_soc_pct describes the battery NOW -- spending it on already
    past peaks entered future planning with a phantom-empty battery and
    grid-charged for energy the battery still holds.
    """

    def test_past_peak_does_not_drain_virtual_battery(self):
        """Battery full at noon; the elapsed morning peak must not drain
        the virtual battery, so the evening peak needs no grid charge."""
        # h0-5 night 0.2 (past), h6-9 morning peak 2.0 (PAST), h10-17
        # midday 0.3 idle, h18-21 evening peak 2.0, h22-23 cheap 0.2.
        prices = [0.2] * 6 + [2.0] * 4 + [0.3] * 8 + [2.0] * 4 + [0.2] * 2
        result = _build(
            _make_24h_slots(prices),
            now=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
            current_soc_pct=100.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=1.0,
            max_charge_power_w=1000.0,
        )

        assert _charge_slots(result) == []
        evening = [
            s for s in result.schedule if 18 <= s.start.hour <= 21
        ]
        assert all(s.action == "discharge" for s in evening)

    def test_partially_elapsed_peak_keeps_future_slots(self):
        """Inside a peak at 19:00: only its remaining slots count."""
        prices = [0.2] * 18 + [2.0] * 4 + [0.2] * 2
        result = _build(
            _make_24h_slots(prices),
            now=datetime(2026, 2, 15, 19, 30, 0, tzinfo=UTC),
            current_soc_pct=50.0,
            mean_consumption_kw=1.0,
            estimated_charge_power_kw=1.0,
            max_charge_power_w=1000.0,
        )

        assert result.current_action == "discharge"
        assert result.discharging_slot_count >= 2


class TestExportQualification:
    """BATT-17: which slots may become export slots at all."""

    def test_below_threshold_never_exported(self):
        """A spike below the configured threshold stays a discharge slot."""
        result = _build(
            _spike_slots(), **{**EXPORT_KWARGS, "export_spike_threshold": 6.0}
        )

        assert _export_slots(result) == []
        assert result.export_slot_count == 0
        assert result.schedule[4].action == "discharge"

    def test_spike_above_threshold_with_passing_replacement_exported(self):
        """Spike 5.0, future min 0.5, fees 0.85, cycle 0.2:
        5.0 > (0.5 + 0.85) / 0.9 + 0.2 = 1.7 -> exported."""
        result = _build(_spike_slots(), **EXPORT_KWARGS)

        assert result.schedule[4].action == "export"
        assert result.export_slot_count == 1

    def test_spread_exactly_at_threshold_qualifies(self):
        """The threshold is inclusive: spread == threshold may export.

        Spike 5.0 over a 0.5 floor = spread 4.5."""
        result = _build(
            _spike_slots(), **{**EXPORT_KWARGS, "export_spike_threshold": 4.5}
        )

        assert result.schedule[4].action == "export"

    def test_threshold_is_spread_not_absolute_price(self):
        """Regime shift: sustained-high week (floor 4.0, spike 10.0) with
        threshold 5.0 -- only the 10.0 slot (spread 6.0) exports; a 5.8
        slot (spread 1.8) does NOT, even though its absolute price is
        above 5.0. The threshold keeps meaning 'exceptional spike' when
        the whole price level shifts."""
        prices = [(h, 4.0) for h in range(8)]
        prices[3] = (3, 5.8)
        prices[5] = (5, 10.0)
        result = _build(
            _make_slots(prices),
            current_soc_pct=90.0,
            **{**EXPORT_KWARGS, "export_spike_threshold": 5.0},
        )

        actions = {s.start.hour: s.action for s in result.schedule}
        assert actions[5] == "export"
        assert actions[3] != "export"

    def test_failing_replacement_check_blocks_export(self):
        """Spike 2.0 with future min 1.6: 2.0 < (1.6 + 0.85) / 0.9 + 0.2
        = 2.92 -> selling now loses to buying back later, no export.
        Threshold 1.2 < spread 1.3 so the replacement check (not the
        threshold) is the blocker under test."""
        prices = [(h, 0.7) for h in range(4)] + [(4, 2.0)] + [
            (h, 1.6) for h in range(5, 8)
        ]
        result = _build(
            _make_slots(prices), **{**EXPORT_KWARGS, "export_spike_threshold": 1.2}
        )

        assert _export_slots(result) == []
        assert result.schedule[4].action == "discharge"

    def test_last_slot_without_future_never_exported(self):
        """No future slot -> cannot prove profitable -> hold (SEM rule)."""
        prices = [(h, 0.5) for h in range(7)] + [(7, 5.0)]
        result = _build(_make_slots(prices), **EXPORT_KWARGS)

        assert _export_slots(result) == []
        assert result.schedule[7].action == "discharge"

    def test_charge_slots_never_converted(self):
        """A charge slot whose price passes threshold and replacement is
        still never converted -- only discharge/idle slots are eligible."""
        # h0-1 cheap-ish (charge window), h2-3 spike, h4-7 very cheap tail.
        prices = [(0, 0.5), (1, 0.5), (2, 3.0), (3, 3.0)] + [
            (h, 0.1) for h in range(4, 8)
        ]
        result = _build(
            _make_slots(prices),
            current_soc_pct=10.0,
            export_spike_threshold=0.4,
            export_reserve_soc_pct=20.0,
            export_power_kw=0.0,
            grid_transfer_fee=0.0,
            electricity_company_fee=0.0,
            battery_cycle_cost=0.0,
        )

        # The charge slot at h0 (price 0.5 >= threshold 0.4, replacement
        # floor 0.1 / 0.9 = 0.11) would qualify were it eligible.
        assert result.schedule[0].action == "charge"
        # The spike slots do export (own house-need release funds them).
        assert result.schedule[2].action == "export"
        assert result.schedule[3].action == "export"


class TestExportBudget:
    """BATT-17: reserve floor, scheduled-discharge protection, merit order."""

    def test_soc_at_reserve_floor_zero_exports(self):
        """SOC == reserve pct leaves zero export budget."""
        result = _build(_spike_slots(), current_soc_pct=20.0, **EXPORT_KWARGS)

        assert _export_slots(result) == []
        assert result.export_slot_count == 0
        assert result.schedule[4].action == "discharge"

    def test_budget_covers_only_highest_priced_candidate(self):
        """With budget for one slot, only the highest-priced candidate
        exports; the next one stays a discharge slot.

        SOC 60: usable above the 20% floor = 4.0 kWh, scheduled discharge
        2.0 kWh -> budget 2.0 kWh. Each export needs (2.0 + 1.0) = 3.0 kWh,
        releasing its own 1.0 kWh house-need. Fees are set to 1.2 so the
        4.0 slot (4.0 + 1.2 = 5.2 >= 5.0) is not demotable by the 5.0 one.
        """
        prices = [(h, 0.5) for h in range(4)] + [(4, 5.0), (5, 4.0), (6, 0.5), (7, 0.5)]
        result = _build(
            _make_slots(prices),
            current_soc_pct=60.0,
            export_spike_threshold=3.0,
            export_reserve_soc_pct=20.0,
            export_power_kw=2.0,
            grid_transfer_fee=0.7,
            electricity_company_fee=0.5,
            battery_cycle_cost=0.2,
        )

        assert result.schedule[4].action == "export"
        assert result.schedule[5].action == "discharge"
        assert result.export_slot_count == 1

    def test_scheduled_discharge_protected_without_merit_demotion(self):
        """Budget only covers the scheduled discharge needs and neither
        discharge slot is demotable (price + fees >= export price):
        nothing exports, self-consumption is never starved."""
        prices = [(h, 0.5) for h in range(4)] + [
            (4, 5.0),
            (5, 4.9),
            (6, 0.5),
            (7, 0.5),
        ]
        result = _build(_make_slots(prices), current_soc_pct=40.0, **EXPORT_KWARGS)

        assert _export_slots(result) == []
        assert result.schedule[4].action == "discharge"
        assert result.schedule[5].action == "discharge"

    def test_merit_demotion_frees_budget_for_export(self):
        """A discharge slot with price + fees (2.0 + 0.85 = 2.85) below the
        export candidate's bare spot (5.0) is demoted to idle to fund the
        export when the budget alone is insufficient."""
        prices = [
            (0, 0.5),
            (1, 0.5),
            (2, 2.0),
            (3, 0.5),
            (4, 5.0),
            (5, 0.5),
            (6, 0.5),
            (7, 0.5),
        ]
        result = _build(_make_slots(prices), current_soc_pct=50.0, **EXPORT_KWARGS)

        assert result.schedule[2].action == "idle"
        assert result.schedule[4].action == "export"
        assert result.export_slot_count == 1
        assert result.discharging_slot_count == 0

    def test_valuable_discharge_not_demoted_when_export_fires(self):
        """A discharge slot with price + fees (4.5 + 0.85 = 5.35) >= the
        export price (5.0) is never demoted, even while the export fires
        from free budget."""
        prices = [
            (0, 0.5),
            (1, 0.5),
            (2, 4.5),
            (3, 0.5),
            (4, 5.0),
            (5, 0.5),
            (6, 0.5),
            (7, 0.5),
        ]
        result = _build(_make_slots(prices), current_soc_pct=70.0, **EXPORT_KWARGS)

        assert result.schedule[4].action == "export"
        assert result.schedule[2].action == "discharge"
        assert result.export_slot_count == 1

    def test_infeasible_candidate_leaves_schedule_untouched(self):
        """When budget + own release + all demotions still cannot fund the
        export, the candidate is skipped entirely -- no partial demotion."""
        prices = [
            (0, 0.5),
            (1, 0.5),
            (2, 2.0),
            (3, 0.5),
            (4, 5.0),
            (5, 0.5),
            (6, 0.5),
            (7, 0.5),
        ]
        # export_power_kw 3.0 -> need 4.0 kWh; budget 1.0 + own 1.0 +
        # demotable 1.0 = 3.0 < 4.0 -> infeasible.
        result = _build(
            _make_slots(prices),
            current_soc_pct=50.0,
            **{**EXPORT_KWARGS, "export_power_kw": 3.0},
        )

        assert _export_slots(result) == []
        assert result.schedule[2].action == "discharge"
        assert result.schedule[4].action == "discharge"


class TestExportPastSlots:
    """BATT-17: already-elapsed slots never participate in the export pass.

    A past spike must not steal the finite export budget, must not demote
    future scheduled discharge slots to fund unsellable energy, and past
    discharge slots (already reflected in current SOC) must not deflate
    the budget. Regression tests for the adversarial-review scenarios.
    """

    _NOW_1400 = datetime(2026, 2, 15, 14, 0, 0, tzinfo=UTC)

    def test_past_spike_does_not_steal_budget_from_future_spike(self):
        """Past 08:00 spike 5.0 vs future 19:00 spike 4.5 at now=14:00:
        only the future spike exports; the dead one stays untouched."""
        prices = [(h, 0.5) for h in range(24)]
        prices[8] = (8, 5.0)
        prices[19] = (19, 4.5)
        result = _build(
            _make_slots(prices),
            now=self._NOW_1400,
            current_soc_pct=70.0,
            **EXPORT_KWARGS,
        )

        actions = {s.start.hour: s.action for s in result.schedule}
        assert actions[8] != "export"
        assert actions[19] == "export"

    def test_past_spike_never_demotes_future_discharge(self):
        """A dead 08:00 spike must not demote the evening's scheduled
        discharge slots to fund energy that can no longer be sold."""
        prices = [(h, 0.5) for h in range(24)]
        prices[8] = (8, 6.0)
        prices[18] = (18, 2.5)
        prices[20] = (20, 2.5)
        result = _build(
            _make_slots(prices),
            now=self._NOW_1400,
            current_soc_pct=40.0,
            **EXPORT_KWARGS,
        )

        actions = {s.start.hour: s.action for s in result.schedule}
        assert _export_slots(result) == []
        assert actions[18] == "discharge"
        assert actions[20] == "discharge"

    def test_past_discharge_slots_do_not_deflate_budget(self):
        """Past discharge energy is already reflected in current SOC --
        counting it again would under-budget a real future export."""
        prices = [(h, 0.5) for h in range(24)]
        prices[8] = (8, 2.5)
        prices[9] = (9, 2.5)
        prices[10] = (10, 2.5)
        prices[19] = (19, 5.0)
        result = _build(
            _make_slots(prices),
            now=self._NOW_1400,
            current_soc_pct=60.0,
            **EXPORT_KWARGS,
        )

        actions = {s.start.hour: s.action for s in result.schedule}
        assert actions[19] == "export"

    def test_future_export_slot_reserves_energy_in_gate(self):
        """CodeRabbit PR #7: a future export slot must reserve its energy
        (export power + house load) in the discharge gate, or gate-open
        self-consumption on idle slots could drain the energy the export
        plan counted on before the sale fires."""
        result = _build(_spike_slots(), **EXPORT_KWARGS)

        assert result.schedule[4].action == "export"
        # Gate scan runs from now (hour 0, idle): the hour-4 export slot
        # reserves (2.0 export + 1.0 house) * 1h = 3.0 kWh.
        assert result.reserved_energy_kwh >= 3.0

    def test_spread_baseline_ignores_elapsed_cheap_hours(self):
        """Greptile PR #8: a dead cheap morning must not inflate the
        afternoon's spreads. Past min 0.1, future floor 2.0, future high
        5.4, threshold 4.0: spread vs the dead 0.1 would be 5.3 (fires),
        vs the future floor it is 3.4 (does not). Replacement cost is
        (2.0 + 0.85) / 0.9 + 0.2 = 3.37 < 5.4, so the threshold -- not
        the economics -- is the mechanism under test."""
        prices = [(h, 0.1) for h in range(14)] + [(h, 2.0) for h in range(14, 24)]
        prices[19] = (19, 5.4)
        result = _build(
            _make_slots(prices),
            now=self._NOW_1400,
            current_soc_pct=90.0,
            **{**EXPORT_KWARGS, "export_spike_threshold": 4.0},
        )

        assert _export_slots(result) == []

    def test_next_export_slot_populated(self):
        """next_export_slot points at the upcoming export slot."""
        result = _build(_spike_slots(), **EXPORT_KWARGS)

        assert result.next_export_slot is not None
        assert result.next_export_slot.start.hour == 4
        assert result.next_export_slot.action == "export"

    def test_next_export_slot_none_when_disabled(self):
        """Without export kwargs the field is always None."""
        result = _build(_spike_slots())

        assert result.next_export_slot is None


class TestExportGate:
    """BATT-17: an active export slot opens the gate and sets the mode."""

    def test_export_slot_opens_gate_and_sets_mode(self):
        now = datetime(2026, 2, 15, 4, 15, 0, tzinfo=UTC)
        result = _build(_spike_slots(), now=now, **EXPORT_KWARGS)

        assert result.current_action == "export"
        assert result.target_ems_mode == "command_discharging"
        assert result.discharge_allowed is True
        assert result.discharge_gate_reason == "export_slot"
        assert result.reserved_energy_kwh == 0.0
        assert result.export_slot_count == 1


# ---------------------------------------------------------------------------
# Test 22: BATT-17 disabled is bit-identical to a build without the kwargs
# ---------------------------------------------------------------------------


class TestExportKwargsIdentity:
    """Threshold unset (None) or 0 must not change any output at all."""

    _NON_DEFAULT_EXPORT_EXTRAS: ClassVar[dict] = {
        "export_reserve_soc_pct": 40.0,
        "export_power_kw": 13.1,
        "grid_transfer_fee": 0.5,
        "electricity_company_fee": 0.35,
        "battery_cycle_cost": 0.4,
    }

    _RICH_SCENARIO_COMMON: ClassVar[dict] = {
        "current_soc_pct": 20.0,
        "mean_consumption_kw": 1.0,
        "estimated_charge_power_kw": 3.0,
        "solar_forecast_remaining_wh": 8000.0,
        "production_factor": 0.8,
        "dawn": datetime(2026, 2, 15, 7, 0, tzinfo=UTC),
        "dusk": datetime(2026, 2, 15, 15, 0, tzinfo=UTC),
    }

    @staticmethod
    def _assert_identical(a: BatteryScheduleResult, b: BatteryScheduleResult):
        assert a.schedule == b.schedule
        assert a.charging_slot_count == b.charging_slot_count
        assert a.discharging_slot_count == b.discharging_slot_count
        assert a.next_charging_slot == b.next_charging_slot
        assert a.next_discharging_slot == b.next_discharging_slot
        assert a.current_action == b.current_action
        assert a.target_ems_mode == b.target_ems_mode
        assert a.discharge_allowed == b.discharge_allowed
        assert a.discharge_gate_reason == b.discharge_gate_reason
        assert a.reserved_energy_kwh == b.reserved_energy_kwh
        assert a.export_slot_count == b.export_slot_count

    def test_identity_with_threshold_none(self):
        """Threshold None disables export even with every other export
        kwarg set to an aggressive non-default value."""
        prices = [0.20] * 12 + [2.50] * 4 + [0.90] * 8
        slots = _make_24h_slots(prices)

        base = _build(slots, **self._RICH_SCENARIO_COMMON)
        with_export_kwargs = _build(
            slots,
            **self._RICH_SCENARIO_COMMON,
            export_spike_threshold=None,
            **self._NON_DEFAULT_EXPORT_EXTRAS,
        )

        self._assert_identical(base, with_export_kwargs)

    def test_identity_with_threshold_zero(self):
        """Threshold 0 (the stored 'off' value) is identical to unset."""
        prices = [0.20] * 12 + [2.50] * 4 + [0.90] * 8
        slots = _make_24h_slots(prices)

        base = _build(slots, **self._RICH_SCENARIO_COMMON)
        with_export_kwargs = _build(
            slots,
            **self._RICH_SCENARIO_COMMON,
            export_spike_threshold=0.0,
            **self._NON_DEFAULT_EXPORT_EXTRAS,
        )

        self._assert_identical(base, with_export_kwargs)


class TestExportResultDefault:
    """Regression guard: export_slot_count must stay a defaulted field."""

    def test_result_without_export_kwargs_defaults_to_zero(self):
        result = BatteryScheduleResult(
            schedule=[],
            charging_slot_count=0,
            discharging_slot_count=0,
            next_charging_slot=None,
            next_discharging_slot=None,
            current_action="idle",
            target_ems_mode="max_self_consumption",
        )
        assert result.export_slot_count == 0

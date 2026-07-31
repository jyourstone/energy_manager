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

from custom_components.energy_manager.battery_scheduler import (
    BatteryScheduleResult,
    ScheduleSlot,
    _normalize_daylight_window,
    _overlap_hours,
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

        result_discharging = _build(
            slots,
            current_soc_pct=10.0,
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

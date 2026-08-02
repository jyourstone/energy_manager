"""Pure-Python battery scheduling algorithm with zero Home Assistant dependencies.

Implements multi-cycle charge/discharge scheduling using peak grouping and
virtual energy tracking. Ported from the proven AppDaemon HomeBatteryManager,
including the March 2026 algorithm refinements (BATT-15).

This module is intentionally free of any HA imports so it can be thoroughly
unit-tested independently.

Algorithm overview (BATT-15 -- SPREAD-based, house-consumption-sized):
    1. Classify discharge candidates by SPREAD against the period's minimum
       price: a slot is profitable discharge when
       ``price - min_price > discharge_threshold``. Charge candidates are
       peak-relative (a slot is worth charging for a given peak when
       ``peak_max_price - price > charge_threshold``) and are therefore only
       evaluated while processing that peak, not up front.
    2. Group discharge candidates into peaks separated by configurable gaps.
    3. Energy needed to serve a peak is sized from house consumption
       (``len(peak_slots) * mean_consumption_kw * slot_duration``), not from
       the battery's max discharge power -- the battery discharges to cover
       house load, it does not export at full inverter power.
    4. Virtual energy tracking simulates the battery through time: solar
       recharge accumulated in the gap before each peak is added first
       (BATT-15a), future more-expensive peaks reserve energy net of their
       own upcoming recharge so an early cheap peak cannot starve a later,
       pricier one (BATT-15b), then a cheapest-first charge deficit (with
       buffer) is scheduled, and finally discharge is marked
       most-expensive-first while consumption energy remains.
    5. Charge slots that fall within a resolved solar window (today's, and
       tomorrow's when a tomorrow forecast is available -- BATT-16) are
       relabeled "solar_charge" -- a cosmetic distinction only (EMS treats
       both the same way) communicating that the draw could be covered by PV.
    6. BATT-17 export arbitrage post-pass (opt-in, off by default): when an
       export spike threshold is configured, qualifying spike slots are
       relabeled "export" after optimization -- funded by a reserve-floored
       energy budget and a unified merit order that may demote cheaper
       discharge slots to idle.
    7. Derive current action and EMS mode from the slot containing 'now'.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# BATT-17: assumed battery round-trip efficiency for the export
# replacement-cost check. Deliberately a constant, not config.
DEFAULT_ROUNDTRIP_EFFICIENCY = 0.9

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleSlot:
    """A single time slot in the battery schedule.

    Attributes:
        start: UTC-aware start time.
        end: UTC-aware end time.
        price: Electricity price in SEK/kWh.
        action: One of "charge", "discharge", "idle", "solar_charge",
            "export".
    """

    start: datetime
    end: datetime
    price: float
    action: str


@dataclass
class BatteryScheduleResult:
    """Result of the battery schedule calculation.

    Attributes:
        schedule: Ordered list of ScheduleSlot objects covering the full period.
        charging_slot_count: Number of remaining charge or solar_charge slots
            (slots that have not yet ended, including the current slot).
        discharging_slot_count: Number of remaining discharge slots (slots
            that have not yet ended, including the current slot).
        next_charging_slot: Next upcoming charge/solar_charge slot relative to now.
        next_discharging_slot: Next upcoming discharge slot relative to now.
        current_action: Action for the slot containing 'now'.
        target_ems_mode: EMS mode string for Phase 3 consumption.
        discharge_allowed: Whether self-consumption discharge is currently
            allowed (see compute_discharge_gate).
        discharge_gate_reason: Machine-readable reason for the discharge
            gate's decision.
        reserved_energy_kwh: Energy earmarked for upcoming scheduled
            discharge slots (before the next charge slot), net of the solar
            energy expected to arrive before them (BATT-16).
        export_slot_count: Number of remaining export slots (0 unless
            BATT-17 export arbitrage is enabled).
        next_export_slot: Next upcoming export slot relative to now, or
            None (always None unless BATT-17 export arbitrage is enabled).
    """

    schedule: list[ScheduleSlot]
    charging_slot_count: int
    discharging_slot_count: int
    next_charging_slot: ScheduleSlot | None
    next_discharging_slot: ScheduleSlot | None
    current_action: str
    target_ems_mode: str
    discharge_allowed: bool = True
    discharge_gate_reason: str = "scheduled_discharge"
    reserved_energy_kwh: float = 0.0
    export_slot_count: int = 0
    next_export_slot: ScheduleSlot | None = None


@dataclass(frozen=True)
class DischargeGate:
    """Whether self-consumption discharge is currently allowed.

    Attributes:
        allowed: True when the battery may discharge to cover house load.
        reason: Machine-readable reason string for diagnostics.
        reserved_energy_kwh: Energy earmarked for upcoming scheduled
            discharge slots (before the next charge slot), net of the solar
            energy expected to arrive before them when solar windows are
            provided (BATT-16).
    """

    allowed: bool
    reason: str
    reserved_energy_kwh: float


# ---------------------------------------------------------------------------
# Internal data structures for the algorithm
# ---------------------------------------------------------------------------


@dataclass
class _SlotInfo:
    """Internal working representation of a price slot during scheduling."""

    start: datetime
    end: datetime
    price: float
    action: str  # "charge", "discharge", "idle", "solar_charge", "export"
    duration_hours: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_battery_schedule(
    price_slots: list[dict],
    charge_threshold: float,
    discharge_threshold: float,
    max_charge_power_w: float,
    battery_capacity_kwh: float,
    current_soc_pct: float,
    now: datetime | None = None,
    mean_consumption_kw: float = 0.5,
    estimated_charge_power_kw: float = 6.0,
    charge_buffer_pct: float = 20.0,
    solar_forecast_remaining_wh: float | None = None,
    solar_forecast_tomorrow_wh: float | None = None,
    production_factor: float = 0.8,
    dawn: datetime | None = None,
    dusk: datetime | None = None,
    tomorrow_start: datetime | None = None,
    peak_gap_hours: float = 2.0,
    min_soc_pct: float = 10.0,
    max_soc_pct: float = 95.0,
    export_spike_threshold: float | None = None,
    export_reserve_soc_pct: float = 20.0,
    export_power_kw: float = 0.0,
    grid_transfer_fee: float = 0.0,
    electricity_company_fee: float = 0.0,
    battery_cycle_cost: float = 0.0,
) -> BatteryScheduleResult:
    """Build a multi-cycle charge/discharge schedule.

    Args:
        price_slots: List of dicts with "start" (datetime), "end" (datetime),
            "price" (float) keys.
        charge_threshold: Spread threshold in SEK/kWh -- a slot is a charge
            candidate for a given peak when ``peak_max_price - price``
            exceeds this value (BATT-15).
        discharge_threshold: Spread threshold in SEK/kWh -- a slot discharges
            when ``price - min_price`` exceeds this value (BATT-15). Callers
            implementing the BATT-14 economics derivation should pass the
            already-derived effective value here.
        max_charge_power_w: Maximum charging power in watts (hard cap;
            combined with estimated_charge_power_kw via min()).
        battery_capacity_kwh: Total battery capacity in kWh.
        current_soc_pct: Current state of charge (0-100).
        now: UTC-aware datetime for current time. Defaults to utcnow().
        mean_consumption_kw: Rolling average house consumption in kW, used
            to size the energy needed to serve each peak and to pace
            discharge. Defaults to a conservative 0.5 kW.
        estimated_charge_power_kw: Assumed charge rate in kW used to size
            how many slots are needed to cover a charge deficit. The actual
            per-slot energy uses min(estimated_charge_power_kw,
            max_charge_power_w / 1000).
        charge_buffer_pct: Percentage buffer added on top of the raw charge
            deficit (default 20%).
        solar_forecast_remaining_wh: Estimated remaining solar production
            for the rest of today, in Wh (already summed across all
            configured Forecast.Solar sensors), or None.
        solar_forecast_tomorrow_wh: Estimated solar production for tomorrow,
            in Wh (already summed across all derived Forecast.Solar tomorrow
            sensors), or None when unavailable (BATT-16).
        production_factor: Multiplier applied to the raw solar forecast
            readings to account for forecast optimism (default 0.8).
        dawn: The next_dawn datetime from sun.sun (NEXT occurrence -- may be
            tomorrow's dawn if it is currently daytime), or None.
        dusk: The next_dusk datetime from sun.sun (NEXT occurrence), or None.
        tomorrow_start: UTC instant of the next local midnight -- the only
            calendar knowledge the scheduler needs to attribute the resolved
            daylight window to today or tomorrow (BATT-16). None disables
            tomorrow attribution.
        peak_gap_hours: Hours gap to separate peak groups.
        min_soc_pct: Minimum SOC to maintain (0-100).
        max_soc_pct: Maximum SOC target (0-100).
        export_spike_threshold: Price spread (per kWh) above the horizon's
            cheapest slot at or above which a slot may become an export
            slot (BATT-17) -- same spread convention as the BATT-15
            discharge threshold. None or <= 0 disables export arbitrage
            entirely -- output is bit-identical to a build without these
            kwargs.
        export_reserve_soc_pct: SOC floor (0-100) below which export is
            never scheduled -- the export energy budget only spends what
            lies above this reserve.
        export_power_kw: Estimated grid-injection power during an export
            slot in kW, fuse-capped, excluding house load; computed by the
            coordinator as (fuse - buffer) x 3 x 0.230.
        grid_transfer_fee: Grid transfer fee in SEK/kWh, part of the
            avoided-import value of a discharge slot and of the export
            replacement-cost check.
        electricity_company_fee: Electricity company fee in SEK/kWh, part
            of the avoided-import value of a discharge slot and of the
            export replacement-cost check.
        battery_cycle_cost: Cost of one battery charge/discharge cycle in
            SEK/kWh, added to the export replacement cost.

    Returns:
        BatteryScheduleResult with the complete schedule and derived state.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Step 1: Handle empty input
    if not price_slots:
        return _idle_result()

    # Step 2: Convert to internal representation and sort by time
    slots = _parse_price_slots(price_slots)
    if not slots:
        return _idle_result()

    slots.sort(key=lambda s: s.start)

    # Step 3: Classify discharge candidates by SPREAD against the period's
    # minimum price (BATT-15). Charge candidates are peak-relative and are
    # evaluated later, per-peak, inside _optimize_schedule.
    min_price = min(s.price for s in slots)
    for slot in slots:
        if slot.price - min_price > discharge_threshold:
            slot.action = "discharge"
        else:
            slot.action = "idle"

    # Step 4: Group discharge candidates into peaks
    discharge_candidates = [s for s in slots if s.action == "discharge"]
    peaks = _group_into_peaks(discharge_candidates, peak_gap_hours)

    # Step 5: Resolve the solar recharge windows and their rates
    # (BATT-15a / BATT-16)
    solar_windows = _resolve_solar_windows(
        dawn=dawn,
        dusk=dusk,
        solar_forecast_remaining_wh=solar_forecast_remaining_wh,
        solar_forecast_tomorrow_wh=solar_forecast_tomorrow_wh,
        production_factor=production_factor,
        tomorrow_start=tomorrow_start,
    )

    # Step 6: Virtual energy tracking -- optimize charge/discharge allocation
    _optimize_schedule(
        slots=slots,
        peaks=peaks,
        now=now,
        battery_capacity_kwh=battery_capacity_kwh,
        current_soc_pct=current_soc_pct,
        charge_threshold=charge_threshold,
        mean_consumption_kw=mean_consumption_kw,
        estimated_charge_power_kw=estimated_charge_power_kw,
        max_charge_power_w=max_charge_power_w,
        charge_buffer_pct=charge_buffer_pct,
        solar_windows=solar_windows,
        min_soc_pct=min_soc_pct,
        max_soc_pct=max_soc_pct,
    )

    # Step 6b: BATT-17 export arbitrage (opt-in; no-op when disabled)
    if export_spike_threshold is not None and export_spike_threshold > 0:
        _mark_export_slots(
            slots,
            now=now,
            export_spike_threshold=export_spike_threshold,
            export_reserve_soc_pct=export_reserve_soc_pct,
            export_power_kw=export_power_kw,
            grid_transfer_fee=grid_transfer_fee,
            electricity_company_fee=electricity_company_fee,
            battery_cycle_cost=battery_cycle_cost,
            mean_consumption_kw=mean_consumption_kw,
            battery_capacity_kwh=battery_capacity_kwh,
            current_soc_pct=current_soc_pct,
            min_soc_pct=min_soc_pct,
        )

    # Step 7: Build the final schedule
    schedule = [
        ScheduleSlot(
            start=s.start,
            end=s.end,
            price=s.price,
            action=s.action,
        )
        for s in slots
    ]

    # Step 8: Derive current state and next slots
    current_action = _find_current_action(schedule, now)
    target_ems_mode = _action_to_ems_mode(current_action)
    next_charge = _find_next_slot(schedule, now, ("charge", "solar_charge"))
    next_discharge = _find_next_slot(schedule, now, ("discharge",))
    next_export = _find_next_slot(schedule, now, ("export",))

    # Count only remaining slots (current slot included) so the counts match
    # the future-filtered schedule exposed on the sensor.
    charging_count = sum(
        1
        for s in schedule
        if s.action in ("charge", "solar_charge") and s.end > now
    )
    discharging_count = sum(
        1 for s in schedule if s.action == "discharge" and s.end > now
    )
    export_count = sum(1 for s in schedule if s.action == "export" and s.end > now)

    gate = compute_discharge_gate(
        schedule=schedule,
        now=now,
        effective_discharge_threshold=discharge_threshold,
        battery_soc_pct=current_soc_pct,
        battery_capacity_kwh=battery_capacity_kwh,
        mean_consumption_kw=mean_consumption_kw,
        min_soc_pct=min_soc_pct,
        solar_windows=solar_windows,
        export_power_kw=export_power_kw,
    )

    return BatteryScheduleResult(
        schedule=schedule,
        charging_slot_count=charging_count,
        discharging_slot_count=discharging_count,
        next_charging_slot=next_charge,
        next_discharging_slot=next_discharge,
        current_action=current_action,
        target_ems_mode=target_ems_mode,
        discharge_allowed=gate.allowed,
        discharge_gate_reason=gate.reason,
        reserved_energy_kwh=gate.reserved_energy_kwh,
        export_slot_count=export_count,
        next_export_slot=next_export,
    )


def compute_effective_discharge_threshold(
    discharge_threshold: float,
    battery_cycle_cost: float,
    grid_transfer_fee: float,
) -> float:
    """Derive the effective discharge spread threshold (BATT-14).

    Parity with the live AppDaemon system's formula: when a battery cycle
    cost is configured, discharging is only profitable once the price
    spread covers the wear cost of a cycle net of the grid transfer fee
    already saved by not importing -- this OVERRIDES the manually configured
    discharge_threshold entity. When battery_cycle_cost is 0 (the default,
    i.e. not configured), the manual discharge_threshold value is used
    unchanged.

    Args:
        discharge_threshold: Manually configured discharge spread threshold
            (SEK/kWh), used unchanged when battery_cycle_cost is 0.
        battery_cycle_cost: Cost of one battery charge/discharge cycle
            (SEK/kWh). 0 disables the derivation.
        grid_transfer_fee: Grid transfer fee (SEK/kWh).

    Returns:
        The discharge spread threshold to use for scheduling.
    """
    if battery_cycle_cost > 0:
        # Clamp at 0.0: a transfer fee larger than the cycle cost must never
        # produce a negative threshold, which would classify the period's
        # cheapest slot as "discharge" and empty charge_candidates entirely.
        return max(0.0, battery_cycle_cost - grid_transfer_fee)
    return discharge_threshold


def compute_discharge_gate(
    schedule: list[ScheduleSlot],
    now: datetime,
    effective_discharge_threshold: float,
    battery_soc_pct: float,
    battery_capacity_kwh: float,
    mean_consumption_kw: float,
    min_soc_pct: float = 10.0,
    solar_windows: list[tuple[tuple[datetime, datetime], float]] | None = None,
    export_power_kw: float = 0.0,
) -> DischargeGate:
    """Determine whether self-consumption discharge is currently allowed.

    Ports the live AppDaemon system's max-discharging-limit gate: the
    battery's discharge limit is only opened up to serve house load once
    the current slot's price spread against the period minimum clears the
    effective discharge threshold. This scheduler improves on that
    AppDaemon behavior with a reservation check: energy already earmarked
    for a scheduled discharge peak later today (before the next planned
    recharge) is protected from being drained by idle-period
    self-consumption, so an early self-consumption drain cannot starve a
    later, already-planned peak.

    Args:
        schedule: Ordered list of schedule slots (as produced by
            build_battery_schedule).
        now: Current UTC-aware datetime.
        effective_discharge_threshold: Spread threshold in SEK/kWh -- see
            compute_effective_discharge_threshold for BATT-14 derivation.
        battery_soc_pct: Current battery state of charge (0-100).
        battery_capacity_kwh: Total battery capacity in kWh.
        mean_consumption_kw: Rolling average house consumption in kW.
        min_soc_pct: Minimum SOC to maintain (0-100).
        solar_windows: Resolved ((start, end), rate_kw) solar windows as
            produced by _resolve_solar_windows, or None. When provided, the
            reservation is computed net of the solar energy expected to
            arrive before each discharge slot (prefix-max over future
            discharge slots, BATT-16); None preserves the gross-reservation
            behavior exactly.
        export_power_kw: Planned grid-injection power for BATT-17 export
            slots (kW). Future export slots reserve at
            (export_power_kw + mean_consumption_kw); 0.0 (the default)
            still reserves an export slot's house-load share.

    Returns:
        DischargeGate describing whether discharge is currently allowed.
    """
    current_slot = _find_current_slot(schedule, now)
    if current_slot is None:
        return DischargeGate(
            allowed=False, reason="no_schedule", reserved_energy_kwh=0.0
        )

    if current_slot.action == "discharge":
        return DischargeGate(
            allowed=True, reason="scheduled_discharge", reserved_energy_kwh=0.0
        )

    if current_slot.action == "export":
        return DischargeGate(
            allowed=True, reason="export_slot", reserved_energy_kwh=0.0
        )

    if current_slot.action in ("charge", "solar_charge"):
        return DischargeGate(
            allowed=True, reason="charging_slot", reserved_energy_kwh=0.0
        )

    # Idle slot: gate self-consumption discharge on price spread, then on
    # energy reserved for an upcoming scheduled discharge peak.
    min_price = min(s.price for s in schedule)
    spread = current_slot.price - min_price

    reserved_energy_kwh = 0.0
    needed_energy_kwh = 0.0
    for slot in schedule:
        if slot.start < now:
            continue
        if slot.action in ("charge", "solar_charge"):
            # A planned recharge resets the reservation -- discharging
            # before a refill is fine.
            break
        # Future "export" slots reserve like discharge slots, at their full
        # rate (export power + house load served by the battery). Without
        # this, gate-open self-consumption on idle slots could spend the
        # above-reserve-floor energy the export plan counted on, and the
        # runtime SOC check would then cancel the sale (lost revenue).
        if slot.action in ("discharge", "export"):
            duration_hours = (slot.end - slot.start).total_seconds() / 3600.0
            rate_kw = mean_consumption_kw
            if slot.action == "export":
                rate_kw += export_power_kw
            needed_energy_kwh += rate_kw * duration_hours
            if solar_windows is None:
                reserved_energy_kwh = needed_energy_kwh
            else:
                # BATT-16 prefix-max net-of-solar reservation: the energy
                # needed NOW is the max over future discharge slots of
                # (cumulative need through that slot minus the solar energy
                # expected to arrive before it). Floored at 0 because
                # reserved_energy_kwh starts there and only ever ratchets up.
                reserved_energy_kwh = max(
                    reserved_energy_kwh,
                    needed_energy_kwh
                    - _solar_energy_kwh(now, slot.start, solar_windows),
                )

    if spread <= effective_discharge_threshold:
        return DischargeGate(
            allowed=False,
            reason="below_threshold",
            reserved_energy_kwh=reserved_energy_kwh,
        )

    # Only energy above the scheduler's minimum SOC is expendable -- the
    # bottom min_soc_pct is never dispatched (same floor the slot sizing
    # uses), so counting it here would open the gate on energy that the
    # battery will not actually deliver.
    usable_kwh = (
        max(0.0, battery_soc_pct - min_soc_pct) / 100.0
    ) * battery_capacity_kwh
    if usable_kwh - reserved_energy_kwh < mean_consumption_kw * 0.5:
        return DischargeGate(
            allowed=False,
            reason="reserved_for_peak",
            reserved_energy_kwh=reserved_energy_kwh,
        )

    return DischargeGate(
        allowed=True,
        reason="spread_above_threshold",
        reserved_energy_kwh=reserved_energy_kwh,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _idle_result() -> BatteryScheduleResult:
    """Return an idle result for empty/invalid input."""
    return BatteryScheduleResult(
        schedule=[],
        charging_slot_count=0,
        discharging_slot_count=0,
        next_charging_slot=None,
        next_discharging_slot=None,
        current_action="idle",
        target_ems_mode="max_self_consumption",
        discharge_allowed=False,
        discharge_gate_reason="no_schedule",
        reserved_energy_kwh=0.0,
    )


def _parse_price_slots(price_slots: list[dict]) -> list[_SlotInfo]:
    """Convert raw price slot dicts to internal _SlotInfo objects.

    Skips invalid entries silently. Derives duration from start/end.
    """
    result: list[_SlotInfo] = []
    for raw in price_slots:
        try:
            start = raw["start"]
            end = raw["end"]
            price = float(raw["price"])

            if not isinstance(start, datetime) or not isinstance(end, datetime):
                continue

            duration_hours = (end - start).total_seconds() / 3600.0
            if duration_hours <= 0:
                continue

            result.append(
                _SlotInfo(
                    start=start,
                    end=end,
                    price=price,
                    action="idle",
                    duration_hours=duration_hours,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return result


def _group_into_peaks(
    discharge_candidates: list[_SlotInfo],
    gap_hours: float,
) -> list[list[_SlotInfo]]:
    """Group discharge-candidate slots into separate peaks.

    Two groups of discharge slots separated by more than gap_hours are
    considered separate peaks. Each peak is a list of consecutive (or
    near-consecutive) discharge slots.

    Args:
        discharge_candidates: Slots classified as discharge, sorted by time.
        gap_hours: Maximum gap in hours to still consider slots as one peak.

    Returns:
        List of peaks, where each peak is a list of _SlotInfo slots.
    """
    if not discharge_candidates:
        return []

    peaks: list[list[_SlotInfo]] = []
    current_peak: list[_SlotInfo] = [discharge_candidates[0]]

    for i in range(1, len(discharge_candidates)):
        prev = discharge_candidates[i - 1]
        curr = discharge_candidates[i]

        gap = (curr.start - prev.end).total_seconds() / 3600.0

        if gap > gap_hours:
            # Start a new peak
            peaks.append(current_peak)
            current_peak = [curr]
        else:
            current_peak.append(curr)

    peaks.append(current_peak)
    return peaks


def _normalize_daylight_window(
    dawn: datetime | None, dusk: datetime | None
) -> tuple[datetime, datetime] | None:
    """Resolve sun.sun's next_dawn/next_dusk into a single daylight window.

    HA's sun.sun entity only exposes the NEXT occurrence of each event, so
    the raw pair does not always describe the same calendar day: during
    daytime (after dawn but before dusk) next_dawn has already rolled over
    to tomorrow while next_dusk is still today's upcoming sunset, so
    next_dawn ends up LATER than next_dusk. In that case the paired dawn for
    the *current* daylight window is next_dawn shifted back by 24h. At
    night, before dawn, next_dawn is already earlier than next_dusk and the
    raw pair already describes one consistent upcoming window as-is.

    Args:
        dawn: The next_dawn datetime (or None if unavailable).
        dusk: The next_dusk datetime (or None if unavailable).

    Returns:
        (window_start, window_end) tuple with window_start < window_end,
        or None if either input is missing or the window is degenerate.
    """
    if dawn is None or dusk is None:
        return None

    if dawn > dusk:
        # Currently daytime: dusk is the upcoming one, dawn needs to roll
        # back a day to describe the same daylight window.
        window_start = dawn - timedelta(days=1)
        window_end = dusk
    else:
        window_start = dawn
        window_end = dusk

    if window_end <= window_start:
        return None

    return window_start, window_end


def _overlap_hours(
    range_start: datetime,
    range_end: datetime,
    window_start: datetime,
    window_end: datetime,
) -> float:
    """Return the overlap in hours between [range_start, range_end) and [window_start, window_end)."""
    latest_start = max(range_start, window_start)
    earliest_end = min(range_end, window_end)
    overlap_seconds = (earliest_end - latest_start).total_seconds()
    return max(0.0, overlap_seconds / 3600.0)


def _estimate_solar_rate_kw(
    solar_forecast_wh: float | None,
    production_factor: float,
    daylight_window: tuple[datetime, datetime] | None,
) -> float:
    """Estimate the solar recharge rate in kW (BATT-15a).

    Args:
        solar_forecast_wh: Estimated production for the given daylight
            window in Wh (remaining-today or tomorrow's total), or None.
        production_factor: Multiplier applied to the raw forecast reading.
        daylight_window: Resolved (dawn, dusk) window, or None.

    Returns:
        Estimated average recharge rate in kW over the window's daylight
        hours, or 0.0 if inputs are missing/degenerate.
    """
    if not solar_forecast_wh or solar_forecast_wh <= 0:
        return 0.0
    if daylight_window is None:
        return 0.0

    daylight_hours = (daylight_window[1] - daylight_window[0]).total_seconds() / 3600.0
    if daylight_hours <= 0:
        return 0.0

    estimated_kwh = (solar_forecast_wh / 1000.0) * production_factor
    return estimated_kwh / daylight_hours


def _resolve_solar_windows(
    dawn: datetime | None,
    dusk: datetime | None,
    solar_forecast_remaining_wh: float | None,
    solar_forecast_tomorrow_wh: float | None,
    production_factor: float,
    tomorrow_start: datetime | None,
) -> list[tuple[tuple[datetime, datetime], float]]:
    """Resolve daylight windows and their solar recharge rates (BATT-16).

    Extends the single-window BATT-15a solar model across the 48h planning
    horizon. sun.sun only describes ONE upcoming daylight window, so which
    forecast belongs to it depends on the time of day: in the evening (after
    dusk) both next_dawn and next_dusk already point at tomorrow, so the
    resolved window IS tomorrow's and must carry tomorrow's forecast energy
    -- naively shifting it +24h would misplace that energy beyond the
    horizon. During daytime/morning the resolved window is today's; a
    synthetic tomorrow window is appended when tomorrow's forecast is
    available.

    Args:
        dawn: The next_dawn datetime from sun.sun, or None.
        dusk: The next_dusk datetime from sun.sun, or None.
        solar_forecast_remaining_wh: Estimated remaining production for the
            rest of today in Wh, or None.
        solar_forecast_tomorrow_wh: Estimated production for tomorrow in Wh,
            or None when tomorrow sensors are unavailable.
        production_factor: Multiplier applied to the raw forecast readings.
        tomorrow_start: UTC instant of the next local midnight, or None to
            disable tomorrow attribution. Must be provided whenever
            solar_forecast_tomorrow_wh is: without it, evening calls cannot
            detect that the sun.sun window already describes tomorrow, and
            tomorrow's energy would be misattributed to a +24h window.

    Returns:
        List of ((window_start, window_end), rate_kw) tuples, empty when no
        daylight window can be resolved.
    """
    window = _normalize_daylight_window(dawn, dusk)
    if window is None:
        return []

    w0_is_tomorrow = tomorrow_start is not None and window[0] >= tomorrow_start
    if w0_is_tomorrow:
        # Evening: the single resolved window is tomorrow's daylight, so it
        # carries tomorrow's forecast. Falling back to remaining-today when
        # tomorrow sensors are absent preserves the pre-BATT-16 behavior.
        energy_wh = (
            solar_forecast_tomorrow_wh
            if solar_forecast_tomorrow_wh is not None
            else solar_forecast_remaining_wh
        )
        return [(window, _estimate_solar_rate_kw(energy_wh, production_factor, window))]

    windows = [
        (
            window,
            _estimate_solar_rate_kw(
                solar_forecast_remaining_wh, production_factor, window
            ),
        )
    ]
    if solar_forecast_tomorrow_wh is not None:
        # Shift +24h in UTC: immune to DST transitions (UTC has none); the
        # only error versus tomorrow's true dawn/dusk is seasonal sun drift
        # of roughly 1-3 minutes per day.
        tomorrow_window = (
            window[0] + timedelta(hours=24),
            window[1] + timedelta(hours=24),
        )
        windows.append(
            (
                tomorrow_window,
                _estimate_solar_rate_kw(
                    solar_forecast_tomorrow_wh, production_factor, tomorrow_window
                ),
            )
        )
    return windows


def _solar_energy_kwh(
    range_start: datetime,
    range_end: datetime,
    solar_windows: list[tuple[tuple[datetime, datetime], float]],
) -> float:
    """Expected solar energy in kWh between two instants across all windows.

    Args:
        range_start: Start of the time range.
        range_end: End of the time range.
        solar_windows: Resolved ((start, end), rate_kw) solar windows.

    Returns:
        Sum of rate x overlap over all windows with a positive rate.
    """
    return sum(
        rate_kw * _overlap_hours(range_start, range_end, window[0], window[1])
        for window, rate_kw in solar_windows
        if rate_kw > 0
    )


def _optimize_schedule(
    slots: list[_SlotInfo],
    peaks: list[list[_SlotInfo]],
    now: datetime,
    battery_capacity_kwh: float,
    current_soc_pct: float,
    charge_threshold: float,
    mean_consumption_kw: float,
    estimated_charge_power_kw: float,
    max_charge_power_w: float,
    charge_buffer_pct: float,
    solar_windows: list[tuple[tuple[datetime, datetime], float]],
    min_soc_pct: float,
    max_soc_pct: float,
) -> None:
    """Optimize the schedule using virtual energy tracking (BATT-15).

    Processes peaks chronologically, tracking a virtual battery energy
    level. For each peak: solar recharge accumulated in the gap before it is
    added first (BATT-15a, summed across all resolved solar windows --
    BATT-16); energy is reserved for future, more expensive peaks net of
    their own upcoming recharge (BATT-15b); a cheapest-first charge deficit
    (with buffer, capped at the usable energy range) is scheduled from
    peak-relative charge candidates; then discharge is marked
    most-expensive-first while energy remains for each slot's own
    house-consumption need. Charge slots inside any solar window are
    finally relabeled "solar_charge" (cosmetic only).

    Mutates slots in place, changing actions as needed.
    """
    min_energy_kwh = (min_soc_pct / 100.0) * battery_capacity_kwh
    max_energy_kwh = (max_soc_pct / 100.0) * battery_capacity_kwh
    max_usable_energy_kwh = max(0.0, max_energy_kwh - min_energy_kwh)
    current_energy_kwh = (current_soc_pct / 100.0) * battery_capacity_kwh

    # BATT-18: elapsed slots are display-only. current_soc_pct describes
    # the battery NOW -- "spending" it on already-past peaks would enter
    # future planning with a phantom-empty battery and grid-charge for
    # energy the battery actually still holds. Fully-elapsed peaks are
    # dropped; partially-elapsed peaks keep only their future slots.
    peaks = [
        [s for s in peak if s.end > now]
        for peak in peaks
        if peak[-1].end > now
    ]
    peaks = [p for p in peaks if p]

    if not peaks:
        # No discharge opportunities -- nothing to charge for either.
        return

    max_charge_power_kw = max_charge_power_w / 1000.0
    charge_power_kw = min(estimated_charge_power_kw, max_charge_power_kw)

    # Precompute each peak's max price and consumption-based energy need.
    peak_max_price = [max(s.price for s in peak) for peak in peaks]
    peak_energy_needed = [
        sum(mean_consumption_kw * s.duration_hours for s in peak) for peak in peaks
    ]

    # Precompute each peak's pre-window (the charging gap before it) and the
    # solar recharge expected to accumulate during that gap (BATT-15a),
    # plus the solar expected DURING the peak itself (BATT-18): on extreme
    # spread days even midday prices clear the discharge threshold, so
    # morning/midday/evening candidates bridge into one mega-peak whose
    # pre-window is the night -- without the intra-peak credit the entire
    # day's solar becomes invisible to the deficit and the optimizer grid
    # charges overnight for an evening a sunny forecast already covers.
    window_bounds: list[tuple[datetime, datetime]] = []
    peak_recharge: list[float] = []
    peak_solar_during: list[float] = []
    for idx, peak in enumerate(peaks):
        window_start = slots[0].start if idx == 0 else peaks[idx - 1][-1].end
        window_end = peak[0].start
        window_bounds.append((window_start, window_end))

        # Solar credit clamps to now (BATT-18): solar that already arrived
        # is part of current_soc_pct. (Charge candidates instead filter on
        # slot end > now, so the in-progress slot stays chargeable.)
        peak_recharge.append(
            _solar_energy_kwh(max(window_start, now), window_end, solar_windows)
        )
        # Capped at the peak's own need: intra-peak solar can serve the
        # house during the peak but surplus beyond that is bounded by the
        # battery's ceiling, which the virtual-energy cap already models.
        peak_solar_during.append(
            min(
                _solar_energy_kwh(peak[0].start, peak[-1].end, solar_windows),
                peak_energy_needed[idx],
            )
        )

    virtual_energy = current_energy_kwh

    for idx, peak in enumerate(peaks):
        window_start, window_end = window_bounds[idx]

        # BATT-15a: add this peak's expected solar recharge before drawing
        # down for it, capped at the battery's usable ceiling.
        virtual_energy = min(virtual_energy + peak_recharge[idx], max_energy_kwh)

        available = max(0.0, virtual_energy - min_energy_kwh)

        # BATT-15b: reserve energy for FUTURE, MORE EXPENSIVE peaks (net of
        # their own upcoming solar recharge AND their own intra-peak solar,
        # BATT-18) so an early cheap peak cannot drain what a later,
        # pricier peak needs.
        reserved_for_future = sum(
            max(
                0.0,
                peak_energy_needed[j] - peak_recharge[j] - peak_solar_during[j],
            )
            for j in range(idx + 1, len(peaks))
            if peak_max_price[j] > peak_max_price[idx]
        )

        adjusted_available = available - reserved_for_future
        effective_need = peak_energy_needed[idx] - peak_solar_during[idx]
        energy_deficit = max(0.0, effective_need - adjusted_available)
        energy_deficit *= 1 + (charge_buffer_pct / 100.0)
        energy_deficit = min(energy_deficit, max_usable_energy_kwh)

        room_in_battery = max(0.0, max_energy_kwh - virtual_energy)
        charge_target = min(energy_deficit, room_in_battery)

        # Peak-relative charge candidates (BATT-15): idle, not-yet-assigned
        # slots in the pre-peak window priced enough below this peak's max.
        charge_candidates = [
            s
            for s in slots
            if s.action == "idle"
            and s.end > now
            and window_start <= s.start < window_end
            and (peak_max_price[idx] - s.price) > charge_threshold
        ]
        charge_candidates.sort(key=lambda s: s.price)

        charged_energy = 0.0
        for cslot in charge_candidates:
            if charged_energy >= charge_target:
                break
            slot_energy = charge_power_kw * cslot.duration_hours
            remaining_room = max_energy_kwh - (virtual_energy + charged_energy)
            actual = min(slot_energy, max(0.0, remaining_room))
            if actual <= 0:
                break
            cslot.action = "charge"
            charged_energy += actual

        virtual_energy = min(virtual_energy + charged_energy, max_energy_kwh)

        # Discharge marking: most-expensive-first while energy remains for
        # that slot's own house-consumption need.
        sorted_peak = sorted(peak, key=lambda s: s.price, reverse=True)
        remaining_energy = max(0.0, virtual_energy - min_energy_kwh)
        discharged = 0.0

        for dslot in sorted_peak:
            slot_need = mean_consumption_kw * dslot.duration_hours
            if remaining_energy >= slot_need:
                remaining_energy -= slot_need
                discharged += slot_need
            else:
                dslot.action = "idle"

        virtual_energy -= discharged

    # Charge slots that fall within any solar window are relabeled
    # solar_charge -- cosmetic only (EMS maps both to command_charging);
    # communicates that the draw could be covered by PV rather than grid.
    # Keyed on window presence, not rate, matching the single-window
    # semantics (a resolved window with a zero forecast still relabels).
    if solar_windows:
        for s in slots:
            if s.action == "charge" and any(
                _overlap_hours(s.start, s.end, window[0], window[1]) > 0
                for window, _rate_kw in solar_windows
            ):
                s.action = "solar_charge"


def _mark_export_slots(
    slots: list[_SlotInfo],
    *,
    now: datetime,
    export_spike_threshold: float,
    export_reserve_soc_pct: float,
    export_power_kw: float,
    grid_transfer_fee: float,
    electricity_company_fee: float,
    battery_cycle_cost: float,
    mean_consumption_kw: float,
    battery_capacity_kwh: float,
    current_soc_pct: float,
    min_soc_pct: float,
) -> None:
    """Mark qualifying spike slots as "export" (BATT-17 post-pass).

    Unified merit order: a discharge slot's value per kWh is its spot
    price plus fees (avoided import) while an export slot earns bare
    spot, so export never demotes a discharge slot whose ``price + fees``
    is >= the export candidate's bare spot price. The reserve-SOC floor
    and the energy already scheduled for self-consumption discharge are
    enforced via the energy budget: export only spends what remains above
    both (a discharge slot's own house-need is released back when the
    slot itself converts, and merit-order demotions release theirs).

    A slot qualifies when its SPREAD above the horizon's cheapest price
    is at or above the spike threshold AND selling now beats buying the
    same energy back at the cheapest future slot (replacement cost
    includes fees, round-trip losses and cycle wear). The spread basis
    matches the BATT-15 discharge classification: an absolute threshold
    would silently change meaning when the whole price level shifts
    (sustained-high weeks would make every slot "a spike"), while the
    spread self-adapts to the current period. A slot with no future slot
    in the horizon never qualifies (SEM rule: cannot prove profitable ->
    hold). Only "discharge"/"idle" slots are eligible; charge/
    solar_charge slots are never converted. Mutates slots in place.
    """
    fees = grid_transfer_fee + electricity_company_fee
    # Spread baseline over FUTURE slots only (Greptile PR #8): an elapsed
    # cheap morning would otherwise inflate every remaining slot's spread
    # and let ordinary later prices qualify as "spikes".
    remaining_prices = [s.price for s in slots if s.end > now]
    if not remaining_prices:
        return
    horizon_min_price = min(remaining_prices)

    # Only slots that have not yet ended participate -- as candidates, as
    # already-committed discharge energy, or as demotion fodder. A past
    # spike must never consume the finite export budget or demote a real
    # future discharge slot to fund energy that can no longer be sold
    # (its energy is already reflected in current SOC).
    candidates: list[_SlotInfo] = []
    for s in slots:
        if s.end <= now:
            continue
        if s.action not in ("discharge", "idle"):
            continue
        if s.price - horizon_min_price < export_spike_threshold:
            continue
        future_prices = [x.price for x in slots if x.start >= s.end]
        if not future_prices:
            continue
        replacement_cost = (
            min(future_prices) + fees
        ) / DEFAULT_ROUNDTRIP_EFFICIENCY + battery_cycle_cost
        if s.price > replacement_cost:
            candidates.append(s)

    if not candidates:
        return

    # Energy budget (conservative -- ignores scheduled charging on
    # purpose, never over-exports).
    min_energy_kwh = (min_soc_pct / 100.0) * battery_capacity_kwh
    export_floor_kwh = max(
        min_energy_kwh, (export_reserve_soc_pct / 100.0) * battery_capacity_kwh
    )
    usable_kwh = max(
        0.0, (current_soc_pct / 100.0) * battery_capacity_kwh - export_floor_kwh
    )
    scheduled_discharge_kwh = sum(
        mean_consumption_kw * s.duration_hours
        for s in slots
        if s.action == "discharge" and s.end > now
    )
    budget = max(0.0, usable_kwh - scheduled_discharge_kwh)

    # Mark most-valuable-first.
    candidates.sort(key=lambda s: s.price, reverse=True)
    for c in candidates:
        # Battery output during an export slot serves house load AND grid
        # injection.
        need = (export_power_kw + mean_consumption_kw) * c.duration_hours
        # A discharge candidate's own house-need was already counted in
        # scheduled_discharge_kwh -- converting it releases that energy.
        own_release = (
            mean_consumption_kw * c.duration_hours if c.action == "discharge" else 0.0
        )
        # Merit-order demotion pool: discharge slots strictly less valuable
        # per kWh than this export candidate, cheapest value first.
        demotable = sorted(
            (
                d
                for d in slots
                if d.action == "discharge"
                and d.end > now
                and d is not c
                and d.price + fees < c.price
            ),
            key=lambda d: d.price,
        )
        demotable_kwh = sum(mean_consumption_kw * d.duration_hours for d in demotable)
        if budget + own_release + demotable_kwh < need:
            # Infeasible even after every merit-order demotion: leave the
            # schedule untouched (no partial demotions, no budget change).
            continue
        budget += own_release
        for d in demotable:
            if budget >= need:
                break
            d.action = "idle"
            budget += mean_consumption_kw * d.duration_hours
        c.action = "export"
        budget -= need


def _find_current_action(schedule: list[ScheduleSlot], now: datetime) -> str:
    """Find the action for the slot containing 'now'.

    Args:
        schedule: Ordered list of schedule slots.
        now: Current UTC-aware datetime.

    Returns:
        Action string for the current slot, or "idle" if now is outside
        all slots.
    """
    slot = _find_current_slot(schedule, now)
    if slot is not None:
        return slot.action
    return "idle"


def _find_current_slot(
    schedule: list[ScheduleSlot], now: datetime
) -> ScheduleSlot | None:
    """Find the schedule slot containing 'now' using linear search.

    Args:
        schedule: Ordered list of schedule slots.
        now: Current UTC-aware datetime.

    Returns:
        The ScheduleSlot containing now, or None.
    """
    for slot in schedule:
        if slot.start <= now < slot.end:
            return slot
    return None


def _find_next_slot(
    schedule: list[ScheduleSlot],
    now: datetime,
    actions: tuple[str, ...],
) -> ScheduleSlot | None:
    """Find the next upcoming slot with one of the specified actions.

    Scans forward from 'now' to find the first future slot matching any
    of the given actions.

    Args:
        schedule: Ordered list of schedule slots.
        now: Current UTC-aware datetime.
        actions: Tuple of action strings to match.

    Returns:
        The next matching ScheduleSlot, or None.
    """
    for slot in schedule:
        if slot.start > now and slot.action in actions:
            return slot
    return None


def _action_to_ems_mode(action: str) -> str:
    """Map a scheduler action to the corresponding EMS mode.

    Idle now means the battery is free to self-consume; discharge
    permission is governed by the discharge gate (see
    compute_discharge_gate), not by freezing the battery in "standby".
    "standby" is no longer produced by the scheduler -- it remains only
    for the EMS car-priority override elsewhere. "export" maps to
    "command_discharging" (BATT-17): the EMS commands forced discharge at
    the fuse-capped export limit.

    Args:
        action: Scheduler action string.

    Returns:
        EMS mode string for Phase 3 consumption.
    """
    if action == "export":
        return "command_discharging"
    if action in ("charge", "solar_charge"):
        return "command_charging"
    return "max_self_consumption"

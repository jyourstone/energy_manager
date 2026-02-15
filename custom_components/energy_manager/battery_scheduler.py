"""Pure-Python battery scheduling algorithm with zero Home Assistant dependencies.

Implements multi-cycle charge/discharge scheduling using peak grouping and
virtual energy tracking. Ported from the proven AppDaemon HomeBatteryManager.

This module is intentionally free of any HA imports so it can be thoroughly
unit-tested independently.

Algorithm overview:
    1. Classify each price slot as charge-candidate, discharge-candidate, or idle
    2. Group discharge candidates into peaks separated by configurable gaps
    3. Virtual energy tracking simulates the battery through time, allocating
       charge slots to fill before discharge peaks and limiting discharge to
       available energy
    4. Solar forecast reduces grid charge needs during daylight hours
    5. Derive current action and EMS mode from the slot containing 'now'
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

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
        action: One of "charge", "discharge", "idle", "solar_charge".
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
        charging_slot_count: Number of slots assigned to charge or solar_charge.
        discharging_slot_count: Number of slots assigned to discharge.
        next_charging_slot: Next upcoming charge/solar_charge slot relative to now.
        next_discharging_slot: Next upcoming discharge slot relative to now.
        current_action: Action for the slot containing 'now'.
        target_ems_mode: EMS mode string for Phase 3 consumption.
    """

    schedule: list[ScheduleSlot]
    charging_slot_count: int
    discharging_slot_count: int
    next_charging_slot: ScheduleSlot | None
    next_discharging_slot: ScheduleSlot | None
    current_action: str
    target_ems_mode: str


# ---------------------------------------------------------------------------
# Internal data structures for the algorithm
# ---------------------------------------------------------------------------


@dataclass
class _SlotInfo:
    """Internal working representation of a price slot during scheduling."""

    start: datetime
    end: datetime
    price: float
    action: str  # "charge", "discharge", "idle", "solar_charge"
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
    solar_forecast_wh: float | None = None,
    peak_gap_hours: float = 2.0,
    min_soc_pct: float = 10.0,
    max_soc_pct: float = 95.0,
) -> BatteryScheduleResult:
    """Build a multi-cycle charge/discharge schedule.

    Args:
        price_slots: List of dicts with "start" (datetime), "end" (datetime),
            "price" (float) keys.
        charge_threshold: Price threshold in SEK/kWh -- charge when below.
        discharge_threshold: Price threshold in SEK/kWh -- discharge when above.
        max_charge_power_w: Maximum charging power in watts.
        battery_capacity_kwh: Total battery capacity in kWh.
        current_soc_pct: Current state of charge (0-100).
        now: UTC-aware datetime for current time. Defaults to utcnow().
        solar_forecast_wh: Expected daily solar production in Wh, or None.
        peak_gap_hours: Hours gap to separate peak groups.
        min_soc_pct: Minimum SOC to maintain (0-100).
        max_soc_pct: Maximum SOC target (0-100).

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

    # Step 3: Initial classification by price thresholds
    for slot in slots:
        if slot.price <= charge_threshold:
            slot.action = "charge"
        elif slot.price >= discharge_threshold:
            slot.action = "discharge"
        else:
            slot.action = "idle"

    # Step 4: Group discharge candidates into peaks
    discharge_candidates = [s for s in slots if s.action == "discharge"]
    peaks = _group_into_peaks(discharge_candidates, peak_gap_hours)

    # Step 5: Virtual energy tracking -- optimize charge/discharge allocation
    _optimize_schedule(
        slots=slots,
        peaks=peaks,
        battery_capacity_kwh=battery_capacity_kwh,
        current_soc_pct=current_soc_pct,
        max_charge_power_w=max_charge_power_w,
        min_soc_pct=min_soc_pct,
        max_soc_pct=max_soc_pct,
    )

    # Step 6: Apply solar forecast adjustment
    if solar_forecast_wh is not None and solar_forecast_wh > 0:
        _apply_solar_forecast(slots, solar_forecast_wh)

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

    charging_count = sum(
        1 for s in schedule if s.action in ("charge", "solar_charge")
    )
    discharging_count = sum(1 for s in schedule if s.action == "discharge")

    return BatteryScheduleResult(
        schedule=schedule,
        charging_slot_count=charging_count,
        discharging_slot_count=discharging_count,
        next_charging_slot=next_charge,
        next_discharging_slot=next_discharge,
        current_action=current_action,
        target_ems_mode=target_ems_mode,
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
        target_ems_mode="standby",
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


def _calculate_slot_energy_kwh(
    max_power_w: float, duration_hours: float
) -> float:
    """Calculate the energy (kWh) that can be transferred in a slot.

    Args:
        max_power_w: Maximum power in watts.
        duration_hours: Slot duration in hours.

    Returns:
        Energy in kWh.
    """
    return (max_power_w / 1000.0) * duration_hours


def _optimize_schedule(
    slots: list[_SlotInfo],
    peaks: list[list[_SlotInfo]],
    battery_capacity_kwh: float,
    current_soc_pct: float,
    max_charge_power_w: float,
    min_soc_pct: float,
    max_soc_pct: float,
) -> None:
    """Optimize the schedule using virtual energy tracking.

    Uses a two-phase approach:
    1. For each discharge peak, determine how many slots the battery can
       serve. Work backwards from the most expensive slots.
    2. For charge slots before each peak, select the cheapest slots that
       fill the battery sufficiently. Respect capacity limits.

    The algorithm processes peaks chronologically, tracking a virtual
    battery energy level that accounts for charging and discharging.

    Mutates slots in place, changing actions as needed.
    """
    min_energy_kwh = (min_soc_pct / 100.0) * battery_capacity_kwh
    max_energy_kwh = (max_soc_pct / 100.0) * battery_capacity_kwh
    current_energy_kwh = (current_soc_pct / 100.0) * battery_capacity_kwh

    if not peaks:
        # No discharge opportunities -- no point charging either
        # (unless there would be future discharge in a later schedule update)
        for s in slots:
            if s.action == "charge":
                s.action = "idle"
        return

    # Process peaks chronologically, tracking virtual battery energy
    virtual_energy = current_energy_kwh

    for peak_idx, peak in enumerate(peaks):
        peak_start = peak[0].start

        # Determine window for charging before this peak
        if peak_idx == 0:
            window_start = slots[0].start
        else:
            window_start = peaks[peak_idx - 1][-1].end

        # Find charge candidate slots in the pre-peak window
        charge_candidates = [
            s for s in slots
            if s.action == "charge"
            and s.start >= window_start
            and s.start < peak_start
        ]

        # Calculate total peak discharge energy possible
        peak_energy_total = sum(
            _calculate_slot_energy_kwh(max_charge_power_w, s.duration_hours)
            for s in peak
        )

        # How much energy we need to fully serve this peak
        energy_needed_from_battery = peak_energy_total
        energy_available = virtual_energy - min_energy_kwh

        # How much additional charging we need (and can fit)
        energy_deficit = max(0, energy_needed_from_battery - energy_available)
        room_in_battery = max(0, max_energy_kwh - virtual_energy)
        charge_target = min(energy_deficit, room_in_battery)

        # But also charge to fill the battery if cheap slots available,
        # even beyond what this peak needs (for future peaks)
        total_future_discharge = sum(
            _calculate_slot_energy_kwh(max_charge_power_w, s.duration_hours)
            for future_peak in peaks[peak_idx:]
            for s in future_peak
        )
        total_charge_desire = min(
            total_future_discharge - energy_available + min_energy_kwh,
            room_in_battery,
        )
        charge_target = max(charge_target, min(total_charge_desire, room_in_battery))

        # Select cheapest charge slots to meet target
        charge_candidates.sort(key=lambda s: s.price)
        charged_energy = 0.0

        for cslot in charge_candidates:
            if charged_energy >= charge_target:
                # Enough charging scheduled -- mark rest as idle
                cslot.action = "idle"
                continue

            slot_energy = _calculate_slot_energy_kwh(
                max_charge_power_w, cslot.duration_hours
            )
            remaining_room = max_energy_kwh - (virtual_energy + charged_energy)
            actual = min(slot_energy, max(0, remaining_room))
            if actual > 0:
                charged_energy += actual
                # Keep as "charge"
            else:
                cslot.action = "idle"

        virtual_energy += charged_energy

        # Allocate discharge slots within this peak, limited by energy
        # Sort peak slots by price descending (most expensive first) to
        # prioritize the most profitable hours
        sorted_peak = sorted(peak, key=lambda s: s.price, reverse=True)
        discharge_energy_remaining = virtual_energy - min_energy_kwh

        for dslot in sorted_peak:
            slot_energy = _calculate_slot_energy_kwh(
                max_charge_power_w, dslot.duration_hours
            )
            if discharge_energy_remaining >= slot_energy:
                # Keep as discharge
                discharge_energy_remaining -= slot_energy
            else:
                dslot.action = "idle"

        # Update virtual energy after peak
        discharged = (virtual_energy - min_energy_kwh) - discharge_energy_remaining
        virtual_energy -= discharged

    # Any charge slots after the last peak have no discharge to serve
    if peaks:
        last_peak_end = peaks[-1][-1].end
        for s in slots:
            if s.action == "charge" and s.start >= last_peak_end:
                s.action = "idle"


def _apply_solar_forecast(
    slots: list[_SlotInfo],
    solar_forecast_wh: float,
) -> None:
    """Apply solar forecast to reduce grid charging.

    Distributes expected solar production across daylight hours (06:00-18:00
    UTC+1, simplified as 05:00-17:00 UTC). During daylight hours with expected
    solar, grid charge slots are converted to solar_charge.

    Args:
        slots: All slots in chronological order (mutated in place).
        solar_forecast_wh: Total expected daily solar production in Wh.
    """
    solar_kwh = solar_forecast_wh / 1000.0

    # Simplified daylight hours: 05:00-17:00 UTC (roughly 06:00-18:00 CET)
    daylight_start_utc = 5
    daylight_end_utc = 17

    # Find charge slots during daylight hours
    daylight_charge_slots = [
        s for s in slots
        if s.action == "charge"
        and daylight_start_utc <= s.start.hour < daylight_end_utc
    ]

    if not daylight_charge_slots:
        # No grid charge slots during daylight. Check if there are idle slots
        # during daylight that could benefit from solar charging
        daylight_idle_slots = [
            s for s in slots
            if s.action == "idle"
            and daylight_start_utc <= s.start.hour < daylight_end_utc
        ]
        if not daylight_idle_slots:
            return

        # Use solar to add solar_charge slots for idle periods during daylight
        daylight_hours = daylight_end_utc - daylight_start_utc
        solar_per_hour_kwh = solar_kwh / daylight_hours
        remaining_solar_kwh = solar_kwh

        for slot in daylight_idle_slots:
            if remaining_solar_kwh <= 0:
                break
            slot_solar = solar_per_hour_kwh * slot.duration_hours
            if remaining_solar_kwh >= slot_solar * 0.5:
                slot.action = "solar_charge"
                remaining_solar_kwh -= slot_solar
        return

    # Distribute solar energy across daylight hours evenly
    daylight_hours = daylight_end_utc - daylight_start_utc
    solar_per_hour_kwh = solar_kwh / daylight_hours
    remaining_solar_kwh = solar_kwh

    # Convert grid charge slots to solar_charge where solar covers the need
    for slot in daylight_charge_slots:
        if remaining_solar_kwh <= 0:
            break

        slot_solar = solar_per_hour_kwh * slot.duration_hours
        if remaining_solar_kwh >= slot_solar * 0.5:
            slot.action = "solar_charge"
            remaining_solar_kwh -= slot_solar


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

    Args:
        action: Scheduler action string.

    Returns:
        EMS mode string for Phase 3 consumption.
    """
    if action in ("charge", "solar_charge"):
        return "command_charging"
    if action == "discharge":
        return "max_self_consumption"
    return "standby"

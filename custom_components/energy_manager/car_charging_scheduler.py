"""Pure-Python car charging schedule algorithm with zero Home Assistant dependencies.

Implements price-optimized EV charging slot selection constrained by departure
deadline and target SOC. Follows the proven Phase 2 battery_scheduler.py pattern.

This module is intentionally free of any HA imports so it can be thoroughly
unit-tested independently.

Algorithm overview:
    1. Calculate energy needed from SOC gap and battery capacity
    2. Calculate hours needed from energy and max charge power
    3. Filter price slots to those within [now, departure] window
    4. Sort available slots by price ascending
    5. Select cheapest slots, accumulating each slot's deliverable energy
       (slot_duration_hours * max_charge_power_kw), until energy_needed_kwh
       is met -- duration-aware so it works for 15-min, 30-min, hourly, or
       mixed-duration slot lists
    6. Build chronological schedule with "charge", "idle", or "solar_charge" actions
    7. Derive current_action from which slot contains 'now'
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CarScheduleSlot:
    """A single time slot in the car charging schedule.

    Attributes:
        start: UTC-aware start time.
        end: UTC-aware end time.
        price: Electricity price in SEK/kWh.
        action: One of "charge", "idle", or "solar_charge".
    """

    start: datetime
    end: datetime
    price: float
    action: str


@dataclass
class CarScheduleResult:
    """Result of the car charging schedule calculation.

    Attributes:
        schedule: Ordered list of CarScheduleSlot objects covering the valid window.
        charging_slot_count: Number of slots assigned to charge or solar_charge.
        energy_needed_kwh: Energy needed to reach target SOC.
        hours_needed: Hours of charging needed at max_charge_power_kw.
        current_action: Action for the slot containing 'now'.
        is_preliminary: True when tomorrow's prices are not yet available.
    """

    schedule: list[CarScheduleSlot]
    charging_slot_count: int
    energy_needed_kwh: float
    hours_needed: float
    current_action: str
    is_preliminary: bool


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass
class _SlotInfo:
    """Internal working representation of a price slot during scheduling."""

    start: datetime
    end: datetime
    price: float
    action: str  # "charge", "idle", "solar_charge"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_car_charging_schedule(
    price_slots: list[dict],
    departure_time_utc: datetime,
    current_soc_pct: float,
    target_soc_pct: float,
    battery_capacity_kwh: float,
    max_charge_power_kw: float,
    now: datetime | None = None,
    fallback_mode: bool = False,
    is_preliminary: bool = False,
    solar_surplus_available: bool = False,
) -> CarScheduleResult:
    """Build a price-optimized charging schedule for one car.

    Args:
        price_slots: List of dicts with "start" (datetime), "end" (datetime),
            "price" (float) keys.
        departure_time_utc: UTC-aware datetime for departure deadline.
        current_soc_pct: Current state of charge (0-100).
        target_soc_pct: Target state of charge (0-100).
        battery_capacity_kwh: Total battery capacity in kWh.
        max_charge_power_kw: Maximum charging power in kW.
        now: UTC-aware datetime for current time. Defaults to utcnow().
        fallback_mode: If True, select cheapest slots covering half of the
            total deliverable energy across available slots, instead of the
            energy needed to reach target SOC (for unrecognized vehicles).
        is_preliminary: If True, marks schedule as based on incomplete price
            data (tomorrow's prices not yet available).
        solar_surplus_available: If True, mark charge slots as "solar_charge"
            instead of "charge" (flag pass-through for Phase 5 PV surplus).

    Returns:
        CarScheduleResult with the complete schedule and derived state.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Step 1: Calculate energy and hours needed
    soc_gap = max(0.0, target_soc_pct - current_soc_pct)
    energy_needed_kwh = (soc_gap / 100.0) * battery_capacity_kwh

    # Guard against division by zero
    if max_charge_power_kw <= 0:
        return _empty_result(energy_needed_kwh, 0.0, is_preliminary)

    hours_needed = energy_needed_kwh / max_charge_power_kw

    # Step 2: Parse and filter slots to [now, departure] window
    parsed = _parse_price_slots(price_slots)
    available = [
        s for s in parsed
        if s.start >= now and s.end <= departure_time_utc
    ]

    if not available:
        return _empty_result(energy_needed_kwh, hours_needed, is_preliminary)

    # Sort available slots chronologically for final output
    available.sort(key=lambda s: s.start)

    # Step 3: Select charge slots (duration-aware: each slot delivers
    # slot_duration_hours * max_charge_power_kw, so this works correctly for
    # 15-min, 30-min, hourly, or mixed-duration slot lists)
    sorted_by_price = sorted(available, key=lambda s: s.price)

    if fallback_mode:
        # EV-08: select cheapest slots covering half of the total
        # deliverable energy across all available slots.
        total_energy = sum(
            _slot_energy_kwh(s, max_charge_power_kw) for s in available
        )
        target_energy = total_energy / 2.0
    else:
        # Normal mode: select cheapest slots until deliverable energy meets
        # energy_needed_kwh (rounding up to a whole slot if needed).
        target_energy = energy_needed_kwh

    charge_ids: set[int] = set()
    cumulative_energy = 0.0
    if target_energy > 0:
        for s in sorted_by_price:
            if cumulative_energy >= target_energy:
                break
            charge_ids.add(id(s))
            cumulative_energy += _slot_energy_kwh(s, max_charge_power_kw)

    # Step 4: Assign actions
    for slot in available:
        if id(slot) in charge_ids:
            slot.action = "solar_charge" if solar_surplus_available else "charge"
        else:
            slot.action = "idle"

    # Step 5: Build final schedule (chronological order)
    schedule = [
        CarScheduleSlot(
            start=s.start,
            end=s.end,
            price=s.price,
            action=s.action,
        )
        for s in available
    ]

    # Step 6: Derive current action
    current_action = _find_current_action(schedule, now)

    # Step 7: Count charging slots
    charging_count = sum(
        1 for s in schedule if s.action in ("charge", "solar_charge")
    )

    return CarScheduleResult(
        schedule=schedule,
        charging_slot_count=charging_count,
        energy_needed_kwh=energy_needed_kwh,
        hours_needed=hours_needed,
        current_action=current_action,
        is_preliminary=is_preliminary,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _empty_result(
    energy_needed_kwh: float,
    hours_needed: float,
    is_preliminary: bool,
) -> CarScheduleResult:
    """Return an idle result for empty/invalid input."""
    return CarScheduleResult(
        schedule=[],
        charging_slot_count=0,
        energy_needed_kwh=energy_needed_kwh,
        hours_needed=hours_needed,
        current_action="idle",
        is_preliminary=is_preliminary,
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

            duration = (end - start).total_seconds()
            if duration <= 0:
                continue

            result.append(
                _SlotInfo(
                    start=start,
                    end=end,
                    price=price,
                    action="idle",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return result


def _slot_energy_kwh(slot: _SlotInfo, max_charge_power_kw: float) -> float:
    """Return the energy deliverable in a slot at max charge power.

    Uses the slot's actual duration so 15-minute, 30-minute, hourly, and
    mixed-duration slot lists are all handled correctly (Nordpool moved from
    hourly to 15-minute price resolution, breaking the old "1 slot = 1 hour"
    assumption).
    """
    duration_hours = (slot.end - slot.start).total_seconds() / 3600.0
    return duration_hours * max_charge_power_kw


def _find_current_action(schedule: list[CarScheduleSlot], now: datetime) -> str:
    """Find the action for the slot containing 'now'.

    Args:
        schedule: Ordered list of schedule slots.
        now: Current UTC-aware datetime.

    Returns:
        Action string for the current slot, or "idle" if now is outside
        all slots.
    """
    for slot in schedule:
        if slot.start <= now < slot.end:
            return slot.action
    return "idle"

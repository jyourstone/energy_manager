"""Pure-Python battery scheduling algorithm with zero Home Assistant dependencies.

Implements multi-cycle charge/discharge scheduling using peak grouping and
virtual energy tracking. Ported from the proven AppDaemon HomeBatteryManager.

This module is intentionally free of any HA imports so it can be thoroughly
unit-tested independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


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
    # Stub: returns empty/idle result so tests can import but will fail assertions
    return BatteryScheduleResult(
        schedule=[],
        charging_slot_count=0,
        discharging_slot_count=0,
        next_charging_slot=None,
        next_discharging_slot=None,
        current_action="idle",
        target_ems_mode="standby",
    )

"""Pure-Python EMS controller calculation module with zero Home Assistant dependencies.

Determines battery EMS mode, safe charging limits, and fuse protection based on:
- Schedule-driven mode selection (from BatteryScheduleCoordinator)
- Dynamic fuse headroom calculation and charge limiting
- Car charging priority override
- PV opportunistic charging with hysteresis

This module is intentionally free of any HA imports so it can be thoroughly
unit-tested independently, following the Phase 2 battery_scheduler.py pattern.

The EMSCoordinator (Plan 03-02) will call compute_ems_state() and handle all I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EMSDecision:
    """Result of EMS state computation.

    Attributes:
        target_mode: EMS mode to set -- "command_charging",
            "max_self_consumption", or "standby".
        charge_limit_kw: Safe charging limit in kW (fuse-limited).
            Only non-zero when target_mode is "command_charging".
        fuse_headroom_amps: Available fuse headroom in amps. Always >= 0.
        override_reason: Why mode differs from the schedule target, or None.
            Possible values: "car_charging_priority", "pv_opportunistic".
    """

    target_mode: str
    charge_limit_kw: float
    fuse_headroom_amps: float
    override_reason: str | None


# ---------------------------------------------------------------------------
# PV Hysteresis State Machine
# ---------------------------------------------------------------------------


class PVHysteresisTracker:
    """State machine to prevent PV charging oscillation from fluctuating solar.

    Uses consecutive-check counting with separate activate/deactivate thresholds
    (hysteresis band) to avoid rapid on/off cycling.

    States: "off" -> "pending_on" -> "on" -> "pending_off" -> "off"

    Args:
        activate_threshold_w: PV power in watts to start considering activation.
        deactivate_threshold_w: PV power in watts below which deactivation starts.
            Must be less than activate_threshold_w for proper hysteresis.
        required_consecutive: Number of consecutive checks at threshold before
            transitioning. Prevents single-sample noise from triggering changes.
    """

    def __init__(
        self,
        activate_threshold_w: float = 500.0,
        deactivate_threshold_w: float = 300.0,
        required_consecutive: int = 2,
    ) -> None:
        self._activate_threshold_w = activate_threshold_w
        self._deactivate_threshold_w = deactivate_threshold_w
        self._required_consecutive = required_consecutive
        self._state: str = "off"
        self._counter: int = 0

    @property
    def state(self) -> str:
        """Current hysteresis state for logging/debugging."""
        return self._state

    def update(self, pv_power_w: float) -> bool:
        """Process a new PV power reading and return whether PV charging is active.

        Args:
            pv_power_w: Current PV power output in watts.

        Returns:
            True if PV charging should be active, False otherwise.
        """
        if self._state == "off":
            if pv_power_w >= self._activate_threshold_w:
                self._state = "pending_on"
                self._counter = 1
            return False

        if self._state == "pending_on":
            if pv_power_w >= self._activate_threshold_w:
                self._counter += 1
                if self._counter >= self._required_consecutive:
                    self._state = "on"
                    self._counter = 0
                    return True
                return False
            # Solar dropped -- reset to off
            self._state = "off"
            self._counter = 0
            return False

        if self._state == "on":
            if pv_power_w < self._deactivate_threshold_w:
                self._state = "pending_off"
                self._counter = 1
                return True  # Still active during pending_off
            return True

        if self._state == "pending_off":
            if pv_power_w < self._deactivate_threshold_w:
                self._counter += 1
                if self._counter >= self._required_consecutive:
                    self._state = "off"
                    self._counter = 0
                    return False
                return True  # Still active during pending_off
            # Solar recovered -- back to on
            self._state = "on"
            self._counter = 0
            return True

        return False  # pragma: no cover -- unreachable with valid states


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_ems_state(
    target_ems_mode: str,
    current_l_amps: float,
    fuse_rating_amps: float,
    max_charge_power_kw: float,
    battery_soc_pct: float,
    car_scheduled: bool,
    car_plugged_in: bool,
    pv_power_w: float,
    pv_hysteresis_active: bool,
    max_soc_pct: float = 95.0,
    safety_buffer_amps: float = 2.0,
    voltage: float = 230.0,
) -> EMSDecision:
    """Compute the EMS mode and safe charging limit.

    All calculated values are clamped to safe ranges. Fuse headroom is never
    negative. Charge limit never exceeds max_charge_power_kw or fuse capacity.

    Processing order (safety-first):
        1. Fuse headroom calculation
        2. Map "idle" to "max_self_consumption"
        3. Car priority override check
        4. Fuse-limited charging power
        5. PV opportunistic charging check
        6. Return final decision

    Args:
        target_ems_mode: Schedule-driven target mode from BatteryScheduleCoordinator.
            One of "command_charging", "max_self_consumption", "standby", "idle".
        current_l_amps: Current highest phase load in amps.
        fuse_rating_amps: Installed fuse rating in amps.
        max_charge_power_kw: Maximum battery charging power in kW.
        battery_soc_pct: Current battery state of charge (0-100).
        car_scheduled: Whether a car charging session is scheduled.
        car_plugged_in: Whether the car is currently plugged in.
        pv_power_w: Current PV power output in watts.
        pv_hysteresis_active: Whether PV hysteresis tracker says PV charging
            should be active (from PVHysteresisTracker.update()).
        max_soc_pct: Maximum SOC target percentage. PV charging skipped above this.
        safety_buffer_amps: Amps reserved as safety margin on fuse headroom.
        voltage: Grid voltage in volts for amps-to-kW conversion.

    Returns:
        EMSDecision with the target mode, safe charge limit, fuse headroom,
        and any override reason.
    """
    # 1. Fuse headroom calculation (EMS-02) -- never negative
    headroom = max(0.0, fuse_rating_amps - current_l_amps - safety_buffer_amps)

    # 2. Map "idle" to "max_self_consumption" (idle = let battery optimize)
    mode = target_ems_mode
    if mode == "idle":
        mode = "max_self_consumption"

    # 3. Car priority override (EMS-03): pause battery charging for car
    if car_scheduled and car_plugged_in and mode == "command_charging":
        return EMSDecision(
            target_mode="standby",
            charge_limit_kw=0.0,
            fuse_headroom_amps=headroom,
            override_reason="car_charging_priority",
        )

    # 4. Fuse-limited charging power (EMS-02)
    headroom_kw = (headroom * voltage) / 1000.0
    safe_charge_kw = max(0.0, min(max_charge_power_kw, headroom_kw))

    # 5. PV opportunistic charging (EMS-08)
    if mode in ("standby", "max_self_consumption") and pv_hysteresis_active:
        if battery_soc_pct < max_soc_pct:
            pv_kw = pv_power_w / 1000.0
            return EMSDecision(
                target_mode="command_charging",
                charge_limit_kw=min(pv_kw, safe_charge_kw),
                fuse_headroom_amps=headroom,
                override_reason="pv_opportunistic",
            )

    # 6. Return final decision
    charge_limit = safe_charge_kw if mode == "command_charging" else 0.0
    return EMSDecision(
        target_mode=mode,
        charge_limit_kw=charge_limit,
        fuse_headroom_amps=headroom,
        override_reason=None,
    )


def clamp_amps(
    value: float,
    min_amps: float = 0.0,
    max_amps: float = 32.0,
) -> float:
    """Hard-clamp an amp value to a safe range.

    Ensures no negative values and no values exceeding the maximum are ever
    sent to hardware. This is the single exit point for all amp values before
    they reach any device control call.

    Args:
        value: The amp value to clamp.
        min_amps: Minimum allowed amps (default 0.0).
        max_amps: Maximum allowed amps (default 32.0).

    Returns:
        The clamped amp value, guaranteed to be in [min_amps, max_amps].
    """
    return max(min_amps, min(value, max_amps))

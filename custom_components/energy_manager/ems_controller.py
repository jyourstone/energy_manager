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
from datetime import datetime

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
    sensor_blocked: bool = False,
    available_ess_amps: float | None = None,
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
        current_l_amps: Signed worst-case phase current in amps. Positive means
            import (load on the fuse); negative means export (adds headroom).
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
        sensor_blocked: When True, the current sensor(s) were unavailable and
            the configured fail-behavior is "block" -- headroom is forced to
            0 (no charge authorization) regardless of current_l_amps.
        available_ess_amps: Optional pre-computed amps ceiling to use for the
            charging-power derivation instead of the raw fuse headroom (see
            compute_available_ess_amps() and ESSLimitRateLimiter). Leave None
            to use the raw fuse headroom directly (previous behavior).
            fuse_headroom_amps in the result always reports the raw,
            instantaneous physical headroom regardless of this override.

    Returns:
        EMSDecision with the target mode, safe charge limit, fuse headroom,
        and any override reason.
    """
    # 1. Fuse headroom calculation (EMS-02) -- never negative. current_l_amps
    # is signed: export (negative) increases headroom, import (positive)
    # reduces it.
    headroom = max(0.0, fuse_rating_amps - current_l_amps - safety_buffer_amps)
    if sensor_blocked:
        headroom = 0.0

    # Ceiling actually used to derive the charging power -- defaults to the
    # raw headroom above, but callers may supply a pre-computed value (e.g.
    # with battery self-consumption add-back and ESS-limit rate limiting
    # already applied externally, see coordinator.py).
    charge_ceiling_amps = headroom if available_ess_amps is None else available_ess_amps
    if sensor_blocked:
        charge_ceiling_amps = 0.0

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
    headroom_kw = (charge_ceiling_amps * voltage) / 1000.0
    safe_charge_kw = max(0.0, min(max_charge_power_kw, headroom_kw))

    # 5. PV opportunistic charging (EMS-08)
    if (
        mode in ("standby", "max_self_consumption")
        and pv_hysteresis_active
        and battery_soc_pct < max_soc_pct
    ):
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


def worst_case_signed_amps(phase_amps: list[float]) -> float:
    """Return the worst-case (highest) signed current across grid phases.

    Positive values represent import (load on the fuse); negative values
    represent export (extra headroom). The worst case for fuse protection
    is always the highest (most-import) value -- an exporting or lightly
    loaded phase must never mask an overloaded one.

    Args:
        phase_amps: Signed amps for each configured phase.

    Returns:
        The maximum value in phase_amps.
    """
    return max(phase_amps)


def compute_available_ess_amps(
    fuse_rating_amps: float,
    safety_buffer_amps: float,
    worst_phase_amps: float,
    battery_own_amps: float = 0.0,
    max_ess_charge_amps: float | None = None,
) -> float:
    """Compute the amps available for the battery's own charging.

    Grid current sensors measure the battery's own charging draw as part of
    the total load. Without compensation this causes a self-reinforcing
    ratchet: each cycle the battery's own previous charging shows up as
    "more load", so the computed limit keeps shrinking. Adding back
    battery_own_amps corrects for this so the ceiling reflects only the
    *other* household load.

    Args:
        fuse_rating_amps: Installed fuse rating in amps.
        safety_buffer_amps: Amps reserved as a safety margin.
        worst_phase_amps: Signed worst-case phase current (see
            worst_case_signed_amps()).
        battery_own_amps: The battery's own current charging draw in amps
            (0.0 when idle or discharging).
        max_ess_charge_amps: Optional hard cap on the result (hardware safety
            limit). None means no additional cap beyond the fuse math.

    Returns:
        Available amps for the battery to charge with, clamped to
        [0, max_ess_charge_amps] (or [0, inf) when no cap is given).
    """
    available = (
        fuse_rating_amps - safety_buffer_amps - worst_phase_amps + battery_own_amps
    )
    if max_ess_charge_amps is None:
        return max(0.0, available)
    return max(0.0, min(available, max_ess_charge_amps))


@dataclass(frozen=True)
class SensorFallbackResult:
    """Result of resolving a missing/unavailable current sensor reading.

    Attributes:
        effective_amps: Amps to use in place of the missing reading.
        force_zero_headroom: True when headroom must be forced to 0
            regardless of effective_amps (the "block" fail-behavior).
    """

    effective_amps: float
    force_zero_headroom: bool


def resolve_current_sensor_fallback(
    fail_behavior: str,
    assumed_load_amps: float,
) -> SensorFallbackResult:
    """Decide what to do when the L-current/phase sensors are unavailable.

    Callers should only invoke this once a sensor read has actually failed
    (unavailable, unknown, or unconfigured) -- use the real reading directly
    otherwise. This replaces the previous silent "assume 0A" fallback, which
    made fuse headroom always report a static, incorrect value.

    Args:
        fail_behavior: "assume_load" to use assumed_load_amps as the measured
            load, or "block" to treat headroom as 0 (no charge authorization).
        assumed_load_amps: Amps to assume when fail_behavior is "assume_load".

    Returns:
        SensorFallbackResult with the effective amps and whether headroom
        should be forced to zero.
    """
    if fail_behavior == "block":
        return SensorFallbackResult(effective_amps=0.0, force_zero_headroom=True)
    return SensorFallbackResult(
        effective_amps=assumed_load_amps, force_zero_headroom=False
    )


def car_demands_priority_charging(cars: list[tuple[bool, bool]]) -> bool:
    """Return True if any car has an active charge slot AND is home+plugged.

    A car "demands" priority charging only when both are true at once: its
    computed schedule currently wants to charge, and it is actually home and
    plugged in. Schedules remain visible/computed regardless of plugged
    state -- this check is only used to decide battery priority override.

    Args:
        cars: List of (active_slot, home_and_plugged) tuples, one per car
            coordinator. active_slot is True when the car's current_action
            is "charge" or "solar_charge".

    Returns:
        True if at least one car currently demands priority charging.
    """
    return any(
        active_slot and home_and_plugged for active_slot, home_and_plugged in cars
    )


class ESSLimitRateLimiter:
    """Asymmetric timing for ESS (battery) charge-limit changes.

    Decreases apply immediately -- reducing the charge limit is always safe
    and must never be delayed. Increases only take effect once the computed
    value has stayed at or above the pending candidate continuously for
    increase_delay_seconds, preventing rapid ramp-up as fuse headroom
    fluctuates. A decrease cancels any pending increase.

    Stateful and mutated by update() -- not part of compute_ems_state() so
    the pure decision function stays stateless (same pattern as
    PVHysteresisTracker: the coordinator owns and updates the tracker once
    per cycle and threads the result through).
    """

    def __init__(self, increase_delay_seconds: float = 180.0) -> None:
        """Initialize the rate limiter.

        Args:
            increase_delay_seconds: How long a higher value must be
                continuously observed before it is applied.
        """
        self._increase_delay_seconds = increase_delay_seconds
        self._applied: float | None = None
        self._pending: float | None = None
        self._pending_since: datetime | None = None

    @property
    def applied(self) -> float | None:
        """Currently applied (rate-limited) value, or None before first update."""
        return self._applied

    def update(self, computed: float, now: datetime) -> float:
        """Process a new computed value and return the value to actually apply.

        Args:
            computed: The freshly computed (unrated) value for this cycle.
            now: Current UTC timestamp.

        Returns:
            The value that should actually be applied this cycle.
        """
        if self._applied is None:
            # First reading -- apply immediately, no history to compare against.
            self._applied = computed
            return self._applied

        if computed <= self._applied:
            # Decrease (or unchanged) -- apply immediately, cancel any pending increase.
            self._applied = computed
            self._pending = None
            self._pending_since = None
            return self._applied

        # computed > self._applied -- candidate increase.
        if self._pending is None or computed < self._pending:
            # New (lower) target than previously tracked -- restart the timer.
            self._pending = computed
            self._pending_since = now
            return self._applied

        elapsed = (now - self._pending_since).total_seconds()
        if elapsed >= self._increase_delay_seconds:
            self._applied = self._pending
            self._pending = None
            self._pending_since = None

        return self._applied


# ---------------------------------------------------------------------------
# Observe-only command gating (CORE-14)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandDecision:
    """Decision of whether to send an outgoing device command, or suppress it.

    This is the single choke-point decision behind the master "Device
    control" switch (CORE-14): when the switch is OFF the integration is
    observe-only -- every coordinator still computes and publishes its
    decisions, but no hass.services.async_call is ever made.

    Attributes:
        should_send: True if the caller should actually invoke the HA
            service call. False means the command must be suppressed.
        dry_run_message: Human-readable "[dry-run] Would call ..." message
            describing exactly what would have been sent. Callers log (and
            record) this only when should_send is False.
    """

    should_send: bool
    dry_run_message: str


def build_command_decision(
    control_enabled: bool,
    service_domain: str,
    service_name: str,
    entity_id: str,
    value: str | float,
) -> CommandDecision:
    """Decide whether to send a device command or suppress it (observe-only).

    Args:
        control_enabled: State of the master "Device control" switch. False
            (the default) means observe-only -- suppress the command.
        service_domain: HA service domain, e.g. "select" or "number".
        service_name: HA service name, e.g. "select_option" or "set_value".
        entity_id: Target entity ID the command would be sent to.
        value: The option string or numeric value that would be sent.

    Returns:
        CommandDecision with should_send and a dry-run description message.
    """
    dry_run_message = (
        f"[dry-run] Would call {service_domain}.{service_name} on "
        f"{entity_id} with value={value!r}"
    )
    return CommandDecision(
        should_send=control_enabled, dry_run_message=dry_run_message
    )

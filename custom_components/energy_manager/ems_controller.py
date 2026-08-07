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

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from .charger_state_machine import POWER_ACTIVE_THRESHOLD_KW

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EMSDecision:
    """Result of EMS state computation.

    Attributes:
        target_mode: EMS mode to set -- "command_charging",
            "command_discharging", "max_self_consumption", or "standby".
        charge_limit_kw: Safe charging limit in kW (fuse-limited).
            Only non-zero when target_mode is "command_charging".
        fuse_headroom_amps: Available fuse headroom in amps. Always >= 0.
        override_reason: Why mode differs from the schedule target, or None.
            Possible values: "car_charging_priority", "pv_opportunistic",
            "discharge_gate_closed", "max_soc_reached".
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
    max_soc_pct: float = 100.0,
    safety_buffer_amps: float = 1.0,
    voltage: float = 230.0,
    sensor_blocked: bool = False,
    available_ess_amps: float | None = None,
    *,
    discharge_allowed: bool,
    discharge_gate_reason: str,
    car_charging_active: bool,
    house_consumption_kw: float,
) -> EMSDecision:
    """Compute the EMS mode and safe charging limit.

    All calculated values are clamped to safe ranges. Fuse headroom is never
    negative. Charge limit never exceeds max_charge_power_kw or fuse capacity.

    Processing order (safety-first):
        1. Fuse headroom calculation
        2. Map "idle" to "max_self_consumption"
        3. Car priority override check
        4. Fuse-limited charging power
        5. PV opportunistic charging check (surplus-capped: PV minus house
           consumption, never gross PV)
        6. Standby hold check (closed discharge gate / active car charge)
        7. Return final decision

    Args:
        target_ems_mode: Schedule-driven target mode from BatteryScheduleCoordinator.
            One of "command_charging", "command_discharging",
            "max_self_consumption", "standby", "idle". "command_discharging"
            (BATT-17 export) passes through untouched with charge_limit_kw
            forced to 0.0 like every other non-charging mode. Car priority
            intentionally does not override export -- battery export offsets
            car import at the meter, never adds fuse load.
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
        discharge_allowed: Whether the discharge gate is currently open
            (from compute_discharge_gate). The SigenStor IGNORES the
            max-discharging-limit register in max_self_consumption mode
            (verified on hardware), so a closed gate must be enforced by
            commanding "standby" -- MSC + limit 0 does not hold the battery.
        discharge_gate_reason: Why the gate is closed. Only economic reasons
            ("below_threshold", "reserved_for_peak") trigger the standby
            hold; "no_schedule" (price-feed outage / cold boot) falls back
            to max_self_consumption -- graceful degradation, a frozen full
            battery during a multi-day price outage is worse than
            self-consumption at unknown prices.
        car_charging_active: Whether a car is actively drawing charge right
            now (see car_actively_charging()). MSC must never run while the
            car draws -- the battery discharges freely in MSC (owner rule:
            battery never discharges into the car). Export
            (command_discharging) stays exempt by design: it offsets car
            import at the meter (EMS-03).
        house_consumption_kw: Net house consumption in kW (CORE-11
            exclusions applied), 0.0 when no house-consumption entity is
            configured. Must EXCLUDE the battery's own charging draw (the
            existing EV surplus formula makes the same assumption).
            Deliberately INCLUDES the EV charger draw, so an active car
            charging session claims the surplus before the battery does.

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

    # 3b. Max-SoC ceiling: the schedule only sizes charge slots at
    # recalculation time, so a slot can outlive the target being reached.
    # Standby (not MSC) -- the gate is definitionally open during a charge
    # slot, and MSC would immediately cycle the just-stored energy into
    # house load at the cheap prices the slot was scheduled for.
    if mode == "command_charging" and battery_soc_pct >= max_soc_pct:
        return EMSDecision(
            target_mode="standby",
            charge_limit_kw=0.0,
            fuse_headroom_amps=headroom,
            override_reason="max_soc_reached",
        )

    # 4. Fuse-limited charging power (EMS-02)
    headroom_kw = (charge_ceiling_amps * voltage) / 1000.0
    safe_charge_kw = max(0.0, min(max_charge_power_kw, headroom_kw))

    # 5. PV opportunistic charging (EMS-08): the SigenStor executes
    # command_charging as "Command Charging (PV First)" -- prefer PV, but
    # top up from the grid to reach the commanded limit. Commanding gross
    # PV power (rather than the surplus left over after house load) would
    # therefore push the house's own consumption onto the grid as an
    # import, silently grid-charging the battery outside any scheduled
    # cheap slot. Capping at surplus keeps this branch grid-neutral.
    if (
        mode in ("standby", "max_self_consumption")
        and pv_hysteresis_active
        and battery_soc_pct < max_soc_pct
    ):
        pv_surplus_kw = max(0.0, pv_power_w / 1000.0 - house_consumption_kw)
        if pv_surplus_kw > 0.0:
            return EMSDecision(
                target_mode="command_charging",
                charge_limit_kw=min(pv_surplus_kw, safe_charge_kw),
                fuse_headroom_amps=headroom,
                override_reason="pv_opportunistic",
            )

    # 6. Standby hold: the SigenStor ignores the max-discharging-limit
    # register in max_self_consumption, so any state that must hold the
    # battery has to command "standby" instead of MSC. Placed after step 5
    # so PV surplus still promotes out of MSC.
    if mode == "max_self_consumption":
        if car_charging_active:
            return EMSDecision(
                target_mode="standby",
                charge_limit_kw=0.0,
                fuse_headroom_amps=headroom,
                override_reason="car_charging_priority",
            )
        if not discharge_allowed and discharge_gate_reason != "no_schedule":
            return EMSDecision(
                target_mode="standby",
                charge_limit_kw=0.0,
                fuse_headroom_amps=headroom,
                override_reason="discharge_gate_closed",
            )

    # 7. Return final decision
    charge_limit = safe_charge_kw if mode == "command_charging" else 0.0
    return EMSDecision(
        target_mode=mode,
        charge_limit_kw=charge_limit,
        fuse_headroom_amps=headroom,
        override_reason=None,
    )


def compute_export_limit_kw(
    fuse_rating_amps: float,
    safety_buffer_amps: float,
    battery_soc_pct: float,
    export_reserve_soc_pct: float,
    soc_available: bool,
    pv_power_kw: float = 0.0,
    max_limit_kw: float = 15.0,
) -> float | None:
    """Compute the fuse-capped discharge limit for a BATT-17 export slot.

    The plant's own discharge limit (14.4 kW) exceeds the 13.8 kW ceiling of
    a 20 A main fuse -- commanding export at the entity max would trip the
    fuse, so this cap is mandatory: total battery output is bounded by
    (fuse - buffer) x 3 phases x 230 V. House load is deliberately NOT
    added on top: the battery exports balanced across phases while house
    load may sit on a single phase, so a total-load add-back could push an
    unloaded phase past its per-phase rating. Capping total output at the
    per-phase-derived ceiling keeps every phase <= (fuse - buffer) amps
    regardless of load distribution (at the cost of up to house-load kW of
    export capacity).

    Fail-safe rules:
    - soc_available False, or a non-finite SOC value (NaN from a Modbus or
      template glitch), returns None (export must not run) -- an unknown
      SOC must never enable export; the coordinator's 50.0 default read
      would otherwise sail past a 20% reserve floor.
    - battery_soc_pct at or below export_reserve_soc_pct returns None --
      the runtime reserve-floor stop, re-checked every cycle.

    The SigenStor inverter's own backup/min-SOC (hardware floor) is a
    documented precondition, not checked here — see
    https://energy-manager.dinsten.se/user-guide/battery-export-arbitrage/

    Args:
        fuse_rating_amps: Installed main fuse rating in amps.
        safety_buffer_amps: Amps reserved as safety margin on the fuse.
        battery_soc_pct: Current battery state of charge (0-100).
        export_reserve_soc_pct: Never export at or below this SOC.
        soc_available: Whether the SOC sensor has a real value right now.
        pv_power_kw: Live PV production in kW. PV and battery share the
            same grid connection on a hybrid inverter, so concurrent PV
            output is subtracted from the battery's export allowance --
            otherwise a sunny export slot could push combined injection
            past the fuse (Greptile PR #7). Negative readings clamp to 0.
            Re-sampled every cycle; PV drops only raise the cap (safe
            direction is instant, the raise is re-asserted declaratively).
        max_limit_kw: Hard ceiling on the returned limit (hardware max).

    Returns:
        The discharge limit in kW to command during export, or None when
        export must not run.
    """
    if not soc_available or not math.isfinite(battery_soc_pct):
        return None
    if battery_soc_pct <= export_reserve_soc_pct:
        return None
    fuse_cap_kw = (fuse_rating_amps - safety_buffer_amps) * 3 * 0.230
    fuse_cap_kw -= max(0.0, pv_power_kw)
    return max(0.0, min(fuse_cap_kw, max_limit_kw))


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


def should_file_fallback_issue(
    fallback_since: float | None,
    now: float,
    threshold_seconds: float,
) -> bool:
    """Decide whether a continuous sensor fallback warrants a Repairs issue.

    The rate-limited log warning fires on the first failed read; the
    Repairs issue is reserved for persistent outages, so it is only filed
    once the fallback has been continuously active for threshold_seconds.

    Args:
        fallback_since: Monotonic timestamp (seconds) of the first read in
            the current uninterrupted fallback streak, or None if the last
            read succeeded.
        now: Current monotonic timestamp in seconds.
        threshold_seconds: Continuous-fallback duration required to file.

    Returns:
        True when the fallback has been active for at least
        threshold_seconds.
    """
    return fallback_since is not None and now - fallback_since >= threshold_seconds


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


def car_actively_charging(
    charger_status: str | None,
    charger_power_kw: float | None,
) -> bool:
    """Return True if a car is actively drawing charge right now.

    Measured truth only: the reported status says "charging", or measured
    power exceeds POWER_ACTIVE_THRESHOLD_KW (status is unreliable, power is
    always cross-checked -- same rule as the charger state machine).
    Deliberately no charger-mode ("forced") term: forced mode persists for
    days with a plugged-but-not-accepting car and would freeze the battery
    with zero current flowing.

    Args:
        charger_status: Normalized charger status string, or None when
            unavailable.
        charger_power_kw: Measured charger power in kW, or None when
            unavailable.

    Returns:
        True if a car is actively charging.
    """
    return (
        charger_status == "charging"
        or (charger_power_kw or 0.0) > POWER_ACTIVE_THRESHOLD_KW
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
# Command-path resilience (incident 2026-08-07)
# ---------------------------------------------------------------------------


class WriteRejectionBackoff:
    """Retry throttle for a device write that keeps getting rejected.

    A Modbus-level rejection (e.g. an ExceptionResponse raised through the
    vendor integration's service call) is typically not transient:
    retrying every evaluation cycle produced an error line every ~10s for
    five hours (incident 2026-08-07). After failure_threshold consecutive
    failures the write is only retried every retry_every_n_cycles-th
    wanted cycle (~5 min at the 30s cadence). Any success resets to full
    cadence.

    Only genuine service-call failures count: a write skipped for other
    reasons (observe-only suppression, unavailable entity) leaves the
    counter untouched.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        retry_every_n_cycles: int = 10,
    ) -> None:
        """Initialize the backoff.

        Args:
            failure_threshold: Consecutive failures that enter backoff.
            retry_every_n_cycles: Retry interval, in wanted-write cycles,
                while in backoff.
        """
        self._failure_threshold = failure_threshold
        self._retry_every_n_cycles = retry_every_n_cycles
        self._consecutive_failures = 0
        self._skipped_cycles = 0

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive-failure count."""
        return self._consecutive_failures

    @property
    def retry_every_n_cycles(self) -> int:
        """Backoff retry interval in wanted-write cycles."""
        return self._retry_every_n_cycles

    @property
    def in_backoff(self) -> bool:
        """True once failure_threshold consecutive failures are recorded."""
        return self._consecutive_failures >= self._failure_threshold

    def should_attempt(self) -> bool:
        """Whether this cycle's wanted write should actually be attempted.

        Call exactly once per evaluation cycle that WANTS this write --
        skipped wanted cycles are what the backoff interval counts.
        """
        if not self.in_backoff:
            return True
        self._skipped_cycles += 1
        if self._skipped_cycles >= self._retry_every_n_cycles:
            self._skipped_cycles = 0
            return True
        return False

    def record_success(self) -> None:
        """Reset to full retry cadence after a successful write."""
        self._consecutive_failures = 0
        self._skipped_cycles = 0

    def record_failure(self) -> bool:
        """Record a rejected write.

        Returns:
            True exactly when this failure ENTERS backoff -- the caller
            logs its one-time WARNING on that transition.
        """
        was_in_backoff = self.in_backoff
        self._consecutive_failures += 1
        return self.in_backoff and not was_in_backoff


async def guarded_device_write(
    target: str,
    send: Callable[[], Awaitable[bool]],
    backoff: WriteRejectionBackoff,
    bypass_backoff: bool = False,
) -> bool:
    """Run one device write with exception isolation and rejection backoff.

    Incident 2026-08-07: a SigenergyModbusError raised straight through
    hass.services.async_call(blocking=True) aborted the whole EMS cycle
    every ~10s for five hours -- everything after the raising write (later
    sends, the mode-belief update, verification scheduling, EMSData
    publishing) never ran. Each write is therefore isolated: a raising
    write logs and fails THAT write only, and persistent rejections back
    off via backoff. Broad Exception on purpose -- vendor integrations
    raise arbitrary types.

    Args:
        target: Human-readable write-target description for log lines.
        send: Zero-arg coroutine factory performing the actual write;
            returns True when the service call was made, False when it was
            skipped (unavailable entity, observe-only suppression).
        backoff: The per-target WriteRejectionBackoff.
        bypass_backoff: Skip the should_attempt() gate (safety-critical
            limit decreases must never be delayed by another target's
            backoff -- incident 2026-08-07 saw a SigenStor accept 0.0 but
            reject a nonzero value on the same register) while still
            recording the outcome on backoff, so a persistently rejected
            bypassed write still enters backoff for its own future
            increases.

    Returns:
        True when the write was actually sent, False otherwise.
    """
    if not bypass_backoff and not backoff.should_attempt():
        _LOGGER.debug(
            "Skipping %s write: backing off after %d consecutive rejections",
            target,
            backoff.consecutive_failures,
        )
        return False
    try:
        sent = await send()
    except Exception as err:  # noqa: BLE001 -- vendor integrations raise arbitrary types
        _LOGGER.error("%s write failed: %s", target, err)
        if backoff.record_failure():
            _LOGGER.warning(
                "%s write rejected %d consecutive times -- backing off to "
                "one retry every %d cycles",
                target,
                backoff.consecutive_failures,
                backoff.retry_every_n_cycles,
            )
        return False
    if sent:
        backoff.record_success()
    return sent


def ems_select_mismatch(
    last_sent_mode: str | None,
    live_option: str | None,
    mode_map: dict[str, str],
) -> bool:
    """Whether the live EMS select option contradicts the mode belief.

    Incident 2026-08-07: a crash between the select send and the
    end-of-cycle belief update left _last_sent_mode behind the hardware,
    and command dedup then silently dropped every later command for the
    actually-needed mode. Reconciling belief against the live select each
    cycle makes that state self-healing.

    Args:
        last_sent_mode: Internal mode EM believes it last sent, or None
            when nothing has been commanded yet -- never a mismatch.
        live_option: The select entity's current option, or None when the
            entity is unavailable (normal for ~2 min after an HA restart)
            -- never a mismatch.
        mode_map: Internal mode -> select option string (EMS_MODE_MAP).

    Returns:
        True when both sides are known and disagree (including a live
        option EM never sends -- the hardware is still not in the
        commanded mode).
    """
    if last_sent_mode is None or live_option is None:
        return False
    expected = mode_map.get(last_sent_mode)
    if expected is None:
        return False
    return live_option != expected


class StandbyDischargeMonitor:
    """Commanded-vs-measured alarm condition for the standby hold.

    Standby is a hard hold contract (owner rule: the battery never
    discharges into the car), but the inverter can sit in another mode
    discharging freely while EM believes it commanded standby (incident
    2026-08-07: 12 kW from the home battery into the car for hours).
    Fires only after required_consecutive cycles above threshold_kw so a
    single stale power reading right after a mode change cannot cry wolf.
    """

    def __init__(
        self,
        threshold_kw: float = 0.5,
        required_consecutive: int = 2,
    ) -> None:
        """Initialize the monitor.

        Args:
            threshold_kw: Discharge power that must be exceeded to count.
            required_consecutive: Consecutive qualifying cycles before the
                alarm condition holds.
        """
        self._threshold_kw = threshold_kw
        self._required_consecutive = required_consecutive
        self._count = 0

    def update(
        self, commanded_standby: bool, discharge_kw: float | None
    ) -> bool:
        """Process one cycle's commanded/measured pair.

        Args:
            commanded_standby: Whether EM currently believes it commanded
                standby (and no mode verification is still pending).
            discharge_kw: Measured battery discharge in kW (>= 0), or None
                when the battery power sensor is unavailable -- resets the
                streak, never alarms.

        Returns:
            True while the alarm condition holds (the caller rate-limits
            the actual log line).
        """
        if (
            not commanded_standby
            or discharge_kw is None
            or discharge_kw <= self._threshold_kw
        ):
            self._count = 0
            return False
        self._count += 1
        return self._count >= self._required_consecutive


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


def derive_battery_status(
    *,
    plan_state: str,
    ems_mode: str,
    charge_limit_kw: float,
    pv_charging_active: bool,
    car_override_active: bool,
    export_limit_kw: float | None,
    discharge_allowed: bool,
    battery_power_kw: float | None = None,
) -> str:
    """Derive the single live battery status for the Battery status sensor.

    Merges the price plan's current slot with the live EMS layer into one
    state: what EM is currently DRIVING the battery to do (its live
    decision). In observe-only mode this is the would-be action -- the
    dry_run and command_verified sensor attributes tell whether commands
    are actually being sent and applied. The state still never claims an
    action EM has not decided on: a scheduled discharge whose gate is
    closed, or a scheduled solar-charge slot with no actual surplus,
    resolves to "self_consumption" (the SigenStor's autonomous
    max-self-consumption behavior), with the blocking reason exposed via
    sensor attributes.

    Args:
        plan_state: The schedule's current slot state (idle / grid_charging /
            solar_charging / discharging / exporting).
        ems_mode: The EMS mode currently commanded (e.g. max_self_consumption,
            command_charging).
        charge_limit_kw: The commanded charge limit -- a charging state is
            only claimed when a positive flow is actually authorized (a
            fuse-tight limit can clamp it to 0 while the override is
            still nominally active).
        pv_charging_active: Whether the PV-opportunistic override is active.
        car_override_active: Whether car priority is pausing battery
            grid-charging (EMS-03).
        export_limit_kw: Fuse-capped export limit while an export slot is
            active and the reserve floor is clear; None otherwise (BATT-17).
        discharge_allowed: Whether the discharge gate is currently open.
        battery_power_kw: Measured battery power in kW (any sign
            convention), or None when unavailable. Splits the fallback
            state: |power| below the noise floor means the battery is
            genuinely doing nothing (night, discharge blocked, house on
            grid) -> "holding"; otherwise it is actively balancing solar
            -> "self_consumption". None keeps "self_consumption" unless
            "standby" is commanded, which reports "holding" (the commanded
            intent) -- but a live nonzero reading always wins: a failed
            select write leaves the hardware in MSC discharging, and the
            sensor must keep reporting the measured truth.

    Returns:
        One of: self_consumption, holding, solar_charging, grid_charging,
        discharging, exporting, paused_car_priority.
    """
    if car_override_active:
        return "paused_car_priority"
    if pv_charging_active and charge_limit_kw > 0.0:
        return "solar_charging"
    if ems_mode == "command_charging" and charge_limit_kw > 0.0:
        return "grid_charging"
    if export_limit_kw is not None and export_limit_kw > 0.0:
        return "exporting"
    if plan_state == "discharging" and discharge_allowed:
        return "discharging"
    if battery_power_kw is not None and abs(battery_power_kw) < 0.05:
        return "holding"
    # No power sensor: trust the commanded standby hold. Checked only after
    # the measured-power branch so a live nonzero reading is never shadowed.
    if ems_mode == "standby" and battery_power_kw is None:
        return "holding"
    return "self_consumption"

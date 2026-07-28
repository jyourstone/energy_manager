"""Pure-Python Easee charger control module with zero Home Assistant dependencies.

Determines charger mode arbitration (forced/scheduled/solar/idle), safe dynamic
amp limits under fuse protection, 1/3-phase switching, and safety layers based
on:
- Mode arbitration: forced charging switch > scheduled car slot > solar
  surplus > idle
- Dynamic fuse headroom calculation (with add-back of the charger's own draw)
- Solar-surplus opportunistic charging with SOC gate + activation/deactivation
  hysteresis
- 1/3-phase switching choreography, modeled as an explicit non-blocking state
  machine (no asyncio.sleep choreography -- every multi-step sequence is a
  state with an entry timestamp, evaluated on ticks)
- Three fuse protection layers: emergency overload pause, headroom-based
  target, and 0A-target safety stop (Easee sometimes ignores a 0A limit)
- Unauthorized-charge suppression and stuck-state (unreliable status) recovery

This module is intentionally free of any HA imports so it can be thoroughly
unit-tested independently, following the Phase 3 ems_controller.py pattern.

The EaseeCoordinator (Wave B) will own a ChargerController instance, build a
ChargerInputs snapshot once per tick, call decide(), and execute the returned
commands via the easee integration's services.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Charger statuses that mean "session over" -- reset all internal state and
#: make no further adjustments until a new session begins.
TERMINAL_STATUSES = frozenset({"disconnected", "completed", "error"})

CHARGING_STATUS = "charging"
PAUSED_STATUS = "paused"

#: Charger power above this (kW) is treated as "actively drawing" even if the
#: reported status disagrees -- status is unreliable (live watchdog evidence),
#: power is always cross-checked.
POWER_ACTIVE_THRESHOLD_KW = 0.5


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CarDemand:
    """One car's charging demand for this tick.

    Attributes:
        active_slot: Whether the car's schedule currently wants to charge
            (current_action in ("charge", "solar_charge")).
        home_and_plugged: Whether the car is actually home and plugged in.
        phase_capability: How many phases this car actually draws on when the
            charger is in 3-phase mode: 1, 2, or 3. Some cars (e.g. VW ID.3)
            only ever use 2 of 3 available phases. Default 3.
        max_charge_kw: The car's own maximum charge power in kW -- an
            additional ceiling on the computed amp target so we never ask for
            more than the car itself can accept.
    """

    active_slot: bool
    home_and_plugged: bool
    phase_capability: int = 3
    max_charge_kw: float = 7.4


@dataclass(frozen=True)
class ChargerInputs:
    """Everything one tick needs to compute a charger decision.

    Attributes:
        charger_status: Raw Easee status string, e.g. "disconnected",
            "awaiting_start", "charging", "paused", "completed", "error".
        charger_power_kw: Measured charger power draw in kW.
        measured_worst_case_signed_amps: Signed worst-case phase current in
            amps (see ems_controller.worst_case_signed_amps) -- positive is
            import, negative is export.
        current_dynamic_limit_amps: The charger's currently configured Easee
            dynamic limit in amps (added back so the charger's own draw
            never counts against its own headroom).
        force_charging: State of the "force grid charging" switch (EASE-03).
        solar_surplus_kw: Current solar surplus available for the charger, in
            kW (pv - house consumption - battery charging + charger power).
        battery_soc_pct: House battery state of charge (0-100).
        current_phase_mode: The charger's actual reported phase mode, one of
            "single" or "three".
        now: UTC-aware current timestamp.
        fuse_rating_amps: Installed main fuse rating in amps.
        cars: Per-car demand snapshots (see CarDemand). v1 assumes a single
            plugged car; if multiple somehow demand at once, the first wins
            (surfaced via override_reason).
        safety_buffer_amps: Amps reserved as a safety margin on fuse headroom.
        min_amps: Easee's minimum settable dynamic limit (6A).
        max_amps: Maximum amps this module will ever request (16A).
        conversion_factor_1phase: A/kW conversion factor for 1-phase cars.
        conversion_factor_2phase: A/kW conversion factor for 2-phase cars.
        conversion_factor_3phase: A/kW conversion factor for 3-phase cars.
        grid_power_cap_kw: Absolute grid charging power ceiling in kW.
        grid_power_safety_buffer_kw: Safety margin subtracted from
            grid_power_cap_kw.
        phase_switch_threshold_kw: Available power threshold (kW) below which
            the charger must run single-phase instead of three-phase.
        solar_start_threshold_kw: Minimum net solar surplus (kW) to consider
            solar charging.
        solar_safety_buffer_kw: Safety margin subtracted from the raw solar
            surplus reading before comparing to solar_start_threshold_kw.
        solar_activation_delay_s: Seconds the raw solar condition must hold
            continuously before solar charging activates.
        solar_deactivation_delay_s: Seconds the raw solar condition must be
            continuously false before solar charging deactivates.
        battery_soc_gate_pct: Minimum house battery SOC before solar charging
            is allowed to divert surplus to the car (default 100%).
        soc_round_up: If True, battery_soc_pct is rounded up (ceil) before
            comparing against battery_soc_gate_pct -- avoids requiring an
            exact 100.0 reading that real SOC sensors rarely produce.
        emergency_margin_amps: Amps above fuse_rating_amps that triggers the
            emergency overload pause while charging.
        amp_increase_delay_s: Seconds a higher computed amp target must hold
            before being applied.
        amp_decrease_delay_s: Seconds a lower computed amp target must hold
            before being applied. Must never exceed amp_increase_delay_s in
            practice -- decreases are a safety property.
        phase_sequence_step_timeout_s: Seconds allowed for each phase-switch
            sequence state (PAUSING/SET_PHASE/RESUMING) to confirm before
            aborting to a safe paused state.
        command_stuck_timeout_s: Seconds allowed for a plain start/resume or
            pause/stop command to show an observable effect before the
            stuck flag is raised.
    """

    charger_status: str
    charger_power_kw: float
    measured_worst_case_signed_amps: float
    current_dynamic_limit_amps: float
    force_charging: bool
    solar_surplus_kw: float
    battery_soc_pct: float
    current_phase_mode: str
    now: datetime
    fuse_rating_amps: float
    cars: tuple[CarDemand, ...] = ()
    safety_buffer_amps: float = 2.0
    min_amps: float = 6.0
    max_amps: float = 16.0
    conversion_factor_1phase: float = 4.3
    conversion_factor_2phase: float = 2.5
    conversion_factor_3phase: float = 1.45
    grid_power_cap_kw: float = 12.0
    grid_power_safety_buffer_kw: float = 0.5
    phase_switch_threshold_kw: float = 4.1
    solar_start_threshold_kw: float = 1.5
    solar_safety_buffer_kw: float = 0.5
    solar_activation_delay_s: float = 300.0
    solar_deactivation_delay_s: float = 60.0
    battery_soc_gate_pct: float = 100.0
    soc_round_up: bool = True
    emergency_margin_amps: float = 2.0
    amp_increase_delay_s: float = 120.0
    amp_decrease_delay_s: float = 5.0
    phase_sequence_step_timeout_s: float = 15.0
    command_stuck_timeout_s: float = 60.0


@dataclass(frozen=True)
class ChargerCommand:
    """One outgoing Easee command.

    Attributes:
        action: One of "start", "pause", "resume", "stop",
            "set_dynamic_limit", "set_phase_mode".
        value: The amp value (set_dynamic_limit) or phase mode string
            (set_phase_mode), or None for the plain action_command actions.
    """

    action: str
    value: float | str | None = None


@dataclass(frozen=True)
class ChargerDecision:
    """Result of one ChargerController.decide() tick.

    Attributes:
        mode: "forced", "scheduled", "solar", or "idle".
        target_amps: The amp target this tick (informational when below
            min_amps or when no command was actually sent).
        target_phase_mode: The desired phase mode ("single"/"three").
        commands: Ordered commands to send this tick (empty when nothing to
            do). Callers execute these through the observe-only choke point.
        notifications: Human-readable safety notification messages (fuse
            emergency overload, 0A-target safety stop). Empty otherwise.
        sequence_state: Phase-switch sequence state: "idle", "pausing",
            "set_phase", "resuming", or "set_limit".
        stuck: True when a command was issued but showed no observable
            status/power effect within its timeout.
        override_reason: Why behavior is notable this tick (terminal state,
            emergency overload, unauthorized suppression, multiple cars
            demanding, phase-switch abort), or None.
    """

    mode: str
    target_amps: float
    target_phase_mode: str
    commands: tuple[ChargerCommand, ...]
    notifications: tuple[str, ...]
    sequence_state: str
    stuck: bool
    override_reason: str | None


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def clamp_amps(value: float, min_amps: float, max_amps: float) -> float:
    """Hard-clamp an amp value to [min_amps, max_amps]."""
    return max(min_amps, min(value, max_amps))


def conversion_factor_for_phase_capability(
    phase_capability: int,
    factor_1phase: float,
    factor_2phase: float,
    factor_3phase: float,
) -> float:
    """Return the A/kW conversion factor for a car's phase capability."""
    if phase_capability <= 1:
        return factor_1phase
    if phase_capability == 2:
        return factor_2phase
    return factor_3phase


def compute_charger_capacity_amps(
    fuse_rating_amps: float,
    safety_buffer_amps: float,
    measured_worst_case_signed_amps: float,
    current_dynamic_limit_amps: float,
    grid_power_cap_kw: float,
    grid_power_safety_buffer_kw: float,
    conversion_factor: float,
    car_max_charge_kw: float,
) -> float:
    """Compute the amps available for the charger this tick.

    Combines three independent ceilings and never returns a negative value:
        1. Fuse headroom: (fuse - buffer) - ceil(worst_signed_phase_amps) +
           current_easee_amps (add-back of the charger's own draw, since the
           measured phase current already includes it). ceil() is used on
           the measured amps because it is always the conservative (less
           generous) rounding direction regardless of sign -- for import
           (positive) it subtracts more; for export (negative) it adds back
           less.
        2. Grid power ceiling: (grid_power_cap_kw - grid_power_safety_buffer_kw)
           converted to amps via conversion_factor.
        3. The car's own max_charge_kw, converted to amps via
           conversion_factor -- never ask for more than the car can accept.

    Args:
        fuse_rating_amps: Installed main fuse rating in amps.
        safety_buffer_amps: Amps reserved as a safety margin.
        measured_worst_case_signed_amps: Signed worst-case phase current.
        current_dynamic_limit_amps: The charger's own currently configured
            dynamic limit (add-back proxy for its own draw).
        grid_power_cap_kw: Absolute grid charging power ceiling in kW.
        grid_power_safety_buffer_kw: Safety margin subtracted from the cap.
        conversion_factor: A/kW conversion factor for the relevant car.
        car_max_charge_kw: The car's own maximum charge power in kW.

    Returns:
        Available amps, clamped to >= 0.
    """
    fuse_available = (
        fuse_rating_amps
        - safety_buffer_amps
        - math.ceil(measured_worst_case_signed_amps)
        + current_dynamic_limit_amps
    )
    grid_power_ceiling_amps = (
        grid_power_cap_kw - grid_power_safety_buffer_kw
    ) * conversion_factor
    car_max_amps = car_max_charge_kw * conversion_factor
    return max(0.0, min(fuse_available, grid_power_ceiling_amps, car_max_amps))


def compute_solar_net_kw(solar_surplus_kw: float, safety_buffer_kw: float) -> float:
    """Return max(0, solar_surplus_kw - safety_buffer_kw)."""
    return max(0.0, solar_surplus_kw - safety_buffer_kw)


def compute_solar_raw_amps(net_solar_kw: float, conversion_factor: float) -> float:
    """Return floor(net_solar_kw * conversion_factor), never negative."""
    return float(math.floor(net_solar_kw * conversion_factor))


def soc_gate_satisfied(battery_soc_pct: float, gate_pct: float, round_up: bool) -> bool:
    """Whether battery SOC clears the solar-charging gate.

    Args:
        battery_soc_pct: Current battery state of charge (0-100).
        gate_pct: Minimum SOC required (e.g. 100.0).
        round_up: If True, battery_soc_pct is rounded up (ceil) first -- a
            real SOC sensor rarely reports an exact 100.0, so without
            rounding a gate of 100 would (almost) never be satisfied.
    """
    effective = math.ceil(battery_soc_pct) if round_up else battery_soc_pct
    return effective >= gate_pct


def phase_switch_target(
    phase_capability: int,
    capacity_amps: float,
    conversion_factor_3phase: float,
    phase_switch_threshold_kw: float,
) -> str:
    """Decide the desired charger phase mode ("single" or "three").

    A car with phase_capability 1 always stays single-phase. Cars able to
    use multiple phases (2 or 3) require the wallbox in "three" mode, which
    is only requested when there is enough available power to sustain the
    Easee 6A-per-phase minimum across all three physical phases -- the
    default 4.1kW threshold is exactly 3 * 6A * 230V.

    Args:
        phase_capability: The car's phase capability (1, 2, or 3).
        capacity_amps: Currently available amps (see
            compute_charger_capacity_amps()).
        conversion_factor_3phase: A/kW conversion factor for a 3-phase car,
            used to convert capacity_amps to the equivalent 3-phase kW.
        phase_switch_threshold_kw: Minimum 3-phase-equivalent available
            power required to run in "three" mode.

    Returns:
        "single" or "three".
    """
    if phase_capability < 2:
        return "single"
    three_phase_kw = capacity_amps / conversion_factor_3phase
    return "three" if three_phase_kw >= phase_switch_threshold_kw else "single"


# ---------------------------------------------------------------------------
# Stateful trackers
# ---------------------------------------------------------------------------


class ChargerAmpHysteresis:
    """Asymmetric timing for the charger's dynamic amp limit.

    Mirrors ems_controller.ESSLimitRateLimiter's "always keep the lower
    pending candidate, only restart the timer on an even-lower candidate"
    semantics, generalized to apply on both sides of the applied value:
    increases wait amp_increase_delay_s (120s tuned default), decreases wait
    amp_decrease_delay_s (5s tuned default) -- the decrease path must never
    be lengthened, so a decrease always uses its own (short) delay and a
    decrease immediately discards any stale increase-pending.

    Unlike ESSLimitRateLimiter, the delays are supplied on each update() call
    rather than at construction time, since here they are part of the
    per-tick ChargerInputs config snapshot (Wave B options can change live).
    """

    def __init__(self) -> None:
        self._applied: float | None = None
        self._pending: float | None = None
        self._pending_since: datetime | None = None

    @property
    def applied(self) -> float | None:
        """Currently applied (rate-limited) value, or None before first update."""
        return self._applied

    def update(
        self,
        computed: float,
        now: datetime,
        increase_delay_seconds: float,
        decrease_delay_seconds: float,
    ) -> float:
        """Process a new computed value and return the value to actually apply."""
        if self._applied is None:
            self._applied = computed
            return self._applied

        if computed == self._applied:
            self._pending = None
            self._pending_since = None
            return self._applied

        if computed < self._applied:
            delay = decrease_delay_seconds
            # Discard a stale increase-pending -- a decrease always cancels it.
            if self._pending is not None and self._pending > self._applied:
                self._pending = None
                self._pending_since = None
        else:
            delay = increase_delay_seconds
            # Discard a stale decrease-pending -- conditions recovered.
            if self._pending is not None and self._pending < self._applied:
                self._pending = None
                self._pending_since = None

        if self._pending is None or _more_extreme(computed, self._pending, self._applied):
            self._pending = computed
            self._pending_since = now

        elapsed = (now - self._pending_since).total_seconds()
        if elapsed >= delay:
            self._applied = self._pending
            self._pending = None
            self._pending_since = None
        return self._applied


def _more_extreme(candidate: float, pending: float, applied: float) -> bool:
    """Whether candidate is a safer (lower-magnitude-of-change) target than pending.

    For an increase (pending > applied), a lower candidate is safer (revalidate
    down). For a decrease (pending < applied), a lower candidate is more
    urgent/safer too. Either way "lower" wins and restarts the delay.
    """
    return candidate < pending


class SolarActivationTracker:
    """Time-delay hysteresis for solar-surplus charging activation.

    Unlike PVHysteresisTracker (consecutive-sample counting), this uses
    wall-clock delays matching the charger's tuned constants: activation
    requires the raw condition to hold continuously for activation_delay_s
    before turning on; deactivation requires it to be continuously false for
    deactivation_delay_s before turning off. The delays are supplied on each
    update() call (see ChargerAmpHysteresis docstring for why).
    """

    def __init__(self) -> None:
        self._active = False
        self._pending_since: datetime | None = None
        self._pending_target: bool | None = None

    @property
    def active(self) -> bool:
        """Whether solar charging is currently active."""
        return self._active

    def update(
        self,
        raw_ok: bool,
        now: datetime,
        activation_delay_seconds: float,
        deactivation_delay_seconds: float,
    ) -> bool:
        """Process a new raw solar-ok reading and return the active state."""
        if raw_ok == self._active:
            self._pending_since = None
            self._pending_target = None
            return self._active

        if self._pending_target != raw_ok:
            self._pending_target = raw_ok
            self._pending_since = now
            return self._active

        delay = activation_delay_seconds if raw_ok else deactivation_delay_seconds
        elapsed = (now - self._pending_since).total_seconds()
        if elapsed >= delay:
            self._active = raw_ok
            self._pending_since = None
            self._pending_target = None
        return self._active


# ---------------------------------------------------------------------------
# ChargerController -- stateful, owned by the coordinator, one decide() per tick
# ---------------------------------------------------------------------------


class ChargerController:
    """Stateful charger decision engine, updated once per coordinator tick.

    Owns the amp hysteresis tracker, the solar activation tracker, the
    phase-switch sequence state machine, and stuck-command tracking. Call
    decide() exactly once per tick with a fresh ChargerInputs snapshot.
    """

    def __init__(self) -> None:
        self._sequence_state: str = "idle"
        self._sequence_entered_at: datetime | None = None
        self._pending_phase_mode: str | None = None
        self._sequence_mode: str = "idle"
        self._amp_hysteresis = ChargerAmpHysteresis()
        self._solar_tracker = SolarActivationTracker()
        self._last_command_expect_active: bool | None = None
        self._last_command_at: datetime | None = None
        self._stuck: bool = False

    @property
    def sequence_state(self) -> str:
        """Current phase-switch sequence state, for diagnostics."""
        return self._sequence_state

    def decide(self, inputs: ChargerInputs) -> ChargerDecision:
        """Compute this tick's charger decision.

        See module docstring for the overall behavior. Processing order:
            1. Terminal-state reset (disconnected/completed/error)
            2. Stuck-command resolution (confirm or timeout)
            3. Mode arbitration (forced > scheduled > solar > idle)
            4. Unauthorized-charge suppression (idle mode)
            5. Fuse Layer 1: emergency overload pause
            6. Capacity computation (fuse headroom + grid power cap + car cap)
            7. Phase-switch sequence continuation, if one is in progress
            8. Phase-switch sequence start, if a switch is newly needed
            9. Pre-start gate + Fuse Layer 3 (0A-target safety stop)
            10. Amp hysteresis + start/resume + set_dynamic_limit
        """
        now = inputs.now
        is_drawing = (
            inputs.charger_status == CHARGING_STATUS
            or inputs.charger_power_kw > POWER_ACTIVE_THRESHOLD_KW
        )

        if inputs.charger_status in TERMINAL_STATUSES:
            self._reset_all()
            return ChargerDecision(
                mode="idle",
                target_amps=0.0,
                target_phase_mode=inputs.current_phase_mode,
                commands=(),
                notifications=(),
                sequence_state="idle",
                stuck=False,
                override_reason=f"terminal_{inputs.charger_status}",
            )

        self._resolve_command_tracking(is_drawing, now, inputs.command_stuck_timeout_s)

        demanding = [c for c in inputs.cars if c.active_slot and c.home_and_plugged]
        present = [c for c in inputs.cars if c.home_and_plugged]
        note = "multiple_cars_demanding_first_selected" if len(demanding) > 1 else None

        net_solar_kw = compute_solar_net_kw(inputs.solar_surplus_kw, inputs.solar_safety_buffer_kw)
        soc_ok = soc_gate_satisfied(
            inputs.battery_soc_pct, inputs.battery_soc_gate_pct, inputs.soc_round_up
        )
        solar_raw_ok = net_solar_kw >= inputs.solar_start_threshold_kw and soc_ok
        solar_active = self._solar_tracker.update(
            solar_raw_ok, now, inputs.solar_activation_delay_s, inputs.solar_deactivation_delay_s
        )

        if not present:
            mode = "idle"
            selected_car: CarDemand | None = None
        elif inputs.force_charging:
            mode = "forced"
            selected_car = demanding[0] if demanding else present[0]
        elif demanding:
            mode = "scheduled"
            selected_car = demanding[0]
        elif solar_active:
            mode = "solar"
            selected_car = present[0]
        else:
            mode = "idle"
            selected_car = None

        if mode == "idle":
            self._sequence_state = "idle"
            self._pending_phase_mode = None
            if is_drawing:
                self._note_command_expectation(False, now)
                commands: tuple[ChargerCommand, ...] = (ChargerCommand("stop"),)
                reason = note or "unauthorized_charge_suppressed"
            else:
                commands = ()
                reason = note
            return ChargerDecision(
                mode="idle",
                target_amps=0.0,
                target_phase_mode=inputs.current_phase_mode,
                commands=commands,
                notifications=(),
                sequence_state="idle",
                stuck=self._stuck,
                override_reason=reason,
            )

        assert selected_car is not None  # mode != "idle" implies present is non-empty

        # Fuse Layer 1: emergency overload while charging -- overrides everything.
        if (
            is_drawing
            and inputs.measured_worst_case_signed_amps
            >= inputs.fuse_rating_amps + inputs.emergency_margin_amps
        ):
            self._sequence_state = "idle"
            self._pending_phase_mode = None
            self._note_command_expectation(False, now)
            msg = (
                "Nödläge: Laddaren pausad – uppmätt fasström "
                f"{inputs.measured_worst_case_signed_amps:.1f} A överskrider "
                f"säkringsgränsen ({inputs.fuse_rating_amps:.0f} A + "
                f"{inputs.emergency_margin_amps:.0f} A marginal)."
            )
            return ChargerDecision(
                mode=mode,
                target_amps=0.0,
                target_phase_mode=inputs.current_phase_mode,
                commands=(ChargerCommand("pause"),),
                notifications=(msg,),
                sequence_state="idle",
                stuck=self._stuck,
                override_reason="emergency_fuse_overload",
            )

        factor = conversion_factor_for_phase_capability(
            selected_car.phase_capability,
            inputs.conversion_factor_1phase,
            inputs.conversion_factor_2phase,
            inputs.conversion_factor_3phase,
        )
        capacity = compute_charger_capacity_amps(
            fuse_rating_amps=inputs.fuse_rating_amps,
            safety_buffer_amps=inputs.safety_buffer_amps,
            measured_worst_case_signed_amps=inputs.measured_worst_case_signed_amps,
            current_dynamic_limit_amps=inputs.current_dynamic_limit_amps,
            grid_power_cap_kw=inputs.grid_power_cap_kw,
            grid_power_safety_buffer_kw=inputs.grid_power_safety_buffer_kw,
            conversion_factor=factor,
            car_max_charge_kw=selected_car.max_charge_kw,
        )

        if mode == "solar":
            solar_raw = compute_solar_raw_amps(net_solar_kw, factor)
            raw = min(capacity, solar_raw, inputs.max_amps)
        else:
            raw = min(capacity, inputs.max_amps)

        if self._sequence_state != "idle":
            self._sequence_mode = mode
            return self._continue_sequence(inputs, capacity, is_drawing)

        # Fuse Layer 3 + pre-start gate, decided BEFORE any phase-mode switch
        # is considered -- there is no point requesting a phase switch when
        # we are not going to charge regardless (capacity collapsed to 0 or
        # into the below-minimum dead zone).
        if raw <= 0.0:
            commands_list: list[ChargerCommand] = [ChargerCommand("set_dynamic_limit", 0.0)]
            notifications: tuple[str, ...] = ()
            if is_drawing:
                commands_list.append(ChargerCommand("pause"))
                notifications = (
                    (
                        "Säkerhetsstopp: Laddaren ska inte ladda (0 A) men drar "
                        "fortfarande ström – pausar."
                    ),
                )
                self._note_command_expectation(False, now)
            return ChargerDecision(
                mode=mode,
                target_amps=0.0,
                target_phase_mode=inputs.current_phase_mode,
                commands=tuple(commands_list),
                notifications=notifications,
                sequence_state="idle",
                stuck=self._stuck,
                override_reason=note,
            )

        if raw < inputs.min_amps:
            # 0 < capacity < 6A -- do not start (avoids a start/stop churn
            # loop below Easee's minimum). Does not stop an already-running
            # session -- "pre-start" gate only.
            return ChargerDecision(
                mode=mode,
                target_amps=raw,
                target_phase_mode=inputs.current_phase_mode,
                commands=(),
                notifications=(),
                sequence_state="idle",
                stuck=self._stuck,
                override_reason=note,
            )

        desired_phase_mode = phase_switch_target(
            selected_car.phase_capability,
            capacity,
            inputs.conversion_factor_3phase,
            inputs.phase_switch_threshold_kw,
        )

        if desired_phase_mode != inputs.current_phase_mode:
            if is_drawing:
                self._sequence_state = "pausing"
                self._sequence_entered_at = now
                self._pending_phase_mode = desired_phase_mode
                self._sequence_mode = mode
                self._last_command_expect_active = None
                self._last_command_at = None
                return ChargerDecision(
                    mode=mode,
                    target_amps=0.0,
                    target_phase_mode=desired_phase_mode,
                    commands=(ChargerCommand("pause"),),
                    notifications=(),
                    sequence_state="pausing",
                    stuck=self._stuck,
                    override_reason=note,
                )
            return ChargerDecision(
                mode=mode,
                target_amps=0.0,
                target_phase_mode=desired_phase_mode,
                commands=(ChargerCommand("set_phase_mode", desired_phase_mode),),
                notifications=(),
                sequence_state="idle",
                stuck=self._stuck,
                override_reason=note,
            )

        hysteresis_target = self._amp_hysteresis.update(
            raw, now, inputs.amp_increase_delay_s, inputs.amp_decrease_delay_s
        )
        hysteresis_target = clamp_amps(hysteresis_target, inputs.min_amps, inputs.max_amps)

        commands_out: list[ChargerCommand] = []
        if not is_drawing:
            action = "resume" if inputs.charger_status == PAUSED_STATUS else "start"
            commands_out.append(ChargerCommand(action))
            self._note_command_expectation(True, now)
        if hysteresis_target != inputs.current_dynamic_limit_amps:
            commands_out.append(ChargerCommand("set_dynamic_limit", hysteresis_target))

        return ChargerDecision(
            mode=mode,
            target_amps=hysteresis_target,
            target_phase_mode=inputs.current_phase_mode,
            commands=tuple(commands_out),
            notifications=(),
            sequence_state="idle",
            stuck=self._stuck,
            override_reason=note,
        )

    # -- Phase-switch sequence continuation -----------------------------

    def _continue_sequence(
        self, inputs: ChargerInputs, capacity: float, is_drawing: bool
    ) -> ChargerDecision:
        now = inputs.now
        assert self._sequence_entered_at is not None
        elapsed = (now - self._sequence_entered_at).total_seconds()
        timeout = inputs.phase_sequence_step_timeout_s

        if self._sequence_state == "pausing":
            if not is_drawing:
                self._sequence_state = "set_phase"
                self._sequence_entered_at = now
                return ChargerDecision(
                    mode=self._sequence_mode,
                    target_amps=0.0,
                    target_phase_mode=self._pending_phase_mode or inputs.current_phase_mode,
                    commands=(ChargerCommand("set_phase_mode", self._pending_phase_mode),),
                    notifications=(),
                    sequence_state="set_phase",
                    stuck=False,
                    override_reason=None,
                )
            if elapsed >= timeout:
                return self._abort_sequence_timeout(inputs, "phase_switch_pause_timeout")
            return ChargerDecision(
                mode=self._sequence_mode,
                target_amps=0.0,
                target_phase_mode=self._pending_phase_mode or inputs.current_phase_mode,
                commands=(),
                notifications=(),
                sequence_state="pausing",
                stuck=False,
                override_reason=None,
            )

        if self._sequence_state == "set_phase":
            if inputs.current_phase_mode == self._pending_phase_mode:
                if capacity < inputs.min_amps:
                    return self._abort_sequence_insufficient(
                        inputs, "phase_switch_insufficient_before_resume"
                    )
                self._sequence_state = "resuming"
                self._sequence_entered_at = now
                return ChargerDecision(
                    mode=self._sequence_mode,
                    target_amps=0.0,
                    target_phase_mode=self._pending_phase_mode or inputs.current_phase_mode,
                    commands=(ChargerCommand("resume"),),
                    notifications=(),
                    sequence_state="resuming",
                    stuck=False,
                    override_reason=None,
                )
            if elapsed >= timeout:
                return self._abort_sequence_timeout(inputs, "phase_switch_set_phase_timeout")
            return ChargerDecision(
                mode=self._sequence_mode,
                target_amps=0.0,
                target_phase_mode=self._pending_phase_mode or inputs.current_phase_mode,
                commands=(),
                notifications=(),
                sequence_state="set_phase",
                stuck=False,
                override_reason=None,
            )

        if self._sequence_state == "resuming":
            if is_drawing:
                if capacity < inputs.min_amps:
                    return self._abort_sequence_insufficient(
                        inputs, "phase_switch_insufficient_before_limit"
                    )
                final_target = self._amp_hysteresis.update(
                    min(capacity, inputs.max_amps),
                    now,
                    inputs.amp_increase_delay_s,
                    inputs.amp_decrease_delay_s,
                )
                final_target = clamp_amps(final_target, inputs.min_amps, inputs.max_amps)
                self._sequence_state = "idle"
                self._pending_phase_mode = None
                return ChargerDecision(
                    mode=self._sequence_mode,
                    target_amps=final_target,
                    target_phase_mode=inputs.current_phase_mode,
                    commands=(ChargerCommand("set_dynamic_limit", final_target),),
                    notifications=(),
                    sequence_state="set_limit",
                    stuck=False,
                    override_reason=None,
                )
            if elapsed >= timeout:
                return self._abort_sequence_timeout(inputs, "phase_switch_resume_timeout")
            return ChargerDecision(
                mode=self._sequence_mode,
                target_amps=0.0,
                target_phase_mode=self._pending_phase_mode or inputs.current_phase_mode,
                commands=(),
                notifications=(),
                sequence_state="resuming",
                stuck=False,
                override_reason=None,
            )

        # pragma: no cover -- unreachable with valid sequence states
        self._sequence_state = "idle"  # pragma: no cover
        return ChargerDecision(  # pragma: no cover
            mode=self._sequence_mode,
            target_amps=0.0,
            target_phase_mode=inputs.current_phase_mode,
            commands=(),
            notifications=(),
            sequence_state="idle",
            stuck=False,
            override_reason=None,
        )

    def _abort_sequence_timeout(self, inputs: ChargerInputs, reason: str) -> ChargerDecision:
        """Per-state sequence timeout -- abort to a safe paused state, flag stuck."""
        self._sequence_state = "idle"
        self._pending_phase_mode = None
        self._stuck = True
        return ChargerDecision(
            mode=self._sequence_mode,
            target_amps=0.0,
            target_phase_mode=inputs.current_phase_mode,
            commands=(ChargerCommand("pause"),),
            notifications=(),
            sequence_state="idle",
            stuck=True,
            override_reason=reason,
        )

    def _abort_sequence_insufficient(self, inputs: ChargerInputs, reason: str) -> ChargerDecision:
        """Fuse re-verification failed mid-sequence -- abort to paused, no stuck flag.

        This is a legitimate safety abort due to real conditions (headroom
        collapsed), not an unresponsive-hardware situation, so stuck is not
        set.
        """
        self._sequence_state = "idle"
        self._pending_phase_mode = None
        return ChargerDecision(
            mode=self._sequence_mode,
            target_amps=0.0,
            target_phase_mode=inputs.current_phase_mode,
            commands=(ChargerCommand("pause"),),
            notifications=(),
            sequence_state="idle",
            stuck=False,
            override_reason=reason,
        )

    # -- Generic stuck-command tracking (outside phase-switch sequences) --

    def _note_command_expectation(self, expect_active: bool, now: datetime) -> None:
        """Record that a start/resume/pause/stop command was just issued.

        Only (re)starts the timeout clock when the expectation actually
        changes -- reissuing the same command every tick while waiting must
        not keep pushing the stuck timeout back.
        """
        if self._last_command_expect_active != expect_active:
            self._last_command_expect_active = expect_active
            self._last_command_at = now

    def _resolve_command_tracking(
        self, is_drawing: bool, now: datetime, timeout_s: float
    ) -> None:
        """Confirm or time out the currently tracked plain command, if any."""
        if self._last_command_expect_active is None:
            return
        if is_drawing == self._last_command_expect_active:
            self._last_command_expect_active = None
            self._last_command_at = None
            self._stuck = False
            return
        if (
            self._last_command_at is not None
            and (now - self._last_command_at).total_seconds() >= timeout_s
        ):
            self._stuck = True

    def _reset_all(self) -> None:
        """Full reset on a terminal charger state -- new session starts clean."""
        self._sequence_state = "idle"
        self._sequence_entered_at = None
        self._pending_phase_mode = None
        self._sequence_mode = "idle"
        self._amp_hysteresis = ChargerAmpHysteresis()
        self._solar_tracker = SolarActivationTracker()
        self._last_command_expect_active = None
        self._last_command_at = None
        self._stuck = False

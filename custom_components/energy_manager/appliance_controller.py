"""Pure solar-surplus appliance decision logic with zero HA dependencies.

Turns user-selected switch loads ON when the measured solar surplus (grid
export) exceeds the load's rated draw with margin, and OFF when the surplus
disappears, with per-appliance priority allocation and anti-short-cycling
protection (APPL-03..APPL-06). This module is intentionally free of any HA
imports so it can be thoroughly unit-tested independently; all time arrives
as epoch seconds via ``now_ts`` so every decision is fully deterministic.

Surplus model:
    1. ``raw_surplus = export - max(0, battery_discharge)`` -- the signed
       grid balance (positive = export, negative = import) cleaned of
       battery discharge (BATT-17 guard: export driven by battery
       arbitrage at spike prices is not solar surplus and must never feed
       appliances). The signal stays signed so the import side reaches
       the release comparison -- with rated credit-back the pool would
       otherwise floor at rated and a load without a power sensor could
       never release through the hysteresis band.
    2. The allocation pool credits back the draw of every appliance actually
       running under EM command (measured draw when a power sensor is
       configured, rated draw otherwise). The meter reading already contains
       those appliances' own consumption, so the credit turns the self-eating
       feedback loop into deterministic arithmetic (the PV-Excess-Control
       idiom). The credit requires the actuator to really be on: in
       observe-only mode the load never draws and the meter still shows the
       full export, so a credit would double-count -- gating on the actual
       actuator state makes dry-run pool arithmetic mirror live behaviour.
    3. Appliances are walked in (priority ascending, insertion order). Each
       appliance staying on subtracts its draw from the remaining pool; each
       newly admitted appliance subtracts its rated kW from the pool and its
       rated amps from the remaining fuse headroom (loads already running
       are part of the measured grid amps, so only new loads reserve
       headroom).

Anti-short-cycling (APPL-05, all knobs per appliance):
    * ON requires the pool at or above ``rated * on_threshold_pct / 100``
      continuously for ``on_sustain_s``, ``min_off_s`` elapsed since the
      last turn-off, and fuse admission (APPL-06).
    * OFF requires the pool below ``rated * off_threshold_pct / 100``
      continuously for ``off_sustain_s`` AND ``min_on_s`` elapsed since
      turn-on. The deficit clock runs during min_on so the release fires
      the moment the floor expires.

Commands are declarative (APPL-09): while EM wants a load on and the
actuator reads off (command suppressed in observe-only, or failed), a
turn-on is issued every tick. Symmetrically, an EM turn-off stays pending
(``release_pending``) until the actuator is actually observed off, and the
turn-off is re-issued every tick while it still reads on -- a single
failed command never strands a load. Trackers advance identically whether
the caller actually sends the commands, so an observe-only soak produces
the same decision history as a live run. An actuator turned on outside EM
is left alone: no command, no allocation, no credit-back, and both
sustain clocks reset so EM re-evaluates from scratch once the external
actor releases the switch.
"""

from __future__ import annotations

from dataclasses import dataclass

# Nominal per-phase grid voltage for the rated-amps fuse admission check:
# rated_amps = rated_power_w / (230 V * phases).
_PHASE_VOLTAGE = 230.0

# ---------------------------------------------------------------------------
# Status values published by the per-appliance status sensor (APPL-08)
# ---------------------------------------------------------------------------

STATUS_DISABLED = "disabled"
STATUS_ACTUATOR_UNAVAILABLE = "actuator_unavailable"
STATUS_OFF_NO_SURPLUS = "off_no_surplus"
STATUS_WAITING_ON_SUSTAIN = "waiting_on_sustain"
STATUS_ON_SURPLUS = "on_surplus"
STATUS_HOLDING_MIN_ON = "holding_min_on"
STATUS_BLOCKED_MIN_OFF = "blocked_min_off"
STATUS_BLOCKED_FUSE = "blocked_fuse"
STATUS_BLOCKED_PRIORITY = "blocked_priority"
STATUS_ON_EXTERNAL = "on_external"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplianceConfig:
    """Static per-appliance configuration snapshot from the subentry.

    Attributes:
        subentry_id: HA subentry id, used as the decision key.
        name: Friendly appliance name (subentry title).
        switch_entity: The switch/input_boolean actuator EM toggles.
        rated_power_w: Expected load in watts (e.g. 4200).
        phases: 1 or 3; rated_amps = rated_power_w / (230 * phases).
        priority: 1 = highest; ties broken by subentry insertion order.
        on_threshold_pct: Turn ON when pool >= rated * pct / 100, sustained.
        off_threshold_pct: Turn OFF when pool < rated * pct / 100, sustained.
        on_sustain_s: Surplus must persist this long before turning ON.
        off_sustain_s: Deficit must persist this long before turning OFF.
        min_on_s: Hard floor once ON (anti-short-cycling).
        min_off_s: Hard floor once OFF (anti-short-cycling).
        power_sensor_entity: Optional power sensor; enables measured
            credit-back instead of rated fallback.
    """

    subentry_id: str
    name: str
    switch_entity: str
    rated_power_w: int
    phases: int
    priority: int
    on_threshold_pct: int
    off_threshold_pct: int
    on_sustain_s: int
    off_sustain_s: int
    min_on_s: int
    min_off_s: int
    power_sensor_entity: str | None = None


@dataclass
class ApplianceTracker:
    """Mutable per-appliance state advanced by decide_appliances().

    Lives in coordinator memory only; on restart/reload the coordinator
    re-seeds from the actuator's actual state with fresh timestamps (a
    state change is worst-case delayed by one min_on/min_off window, never
    flipped).

    Attributes:
        em_commanded_on: True while EM has decided the load should be on.
        last_on_ts: Epoch seconds of the last EM turn-on decision.
        last_off_ts: Epoch seconds of the last EM turn-off decision.
        surplus_since_ts: Start of the current continuous surplus period
            (ON-sustain clock), None when not running.
        deficit_since_ts: Start of the current continuous deficit period
            (OFF-sustain clock), None when not running.
        release_pending: True while an EM turn-off has been decided but
            the actuator has not yet been observed off -- the turn-off is
            re-issued every tick (APPL-09, no one-shot commands) and the
            flag clears once the actuator reads off while available.
    """

    em_commanded_on: bool = False
    last_on_ts: float | None = None
    last_off_ts: float | None = None
    surplus_since_ts: float | None = None
    deficit_since_ts: float | None = None
    release_pending: bool = False


@dataclass(frozen=True)
class ApplianceInputs:
    """Live per-appliance readings snapshotted by the coordinator each tick.

    Attributes:
        actuator_available: False when the actuator entity is missing,
            unavailable or unknown.
        actuator_is_on: Whether the actuator currently reads "on".
        em_control_enabled: State of the per-appliance EM control switch.
        measured_power_w: Reading of the optional power sensor in watts,
            None when unconfigured or unavailable (falls back to rated).
    """

    actuator_available: bool
    actuator_is_on: bool
    em_control_enabled: bool
    measured_power_w: float | None


@dataclass(frozen=True)
class ApplianceDecision:
    """Outcome of one allocation walk for one appliance.

    Attributes:
        subentry_id: The appliance this decision applies to.
        status: One of the STATUS_* constants (APPL-08 sensor state).
        desired_on: Whether EM wants the load on after this tick.
        should_command: Whether the caller should issue a command this tick
            (through the CORE-14-gated send site).
        turn_on: True for a turn-on command, False for a turn-off command;
            mirrors desired_on when should_command is False.
        allocated_kw: Pool share consumed by this appliance this tick.
        reason: Human-readable explanation of the decision.
    """

    subentry_id: str
    status: str
    desired_on: bool
    should_command: bool
    turn_on: bool
    allocated_kw: float
    reason: str


# ---------------------------------------------------------------------------
# Pure computations
# ---------------------------------------------------------------------------


def compute_raw_surplus_kw(export_kw: float, battery_discharge_kw: float) -> float:
    """Return the signed solar surplus in kW after the BATT-17 discharge guard.

    Grid export driven by battery arbitrage (discharging into the grid at
    spike prices) is not solar surplus, so the battery's discharge power is
    subtracted from the measured export before appliances see it.

    The result is deliberately signed: import shows up as a negative
    surplus so the hysteresis band's release comparison sees it. Clamping
    at zero here would let rated credit-back floor the pool at rated while
    a load runs, making the documented off threshold unreachable for
    appliances without a power sensor.

    Args:
        export_kw: Signed grid balance in kW (positive = export,
            negative = import).
        battery_discharge_kw: Battery discharge power in kW; zero/negative
            (idle/charging) values contribute nothing.

    Returns:
        Signed surplus in kW (negative while importing).
    """
    return export_kw - max(0.0, battery_discharge_kw)


def _draw_kw(config: ApplianceConfig, inputs: ApplianceInputs) -> float:
    """Return the appliance's current draw in kW (measured, else rated)."""
    if inputs.measured_power_w is not None:
        return inputs.measured_power_w / 1000.0
    return config.rated_power_w / 1000.0


def _credit_back_kw(
    config: ApplianceConfig,
    inputs: ApplianceInputs,
    tracker: ApplianceTracker,
) -> float:
    """Return the pool credit for an appliance actually running under EM.

    The credit requires the actuator to really be on: an EM-commanded load
    whose actuator reads off (observe-only, or a failed command) is not
    drawing, so the meter still shows the full export and a credit would
    double-count.
    """
    if not (
        inputs.em_control_enabled and tracker.em_commanded_on and inputs.actuator_is_on
    ):
        return 0.0
    return _draw_kw(config, inputs)


def decide_appliances(
    *,
    now_ts: float,
    raw_surplus_kw: float,
    headroom_amps: float | None,
    items: list[tuple[ApplianceConfig, ApplianceInputs, ApplianceTracker]],
) -> list[ApplianceDecision]:
    """Run one allocation walk and return a decision per appliance.

    Mutates the supplied trackers (timestamps and em_commanded_on) --
    identically whether or not the caller actually sends the resulting
    commands, so observe-only mirrors live behaviour.

    Args:
        now_ts: Current time as epoch seconds; the only clock used.
        raw_surplus_kw: Output of compute_raw_surplus_kw() for this tick.
        headroom_amps: Remaining fuse headroom for NEW loads. None means
            fuse sensors are configured but currently unavailable (no new
            turn-ons); float("inf") means no fuse data is configured
            (admission always passes); a finite value admits an appliance
            iff its rated amps fit, and each admission subtracts its rated
            amps from the remaining headroom within the same tick. Loads
            already running are part of the measured grid amps and are
            never re-checked (admission-only gate).
        items: One (config, inputs, tracker) tuple per appliance, in
            subentry insertion order. Evaluated sorted by (priority
            ascending, insertion order).

    Returns:
        One ApplianceDecision per appliance, in evaluation order.
    """
    ordered = sorted(items, key=lambda item: item[0].priority)
    pool_total_kw = raw_surplus_kw + sum(
        _credit_back_kw(config, inputs, tracker) for config, inputs, tracker in ordered
    )
    remaining_pool_kw = pool_total_kw
    remaining_headroom = headroom_amps
    decisions: list[ApplianceDecision] = []

    for config, inputs, tracker in ordered:
        rated_kw = config.rated_power_w / 1000.0
        threshold_on_kw = rated_kw * config.on_threshold_pct / 100.0
        threshold_off_kw = rated_kw * config.off_threshold_pct / 100.0

        if not inputs.em_control_enabled:
            if tracker.em_commanded_on:
                tracker.em_commanded_on = False
                tracker.last_off_ts = now_ts
                tracker.release_pending = True
            if tracker.release_pending and inputs.actuator_available:
                if not inputs.actuator_is_on:
                    # Turn-off landed (or was never needed): done.
                    tracker.release_pending = False
                releasing = tracker.release_pending
            else:
                # Nothing pending, or the actuator is unavailable: no
                # command -- a pending release is retained until the
                # actuator returns (APPL failure table).
                releasing = False
            tracker.surplus_since_ts = None
            tracker.deficit_since_ts = None
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_DISABLED,
                    desired_on=False,
                    should_command=releasing,
                    turn_on=False,
                    allocated_kw=0.0,
                    reason=(
                        "EM control disabled; releasing actuator"
                        if releasing
                        else "EM control disabled"
                    ),
                )
            )
            continue

        if not inputs.actuator_available:
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_ACTUATOR_UNAVAILABLE,
                    desired_on=tracker.em_commanded_on,
                    should_command=False,
                    turn_on=tracker.em_commanded_on,
                    allocated_kw=0.0,
                    reason=f"actuator {config.switch_entity} is unavailable",
                )
            )
            continue

        if tracker.release_pending and not inputs.actuator_is_on:
            # The pending EM turn-off landed (actuator observed off while
            # available): release complete.
            tracker.release_pending = False

        if tracker.em_commanded_on:
            # Keep-or-release path. The remaining pool at this position
            # already contains this appliance's own credit-back (added
            # during pool construction) minus higher-priority allocations.
            draw_kw = _draw_kw(config, inputs)
            stay_on = True
            if remaining_pool_kw >= threshold_off_kw:
                tracker.deficit_since_ts = None
                status = STATUS_ON_SURPLUS
                reason = (
                    f"pool {remaining_pool_kw:.2f} kW >= off threshold "
                    f"{threshold_off_kw:.2f} kW"
                )
            else:
                if tracker.deficit_since_ts is None:
                    tracker.deficit_since_ts = now_ts
                deficit_s = now_ts - tracker.deficit_since_ts
                min_on_elapsed_s = (
                    now_ts - tracker.last_on_ts
                    if tracker.last_on_ts is not None
                    else float("inf")
                )
                if min_on_elapsed_s < config.min_on_s:
                    status = STATUS_HOLDING_MIN_ON
                    reason = (
                        "deficit but min_on floor holds for "
                        f"{config.min_on_s - min_on_elapsed_s:.0f} s more"
                    )
                elif deficit_s >= config.off_sustain_s:
                    stay_on = False
                    reason = (
                        f"deficit held {deficit_s:.0f} s >= "
                        f"{config.off_sustain_s} s off-sustain; releasing"
                    )
                else:
                    status = STATUS_ON_SURPLUS
                    reason = (
                        f"deficit held {deficit_s:.0f} s of "
                        f"{config.off_sustain_s} s off-sustain"
                    )
            if stay_on:
                remaining_pool_kw -= draw_kw
                decisions.append(
                    ApplianceDecision(
                        subentry_id=config.subentry_id,
                        status=status,
                        desired_on=True,
                        should_command=not inputs.actuator_is_on,
                        turn_on=True,
                        allocated_kw=draw_kw,
                        reason=reason,
                    )
                )
            else:
                tracker.em_commanded_on = False
                tracker.last_off_ts = now_ts
                # Pending until the actuator is observed off -- a failed
                # or missed turn-off is re-issued next tick (APPL-09).
                tracker.release_pending = inputs.actuator_is_on
                tracker.surplus_since_ts = None
                tracker.deficit_since_ts = None
                decisions.append(
                    ApplianceDecision(
                        subentry_id=config.subentry_id,
                        status=STATUS_OFF_NO_SURPLUS,
                        desired_on=False,
                        should_command=True,
                        turn_on=False,
                        allocated_kw=0.0,
                        reason=reason,
                    )
                )
            continue

        if inputs.actuator_is_on:
            if tracker.release_pending:
                # EM's turn-off has not landed yet (failed or missed
                # command): re-assert instead of classifying the still-on
                # actuator as externally controlled.
                tracker.surplus_since_ts = None
                tracker.deficit_since_ts = None
                decisions.append(
                    ApplianceDecision(
                        subentry_id=config.subentry_id,
                        status=STATUS_OFF_NO_SURPLUS,
                        desired_on=False,
                        should_command=True,
                        turn_on=False,
                        allocated_kw=0.0,
                        reason=(
                            "re-asserting EM turn-off until the actuator reads off"
                        ),
                    )
                )
                continue
            # Turned on outside EM: leave alone, no allocation, no credit.
            tracker.surplus_since_ts = None
            tracker.deficit_since_ts = None
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_ON_EXTERNAL,
                    desired_on=False,
                    should_command=False,
                    turn_on=False,
                    allocated_kw=0.0,
                    reason="actuator turned on outside EM; leaving it alone",
                )
            )
            continue

        # Admission path (off, EM-managed).
        min_off_elapsed_s = (
            now_ts - tracker.last_off_ts
            if tracker.last_off_ts is not None
            else float("inf")
        )
        if min_off_elapsed_s < config.min_off_s:
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_BLOCKED_MIN_OFF,
                    desired_on=False,
                    should_command=False,
                    turn_on=False,
                    allocated_kw=0.0,
                    reason=(
                        "min_off floor holds for "
                        f"{config.min_off_s - min_off_elapsed_s:.0f} s more"
                    ),
                )
            )
            continue

        if pool_total_kw < threshold_on_kw:
            tracker.surplus_since_ts = None
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_OFF_NO_SURPLUS,
                    desired_on=False,
                    should_command=False,
                    turn_on=False,
                    allocated_kw=0.0,
                    reason=(
                        f"pool {pool_total_kw:.2f} kW < on threshold "
                        f"{threshold_on_kw:.2f} kW"
                    ),
                )
            )
            continue

        if tracker.surplus_since_ts is None:
            tracker.surplus_since_ts = now_ts
        sustained_s = now_ts - tracker.surplus_since_ts
        if sustained_s < config.on_sustain_s:
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_WAITING_ON_SUSTAIN,
                    desired_on=False,
                    should_command=False,
                    turn_on=False,
                    allocated_kw=0.0,
                    reason=(
                        f"surplus held {sustained_s:.0f} s of "
                        f"{config.on_sustain_s} s on-sustain"
                    ),
                )
            )
            continue

        rated_amps = config.rated_power_w / (_PHASE_VOLTAGE * config.phases)
        if remaining_headroom is None:
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_BLOCKED_FUSE,
                    desired_on=False,
                    should_command=False,
                    turn_on=False,
                    allocated_kw=0.0,
                    reason="fuse headroom unavailable; new turn-ons blocked",
                )
            )
            continue
        if rated_amps > remaining_headroom:
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_BLOCKED_FUSE,
                    desired_on=False,
                    should_command=False,
                    turn_on=False,
                    allocated_kw=0.0,
                    reason=(
                        f"rated {rated_amps:.1f} A exceeds remaining fuse "
                        f"headroom {remaining_headroom:.1f} A"
                    ),
                )
            )
            continue

        if remaining_pool_kw < threshold_on_kw:
            decisions.append(
                ApplianceDecision(
                    subentry_id=config.subentry_id,
                    status=STATUS_BLOCKED_PRIORITY,
                    desired_on=False,
                    should_command=False,
                    turn_on=False,
                    allocated_kw=0.0,
                    reason=(
                        f"remaining pool {remaining_pool_kw:.2f} kW < on "
                        f"threshold {threshold_on_kw:.2f} kW after "
                        "higher-priority allocation"
                    ),
                )
            )
            continue

        tracker.em_commanded_on = True
        tracker.last_on_ts = now_ts
        tracker.surplus_since_ts = None
        tracker.deficit_since_ts = None
        remaining_pool_kw -= rated_kw
        remaining_headroom -= rated_amps
        decisions.append(
            ApplianceDecision(
                subentry_id=config.subentry_id,
                status=STATUS_ON_SURPLUS,
                desired_on=True,
                should_command=True,
                turn_on=True,
                allocated_kw=rated_kw,
                reason=(
                    f"pool {remaining_pool_kw + rated_kw:.2f} kW >= on "
                    f"threshold {threshold_on_kw:.2f} kW held "
                    f"{sustained_s:.0f} s; turning on"
                ),
            )
        )

    return decisions

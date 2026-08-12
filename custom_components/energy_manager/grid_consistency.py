"""Pure grid-sensor consistency detection with zero HA dependencies.

When BOTH the three per-phase grid power entities AND the total grid power
entity are configured, their readings describe the same physical quantity
and must roughly agree. A persistent disagreement almost always means a
misconfiguration -- the observed field case was inverter-output phase
sensors selected as grid phase sensors: the phase sum read +7 kW while the
real grid total read -6.2 kW, inverting the surplus sign so appliances
never ran and fuse headroom was computed from the wrong load.

This module is intentionally free of any HA imports so it can be
thoroughly unit-tested independently; all time arrives as seconds via
``now_ts`` so every decision is fully deterministic (the
appliance_controller.py pattern). Only differences of ``now_ts`` are
consumed, so any steadily increasing clock works -- the FuseSensorReader
feeds time.monotonic() (its _fallback_since idiom), tests feed epoch
seconds. The FuseSensorReader owns the tracker, performs the sensor
reads, and files the Repairs issue on the returned EVENT_RAISE.

Detection rule:
    * A tick is a mismatch when the disagreement exceeds
      ``max(1.0 kW, 20 % of the larger |reading|)`` -- the absolute floor
      avoids noise near zero, the relative band avoids false positives
      from sensor timing skew at high load (the real misconfig produced a
      ~13 kW disagreement, far past both).
    * The mismatch must hold continuously for 300 s before flagging (same
      sustain idiom as the appliance on/off sustain clocks).
    * Either signal None or non-finite (inf/nan) resets the sustain clock
      but keeps the current flagged/unflagged state -- an outage is
      evidence of nothing.
    * A consistent reading resets the sustain clock; it never clears a
      raised flag.

Flag lifecycle: once raised, the flag is sticky for the tracker's
lifetime. The exact misconfiguration this guard exists for produces
readings that agree whenever flows are small (at night PV is 0 and the
house floor load sits under the 1 kW tolerance), so clearing on a
consistent tick would flap: clear every evening, re-raise with a fresh
WARNING every morning. The Repairs issue is instead cleared when the
config entry unloads (repairs.py ALL_ISSUE_IDS in async_unload_entry),
and fixing the sensor configuration requires an entry reload anyway --
after the reload a still-broken setup re-raises within one sustain
window while a fixed one stays clean.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Absolute disagreement floor in kW -- readings closer than this are never
# a mismatch, regardless of magnitude (avoids noise/timing skew near 0 kW).
MISMATCH_ABS_FLOOR_KW = 1.0
# Relative disagreement band -- at high load the tolerance grows to 20 % of
# the larger |reading| so ordinary sensor timing skew does not flag.
MISMATCH_REL_FRACTION = 0.2
# Continuous mismatch duration required before flagging.
MISMATCH_SUSTAIN_SECONDS = 300.0

# Events returned by update_grid_consistency() for the caller to act on.
EVENT_RAISE = "raise"  # flag just raised: log once + file the Repairs issue
EVENT_NONE = "none"  # no state change this tick


@dataclass
class GridConsistencyTracker:
    """Mutable sustain-clock state advanced by update_grid_consistency().

    Lives in FuseSensorReader memory only; on restart/reload the detector
    simply re-runs its sustain window from scratch (worst case the flag is
    delayed by one window, never wrongly raised).

    Attributes:
        mismatch_since_ts: Start of the current continuous mismatch streak
            (sustain clock), None when the last tick was consistent or a
            signal was unavailable.
        flagged: True once a sustained mismatch has been raised; sticky
            for the tracker's lifetime (see the module docstring).
    """

    mismatch_since_ts: float | None = None
    flagged: bool = False


def is_grid_reading_mismatch(phase_sum_kw: float, total_kw: float) -> bool:
    """Return True when the per-phase sum and total grid reading disagree.

    Args:
        phase_sum_kw: Sum of the three per-phase grid power readings in kW
            (signed: positive = import, negative = export).
        total_kw: The total grid power reading in kW (same sign convention).

    Returns:
        True when the disagreement exceeds max(MISMATCH_ABS_FLOOR_KW,
        MISMATCH_REL_FRACTION * the larger absolute reading).
    """
    tolerance_kw = max(
        MISMATCH_ABS_FLOOR_KW,
        MISMATCH_REL_FRACTION * max(abs(phase_sum_kw), abs(total_kw)),
    )
    return abs(phase_sum_kw - total_kw) > tolerance_kw


def update_grid_consistency(
    tracker: GridConsistencyTracker,
    phase_sum_kw: float | None,
    total_kw: float | None,
    now_ts: float,
) -> str:
    """Advance the consistency tracker one tick and return the event.

    Args:
        tracker: Mutable sustain-clock state, owned by the caller.
        phase_sum_kw: Sum of the per-phase readings in kW, or None when any
            phase entity is unavailable.
        total_kw: The total grid power reading in kW, or None when the
            total entity is unavailable.
        now_ts: Current time in seconds; the only clock used.

    Returns:
        EVENT_RAISE when a mismatch has held continuously for
        MISMATCH_SUSTAIN_SECONDS and the flag was not already raised, and
        EVENT_NONE otherwise (including any tick with a signal missing --
        an outage keeps the current flagged/unflagged state unchanged).
    """
    if (
        phase_sum_kw is None
        or total_kw is None
        or not math.isfinite(phase_sum_kw)
        or not math.isfinite(total_kw)
    ):
        tracker.mismatch_since_ts = None
        return EVENT_NONE

    if not is_grid_reading_mismatch(phase_sum_kw, total_kw):
        # Consistent tick: reset the sustain clock. The flag is
        # deliberately never cleared (sticky -- see the module docstring).
        tracker.mismatch_since_ts = None
        return EVENT_NONE

    if tracker.mismatch_since_ts is None:
        tracker.mismatch_since_ts = now_ts
    if (
        not tracker.flagged
        and now_ts - tracker.mismatch_since_ts >= MISMATCH_SUSTAIN_SECONDS
    ):
        tracker.flagged = True
        return EVENT_RAISE
    return EVENT_NONE

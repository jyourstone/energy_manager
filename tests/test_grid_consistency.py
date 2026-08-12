"""Tests for the pure grid-sensor consistency detection logic.

All timing uses raw epoch seconds fed through ``now_ts`` -- no wall clock,
no Home Assistant. Covered: the mismatch tolerance boundaries (1.0 kW
absolute floor, 20 % relative), the 300 s sustain window, the
unavailability clock reset (flag state preserved), and the sticky flag
(consistent readings never clear it -- see the module docstring's flag
lifecycle). The coordinator wiring (Repairs issue filing, warning log,
cross-reader dedupe) is covered in test_repairs.py.
"""

from __future__ import annotations

import pytest

from custom_components.energy_manager.grid_consistency import (
    EVENT_NONE,
    EVENT_RAISE,
    MISMATCH_SUSTAIN_SECONDS,
    GridConsistencyTracker,
    is_grid_reading_mismatch,
    update_grid_consistency,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = 1_700_000_000.0


def _sustained_raise(tracker: GridConsistencyTracker, start_ts: float) -> str:
    """Feed a mismatch at start_ts and again after the full sustain window."""
    update_grid_consistency(tracker, 7.0, -6.2, start_ts)
    return update_grid_consistency(
        tracker, 7.0, -6.2, start_ts + MISMATCH_SUSTAIN_SECONDS
    )


# ---------------------------------------------------------------------------
# Constants -- pin the documented sustain window
# ---------------------------------------------------------------------------


def test_mismatch_sustain_seconds_is_pinned_at_300() -> None:
    """Pins the documented sustain window; symbolic timestamps elsewhere
    make the suite mutation-blind without it.
    """
    assert MISMATCH_SUSTAIN_SECONDS == 300.0


# ---------------------------------------------------------------------------
# is_grid_reading_mismatch() -- tolerance boundaries
# ---------------------------------------------------------------------------


class TestMismatchBoundaries:
    def test_real_misconfig_case_is_a_mismatch(self):
        # The field incident: inverter-output phases summed to +7 kW while
        # the real grid total read -6.2 kW (13.2 kW disagreement).
        assert is_grid_reading_mismatch(7.0, -6.2) is True

    def test_identical_readings_are_consistent(self):
        assert is_grid_reading_mismatch(3.0, 3.0) is False

    def test_disagreement_at_absolute_floor_is_consistent(self):
        # abs floor is strict (> 1.0 kW), so exactly 1.0 kW apart is noise.
        assert is_grid_reading_mismatch(0.5, -0.5) is False

    def test_disagreement_just_above_absolute_floor_is_a_mismatch(self):
        assert is_grid_reading_mismatch(0.6, -0.5) is True

    def test_small_near_zero_noise_is_consistent(self):
        # Timing skew around 0 kW must never flag (the 1.0 kW floor).
        assert is_grid_reading_mismatch(0.3, -0.2) is False

    @pytest.mark.parametrize(
        ("phase_sum_kw", "total_kw", "expected"),
        [
            # At high load the 20 % relative band dominates the 1.0 kW floor:
            # tolerance = 0.2 * max(|10.0|, |8.0|) = 2.0 kW.
            (10.0, 8.0, False),  # exactly at tolerance -> consistent
            (10.0, 7.9, True),  # just past tolerance -> mismatch
            (-10.0, -8.0, False),  # sign-symmetric: export side
            (-10.0, -7.9, True),
        ],
    )
    def test_relative_band_at_high_load(self, phase_sum_kw, total_kw, expected):
        assert is_grid_reading_mismatch(phase_sum_kw, total_kw) is expected


# ---------------------------------------------------------------------------
# update_grid_consistency() -- sustain window
# ---------------------------------------------------------------------------


class TestSustainWindow:
    def test_first_mismatch_tick_does_not_raise(self):
        tracker = GridConsistencyTracker()
        assert update_grid_consistency(tracker, 7.0, -6.2, T0) == EVENT_NONE
        assert tracker.flagged is False

    def test_mismatch_below_sustain_window_does_not_raise(self):
        tracker = GridConsistencyTracker()
        update_grid_consistency(tracker, 7.0, -6.2, T0)
        event = update_grid_consistency(
            tracker, 7.0, -6.2, T0 + MISMATCH_SUSTAIN_SECONDS - 1.0
        )
        assert event == EVENT_NONE
        assert tracker.flagged is False

    def test_mismatch_sustained_for_window_raises(self):
        tracker = GridConsistencyTracker()
        assert _sustained_raise(tracker, T0) == EVENT_RAISE
        assert tracker.flagged is True

    def test_raise_fires_only_once_per_episode(self):
        # The wiring logs on EVENT_RAISE, so continued mismatch must not
        # re-emit it every tick.
        tracker = GridConsistencyTracker()
        _sustained_raise(tracker, T0)
        event = update_grid_consistency(tracker, 7.0, -6.2, T0 + 10_000.0)
        assert event == EVENT_NONE
        assert tracker.flagged is True

    def test_consistent_tick_resets_the_sustain_clock(self):
        # 200 s mismatch, one consistent tick, then mismatch again: the
        # window must run the full 300 s anew from the second streak.
        tracker = GridConsistencyTracker()
        update_grid_consistency(tracker, 7.0, -6.2, T0)
        update_grid_consistency(tracker, 3.0, 3.0, T0 + 200.0)
        update_grid_consistency(tracker, 7.0, -6.2, T0 + 250.0)
        event = update_grid_consistency(tracker, 7.0, -6.2, T0 + 400.0)
        assert event == EVENT_NONE  # only 150 s into the fresh streak
        event = update_grid_consistency(
            tracker, 7.0, -6.2, T0 + 250.0 + MISMATCH_SUSTAIN_SECONDS
        )
        assert event == EVENT_RAISE


# ---------------------------------------------------------------------------
# update_grid_consistency() -- unavailability handling
# ---------------------------------------------------------------------------


class TestUnavailability:
    @pytest.mark.parametrize(
        ("phase_sum_kw", "total_kw"),
        [(None, -6.2), (7.0, None), (None, None)],
    )
    def test_unavailable_signal_resets_clock_without_flagging(
        self, phase_sum_kw, total_kw
    ):
        # Mismatch streak interrupted by an outage: the post-recovery
        # streak must run the full window before raising.
        tracker = GridConsistencyTracker()
        update_grid_consistency(tracker, 7.0, -6.2, T0)
        event = update_grid_consistency(tracker, phase_sum_kw, total_kw, T0 + 200.0)
        assert event == EVENT_NONE
        assert tracker.flagged is False
        event = update_grid_consistency(
            tracker, 7.0, -6.2, T0 + MISMATCH_SUSTAIN_SECONDS + 100.0
        )
        assert event == EVENT_NONE  # fresh streak started at T0+400, not T0
        event = update_grid_consistency(
            tracker, 7.0, -6.2, T0 + 400.0 + MISMATCH_SUSTAIN_SECONDS
        )
        assert event == EVENT_RAISE  # full window from the fresh streak

    def test_unavailable_signal_keeps_the_flag_raised(self):
        # An outage is not evidence of consistency -- the flag stays
        # raised through it (sticky, like every other tick).
        tracker = GridConsistencyTracker()
        _sustained_raise(tracker, T0)
        event = update_grid_consistency(tracker, None, None, T0 + 500.0)
        assert event == EVENT_NONE
        assert tracker.flagged is True

    def test_non_finite_phase_sum_treated_as_unavailable(self):
        # inf/nan must reset the sustain clock like None, not be compared
        # as a numeric mismatch (inf especially would always "mismatch").
        tracker = GridConsistencyTracker()
        update_grid_consistency(tracker, 7.0, -6.2, T0)
        event = update_grid_consistency(tracker, float("inf"), -6.2, T0 + 200.0)
        assert event == EVENT_NONE
        assert tracker.flagged is False
        assert tracker.mismatch_since_ts is None

        event = update_grid_consistency(tracker, float("nan"), -6.2, T0 + 250.0)
        assert event == EVENT_NONE
        assert tracker.flagged is False
        assert tracker.mismatch_since_ts is None


# ---------------------------------------------------------------------------
# update_grid_consistency() -- sticky flag
# ---------------------------------------------------------------------------


class TestStickyFlag:
    def test_consistent_reading_never_clears_the_flag(self):
        # The misconfigured sensors agree at night (PV=0, house load under
        # the 1 kW floor) -- clearing on a consistent tick would flap the
        # Repairs issue daily, so the flag is sticky for the tracker's
        # lifetime.
        tracker = GridConsistencyTracker()
        _sustained_raise(tracker, T0)
        event = update_grid_consistency(tracker, 3.0, 3.0, T0 + 400.0)
        assert event == EVENT_NONE
        assert tracker.flagged is True

    def test_no_second_raise_after_consistent_interlude(self):
        # Overnight agreement followed by the morning mismatch must not
        # re-emit EVENT_RAISE (which would re-log the warning).
        tracker = GridConsistencyTracker()
        _sustained_raise(tracker, T0)
        update_grid_consistency(tracker, 3.0, 3.0, T0 + 400.0)
        assert _sustained_raise(tracker, T0 + 500.0) == EVENT_NONE
        assert tracker.flagged is True

    def test_consistent_readings_never_flag_when_not_flagged(self):
        tracker = GridConsistencyTracker()
        assert update_grid_consistency(tracker, 3.0, 3.0, T0) == EVENT_NONE
        assert tracker.flagged is False

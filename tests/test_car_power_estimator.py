"""Exhaustive tests for the pure per-car charge-throughput estimator.

Covers every behaviour car_power_estimator.py is responsible for:

- The derived cold-start seed (derive_car_max_charge_power_kw, which lives in
  charger_state_machine.py beside the conversion factors it divides by).
- Tick classification: the common gates, the deliver/paused split, and both
  1000x unit footguns coordinator._read_power_kw can produce.
- Trapezoid accumulation, re-anchoring across gaps and backwards clocks, the
  minimum-segment discard, and the rolling commit.
- Credited paused seconds -- the flaw all three design proposals shared, and
  the reason the planner books more slots when EM spends minutes paused
  inside a booked slot.
- Per-phase bucketing (the anti-poisoning rule that deliberately disagrees
  with coordinator._derive_phase_mode on Easee's "auto" value).
- Solar exclusion, the read-out validity floor, duration weighting, the age
  and count window, the planning clamp, and the full persistence round trip
  including the restored in-flight segment's gap rule.

Everything here is sync: nothing in the module under test is a coroutine, so
there is no pytest-asyncio anywhere. Time is always injected from a fixed
epoch -- no freezegun, no wall clock, so a test can never read differently on
a slow machine or at a month boundary.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.energy_manager import coordinator as coordinator_module
from custom_components.energy_manager.car_charging_scheduler import (
    build_car_charging_schedule,
)
from custom_components.energy_manager.car_power_estimator import (
    MAX_PAUSED_SECONDS_PER_SEGMENT,
    MAX_PLAUSIBLE_SAMPLE_KW,
    MAX_RESTORED_SEGMENT_GAP_SECONDS,
    MAX_SAMPLES_PER_BUCKET,
    MAX_SEGMENT_SECONDS,
    MAX_TICK_GAP_SECONDS,
    MIN_SAMPLES,
    MIN_SEGMENT_SECONDS,
    MIN_TOTAL_SECONDS,
    SAMPLE_MAX_AGE_DAYS,
    SEGMENT_PERSIST_INTERVAL_SECONDS,
    CarThroughputLearner,
    ThroughputSample,
    ThroughputTick,
    attributable_car,
    classify_tick,
    observed_phase_count,
    planning_power_kw,
    prune_samples,
    selection_is_unambiguous,
    weighted_mean_kw,
)
from custom_components.energy_manager.charger_state_machine import (
    CarDemand,
    derive_car_max_charge_power_kw,
)
from custom_components.energy_manager.const import (
    DEFAULT_CHARGER_CONVERSION_FACTOR_1PHASE,
    DEFAULT_CHARGER_CONVERSION_FACTOR_2PHASE,
    DEFAULT_CHARGER_CONVERSION_FACTOR_3PHASE,
    MAX_CAR_MAX_CHARGE_POWER_KW,
    MAX_MAX_CHARGE_AMPS,
    MIN_CAR_MAX_CHARGE_POWER_KW,
    MIN_MAX_CHARGE_AMPS,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc

#: Fixed epoch. Every timestamp in this file is an offset from it, so nothing
#: here depends on the wall clock or on the month the suite happens to run in.
T0 = datetime(2026, 1, 1, tzinfo=UTC)

DEFAULT_CAR_ID = "car_a"

#: The flat default this feature replaces. Kept as a literal so the seed tests
#: still pin the point of the change after the constant itself was deleted.
OLD_FLAT_DEFAULT_KW = 7.4


def _at(seconds: float) -> datetime:
    """A UTC-aware timestamp `seconds` after the fixed epoch."""
    return T0 + timedelta(seconds=seconds)


def _tick(at_seconds: float = 0.0, **overrides) -> ThroughputTick:
    """A qualifying "deliver" tick, with any field overridden by keyword."""
    fields = {
        "now": _at(at_seconds),
        "car_id": DEFAULT_CAR_ID,
        "phases": 3,
        "power_kw": 11.0,
        "mode": "scheduled",
        "sequence_state": "idle",
        "stuck": False,
        "dry_run": False,
        "target_amps": 16.0,
        "min_amps": 6.0,
        "fallback_mode": False,
    }
    fields.update(overrides)
    return ThroughputTick(**fields)


def _car(
    *,
    car_id: str | None = None,
    present: bool = True,
    demanding: bool = False,
    capability: int = 3,
) -> CarDemand:
    """A CarDemand shaped only by what attribution actually reads."""
    return CarDemand(
        active_slot=demanding,
        home_and_plugged=present,
        phase_capability=capability,
        car_id=car_id,
    )


def _run_delivering(
    learner: CarThroughputLearner,
    *,
    start: int = 0,
    duration: int = 600,
    power_kw: float = 11.0,
    phases: int = 3,
    car_id: str = DEFAULT_CAR_ID,
    step: int = 30,
) -> int:
    """Emit delivering ticks every `step` seconds across `duration`.

    The tick that OPENS a segment only anchors it and credits nothing, so a
    run of `duration` measured seconds needs ticks at start, start + step, ...
    start + duration inclusive. `duration` must be a whole multiple of `step`.

    Returns the offset of the last tick emitted.
    """
    for offset in range(start, start + duration + 1, step):
        learner.observe(
            _tick(offset, power_kw=power_kw, phases=phases, car_id=car_id)
        )
    return start + duration


def _close(
    learner: CarThroughputLearner, at_seconds: float, car_id: str = DEFAULT_CAR_ID
) -> bool:
    """Close the open segment with a rejecting tick, as a real session end does."""
    return learner.observe(_tick(at_seconds, mode="idle", car_id=car_id))


def _samples(
    learner: CarThroughputLearner, car_id: str = DEFAULT_CAR_ID, phases: int = 3
) -> list[dict]:
    """One bucket's committed samples, read through the public serialize()."""
    return learner.serialize()["cars"].get(car_id, {}).get(str(phases), [])


def _stored_sample(mean_kw: float, seconds: float, age_seconds: float) -> dict:
    """A persisted sample dict, `age_seconds` old relative to the epoch."""
    return {
        "committed_at": _at(-age_seconds).isoformat(),
        "mean_kw": mean_kw,
        "seconds": seconds,
    }


def _restored(
    sample_specs: list[tuple[float, float, float]],
    *,
    car_id: str = DEFAULT_CAR_ID,
    phases: int = 3,
    now: datetime = T0,
) -> CarThroughputLearner:
    """A learner restored from (mean_kw, seconds, age_seconds) triples.

    Building a bucket through restore() rather than through observe() is the
    only way to pin read-out gating on exact durations: observe() can only
    produce the durations a tick cadence happens to add up to.
    """
    raw = {
        "cars": {
            car_id: {
                str(phases): [
                    _stored_sample(mean_kw, seconds, age)
                    for mean_kw, seconds, age in sample_specs
                ]
            }
        },
        "segment": None,
    }
    return CarThroughputLearner.restore(raw, now)


def _seed(capability: int, amps: float) -> float:
    """derive_car_max_charge_power_kw() bound to the real const.py constants."""
    return derive_car_max_charge_power_kw(
        capability,
        amps,
        DEFAULT_CHARGER_CONVERSION_FACTOR_1PHASE,
        DEFAULT_CHARGER_CONVERSION_FACTOR_2PHASE,
        DEFAULT_CHARGER_CONVERSION_FACTOR_3PHASE,
        MIN_CAR_MAX_CHARGE_POWER_KW,
        MAX_CAR_MAX_CHARGE_POWER_KW,
    )


# ---------------------------------------------------------------------------
# The derived cold-start seed
# ---------------------------------------------------------------------------


class TestDeriveCarMaxChargePowerKw:
    """The per-car Max Charge Power default, derived instead of flat."""

    @pytest.mark.parametrize(
        ("capability", "expected"),
        [(1, 3.7), (2, 6.4), (3, 11.0)],
    )
    def test_default_amps_seed_per_capability(self, capability, expected):
        # 16 / 4.3, 16 / 2.5, 16 / 1.45 -- the factors are A/kW, so this divides.
        assert _seed(capability, 16.0) == pytest.approx(expected)

    def test_every_seed_differs_from_the_old_flat_default(self):
        """The whole point of the change: 7.4 kW matched no capability at 16 A."""
        for capability in (1, 2, 3):
            assert _seed(capability, 16.0) != pytest.approx(OLD_FLAT_DEFAULT_KW)

    def test_the_ceiling_clamp_binds_at_32a_three_phase(self):
        # Raw 32 / 1.45 = 22.069 rounds to 22.1, above the number entity's max.
        assert 32.0 / DEFAULT_CHARGER_CONVERSION_FACTOR_3PHASE > 22.0
        assert _seed(3, 32.0) == pytest.approx(MAX_CAR_MAX_CHARGE_POWER_KW)

    def test_the_floor_clamp_only_fires_outside_the_legal_amp_range(self):
        """At the legal minimum, rounding alone already yields the floor.

        6 / 4.3 = 1.39535 rounds to 1.4, so the only way to exercise the floor
        is an out-of-range max_charge_amps -- which is exactly what the clamp
        is a guard against.
        """
        assert _seed(1, 6.0) == pytest.approx(MIN_CAR_MAX_CHARGE_POWER_KW)
        assert round(6.0 / DEFAULT_CHARGER_CONVERSION_FACTOR_1PHASE, 1) == pytest.approx(
            MIN_CAR_MAX_CHARGE_POWER_KW
        )
        assert 2.0 / DEFAULT_CHARGER_CONVERSION_FACTOR_1PHASE < MIN_CAR_MAX_CHARGE_POWER_KW
        assert _seed(1, 2.0) == pytest.approx(MIN_CAR_MAX_CHARGE_POWER_KW)

    def test_the_whole_legal_grid_stays_inside_the_number_entity_band(self):
        for amps in range(MIN_MAX_CHARGE_AMPS, MAX_MAX_CHARGE_AMPS + 1):
            for capability in (1, 2, 3):
                value = _seed(capability, float(amps))
                assert MIN_CAR_MAX_CHARGE_POWER_KW <= value <= MAX_CAR_MAX_CHARGE_POWER_KW
                # The number entity's step is 0.1; a seed it cannot represent
                # would be silently rounded by HA on first write.
                assert value == pytest.approx(round(value, 1))


# ---------------------------------------------------------------------------
# Phase bucketing
# ---------------------------------------------------------------------------


class TestObservedPhaseCount:
    """Which bucket a tick's energy is filed in -- or a refusal to guess."""

    @pytest.mark.parametrize("capability", [1, 2, 3])
    def test_raw_single_is_one_phase_whatever_the_car_can_do(self, capability):
        assert observed_phase_count(1, capability) == 1
        assert observed_phase_count("1", capability) == 1

    @pytest.mark.parametrize(
        ("capability", "expected"),
        [(1, 1), (2, 2), (3, 3)],
    )
    def test_raw_three_files_into_the_cars_own_capability(self, capability, expected):
        # A 3-phase charger only ever delivers what the car can take, so a
        # capability-2 car (e.g. VW ID.3) belongs in bucket 2, not 3.
        assert observed_phase_count(3, capability) == expected
        assert observed_phase_count("3", str(capability)) == expected

    @pytest.mark.parametrize("raw", [2, "2"])
    def test_auto_refuses_to_guess(self, raw):
        assert observed_phase_count(raw, 3) is None

    @pytest.mark.parametrize("raw", [None, "", "auto", object(), [], {}])
    def test_missing_or_unparseable_refuses(self, raw):
        assert observed_phase_count(raw, 3) is None

    @pytest.mark.parametrize(
        ("capability", "expected"),
        [(0, 1), (-5, 1), (9, 3)],
    )
    def test_capability_is_clamped_into_one_to_three(self, capability, expected):
        assert observed_phase_count(3, capability) == expected

    @pytest.mark.parametrize("capability", [None, "auto", object()])
    def test_an_unparseable_capability_refuses_rather_than_assuming_three(
        self, capability
    ):
        """Filing unknown-capability energy in bucket 3 is the poisoning case."""
        assert observed_phase_count(3, capability) is None

    def test_it_deliberately_disagrees_with_derive_phase_mode(self):
        """The contract that stops a future "cleanup" from unifying the two.

        _derive_phase_mode folds auto/missing/unparseable into "three" so the
        CONTROLLER fails safe. Measurement has the opposite requirement: guessing
        three-phase for a charger that may be on one phase poisons the bucket
        permanently. They must keep disagreeing on exactly these values.
        """
        assert coordinator_module._derive_phase_mode(2) == "three"
        assert observed_phase_count(2, 3) is None
        assert coordinator_module._derive_phase_mode(None) == "three"
        assert observed_phase_count(None, 3) is None
        assert coordinator_module._derive_phase_mode("auto") == "three"
        assert observed_phase_count("auto", 3) is None
        # ...and agree on the two values the charger actually proves.
        assert coordinator_module._derive_phase_mode(1) == "single"
        assert observed_phase_count(1, 3) == 1
        assert coordinator_module._derive_phase_mode(3) == "three"
        assert observed_phase_count(3, 3) == 3


# ---------------------------------------------------------------------------
# Car attribution
# ---------------------------------------------------------------------------


class TestAttributableCar:
    """Whose energy this is -- refused whenever it is not provable."""

    def test_no_cars_refuses(self):
        assert attributable_car([]) is None
        assert selection_is_unambiguous([]) is False

    def test_a_single_present_car_is_attributable(self):
        car = _car(car_id="a")
        assert attributable_car([car]) is car
        assert selection_is_unambiguous([car]) is True

    def test_two_present_one_demanding_picks_the_demanding_car(self):
        """The case a blanket "refuse when >1 present" rule would throw away.

        The controller resolves this deterministically (scheduled picks
        demanding[0]), so refusing it would cost a two-car household most of
        its learning.
        """
        idle_car = _car(car_id="a")
        wanting = _car(car_id="b", demanding=True)
        assert attributable_car([idle_car, wanting]) is wanting

    def test_two_present_two_demanding_refuses(self):
        cars = [_car(car_id="a", demanding=True), _car(car_id="b", demanding=True)]
        assert attributable_car(cars) is None
        assert selection_is_unambiguous(cars) is False

    def test_two_present_none_demanding_refuses(self):
        cars = [_car(car_id="a"), _car(car_id="b")]
        assert attributable_car(cars) is None

    def test_a_present_car_without_an_id_cannot_be_filed(self):
        """Selection is unambiguous, but there is no bucket to file it under."""
        car = _car(car_id=None)
        assert selection_is_unambiguous([car]) is True
        assert attributable_car([car]) is None

    def test_absent_cars_are_ignored_even_when_they_hold_the_only_id(self):
        present = _car(car_id="a")
        away = _car(car_id="b", present=False, demanding=True)
        assert attributable_car([present, away]) is present

        anonymous = _car(car_id=None)
        assert attributable_car([anonymous, away]) is None


# ---------------------------------------------------------------------------
# Tick classification
# ---------------------------------------------------------------------------


class TestClassifyTick:
    """Which ticks may be integrated, and in which tier."""

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"mode": "solar"}, "reject"),
            ({"mode": "idle"}, "reject"),
            ({"mode": "scheduled"}, "deliver"),
            ({"mode": "forced"}, "deliver"),
            ({"sequence_state": "pausing"}, "paused"),
            ({"stuck": True}, "reject"),
            ({"dry_run": True}, "reject"),
            ({"target_amps": 0.0, "min_amps": 6.0}, "paused"),
            ({"target_amps": 16.0, "power_kw": 0.2}, "reject"),
            ({"power_kw": 0.5}, "reject"),
            ({"power_kw": 0.51}, "deliver"),
            ({"power_kw": 30.0}, "reject"),
            ({"power_kw": 0.0074}, "reject"),
            ({"car_id": None}, "reject"),
            ({"phases": None}, "reject"),
            ({"fallback_mode": True}, "reject"),
        ],
    )
    def test_classification_table(self, overrides, expected):
        assert classify_tick(_tick(**overrides)) == expected

    def test_the_plausibility_bound_is_inclusive(self):
        """25 kW is believable; the mislabelled-watts reading above it is not."""
        assert classify_tick(_tick(power_kw=MAX_PLAUSIBLE_SAMPLE_KW)) == "deliver"
        assert classify_tick(_tick(power_kw=MAX_PLAUSIBLE_SAMPLE_KW + 0.1)) == "reject"

    @pytest.mark.parametrize("power_kw", [math.nan, math.inf, -math.inf, -1.0])
    def test_non_finite_and_negative_power_never_integrates(self, power_kw):
        assert classify_tick(_tick(power_kw=power_kw)) == "reject"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"sequence_state": "pausing", "stuck": True},
            {"sequence_state": "pausing", "dry_run": True},
            {"sequence_state": "pausing", "power_kw": 30.0},
            {"target_amps": 0.0, "mode": "solar"},
            {"target_amps": 0.0, "fallback_mode": True},
            {"target_amps": 0.0, "car_id": None},
        ],
    )
    def test_the_common_gates_win_over_the_paused_tier(self, overrides):
        """A pause is only creditable inside an otherwise valid EM-directed run."""
        assert classify_tick(_tick(**overrides)) == "reject"

    def test_a_pause_does_not_have_to_carry_any_power(self):
        """The 0A safety stop reads 0 kW and its seconds still count."""
        tick = _tick(target_amps=0.0, power_kw=0.0)
        assert classify_tick(tick) == "paused"

    def test_target_below_min_amps_is_a_pause_not_a_request(self):
        assert classify_tick(_tick(target_amps=5.9, min_amps=6.0)) == "paused"
        assert classify_tick(_tick(target_amps=6.0, min_amps=6.0)) == "deliver"


# ---------------------------------------------------------------------------
# Accumulation
# ---------------------------------------------------------------------------


class TestObserveAccumulation:
    """Trapezoid integration, re-anchoring, and the two commit rules."""

    def test_a_constant_run_commits_one_sample(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=1200)
        assert _close(learner, 1230) is True

        samples = _samples(learner)
        assert len(samples) == 1
        assert samples[0]["seconds"] == pytest.approx(1200.0)
        assert samples[0]["mean_kw"] == pytest.approx(11.0)
        assert samples[0]["committed_at"] == _at(1230).isoformat()

    def test_a_power_step_integrates_at_the_midpoint(self):
        """Trapezoid, not rectangle.

        Easee ticks are TRIGGERED by the power entity changing, so crediting
        the whole interval at the post-change value would bias every
        measurement in one direction.
        """
        learner = CarThroughputLearner()
        learner.observe(_tick(0, power_kw=11.0))
        learner.observe(_tick(30, power_kw=7.0))

        segment = learner.snapshot()["segment"]
        assert segment["energy_kwh"] == pytest.approx(9.0 * 30 / 3600)
        assert segment["seconds"] == pytest.approx(30.0)

    def test_a_gap_beyond_the_tick_limit_re_anchors_and_credits_nothing(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=600)
        before = learner.snapshot()["segment"]

        learner.observe(_tick(600 + MAX_TICK_GAP_SECONDS + 1))

        after = learner.snapshot()["segment"]
        assert after["seconds"] == pytest.approx(before["seconds"])
        assert after["energy_kwh"] == pytest.approx(before["energy_kwh"])

    def test_a_gap_exactly_at_the_tick_limit_still_integrates(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=600)
        learner.observe(_tick(600 + MAX_TICK_GAP_SECONDS))

        assert learner.snapshot()["segment"]["seconds"] == pytest.approx(
            600.0 + MAX_TICK_GAP_SECONDS
        )

    def test_a_backwards_clock_re_anchors_without_crashing(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=600)

        learner.observe(_tick(570))
        assert learner.snapshot()["segment"]["seconds"] == pytest.approx(600.0)

        learner.observe(_tick(600))
        assert learner.snapshot()["segment"]["seconds"] == pytest.approx(630.0)

    def test_a_segment_below_the_minimum_is_discarded(self):
        learner = CarThroughputLearner()
        # Every gap stays inside MAX_TICK_GAP_SECONDS: a lone late tick would
        # re-anchor instead of extending, and measure nothing at all.
        _run_delivering(learner, start=0, duration=270)
        learner.observe(_tick(299))
        assert learner.snapshot()["segment"]["seconds"] == pytest.approx(299.0)
        assert MIN_SEGMENT_SECONDS > 299.0

        assert _close(learner, 329) is True
        assert _samples(learner) == []

    def test_a_segment_above_the_minimum_commits(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=270)
        learner.observe(_tick(301))
        assert MIN_SEGMENT_SECONDS <= 301.0

        _close(learner, 331)
        samples = _samples(learner)
        assert len(samples) == 1
        assert samples[0]["seconds"] == pytest.approx(301.0)

    def test_a_long_run_rolling_commits_and_keeps_accumulating(self):
        """An 8-hour night must not be one all-or-nothing sample."""
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=4200)
        _close(learner, 4230)

        samples = _samples(learner)
        assert len(samples) == 2
        assert samples[0]["seconds"] == pytest.approx(MAX_SEGMENT_SECONDS)
        assert samples[1]["seconds"] == pytest.approx(600.0)
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, _at(4230)) == pytest.approx(11.0)


# ---------------------------------------------------------------------------
# Credited paused seconds
# ---------------------------------------------------------------------------


class TestPausedCredit:
    """EM-commanded pauses inside a booked slot are part of what it delivers."""

    def test_paused_minutes_pull_the_mean_below_the_delivering_rate(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=1200)
        for offset in range(1230, 1801, 30):
            learner.observe(_tick(offset, power_kw=0.0, target_amps=0.0))
        _close(learner, 1830)

        samples = _samples(learner)
        assert len(samples) == 1
        assert samples[0]["seconds"] == pytest.approx(1800.0)

        # 20 min at 11 kW = 3.66667 kWh. The FIRST paused tick's trapezoid also
        # credits the 11 -> 0 ramp-down half-interval, (11 + 0) / 2 * 30/3600 =
        # 0.045833 kWh; every later paused tick credits 30 s and no energy. So
        # 3.7125 kWh over 0.5 h = 7.425 kW, not the 7.333 a rectangular
        # integrator would give.
        assert samples[0]["mean_kw"] == pytest.approx(7.425)

        # The point of the whole feature: the planner sizes slots at 7.4 kW
        # rather than 11.0, and therefore books more of them.
        assert samples[0]["mean_kw"] < 11.0

    def test_a_pause_longer_than_the_cap_closes_the_segment(self):
        """Beyond half an hour a "pause" is a stopped session, not throttling."""
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=1200)
        for offset in range(1230, 3031, 30):
            learner.observe(_tick(offset, power_kw=0.0, target_amps=0.0))

        assert MAX_PAUSED_SECONDS_PER_SEGMENT < 3030.0 - 1200.0
        assert learner.snapshot()["segment"] is None
        samples = _samples(learner)
        assert len(samples) == 1
        assert samples[0]["seconds"] == pytest.approx(3030.0)

    def test_the_paused_cap_is_checked_before_the_rolling_commit(self):
        """A stuck pause must never keep a segment rolling forever."""
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=1200)
        for offset in range(1230, 3031, 30):
            learner.observe(_tick(offset, power_kw=0.0, target_amps=0.0))

        # Closed at 3030 s, well short of the 3600 s rolling commit.
        assert _samples(learner)[0]["seconds"] < MAX_SEGMENT_SECONDS

    def test_a_paused_tick_with_no_open_segment_credits_nothing(self):
        learner = CarThroughputLearner()
        assert learner.observe(_tick(0, power_kw=0.0, target_amps=0.0)) is False
        assert learner.snapshot()["segment"] is None
        assert learner.serialize()["cars"] == {}

    def test_a_pause_at_a_small_nonzero_power_still_opens_nothing(self):
        """The zero-power case is the easy half of the same invariant.

        _commit() only discards on an energy of exactly 0.0, so a pause that
        carries any standby draw at all -- Easee's own idle consumption, or a
        power sensor that has not caught up with the 0 A command yet -- would
        commit a sample whose delivering seconds are zero and whose mean is
        that standby figure.
        """
        learner = CarThroughputLearner()
        for offset in range(0, 1201, 30):
            assert (
                learner.observe(_tick(offset, power_kw=0.3, target_amps=0.0)) is False
            )
        assert learner.snapshot()["segment"] is None
        _close(learner, 1230)
        assert learner.serialize()["cars"] == {}

    def test_a_rolling_commit_landing_on_a_paused_tick_is_deferred(self):
        """A pause straddling the rolling-commit boundary keeps its seconds.

        Two invariants meet here and both must hold. The commit must not
        re-open on a paused tick -- that segment would hold only paused
        seconds, commit a sample with zero delivering time at whatever
        standby power the charger reports, and hand itself a fresh
        MAX_PAUSED_SECONDS_PER_SEGMENT budget. But it must also not commit
        and leave nothing open, because then every remaining tick of the
        pause hits the cold-start branch and is discarded, dropping the tail
        of the pause and biasing the estimate HIGH -- the under-booking
        direction. So the rollover waits for the next delivering tick, and
        the pause accumulates where it belongs. Reachable on the target
        install: 30+ minutes of charging followed by a fuse pause, a 0 A
        safety stop, or a phase-switch sequence.
        """
        learner = CarThroughputLearner()
        # 3570 measured seconds: the next 30 s tick lands exactly on the
        # MAX_SEGMENT_SECONDS boundary.
        assert _run_delivering(learner, start=0, duration=3570) == 3570

        # The boundary tick is a pause: nothing is committed, nothing is
        # opened, and the segment stays put to receive the pause.
        learner.observe(_tick(3600, power_kw=0.3, target_amps=0.0))
        segment = learner.snapshot()["segment"]
        assert segment is not None
        assert _samples(learner) == []

        # Ten more minutes of the same pause are credited to that segment,
        # not discarded and not turned into a second sample.
        for offset in range(3630, 4231, 30):
            learner.observe(_tick(offset, power_kw=0.3, target_amps=0.0))
        assert _samples(learner) == []
        # 30 s from the boundary tick itself plus 21 further paused ticks.
        assert learner.snapshot()["segment"]["paused_seconds"] == pytest.approx(660.0)

        _close(learner, 4260)
        samples = _samples(learner)
        assert len(samples) == 1
        # 3600 delivering + 630 paused seconds, so the mean is dragged below
        # the delivering-only 11.0 -- exactly what the planner needs.
        assert samples[0]["seconds"] == pytest.approx(4230.0)
        assert samples[0]["mean_kw"] < 11.0
        assert samples[0]["mean_kw"] == pytest.approx(11.0 * 3600.0 / 4230.0, abs=0.1)

    def test_a_pause_past_the_cap_still_closes_a_rolled_over_segment(self):
        """Deferring the rollover must not let a stuck pause run forever.

        The MAX_PAUSED_SECONDS_PER_SEGMENT check runs before the rollover, so
        a segment held open past MAX_SEGMENT_SECONDS by a pause is still
        closed once that pause exceeds the cap.
        """
        learner = CarThroughputLearner()
        assert _run_delivering(learner, start=0, duration=3570) == 3570
        for offset in range(3600, 3600 + int(MAX_PAUSED_SECONDS_PER_SEGMENT) + 120, 30):
            learner.observe(_tick(offset, power_kw=0.3, target_amps=0.0))

        assert learner.snapshot()["segment"] is None
        assert len(_samples(learner)) == 1


# ---------------------------------------------------------------------------
# Per-phase segmentation
# ---------------------------------------------------------------------------


class TestPhaseSegmentation:
    """1-phase energy can never be read back as a 3-phase figure."""

    def test_a_phase_switch_closes_the_segment_and_opens_a_new_bucket(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=600, power_kw=11.0, phases=3)
        _run_delivering(learner, start=630, duration=600, power_kw=3.5, phases=1)
        _close(learner, 1260)

        three_phase = _samples(learner, phases=3)
        single_phase = _samples(learner, phases=1)
        assert len(three_phase) == 1
        assert three_phase[0]["mean_kw"] == pytest.approx(11.0)
        assert three_phase[0]["seconds"] == pytest.approx(600.0)
        assert len(single_phase) == 1
        assert single_phase[0]["mean_kw"] == pytest.approx(3.5)
        assert single_phase[0]["seconds"] == pytest.approx(600.0)

    def test_buckets_never_leak_into_each_other(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=4200, power_kw=11.0, phases=3)
        _close(learner, 4230)
        _run_delivering(learner, start=10000, duration=4200, power_kw=3.5, phases=1)
        _close(learner, 14230)

        now = _at(14230)
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, now) == pytest.approx(11.0)
        assert learner.estimate_kw(DEFAULT_CAR_ID, 1, now) == pytest.approx(3.5)
        # No cross-bucket fallback: an unlearned bucket says so.
        assert learner.estimate_kw(DEFAULT_CAR_ID, 2, now) is None

    def test_a_capability_two_car_on_a_three_phase_charger_files_into_bucket_two(self):
        bucket = observed_phase_count(3, 2)
        assert bucket == 2

        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=600, power_kw=7.0, phases=bucket)
        _close(learner, 630)

        assert list(learner.serialize()["cars"][DEFAULT_CAR_ID]) == ["2"]


class TestPhaseBucketClampSymmetry:
    """The read side must clamp the bucket exactly like the write side.

    observed_phase_count() files a capability outside 1..3 into the clamped
    bucket. If estimate_kw() read the raw value instead, an out-of-range
    stored capability would write bucket 1 or 3 and read bucket 0 or 4
    forever -- accumulating but never returning anything, and indistinguishable
    from "not learned yet". Latent while the SelectSelector only offers
    "1"/"2"/"3", but the asymmetry is the trap.
    """

    def test_an_above_range_capability_reads_the_bucket_it_wrote(self):
        assert observed_phase_count(3, 5) == 3
        learner = _restored([(11.0, 3600.0, 120), (11.0, 3600.0, 60)], phases=3)
        assert learner.estimate_kw(DEFAULT_CAR_ID, 5, T0) == pytest.approx(11.0)

    def test_a_below_range_capability_reads_the_bucket_it_wrote(self):
        assert observed_phase_count(1, 0) == 1
        learner = _restored([(3.6, 3600.0, 120), (3.6, 3600.0, 60)], phases=1)
        assert learner.estimate_kw(DEFAULT_CAR_ID, 0, T0) == pytest.approx(3.6)

    def test_unparseable_still_refuses_rather_than_clamping(self):
        assert observed_phase_count(3, "x") is None
        learner = _restored([(11.0, 3600.0, 120), (11.0, 3600.0, 60)], phases=3)
        assert learner.estimate_kw(DEFAULT_CAR_ID, "x", T0) is None


# ---------------------------------------------------------------------------
# Solar exclusion
# ---------------------------------------------------------------------------


class TestSolarExclusion:
    """Requirement 3: solar sessions are not evidence of grid throughput."""

    def test_a_two_hour_solar_session_produces_no_samples_at_all(self):
        learner = CarThroughputLearner()
        for offset in range(0, 7201, 30):
            learner.observe(_tick(offset, power_kw=1.4, mode="solar"))

        assert learner.serialize()["cars"] == {}
        assert learner.snapshot()["segment"] is None

    def test_only_the_grid_session_that_follows_is_measured(self):
        learner = CarThroughputLearner()
        for offset in range(0, 7201, 30):
            learner.observe(_tick(offset, power_kw=1.4, mode="solar"))

        # 70 min of grid charging: a 3600 s rolling commit plus a 600 s tail,
        # which is the shortest run that clears MIN_SAMPLES and MIN_TOTAL_SECONDS.
        _run_delivering(learner, start=7230, duration=4200, power_kw=11.0)
        _close(learner, 11460)

        samples = _samples(learner)
        assert [s["seconds"] for s in samples] == [
            pytest.approx(3600.0),
            pytest.approx(600.0),
        ]
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, _at(11460)) == pytest.approx(11.0)


# ---------------------------------------------------------------------------
# Read-out gating
# ---------------------------------------------------------------------------


class TestEstimateGating:
    """A window can say "not enough evidence yet" -- the reason it beat an EMA."""

    def test_one_long_sample_is_not_enough(self):
        assert MIN_SAMPLES == 2
        learner = _restored([(11.0, 3600.0, 60)])
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, T0) is None

    def test_two_short_samples_are_not_enough(self):
        assert MIN_TOTAL_SECONDS > 1800.0
        learner = _restored([(11.0, 900.0, 120), (11.0, 900.0, 60)])
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, T0) is None

    def test_the_mean_is_duration_weighted_not_sample_weighted(self):
        """Both durations respect MAX_SEGMENT_SECONDS, so this history is real."""
        learner = _restored([(4.0, 1800.0, 120), (10.0, 3600.0, 60)])
        # (4 * 1800 + 10 * 3600) / 5400 = 8.0. The unweighted mean would be 7.0.
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, T0) == pytest.approx(8.0)
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, T0) != pytest.approx(7.0)
        assert max(1800.0, 3600.0) <= MAX_SEGMENT_SECONDS

    @pytest.mark.parametrize("mean_kw", [23.0, 1.0])
    def test_an_estimate_outside_the_number_entity_band_is_refused(self, mean_kw):
        """Outside [1.4, 22.0] the measurement is not believable, so say None."""
        learner = _restored([(mean_kw, 3600.0, 120), (mean_kw, 3600.0, 60)])
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, T0) is None

    @pytest.mark.parametrize(
        "mean_kw", [MIN_CAR_MAX_CHARGE_POWER_KW, MAX_CAR_MAX_CHARGE_POWER_KW]
    )
    def test_the_band_edges_are_inclusive(self, mean_kw):
        learner = _restored([(mean_kw, 3600.0, 120), (mean_kw, 3600.0, 60)])
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, T0) == pytest.approx(mean_kw)

    def test_unknown_car_bucket_or_key_type_is_none(self):
        learner = _restored([(11.0, 3600.0, 120), (11.0, 3600.0, 60)])
        assert learner.estimate_kw("no-such-car", 3, T0) is None
        assert learner.estimate_kw(DEFAULT_CAR_ID, 1, T0) is None
        assert learner.estimate_kw(None, 3, T0) is None
        assert learner.estimate_kw(DEFAULT_CAR_ID, None, T0) is None
        assert learner.estimate_kw(DEFAULT_CAR_ID, "x", T0) is None
        # The bucket may be asked for as an int or as a str.
        assert learner.estimate_kw(DEFAULT_CAR_ID, "3", T0) == pytest.approx(11.0)

    def test_weighted_mean_kw_gates_the_same_way_directly(self):
        samples = [
            ThroughputSample(_at(-120), 4.0, 1800.0),
            ThroughputSample(_at(-60), 10.0, 3600.0),
        ]
        assert weighted_mean_kw(samples) == pytest.approx(8.0)
        assert weighted_mean_kw(samples[:1]) is None
        assert weighted_mean_kw([]) is None


# ---------------------------------------------------------------------------
# The age and count window
# ---------------------------------------------------------------------------


class TestPruning:
    """A stale or wild estimate must age out on its own."""

    def test_prune_samples_drops_aged_out_entries(self):
        old = ThroughputSample(
            T0 - timedelta(days=SAMPLE_MAX_AGE_DAYS + 1), 11.0, 3600.0
        )
        fresh = ThroughputSample(T0 - timedelta(days=1), 11.0, 3600.0)
        assert prune_samples([old, fresh], T0) == [fresh]

    def test_estimate_kw_drops_aged_out_entries(self):
        learner = _restored([(11.0, 3600.0, 120), (11.0, 3600.0, 60)])
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, T0) == pytest.approx(11.0)

        later = T0 + timedelta(days=SAMPLE_MAX_AGE_DAYS + 1)
        assert learner.estimate_kw(DEFAULT_CAR_ID, 3, later) is None

    def test_only_the_newest_samples_survive_the_count_cap(self):
        # sample i was committed i minutes ago and carries mean_kw i + 1.
        samples = [
            ThroughputSample(T0 - timedelta(minutes=i), float(i + 1), 600.0)
            for i in range(MAX_SAMPLES_PER_BUCKET + 5)
        ]
        kept = prune_samples(samples, T0)

        assert len(kept) == MAX_SAMPLES_PER_BUCKET
        # Oldest first, and the five oldest (means 21..25) are gone.
        assert [s.mean_kw for s in kept] == [
            float(i) for i in range(MAX_SAMPLES_PER_BUCKET, 0, -1)
        ]

    def test_a_car_whose_samples_all_aged_out_disappears_from_storage(self):
        """Reading prunes, so nothing lingers in the Store forever."""
        learner = _restored([(11.0, 3600.0, 120), (11.0, 3600.0, 60)])
        assert DEFAULT_CAR_ID in learner.serialize()["cars"]

        learner.estimate_kw(
            DEFAULT_CAR_ID, 3, T0 + timedelta(days=SAMPLE_MAX_AGE_DAYS + 1)
        )
        assert learner.serialize()["cars"] == {}

    def test_the_age_window_is_enforced_at_write_time_too(self):
        """A commit prunes, so a long-lived HA process cannot blend months.

        The learner is restored holding a 29-day-old sample, then a fresh
        segment commits two days later -- by which point that sample is 31 days
        old and must not survive the write.
        """
        learner = _restored([(11.0, 3600.0, 29 * 86400)])
        assert len(_samples(learner)) == 1

        two_days = 2 * 86400
        _run_delivering(learner, start=two_days, duration=600, power_kw=7.0)
        _close(learner, two_days + 630)

        samples = _samples(learner)
        assert len(samples) == 1
        assert samples[0]["mean_kw"] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# The planning clamp
# ---------------------------------------------------------------------------


class TestPlanningPowerKw:
    """Requirement 4: floor first, the user's ceiling second."""

    def test_no_estimate_uses_the_ceiling(self):
        assert planning_power_kw(None, 11.0) == pytest.approx(11.0)

    def test_an_estimate_above_the_ceiling_is_capped(self):
        assert planning_power_kw(14.0, 11.0) == pytest.approx(11.0)

    def test_an_estimate_below_the_floor_is_raised(self):
        assert planning_power_kw(0.9, 11.0) == pytest.approx(
            MIN_CAR_MAX_CHARGE_POWER_KW
        )

    def test_an_estimate_inside_the_band_is_used_as_is(self):
        assert planning_power_kw(7.0, 11.0) == pytest.approx(7.0)

    @pytest.mark.parametrize(
        "estimate", [math.nan, math.inf, -math.inf, 0.0, -3.0]
    )
    def test_unusable_estimates_fall_back_to_the_ceiling(self, estimate):
        assert planning_power_kw(estimate, 11.0) == pytest.approx(11.0)

    def test_the_result_is_always_positive_and_never_above_the_ceiling(self):
        """A zero would make build_car_charging_schedule return _empty_result."""
        ceilings = [
            MIN_CAR_MAX_CHARGE_POWER_KW,
            3.7,
            6.4,
            OLD_FLAT_DEFAULT_KW,
            11.0,
            MAX_CAR_MAX_CHARGE_POWER_KW,
        ]
        estimates = [
            None,
            math.nan,
            math.inf,
            -math.inf,
            -5.0,
            0.0,
            0.4,
            1.4,
            3.3,
            7.0,
            11.0,
            25.0,
        ]
        for ceiling in ceilings:
            for estimate in estimates:
                value = planning_power_kw(estimate, ceiling)
                assert value > 0.0
                assert value <= ceiling


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestSerializeRestore:
    """Corrupt storage must never block setup, and never cost more than itself."""

    def test_round_trip_preserves_buckets_and_the_in_flight_segment(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=600)
        _close(learner, 630)
        _run_delivering(learner, start=1000, duration=390)

        stored = learner.serialize_stored(_at(1400))
        restored = CarThroughputLearner.restore(stored, _at(1400))

        assert restored.serialize() == learner.serialize()
        # last_ts is deliberately not persisted: the first tick back must
        # re-anchor rather than integrate across unobserved downtime.
        assert restored._segment is not None
        assert restored._segment.last_ts is None
        assert "last_ts" not in stored["segment"]

    def test_serialize_has_no_saved_at_but_serialize_stored_does(self):
        learner = CarThroughputLearner()
        assert "saved_at" not in learner.serialize()
        assert learner.serialize_stored(T0)["saved_at"] == T0.isoformat()

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "garbage",
            [],
            {},
            5,
            {"cars": 5},
            {"cars": {"a": 3}},
            {"cars": {"a": {"3": "x"}}},
            {"cars": {5: {"3": []}}},
        ],
    )
    def test_corrupt_payloads_restore_an_empty_learner(self, raw):
        learner = CarThroughputLearner.restore(raw, T0)
        assert learner.serialize() == {"cars": {}, "segment": None}

    def test_one_malformed_sample_costs_only_itself(self):
        raw = {
            "cars": {
                DEFAULT_CAR_ID: {
                    "3": [
                        _stored_sample(11.0, 3600.0, 60),
                        {"mean_kw": 11.0, "seconds": 3600.0},  # no committed_at
                        {
                            "committed_at": "not-a-date",
                            "mean_kw": 11.0,
                            "seconds": 3600.0,
                        },
                        _stored_sample("nan", 3600.0, 120),
                        _stored_sample(11.0, "inf", 180),
                        # An int too large for float() raises OverflowError.
                        _stored_sample(10**400, 3600.0, 240),
                        _stored_sample(11.0, 3600.0, -3600),  # committed in the future
                        _stored_sample(-1.0, 3600.0, 300),  # non-physical
                        _stored_sample(9.0, 3600.0, 30),
                    ]
                }
            },
            "segment": None,
        }
        learner = CarThroughputLearner.restore(raw, T0)

        # Oldest first: the 60 s-old 11.0 kW sample, then the 30 s-old 9.0 kW one.
        assert [s["mean_kw"] for s in _samples(learner)] == [11.0, 9.0]

    def test_a_naive_committed_at_is_coerced_to_utc(self):
        raw = {
            "cars": {
                DEFAULT_CAR_ID: {
                    "3": [
                        {
                            "committed_at": "2025-12-31T12:00:00",
                            "mean_kw": 11.0,
                            "seconds": 3600.0,
                        }
                    ]
                }
            },
            "segment": None,
        }
        learner = CarThroughputLearner.restore(raw, T0)
        assert _samples(learner)[0]["committed_at"] == "2025-12-31T12:00:00+00:00"

    def test_only_valid_bucket_keys_survive(self):
        entry = _stored_sample(11.0, 3600.0, 60)
        raw = {
            "cars": {
                DEFAULT_CAR_ID: {
                    "0": [dict(entry)],
                    "4": [dict(entry)],
                    "x": [dict(entry)],
                    "3.5": [dict(entry)],
                    "3": [dict(entry)],
                }
            },
            "segment": None,
        }
        learner = CarThroughputLearner.restore(raw, T0)
        assert list(learner.serialize()["cars"][DEFAULT_CAR_ID]) == ["3"]

    def test_the_window_is_re_applied_on_load(self):
        """A long outage must not resurrect a month-old bucket."""
        learner = _restored(
            [
                (11.0, 3600.0, (SAMPLE_MAX_AGE_DAYS + 1) * 86400),
                (11.0, 3600.0, 60),
            ]
        )
        assert len(_samples(learner)) == 1


class TestRestorePrunesUnknownCars:
    """Deleting a car garbage-collects its learning; HA offers no removal hook."""

    @staticmethod
    def _two_car_payload() -> dict:
        return {
            "cars": {
                "a": {"3": [_stored_sample(11.0, 3600.0, 60)]},
                "b": {"3": [_stored_sample(3.5, 3600.0, 60)]},
            },
            "segment": None,
        }

    def test_a_car_that_is_no_longer_a_subentry_is_dropped(self):
        learner = CarThroughputLearner.restore(
            self._two_car_payload(), T0, frozenset({"a"})
        )
        assert list(learner.serialize()["cars"]) == ["a"]

    def test_no_known_set_keeps_everything(self):
        learner = CarThroughputLearner.restore(self._two_car_payload(), T0, None)
        assert sorted(learner.serialize()["cars"]) == ["a", "b"]

    def test_an_empty_known_set_drops_everything(self):
        learner = CarThroughputLearner.restore(
            self._two_car_payload(), T0, frozenset()
        )
        assert learner.serialize()["cars"] == {}

    @staticmethod
    def _deleted_car_segment_payload(saved_age_seconds: float) -> dict:
        """A payload whose in-flight segment belongs to a car that is gone."""
        return {
            "cars": {"a": {"3": [_stored_sample(11.0, 3600.0, 60)]}},
            "segment": {
                "car_id": "b",
                "phases": 3,
                "energy_kwh": 6.0,
                "seconds": 1800.0,
                "paused_seconds": 0.0,
                "last_power_kw": 12.0,
            },
            "saved_at": _at(-saved_age_seconds).isoformat(),
        }

    def test_a_deleted_cars_in_flight_segment_is_never_committed(self):
        """The segment path must consult known_car_ids too.

        _commit() does _samples.setdefault(car_id, {}), so committing a
        restored segment for a deleted car resurrects exactly the bucket the
        filter exists to garbage-collect.
        """
        learner = CarThroughputLearner.restore(
            self._deleted_car_segment_payload(MAX_RESTORED_SEGMENT_GAP_SECONDS + 60),
            T0,
            frozenset({"a"}),
        )
        assert list(learner.serialize()["cars"]) == ["a"]
        assert learner.snapshot()["segment"] is None

    def test_a_deleted_cars_segment_is_dropped_on_a_quick_reload_too(self):
        """The short-gap path keeps the segment open; it must not keep a
        deleted car's, or the next tick on a reused key resurrects it."""
        learner = CarThroughputLearner.restore(
            self._deleted_car_segment_payload(60.0), T0, frozenset({"a"})
        )
        assert learner.snapshot()["segment"] is None
        assert list(learner.serialize()["cars"]) == ["a"]

    def test_no_known_set_still_keeps_the_segment(self):
        """The filter is opt-in: without it, restore behaves exactly as before."""
        learner = CarThroughputLearner.restore(
            self._deleted_car_segment_payload(60.0), T0, None
        )
        assert learner.snapshot()["segment"]["car_id"] == "b"


class TestRestoreSegmentGap:
    """The SolarActivationTracker gap rule, applied to the in-flight segment."""

    @staticmethod
    def _stored(seconds: float, saved_at: datetime | None = T0) -> dict:
        stored = {
            "cars": {},
            "segment": {
                "car_id": DEFAULT_CAR_ID,
                "phases": 3,
                "energy_kwh": 11.0 * seconds / 3600.0,
                "seconds": float(seconds),
                "paused_seconds": 0.0,
                "last_power_kw": 11.0,
            },
        }
        if saved_at is not None:
            stored["saved_at"] = saved_at.isoformat()
        return stored

    def test_a_short_gap_keeps_the_segment_and_never_credits_the_gap(self):
        gap = MAX_RESTORED_SEGMENT_GAP_SECONDS - 60
        learner = CarThroughputLearner.restore(self._stored(600), _at(gap))

        assert learner.snapshot()["segment"]["seconds"] == pytest.approx(600.0)
        assert learner._segment.last_ts is None
        assert learner.serialize()["cars"] == {}

        # The first tick back re-anchors: the reload gap is not measured time.
        learner.observe(_tick(gap))
        assert learner.snapshot()["segment"]["seconds"] == pytest.approx(600.0)

        learner.observe(_tick(gap + 30))
        assert learner.snapshot()["segment"]["seconds"] == pytest.approx(630.0)

    def test_a_long_gap_commits_a_qualifying_segment_at_saved_at(self):
        now = _at(MAX_RESTORED_SEGMENT_GAP_SECONDS + 60)
        learner = CarThroughputLearner.restore(self._stored(600), now)

        assert learner.snapshot()["segment"] is None
        samples = _samples(learner)
        assert len(samples) == 1
        assert samples[0]["mean_kw"] == pytest.approx(11.0)
        # Committed at saved_at, never at now: a restored sample must not look
        # fresher than the measurement actually is.
        assert samples[0]["committed_at"] == T0.isoformat()

    def test_a_long_gap_discards_a_segment_below_the_minimum(self):
        now = _at(MAX_RESTORED_SEGMENT_GAP_SECONDS + 60)
        learner = CarThroughputLearner.restore(self._stored(200), now)

        assert MIN_SEGMENT_SECONDS > 200.0
        assert learner.snapshot()["segment"] is None
        assert learner.serialize()["cars"] == {}

    @pytest.mark.parametrize("saved_at", ["missing", "not-a-date", "future"])
    def test_an_unusable_saved_at_discards_the_segment(self, saved_at):
        """Downtime of unknown length is not measurable, so measure nothing."""
        stored = self._stored(600, saved_at=None)
        if saved_at == "not-a-date":
            stored["saved_at"] = "not-a-date"
        elif saved_at == "future":
            stored["saved_at"] = _at(3600).isoformat()

        learner = CarThroughputLearner.restore(stored, T0)
        assert learner.snapshot()["segment"] is None
        assert learner.serialize()["cars"] == {}

    def test_a_segment_with_an_impossible_phase_key_is_discarded(self):
        stored = self._stored(600)
        stored["segment"]["phases"] = 4
        learner = CarThroughputLearner.restore(stored, _at(60))

        assert learner.snapshot()["segment"] is None
        assert learner.serialize()["cars"] == {}


# ---------------------------------------------------------------------------
# Persist gating
# ---------------------------------------------------------------------------


class TestPersistGating:
    """Measurement must not turn steady-state charging into a write storm."""

    def test_ordinary_in_flight_ticks_do_not_ask_to_save(self):
        learner = CarThroughputLearner()
        assert learner.observe(_tick(0)) is False
        assert learner.observe(_tick(30)) is False
        assert learner.observe(_tick(60)) is False

    def test_a_commit_asks_to_save(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=600)
        assert _close(learner, 630) is True

    def test_a_discarded_segment_still_asks_to_save(self):
        """The persisted payload carries the in-flight segment either way."""
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=60)
        assert _close(learner, 90) is True
        assert _samples(learner) == []

    def test_a_key_change_asks_to_save(self):
        learner = CarThroughputLearner()
        _run_delivering(learner, start=0, duration=600)
        assert learner.observe(_tick(630, phases=1, power_kw=3.5)) is True

    def test_an_in_flight_segment_asks_to_save_once_per_interval(self):
        learner = CarThroughputLearner()
        saves = sum(bool(learner.observe(_tick(o))) for o in range(0, 1201, 30))

        assert saves == 4
        assert saves == int(1200 / SEGMENT_PERSIST_INTERVAL_SECONDS)

    def test_steady_state_charging_is_not_a_write_storm(self):
        learner = CarThroughputLearner()
        offsets = list(range(0, 3571, 30))
        saves = sum(bool(learner.observe(_tick(o))) for o in offsets)

        assert len(offsets) == 120
        # One save per 300 s of accumulation, not one per tick.
        assert saves == 11
        assert saves < len(offsets) / 5


# ---------------------------------------------------------------------------
# Cross-module acceptance test (requirement 1)
# ---------------------------------------------------------------------------


def _price_slots(count: int = 24) -> list[dict]:
    """`count` consecutive hourly slots from the epoch, all distinctly priced."""
    return [
        {
            "start": _at(i * 3600),
            "end": _at((i + 1) * 3600),
            "price": 1.0 + i * 0.1,
        }
        for i in range(count)
    ]


def test_learned_estimate_books_more_slots():
    """The whole feature in one assertion, across both pure modules.

    A car that only ever receives 7 kW inside a booked slot -- because the
    fuse, the house load or the car itself throttles it -- needs more slots
    than the 11 kW ceiling implies. Booking too few is the one direction that
    leaves the car short at departure.
    """
    learner = CarThroughputLearner()
    _run_delivering(learner, start=0, duration=4200, power_kw=7.0)
    _close(learner, 4230)

    estimate = learner.estimate_kw(DEFAULT_CAR_ID, 3, _at(4230))
    assert estimate == pytest.approx(7.0)

    ceiling_kw = 11.0
    planning_kw = planning_power_kw(estimate, ceiling_kw)
    assert planning_kw == pytest.approx(7.0)

    plan_inputs = {
        "price_slots": _price_slots(),
        "departure_time_utc": _at(24 * 3600),
        "current_soc_pct": 20.0,
        "target_soc_pct": 80.0,
        "battery_capacity_kwh": 60.0,
        "now": T0,
    }
    at_ceiling = build_car_charging_schedule(
        max_charge_power_kw=ceiling_kw, **plan_inputs
    )
    at_learned = build_car_charging_schedule(
        max_charge_power_kw=planning_kw, **plan_inputs
    )

    # 36 kWh needed: 4 hourly slots at 11 kW, 6 at 7 kW.
    assert at_ceiling.charging_slot_count == 4
    assert at_learned.charging_slot_count == 6
    assert at_learned.charging_slot_count > at_ceiling.charging_slot_count
    assert at_learned.hours_needed > at_ceiling.hours_needed
    assert at_learned.energy_needed_kwh == pytest.approx(at_ceiling.energy_needed_kwh)

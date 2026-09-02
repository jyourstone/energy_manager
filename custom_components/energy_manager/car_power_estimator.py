"""Pure per-car charge-throughput measurement with zero Home Assistant dependencies.

The EaseeCoordinator measures the kW a car ACTUALLY receives during
EM-directed grid charging and feeds that figure to the price-slot planner
instead of the per-car Max Charge Power number, which becomes a ceiling and a
cold-start seed. Real charging is throttled by the house fuse, by house load
and by the car itself, so the configured ceiling systematically over-states
what a slot delivers -- and an over-stated power under-books slots, the one
direction that leaves the car short at departure.

One sample is a committed *segment*: a contiguous run of qualifying ticks on
one (car_id, phases) key, trapezoid-integrated into (energy_kwh, seconds) and
reduced at commit time to mean_kw = energy_kwh / (seconds / 3600). It is
deliberately not a tick (Easee ticks are event-driven, dense and irregular
exactly when power is moving, so a per-tick mean is biased toward volatile
periods) and not a session (there is no session id, no session-start event,
_reset_all() wipes controller state on a lying terminal status, and the phase
mode can change mid-session).

Two things this module gets right on purpose:

- Integration is trapezoidal. Easee ticks are TRIGGERED by the power entity
  changing, so a rectangular integrator would credit every interval at the
  post-change value -- a systematic bias, not noise.
- EM-commanded paused seconds ARE credited, bounded by
  MAX_PAUSED_SECONDS_PER_SEGMENT. The planner sizes a slot as wall-clock
  hours x power, so the minutes EM spends paused inside a booked slot (fuse
  Layer-1 emergency pause, the 0A safety stop, the pre-start gate, phase-switch
  choreography) are part of what that slot actually delivers. Dropping them
  makes the estimate read high -- the under-booking direction.

Read-out is duration-weighted, so an 8-hour night arriving as one sample or as
eight rolling commits gives exactly the same answer (total energy / total
time).

Everything here is pure and HA-free so it can be unit tested directly. NOT
covered by this module or its tests: the Store wiring and restore call in
EaseeCoordinator, the entity reads that build a ThroughputTick, the
CarChargingCoordinator read path, and the number entity's cold-start seed
(derive_car_max_charge_power_kw lives in charger_state_machine.py, beside the
conversion factors it divides by).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .charger_state_machine import POWER_ACTIVE_THRESHOLD_KW, CarDemand
from .const import MAX_CAR_MAX_CHARGE_POWER_KW, MIN_CAR_MAX_CHARGE_POWER_KW

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Ticks further apart than this are not integrated -- ~6 poll intervals,
#: enough to bridge a restart-free blip or a burst of missed state changes,
#: never enough to invent energy across an outage. A longer gap re-anchors
#: the segment and credits nothing.
MAX_TICK_GAP_SECONDS = 180.0

#: A segment shorter than this is discarded at close: ramp-up, a single
#: hysteresis step, or the tail of a session is not evidence of throughput.
MIN_SEGMENT_SECONDS = 300.0

#: A segment this long rolling-commits and keeps accumulating on the same
#: key. An 8-hour night therefore yields ~8 samples, so a reload never costs
#: the whole night and one long session cannot dominate the window.
MAX_SEGMENT_SECONDS = 3600.0

#: Paused seconds are credited only up to this per segment. Beyond half an
#: hour a "pause" is a stopped session the status entity has not reflected,
#: not throttling -- crediting it would drag the estimate toward zero.
MAX_PAUSED_SECONDS_PER_SEGMENT = 1800.0

#: How often an unchanged in-flight segment asks to be persisted. A hard
#: crash therefore loses at most this much accumulation; the flush on unload
#: makes the common reload path (which fires on ANY options or subentry save)
#: lossless.
SEGMENT_PERSIST_INTERVAL_SECONDS = 300.0

#: Read-out validity floor (mirrors forecast_accuracy.MIN_VALID_DAYS): fewer
#: samples, or less total measured time than this, means "not learned yet"
#: and the planner falls back to the ceiling.
MIN_SAMPLES = 2
MIN_TOTAL_SECONDS = 3600.0

#: Window bounds. Together they age out a stale or wild estimate: a car
#: physically swapped behind the same subentry converges within a few nights.
MAX_SAMPLES_PER_BUCKET = 20
SAMPLE_MAX_AGE_DAYS = 30

#: A restored in-flight segment is kept only when the save-to-restore gap is
#: within this (a quick config-entry reload). A longer gap is unobserved
#: charging, so the segment is committed on its own merits or discarded --
#: the SolarActivationTracker.restore() rule.
MAX_RESTORED_SEGMENT_GAP_SECONDS = 300.0

#: Upper plausibility bound on a single tick's power. coordinator._read_power_kw
#: treats the reading as watts unless unit_of_measurement is exactly "kW", so a
#: watts-valued sensor mislabelled "kW" reads ~7400 kW -- caught here. The
#: mirror-image footgun (a kW-valued sensor labelled "kw" reading ~0.0074) is
#: caught by the POWER_ACTIVE_THRESHOLD_KW gate below. Both 1000x unit errors
#: fail closed.
MAX_PLAUSIBLE_SAMPLE_KW = 25.0

#: The only modes whose energy is EM-directed grid charging. "solar" is
#: excluded by requirement: EaseeData.mode is the only trustworthy solar
#: marker (current_action == "solar_charge" is a display relabel of a
#: price-chosen slot and still yields mode "scheduled"). "idle" is excluded
#: because a drawing charger there is an unauthorized session -- real power,
#: but not ours and not attributable. "forced" is included: it is grid
#: charging at min(capacity, max_amps), physically identical to scheduled.
GRID_CHARGING_MODES = frozenset({"scheduled", "forced"})


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThroughputTick:
    """One Easee coordinator tick, reduced to what measurement needs.

    Attributes:
        now: UTC-aware timestamp of this tick.
        car_id: Subentry id of the unambiguously selected car, or None when
            attribution failed (see attributable_car).
        phases: Phases physically in use, 1/2/3, or None when the charger's
            raw config_phaseMode does not prove it (see observed_phase_count).
        power_kw: Measured charger power draw in kW.
        mode: The charger decision's mode -- "scheduled", "forced", "solar"
            or "idle".
        sequence_state: The phase-switch state machine's state; anything but
            "idle" means EM is mid-choreography and the charger is paused.
        stuck: True when a command produced no observable effect, i.e. the
            commanded state is not reality.
        dry_run: True in observe-only mode (CORE-14), where the amp target was
            never actually sent.
        target_amps: The dynamic limit EM commanded this tick.
        min_amps: Easee's minimum settable dynamic limit (6A); a target below
            it is a commanded pause, not a request for current.
        fallback_mode: True when the car's own SOC/plan is a guest-car
            fallback (EV-08) -- that energy must never be credited to a
            configured car.
    """

    now: datetime
    car_id: str | None
    phases: int | None
    power_kw: float
    mode: str
    sequence_state: str
    stuck: bool
    dry_run: bool
    target_amps: float
    min_amps: float
    fallback_mode: bool


@dataclass(frozen=True, slots=True)
class ThroughputSample:
    """One committed segment: the mean kW actually delivered over `seconds`."""

    committed_at: datetime
    mean_kw: float
    seconds: float


@dataclass
class _Segment:
    """Mutable in-flight accumulator for one (car_id, phases) key.

    Mutable by design (the SolarActivationTracker precedent): it is folded
    once per tick and reduced to an immutable ThroughputSample at commit.

    last_ts is deliberately Optional and deliberately NOT persisted: after a
    restore it is None, so the first tick back re-anchors instead of
    integrating across the downtime it never observed.
    """

    car_id: str
    phases: int
    energy_kwh: float = 0.0
    seconds: float = 0.0
    paused_seconds: float = 0.0
    last_power_kw: float = 0.0
    last_ts: datetime | None = None


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def classify_tick(tick: ThroughputTick) -> str:
    """Classify one tick as "deliver", "paused" or "reject".

    Both accepted tiers are integrated identically; the tier only decides
    which validity gates apply and whether the seconds count against
    MAX_PAUSED_SECONDS_PER_SEGMENT. That is why a pause whose power decays to
    ~0 needs no special case: the trapezoid credits the seconds and a
    near-zero energy on its own.

    "reject" notably covers target_amps >= min_amps with power at or below
    POWER_ACTIVE_THRESHOLD_KW: EM is asking for current and the car is not
    taking it (SOC taper, session finished). Those seconds are not
    throttling and must not be credited.
    """
    if tick.mode not in GRID_CHARGING_MODES:
        return "reject"
    if tick.dry_run or tick.stuck or tick.fallback_mode:
        return "reject"
    if tick.car_id is None or tick.phases is None:
        return "reject"
    # Non-finite and negative readings reject through the same comparison
    # chain as the mislabelled-watts case -- garbage in must never integrate.
    if not math.isfinite(tick.power_kw):
        return "reject"
    if tick.power_kw < 0.0 or tick.power_kw > MAX_PLAUSIBLE_SAMPLE_KW:
        return "reject"
    if tick.sequence_state != "idle" or tick.target_amps < tick.min_amps:
        return "paused"
    if tick.power_kw > POWER_ACTIVE_THRESHOLD_KW:
        return "deliver"
    return "reject"


def phase_bucket_key(raw_phases: object) -> int | None:
    """Coerce a phase count into its 1..3 bucket key, or None if unparseable.

    The single definition of the bucket clamp. The write side
    (observed_phase_count) and the read side (CarThroughputLearner.estimate_kw)
    MUST agree: if one clamped and the other did not, an out-of-range stored
    phase capability would file into bucket 1 or 3 and be read back from
    bucket 0 or 4 forever -- accumulating but never returning anything, with
    no diagnostic difference from "not learned yet".
    """
    try:
        value = int(raw_phases)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: int(float("inf")). The read side routes through here
        # from CarChargingCoordinator, which has no try/except of its own, so
        # an escaping exception would fail the whole car refresh.
        return None
    return min(max(value, 1), 3)


def observed_phase_count(
    raw_config_phase_mode: object, car_capability: object
) -> int | None:
    """Phases proven to be in use, or None when the charger does not prove it.

    Reads Easee's RAW config_phaseMode attribute: 1 = single, 2 = auto,
    3 = three. Auto, missing and unparseable all return None, because the
    charger may be running on one phase while reporting nothing -- filing
    that energy in the 3-phase bucket would poison it permanently.

    This is deliberately NOT coordinator._derive_phase_mode(), which folds
    auto/missing/unparseable into "three" so the *controller* fails safe.
    Failing safe for control and refusing to guess for measurement are
    opposite requirements; a paired test pins that they disagree on 2.

    A 3-phase charger only ever delivers what the car can take, so the
    bucket is clamp(car_capability, 1, 3) -- a capability-2 car on a
    3-phase charger files into bucket 2, matching
    conversion_factor_for_phase_capability.
    """
    try:
        raw = int(raw_config_phase_mode)
    except (TypeError, ValueError):
        return None
    if raw == 1:
        return 1
    if raw != 3:
        return None
    # Refuse rather than guess 3 on an unparseable capability: filing it as
    # 3-phase is exactly the poisoning this function exists to prevent.
    return phase_bucket_key(car_capability)


def selection_is_unambiguous(cars: Iterable[CarDemand]) -> bool:
    """Whether exactly one car can own this tick's energy.

    True when exactly one car is home and plugged in, or -- when several
    are -- exactly one of them holds an active slot. The second clause
    rescues the ordinary two-car scheduled case the controller already
    resolves deterministically; refusing it would throw away most of a
    two-car household's learning.
    """
    return _unambiguous_car(cars) is not None


def attributable_car(cars: Iterable[CarDemand]) -> CarDemand | None:
    """The car this tick's energy belongs to, or None to refuse attribution.

    Under selection_is_unambiguous this is provably the ChargerController's
    own selected_car in every non-idle branch -- forced picks
    demanding[0] if demanding else present[0], scheduled picks demanding[0]
    (and demanding is a subset of present), solar picks the first
    solar-eligible car (also a subset of present) -- and classify_tick
    already rejects the one divergent branch, "idle". Widening
    ChargerDecision to carry the selection would therefore buy nothing.

    A car without a car_id cannot be filed, so it refuses too.
    """
    car = _unambiguous_car(cars)
    if car is None or car.car_id is None:
        return None
    return car


def _unambiguous_car(cars: Iterable[CarDemand]) -> CarDemand | None:
    """Shared selection rule behind selection_is_unambiguous/attributable_car."""
    present = [car for car in cars if car.home_and_plugged]
    if len(present) == 1:
        return present[0]
    demanding = [car for car in present if car.active_slot]
    if len(demanding) == 1:
        return demanding[0]
    return None


def prune_samples(
    samples: Iterable[ThroughputSample], now: datetime
) -> list[ThroughputSample]:
    """Drop samples older than SAMPLE_MAX_AGE_DAYS, keep the newest ones.

    Applied at read time AND at write time, so a long-lived HA process can
    never blend month-old measurements into today's estimate. Oldest first
    in the result.
    """
    cutoff = now - timedelta(days=SAMPLE_MAX_AGE_DAYS)
    kept = [sample for sample in samples if sample.committed_at >= cutoff]
    kept.sort(key=lambda sample: sample.committed_at)
    return kept[-MAX_SAMPLES_PER_BUCKET:]


def _duration_weighted_kw(samples: Sequence[ThroughputSample]) -> float | None:
    """Total energy / total time over the samples, or None when there is none.

    Duration weighting is what makes the read-out identical whether a night
    arrives as one sample or as eight rolling commits.
    """
    total_seconds = sum(sample.seconds for sample in samples)
    if total_seconds <= 0:
        return None
    return sum(sample.mean_kw * sample.seconds for sample in samples) / total_seconds


def weighted_mean_kw(samples: Sequence[ThroughputSample]) -> float | None:
    """The learned throughput for one bucket, or None when it is not learned.

    None until MIN_SAMPLES samples covering MIN_TOTAL_SECONDS exist (the
    forecast_accuracy.suggested_factor validity-floor precedent -- a window
    can say "not enough evidence yet", which is the whole reason it was
    chosen over an EMA). A result outside the number entity's own
    [MIN_CAR_MAX_CHARGE_POWER_KW, MAX_CAR_MAX_CHARGE_POWER_KW] band is
    returned as None rather than clamped: outside that band the measurement
    is not believable, and a second, inconsistent band would be worse.
    """
    if len(samples) < MIN_SAMPLES:
        return None
    if sum(sample.seconds for sample in samples) < MIN_TOTAL_SECONDS:
        return None
    mean_kw = _duration_weighted_kw(samples)
    if mean_kw is None or not math.isfinite(mean_kw):
        return None
    if not MIN_CAR_MAX_CHARGE_POWER_KW <= mean_kw <= MAX_CAR_MAX_CHARGE_POWER_KW:
        return None
    return mean_kw


def planning_power_kw(estimate_kw: float | None, ceiling_kw: float) -> float:
    """The kW the slot planner should assume: floor first, ceiling second.

    The ceiling is the car's Max Charge Power number, so a learned value can
    never raise what the user configured (requirement 4). The result is never
    <= 0, which keeps build_car_charging_schedule's _empty_result()
    short-circuit -- a permanently idle car -- unreachable.
    """
    if estimate_kw is None or not math.isfinite(estimate_kw) or estimate_kw <= 0:
        return ceiling_kw
    return min(ceiling_kw, max(estimate_kw, MIN_CAR_MAX_CHARGE_POWER_KW))


def _parse_timestamp(raw: object) -> datetime:
    """Parse a stored ISO timestamp into an aware UTC datetime.

    Raises TypeError/ValueError on anything unparseable so the caller's
    per-entry except clause invalidates only that entry. Naive timestamps are
    coerced to UTC (mirrors coordinator._restore_samples).
    """
    parsed = datetime.fromisoformat(raw)
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


# ---------------------------------------------------------------------------
# CarThroughputLearner -- stateful, owned by the EaseeCoordinator
# ---------------------------------------------------------------------------


class CarThroughputLearner:
    """Accumulates measured charge throughput per (car, phases) bucket.

    Owned by the EaseeCoordinator: observe() once per Easee tick, estimate_kw()
    once per car tick. Buckets are keyed by the phases actually in use, never
    by the charger's mode alone, so 1-phase energy can never be read back as a
    3-phase figure.

    There is deliberately NO cross-bucket fallback. An empty bucket means "not
    learned yet" and the planner uses the ceiling; borrowing a 1-phase
    estimate for a 3-phase plan would book roughly eight times the slots.
    """

    def __init__(self) -> None:
        self._samples: dict[str, dict[int, list[ThroughputSample]]] = {}
        self._segment: _Segment | None = None
        self._last_persist: datetime | None = None

    # -- write path ---------------------------------------------------------

    def observe(self, tick: ThroughputTick) -> bool:
        """Fold one Easee tick into the in-flight segment.

        Returns True when the caller should persist the learner (a segment
        was committed or dropped, the key changed, or the in-flight segment
        has not been saved for SEGMENT_PERSIST_INTERVAL_SECONDS). Ordinary
        in-flight ticks return False, so steady-state charging is not a write
        storm -- an hour of 30s ticks asks for ~12 saves, not 120.
        """
        kind = classify_tick(tick)
        if kind == "reject":
            return self._close_segment(tick.now)

        # classify_tick guarantees both are set on an accepted tick.
        car_id = tick.car_id
        phases = tick.phases
        if car_id is None or phases is None:  # pragma: no cover - defensive
            return self._close_segment(tick.now)

        dirty = False
        segment = self._segment
        if segment is not None and (segment.car_id, segment.phases) != (car_id, phases):
            # A phase switch mid-session changes the key; the old segment must
            # not absorb the new key's seconds.
            dirty = self._close_segment(tick.now)
            segment = None

        if segment is None:
            self._open_segment(tick, car_id, phases, kind)
            return dirty

        self._integrate(segment, tick, kind)

        if segment.paused_seconds > MAX_PAUSED_SECONDS_PER_SEGMENT:
            return self._close_segment(tick.now) or dirty
        if segment.seconds >= MAX_SEGMENT_SECONDS and kind == "deliver":
            # Rolling commit: close and immediately re-open on the same key,
            # anchored at this tick so the following interval is not lost.
            #
            # Deferred while paused. Rolling over on a paused tick would
            # commit the segment and then open nothing (the opener refuses a
            # pause), so every remaining tick of that pause would hit the
            # cold-start branch and be discarded -- dropping the tail of a
            # pause that happens to straddle the boundary and biasing the
            # estimate HIGH, which is the under-booking direction. Waiting
            # for the next delivering tick keeps those seconds; the pause is
            # still bounded by the MAX_PAUSED_SECONDS_PER_SEGMENT check
            # above, so a segment cannot grow without limit here.
            closed = self._close_segment(tick.now)
            self._open_segment(tick, car_id, phases, kind)
            return closed or dirty

        return self._should_persist(tick.now) or dirty

    def _open_segment(
        self, tick: ThroughputTick, car_id: str, phases: int, kind: str
    ) -> None:
        """Open a fresh segment on this key, unless the tick is a pause.

        The single place a segment is ever opened, so the invariant holds
        everywhere: a commanded pause with nothing open is not throughput,
        because there is no delivering run for it to be part of. Opening on a
        paused tick would build a segment of purely paused seconds -- one that
        _commit() discards only when its energy is exactly 0.0, so any standby
        draw commits a sample with zero delivering time -- and would hand it a
        fresh MAX_PAUSED_SECONDS_PER_SEGMENT budget.
        """
        if kind == "paused":
            return
        self._segment = _Segment(
            car_id=car_id,
            phases=phases,
            last_power_kw=tick.power_kw,
            last_ts=tick.now,
        )
        self._last_persist = tick.now

    def _integrate(self, segment: _Segment, tick: ThroughputTick, kind: str) -> None:
        """Trapezoid-integrate one interval into the segment.

        Trapezoid, not rectangle: Easee ticks are TRIGGERED by the power
        entity changing, so crediting the whole interval at the post-change
        value would bias every measurement in one direction. A gap beyond
        MAX_TICK_GAP_SECONDS (or a backwards clock) re-anchors and credits
        nothing -- energy is never invented across time nobody observed.
        """
        if segment.last_ts is None:
            segment.last_ts = tick.now
            segment.last_power_kw = tick.power_kw
            return
        dt_hours = (tick.now - segment.last_ts).total_seconds() / 3600.0
        if 0 < dt_hours <= MAX_TICK_GAP_SECONDS / 3600.0:
            segment.energy_kwh += (
                dt_hours * (tick.power_kw + segment.last_power_kw) / 2.0
            )
            segment.seconds += dt_hours * 3600.0
            if kind == "paused":
                segment.paused_seconds += dt_hours * 3600.0
        segment.last_ts = tick.now
        segment.last_power_kw = tick.power_kw

    def _close_segment(self, now: datetime) -> bool:
        """Commit the in-flight segment if it qualifies, else drop it.

        Returns True whenever there was a segment to close: the persisted
        payload changed either way, since it carries the in-flight segment.
        """
        segment = self._segment
        if segment is None:
            return False
        self._segment = None
        self._commit(segment, now)
        self._last_persist = now
        return True

    def _commit(self, segment: _Segment, committed_at: datetime) -> bool:
        """Reduce a closed segment to a sample, or discard it silently."""
        if segment.seconds < MIN_SEGMENT_SECONDS:
            return False
        mean_kw = segment.energy_kwh / (segment.seconds / 3600.0)
        if not math.isfinite(mean_kw) or mean_kw <= 0:
            return False
        bucket = self._samples.setdefault(segment.car_id, {}).setdefault(
            segment.phases, []
        )
        bucket.append(ThroughputSample(committed_at, mean_kw, segment.seconds))
        self._prune_all(committed_at)
        return True

    def _should_persist(self, now: datetime) -> bool:
        """Rate-limit in-flight saves to one per SEGMENT_PERSIST_INTERVAL_SECONDS."""
        if self._last_persist is None:
            self._last_persist = now
            return False
        elapsed = (now - self._last_persist).total_seconds()
        if elapsed < 0:
            self._last_persist = now
            return False
        if elapsed >= SEGMENT_PERSIST_INTERVAL_SECONDS:
            self._last_persist = now
            return True
        return False

    def _prune_all(self, now: datetime) -> None:
        """Apply the age/count window everywhere, dropping empty buckets and cars."""
        for car_id in list(self._samples):
            buckets = self._samples[car_id]
            for phases in list(buckets):
                kept = prune_samples(buckets[phases], now)
                if kept:
                    buckets[phases] = kept
                else:
                    del buckets[phases]
            if not buckets:
                del self._samples[car_id]

    # -- read path ----------------------------------------------------------

    def estimate_kw(
        self, car_id: str | None, phases: object, now: datetime | None = None
    ) -> float | None:
        """Measured throughput for one (car, phases) bucket, or None.

        None means "not learned yet" and the caller must fall back to the
        car's configured ceiling -- see planning_power_kw. `phases` goes
        through phase_bucket_key, the same coercion and clamp the write side
        uses, so the bucket may be asked for as 3 or as "3" and an
        out-of-range capability reads back the bucket it wrote.

        Reading prunes: the age/count window is applied in place, so an
        aged-out bucket (and a car left with none) also disappears from
        serialize() rather than lingering in storage forever.
        """
        if car_id is None:
            return None
        bucket_key = phase_bucket_key(phases)
        if bucket_key is None:
            return None
        now = datetime.now(timezone.utc) if now is None else now
        self._prune_all(now)
        return weighted_mean_kw(self._samples.get(car_id, {}).get(bucket_key, []))

    def snapshot(self, now: datetime | None = None) -> dict:
        """Auditable learner state for the diagnostics download.

        Exposes both the raw duration-weighted mean and the gated estimate,
        so a bucket that is accumulating but not yet believed is visible as
        such rather than as an opaque "no estimate".
        """
        now = datetime.now(timezone.utc) if now is None else now
        cars: dict[str, dict[str, dict]] = {}
        for car_id, buckets in self._samples.items():
            bucket_snapshot: dict[str, dict] = {}
            for phases, samples in buckets.items():
                kept = prune_samples(samples, now)
                if not kept:
                    continue
                bucket_snapshot[str(phases)] = {
                    "samples": len(kept),
                    "total_seconds": round(sum(s.seconds for s in kept), 1),
                    "mean_kw": _duration_weighted_kw(kept),
                    "estimate_kw": weighted_mean_kw(kept),
                    "newest_committed_at": kept[-1].committed_at.isoformat(),
                }
            if bucket_snapshot:
                cars[car_id] = bucket_snapshot
        segment = self._segment
        return {
            "cars": cars,
            "segment": None
            if segment is None
            else {
                "car_id": segment.car_id,
                "phases": segment.phases,
                "energy_kwh": round(segment.energy_kwh, 4),
                "seconds": round(segment.seconds, 1),
                "paused_seconds": round(segment.paused_seconds, 1),
            },
        }

    # -- persistence --------------------------------------------------------

    def serialize(self) -> dict:
        """Serialize buckets plus the in-flight segment to a JSON-storable dict.

        last_ts is deliberately absent: after a restore the first tick must
        re-anchor rather than integrate across unobserved downtime. This shape
        is also the change-detection key -- use serialize_stored() for the
        persisted payload, which additionally stamps saved_at.
        """
        cars: dict[str, dict[str, list[dict]]] = {}
        for car_id, buckets in self._samples.items():
            serialized_buckets = {
                str(phases): [
                    {
                        "committed_at": sample.committed_at.isoformat(),
                        "mean_kw": sample.mean_kw,
                        "seconds": sample.seconds,
                    }
                    for sample in samples
                ]
                for phases, samples in buckets.items()
                if samples
            }
            if serialized_buckets:
                cars[car_id] = serialized_buckets
        segment = self._segment
        return {
            "cars": cars,
            "segment": None
            if segment is None
            else {
                "car_id": segment.car_id,
                "phases": segment.phases,
                "energy_kwh": segment.energy_kwh,
                "seconds": segment.seconds,
                "paused_seconds": segment.paused_seconds,
                "last_power_kw": segment.last_power_kw,
            },
        }

    def serialize_stored(self, now: datetime) -> dict:
        """Serialize for persistence: serialize() plus a saved_at stamp.

        restore() uses saved_at to measure downtime -- an in-flight segment is
        only carried across a gap short enough to be a config-entry reload.
        """
        return {**self.serialize(), "saved_at": now.isoformat()}

    @classmethod
    def restore(
        cls,
        raw: object,
        now: datetime,
        known_car_ids: frozenset[str] | None = None,
    ) -> CarThroughputLearner:
        """Restore a learner persisted by serialize_stored().

        Defensive throughout, mirroring forecast_accuracy.restore_fa_store:
        corrupt storage must never block setup, and one malformed entry must
        never cost the rest. Non-dict payloads restore empty; a bad car, a bad
        bucket key or a bad sample is skipped individually; every float is
        checked with math.isfinite; naive committed_at stamps are coerced to
        UTC and future ones dropped; the age/count window is re-applied on
        load so a long outage cannot resurrect a stale bucket.

        known_car_ids, when supplied, drops every car that is no longer a
        configured subentry -- this is what garbage-collects a deleted car's
        learning, since HA gives the integration no subentry-removal hook. It
        filters the in-flight segment as well as the committed tree; a segment
        alone would otherwise re-create the bucket on its next commit.

        The in-flight segment obeys the SolarActivationTracker gap rule: kept
        (with last_ts=None, so the next tick re-anchors) only when the
        save-to-restore gap is within MAX_RESTORED_SEGMENT_GAP_SECONDS,
        otherwise committed on its own merits or discarded. A missing,
        unparseable or future saved_at discards it -- downtime of unknown
        length is not measurable.
        """
        learner = cls()
        if not isinstance(raw, dict):
            return learner
        learner._samples = cls._restore_cars(raw.get("cars"), now, known_car_ids)
        learner._restore_segment(
            raw.get("segment"), raw.get("saved_at"), now, known_car_ids
        )
        return learner

    @staticmethod
    def _restore_cars(
        raw_cars: object, now: datetime, known_car_ids: frozenset[str] | None
    ) -> dict[str, dict[int, list[ThroughputSample]]]:
        """Restore the per-car bucket tree, skipping anything malformed."""
        samples: dict[str, dict[int, list[ThroughputSample]]] = {}
        if not isinstance(raw_cars, dict):
            return samples
        for car_id, raw_buckets in raw_cars.items():
            if not isinstance(car_id, str) or not isinstance(raw_buckets, dict):
                continue
            if known_car_ids is not None and car_id not in known_car_ids:
                continue
            buckets: dict[int, list[ThroughputSample]] = {}
            for raw_key, raw_samples in raw_buckets.items():
                try:
                    phases = int(raw_key)
                except (TypeError, ValueError):
                    continue
                if phases not in (1, 2, 3) or not isinstance(raw_samples, list):
                    continue
                restored = prune_samples(
                    CarThroughputLearner._restore_samples(raw_samples, now), now
                )
                if restored:
                    buckets[phases] = restored
            if buckets:
                samples[car_id] = buckets
        return samples

    @staticmethod
    def _restore_samples(raw_samples: list, now: datetime) -> list[ThroughputSample]:
        """Restore one bucket's samples; a bad entry costs only itself."""
        samples: list[ThroughputSample] = []
        for entry in raw_samples:
            # OverflowError: float(huge int) must invalidate only this sample.
            try:
                sample = ThroughputSample(
                    _parse_timestamp(entry["committed_at"]),
                    float(entry["mean_kw"]),
                    float(entry["seconds"]),
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(sample.mean_kw) or not math.isfinite(sample.seconds):
                continue
            if sample.mean_kw <= 0 or sample.seconds <= 0:
                continue
            if sample.committed_at > now:
                continue
            samples.append(sample)
        return samples

    def _restore_segment(
        self,
        raw_segment: object,
        raw_saved_at: object,
        now: datetime,
        known_car_ids: frozenset[str] | None = None,
    ) -> None:
        """Restore, commit or discard the persisted in-flight segment.

        known_car_ids applies here exactly as it does to the committed tree:
        the long-gap path calls _commit(), whose setdefault would otherwise
        resurrect the very bucket the filter exists to garbage-collect, and
        the short-gap path would keep a deleted car's segment open.
        """
        if not isinstance(raw_segment, dict):
            return
        car_id = raw_segment.get("car_id")
        if known_car_ids is not None and car_id not in known_car_ids:
            return
        try:
            segment = _Segment(
                car_id=str(raw_segment["car_id"]),
                phases=int(raw_segment["phases"]),
                energy_kwh=float(raw_segment["energy_kwh"]),
                seconds=float(raw_segment["seconds"]),
                paused_seconds=float(raw_segment.get("paused_seconds", 0.0)),
                last_power_kw=float(raw_segment.get("last_power_kw", 0.0)),
            )
            saved_at = _parse_timestamp(raw_saved_at)
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        if segment.phases not in (1, 2, 3):
            return
        if not all(
            math.isfinite(value)
            for value in (
                segment.energy_kwh,
                segment.seconds,
                segment.paused_seconds,
                segment.last_power_kw,
            )
        ):
            return
        if segment.seconds < 0 or segment.energy_kwh < 0 or saved_at > now:
            return
        if (now - saved_at).total_seconds() <= MAX_RESTORED_SEGMENT_GAP_SECONDS:
            # Quick reload: keep accumulating. last_ts stays None so the first
            # tick back re-anchors instead of crediting the gap.
            self._segment = segment
            self._last_persist = now
            return
        # Real downtime: the segment ended when it was saved, so commit it at
        # saved_at (never at now, which would make it look fresher than it is).
        self._commit(segment, saved_at)

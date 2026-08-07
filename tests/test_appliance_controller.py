"""Tests for the pure solar-surplus appliance decision logic.

All timing uses raw epoch seconds fed through ``now_ts`` -- no wall clock,
no Home Assistant. The default test appliance is 4000 W, 3-phase, thresholds
110/90 % (ON at pool >= 4.4 kW, OFF at pool < 3.6 kW), with every sustain
and floor timer zeroed unless a test exercises it.

Pool arithmetic reminder: the pool credits back the draw of appliances whose
actuator is actually on under EM command, so the surplus signal must stay
signed (import as negative) -- with rated credit-back a clamped (>= 0)
signal would floor the pool at rated while a load runs and the off
threshold could never be reached. Both variants are pinned below.
"""

from __future__ import annotations

import pytest

from custom_components.energy_manager.appliance_controller import (
    STATUS_ACTUATOR_UNAVAILABLE,
    STATUS_BLOCKED_FUSE,
    STATUS_BLOCKED_MIN_OFF,
    STATUS_BLOCKED_PRIORITY,
    STATUS_DISABLED,
    STATUS_HOLDING_MIN_ON,
    STATUS_OFF_NO_SURPLUS,
    STATUS_ON_EXTERNAL,
    STATUS_ON_SURPLUS,
    STATUS_WAITING_ON_SUSTAIN,
    ApplianceConfig,
    ApplianceInputs,
    ApplianceTracker,
    clamp_hysteresis,
    compute_raw_surplus_kw,
    decide_appliances,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = 1_700_000_000.0

INF = float("inf")


def _config(**overrides) -> ApplianceConfig:
    """Create a test appliance config; all timers zero unless overridden."""
    base = {
        "subentry_id": "app1",
        "name": "VVB",
        "switch_entity": "switch.vvb",
        "rated_power_w": 4000,
        "phases": 3,
        "priority": 5,
        "on_threshold_pct": 110,
        "off_threshold_pct": 90,
        "on_sustain_s": 0,
        "off_sustain_s": 0,
        "min_on_s": 0,
        "min_off_s": 0,
        "power_sensor_entity": None,
    }
    base.update(overrides)
    return ApplianceConfig(**base)


def _inputs(
    *,
    available: bool = True,
    is_on: bool = False,
    em_control: bool = True,
    measured: float | None = None,
) -> ApplianceInputs:
    return ApplianceInputs(
        actuator_available=available,
        actuator_is_on=is_on,
        em_control_enabled=em_control,
        measured_power_w=measured,
    )


def _on_tracker(last_on_ts: float = T0) -> ApplianceTracker:
    return ApplianceTracker(em_commanded_on=True, last_on_ts=last_on_ts)


def _decide_one(
    config: ApplianceConfig,
    inputs: ApplianceInputs,
    tracker: ApplianceTracker,
    *,
    now_ts: float = T0,
    surplus: float = 0.0,
    headroom: float | None = INF,
):
    return decide_appliances(
        now_ts=now_ts,
        raw_surplus_kw=surplus,
        headroom_amps=headroom,
        items=[(config, inputs, tracker)],
    )[0]


# ---------------------------------------------------------------------------
# compute_raw_surplus_kw (BATT-17 guard)
# ---------------------------------------------------------------------------


class TestRawSurplus:
    def test_discharge_subtracted_from_export(self):
        assert compute_raw_surplus_kw(5.0, 2.0) == pytest.approx(3.0)

    def test_discharge_exceeding_export_goes_negative(self):
        # Signed on purpose: the import side must reach the release
        # comparison (rated credit-back would otherwise floor the pool).
        assert compute_raw_surplus_kw(2.0, 5.0) == pytest.approx(-3.0)

    def test_import_is_negative_surplus(self):
        assert compute_raw_surplus_kw(-1.5, 0.0) == pytest.approx(-1.5)

    def test_charging_battery_contributes_nothing(self):
        assert compute_raw_surplus_kw(3.0, -1.5) == pytest.approx(3.0)

    def test_no_export_no_surplus(self):
        assert compute_raw_surplus_kw(0.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# Hysteresis band (on 110 % / off 90 %)
# ---------------------------------------------------------------------------


class TestHysteresisBand:
    def test_below_on_threshold_stays_off(self):
        tracker = ApplianceTracker()
        decision = _decide_one(_config(), _inputs(), tracker, surplus=4.3)
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert not decision.desired_on
        assert not decision.should_command
        assert not tracker.em_commanded_on

    def test_at_on_threshold_turns_on(self):
        tracker = ApplianceTracker()
        decision = _decide_one(_config(), _inputs(), tracker, surplus=4.4)
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.desired_on
        assert decision.should_command
        assert decision.turn_on
        assert decision.allocated_kw == pytest.approx(4.0)
        assert tracker.em_commanded_on
        assert tracker.last_on_ts == T0

    def test_pool_between_thresholds_keeps_running_appliance_on(self):
        # Measured credit-back 4.0 kW + raw 0.0 => pool 4.0 kW, inside the
        # 3.6..4.4 band: an OFF appliance would not start, but the running
        # one stays on.
        config = _config(power_sensor_entity="sensor.p")
        decision = _decide_one(
            config,
            _inputs(is_on=True, measured=4000.0),
            _on_tracker(),
            now_ts=T0 + 600,
            surplus=0.0,
        )
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.desired_on
        assert not decision.should_command

    def test_measured_credit_below_off_threshold_releases(self):
        # Appliance idles at 3.0 kW measured, no export left: pool 3.0 kW
        # < 3.6 kW off threshold, all timers zero => immediate release.
        config = _config(power_sensor_entity="sensor.p")
        tracker = _on_tracker()
        decision = _decide_one(
            config,
            _inputs(is_on=True, measured=3000.0),
            tracker,
            now_ts=T0 + 600,
            surplus=0.0,
        )
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert decision.should_command
        assert not decision.turn_on
        assert not tracker.em_commanded_on
        assert tracker.last_off_ts == T0 + 600

    def test_rated_credit_back_floors_pool_at_rated(self):
        # Without a power sensor the credit-back equals rated draw, so a
        # non-negative surplus input keeps the pool at or above rated while
        # the actuator is on -- release relies on the coordinator feeding
        # the signed signal (next test). Pins the pure arithmetic.
        decision = _decide_one(
            _config(),
            _inputs(is_on=True),
            _on_tracker(),
            now_ts=T0 + 600,
            surplus=0.0,
        )
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.desired_on

    def test_signed_negative_surplus_releases_rated_credit_appliance(self):
        # A signed surplus signal (import as negative) restores the
        # documented 110/90 band: -0.5 + 4.0 = 3.5 kW < 3.6 kW => release.
        tracker = _on_tracker()
        decision = _decide_one(
            _config(),
            _inputs(is_on=True),
            tracker,
            now_ts=T0 + 600,
            surplus=-0.5,
        )
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert not tracker.em_commanded_on


# ---------------------------------------------------------------------------
# Sustain timers
# ---------------------------------------------------------------------------


class TestOnSustain:
    def test_waits_then_fires(self):
        config = _config(on_sustain_s=300)
        tracker = ApplianceTracker()
        decision = _decide_one(config, _inputs(), tracker, now_ts=T0, surplus=5.0)
        assert decision.status == STATUS_WAITING_ON_SUSTAIN
        assert tracker.surplus_since_ts == T0

        decision = _decide_one(config, _inputs(), tracker, now_ts=T0 + 299, surplus=5.0)
        assert decision.status == STATUS_WAITING_ON_SUSTAIN

        decision = _decide_one(config, _inputs(), tracker, now_ts=T0 + 300, surplus=5.0)
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.should_command
        assert decision.turn_on

    def test_dip_resets_the_clock(self):
        config = _config(on_sustain_s=300)
        tracker = ApplianceTracker()
        _decide_one(config, _inputs(), tracker, now_ts=T0, surplus=5.0)
        decision = _decide_one(config, _inputs(), tracker, now_ts=T0 + 100, surplus=1.0)
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert tracker.surplus_since_ts is None

        _decide_one(config, _inputs(), tracker, now_ts=T0 + 200, surplus=5.0)
        assert tracker.surplus_since_ts == T0 + 200
        decision = _decide_one(config, _inputs(), tracker, now_ts=T0 + 499, surplus=5.0)
        assert decision.status == STATUS_WAITING_ON_SUSTAIN
        decision = _decide_one(config, _inputs(), tracker, now_ts=T0 + 500, surplus=5.0)
        assert decision.status == STATUS_ON_SURPLUS


class TestOffSustain:
    def test_deficit_waits_then_releases(self):
        config = _config(off_sustain_s=600, power_sensor_entity="sensor.p")
        tracker = _on_tracker()
        inputs = _inputs(is_on=True, measured=3000.0)

        decision = _decide_one(config, inputs, tracker, now_ts=T0 + 30)
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.desired_on
        assert tracker.deficit_since_ts == T0 + 30

        decision = _decide_one(config, inputs, tracker, now_ts=T0 + 629)
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.desired_on

        decision = _decide_one(config, inputs, tracker, now_ts=T0 + 630)
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert decision.should_command
        assert not decision.turn_on
        assert not tracker.em_commanded_on

    def test_surplus_return_resets_deficit_clock(self):
        config = _config(off_sustain_s=600, power_sensor_entity="sensor.p")
        tracker = _on_tracker()
        deficit = _inputs(is_on=True, measured=3000.0)

        _decide_one(config, deficit, tracker, now_ts=T0)
        assert tracker.deficit_since_ts == T0

        decision = _decide_one(config, deficit, tracker, now_ts=T0 + 300, surplus=1.0)
        assert decision.status == STATUS_ON_SURPLUS
        assert tracker.deficit_since_ts is None

        _decide_one(config, deficit, tracker, now_ts=T0 + 400)
        decision = _decide_one(config, deficit, tracker, now_ts=T0 + 900)
        assert decision.desired_on
        decision = _decide_one(config, deficit, tracker, now_ts=T0 + 1000)
        assert decision.status == STATUS_OFF_NO_SURPLUS


# ---------------------------------------------------------------------------
# min_on / min_off floors
# ---------------------------------------------------------------------------


class TestMinOnFloor:
    def test_holds_through_deficit_then_releases_at_expiry(self):
        config = _config(min_on_s=900, power_sensor_entity="sensor.p")
        tracker = _on_tracker()
        inputs = _inputs(is_on=True, measured=3000.0)

        decision = _decide_one(config, inputs, tracker, now_ts=T0 + 30)
        assert decision.status == STATUS_HOLDING_MIN_ON
        assert decision.desired_on
        assert decision.allocated_kw == pytest.approx(3.0)

        decision = _decide_one(config, inputs, tracker, now_ts=T0 + 899)
        assert decision.status == STATUS_HOLDING_MIN_ON

        # Deficit clock ran during min_on, so the release fires the moment
        # the floor expires.
        decision = _decide_one(config, inputs, tracker, now_ts=T0 + 900)
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert decision.should_command
        assert not tracker.em_commanded_on


class TestMinOffFloor:
    def test_blocks_turn_on_until_expiry(self):
        config = _config(min_off_s=300)
        tracker = ApplianceTracker(last_off_ts=T0)

        decision = _decide_one(config, _inputs(), tracker, now_ts=T0 + 30, surplus=10.0)
        assert decision.status == STATUS_BLOCKED_MIN_OFF
        assert not decision.should_command

        decision = _decide_one(
            config, _inputs(), tracker, now_ts=T0 + 299, surplus=10.0
        )
        assert decision.status == STATUS_BLOCKED_MIN_OFF

        decision = _decide_one(
            config, _inputs(), tracker, now_ts=T0 + 300, surplus=10.0
        )
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.turn_on

    def test_sustain_starts_fresh_after_min_off(self):
        config = _config(min_off_s=300, on_sustain_s=300)
        tracker = ApplianceTracker(last_off_ts=T0)

        decision = _decide_one(
            config, _inputs(), tracker, now_ts=T0 + 100, surplus=10.0
        )
        assert decision.status == STATUS_BLOCKED_MIN_OFF
        assert tracker.surplus_since_ts is None

        decision = _decide_one(
            config, _inputs(), tracker, now_ts=T0 + 300, surplus=10.0
        )
        assert decision.status == STATUS_WAITING_ON_SUSTAIN
        assert tracker.surplus_since_ts == T0 + 300

        decision = _decide_one(
            config, _inputs(), tracker, now_ts=T0 + 600, surplus=10.0
        )
        assert decision.status == STATUS_ON_SURPLUS


# ---------------------------------------------------------------------------
# Priority allocation and credit-back
# ---------------------------------------------------------------------------


class TestPriorityAllocation:
    def test_measured_credit_back_feeds_lower_priority(self):
        a = _config(subentry_id="a", priority=1, power_sensor_entity="sensor.a")
        b = _config(subentry_id="b", priority=2, rated_power_w=2000)
        items = [
            (a, _inputs(is_on=True, measured=3000.0), _on_tracker(T0 - 1200)),
            (b, _inputs(), ApplianceTracker()),
        ]
        # pool = 2.5 raw + 3.0 measured credit = 5.5; A keeps 3.0,
        # leaving 2.5 >= B's 2.2 on threshold.
        decisions = decide_appliances(
            now_ts=T0, raw_surplus_kw=2.5, headroom_amps=INF, items=items
        )
        assert [d.subentry_id for d in decisions] == ["a", "b"]
        assert decisions[0].status == STATUS_ON_SURPLUS
        assert decisions[0].allocated_kw == pytest.approx(3.0)
        assert decisions[1].status == STATUS_ON_SURPLUS
        assert decisions[1].turn_on
        assert decisions[1].allocated_kw == pytest.approx(2.0)

    def test_rated_credit_back_without_power_sensor(self):
        a = _config(subentry_id="a", priority=1)
        b = _config(subentry_id="b", priority=2, rated_power_w=2000)
        items = [
            (a, _inputs(is_on=True), _on_tracker(T0 - 1200)),
            (b, _inputs(), ApplianceTracker()),
        ]
        # pool = 2.5 raw + 4.0 rated credit = 6.5; A allocates rated 4.0,
        # leaving 2.5 >= 2.2 for B.
        decisions = decide_appliances(
            now_ts=T0, raw_surplus_kw=2.5, headroom_amps=INF, items=items
        )
        assert decisions[0].allocated_kw == pytest.approx(4.0)
        assert decisions[1].status == STATUS_ON_SURPLUS

    def test_pool_exhaustion_blocks_lower_priority(self):
        a = _config(subentry_id="a", priority=1)
        b = _config(subentry_id="b", priority=2, rated_power_w=2000)
        b_tracker = ApplianceTracker()
        items = [
            (a, _inputs(), ApplianceTracker()),
            (b, _inputs(), b_tracker),
        ]
        # 4.5 kW admits A (threshold 4.4), leaving 0.5 < B's 2.2 threshold
        # even though the total pool would have admitted B alone.
        decisions = decide_appliances(
            now_ts=T0, raw_surplus_kw=4.5, headroom_amps=INF, items=items
        )
        assert decisions[0].status == STATUS_ON_SURPLUS
        assert decisions[1].status == STATUS_BLOCKED_PRIORITY
        assert not b_tracker.em_commanded_on

    def test_insertion_order_breaks_priority_tie(self):
        a = _config(subentry_id="a", priority=5)
        b = _config(subentry_id="b", priority=5)
        decisions = decide_appliances(
            now_ts=T0,
            raw_surplus_kw=4.5,
            headroom_amps=INF,
            items=[
                (a, _inputs(), ApplianceTracker()),
                (b, _inputs(), ApplianceTracker()),
            ],
        )
        assert decisions[0].subentry_id == "a"
        assert decisions[0].status == STATUS_ON_SURPLUS
        assert decisions[1].status == STATUS_BLOCKED_PRIORITY

        decisions = decide_appliances(
            now_ts=T0,
            raw_surplus_kw=4.5,
            headroom_amps=INF,
            items=[
                (b, _inputs(), ApplianceTracker()),
                (a, _inputs(), ApplianceTracker()),
            ],
        )
        assert decisions[0].subentry_id == "b"
        assert decisions[0].status == STATUS_ON_SURPLUS

    def test_newly_admitted_appliance_allocates_rated_not_measured(self):
        config = _config(power_sensor_entity="sensor.p")
        decision = _decide_one(
            config, _inputs(measured=0.0), ApplianceTracker(), surplus=5.0
        )
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.allocated_kw == pytest.approx(4.0)

    def test_starved_appliance_resets_sustain_and_waits_full_window_after_release(
        self,
    ):
        # Regression: the admission gate must run against the
        # post-allocation remaining pool, not the pre-allocation total, and
        # blocked_priority must reset the sustain clock -- otherwise a
        # lower-priority appliance accumulates sustain time on capacity a
        # higher-priority one is consuming and turns on instantly once that
        # capacity is released.
        a = _config(subentry_id="a", priority=1)
        b = _config(subentry_id="b", priority=2, rated_power_w=2000, on_sustain_s=300)
        a_tracker = _on_tracker(T0 - 1200)
        b_tracker = ApplianceTracker()
        a_inputs = _inputs(is_on=True)

        # A consumes the whole pool for several ticks; B is starved and
        # must not accumulate any sustain time while blocked.
        for t in (T0, T0 + 60, T0 + 120):
            decisions = decide_appliances(
                now_ts=t,
                raw_surplus_kw=2.0,
                headroom_amps=INF,
                items=[(a, a_inputs, a_tracker), (b, _inputs(), b_tracker)],
            )
            assert decisions[0].status == STATUS_ON_SURPLUS
            assert decisions[1].status == STATUS_BLOCKED_PRIORITY
            assert b_tracker.surplus_since_ts is None

        # The pool grows (A's allocation no longer exhausts it): B's
        # sustain clock starts fresh from this tick, not from the earlier
        # starved ticks.
        decisions = decide_appliances(
            now_ts=T0 + 180,
            raw_surplus_kw=6.0,
            headroom_amps=INF,
            items=[(a, a_inputs, a_tracker), (b, _inputs(), b_tracker)],
        )
        assert decisions[1].status == STATUS_WAITING_ON_SUSTAIN
        assert b_tracker.surplus_since_ts == T0 + 180

        decisions = decide_appliances(
            now_ts=T0 + 180 + 299,
            raw_surplus_kw=6.0,
            headroom_amps=INF,
            items=[(a, a_inputs, a_tracker), (b, _inputs(), b_tracker)],
        )
        assert decisions[1].status == STATUS_WAITING_ON_SUSTAIN

        decisions = decide_appliances(
            now_ts=T0 + 180 + 300,
            raw_surplus_kw=6.0,
            headroom_amps=INF,
            items=[(a, a_inputs, a_tracker), (b, _inputs(), b_tracker)],
        )
        assert decisions[1].status == STATUS_ON_SURPLUS
        assert decisions[1].turn_on


# ---------------------------------------------------------------------------
# Fuse admission
# ---------------------------------------------------------------------------


class TestFuseAdmission:
    def test_none_headroom_blocks_new_turn_ons(self):
        decision = _decide_one(
            _config(), _inputs(), ApplianceTracker(), surplus=10.0, headroom=None
        )
        assert decision.status == STATUS_BLOCKED_FUSE
        assert not decision.should_command

    def test_none_headroom_keeps_running_appliance_on(self):
        decision = _decide_one(
            _config(),
            _inputs(is_on=True),
            _on_tracker(),
            now_ts=T0 + 60,
            surplus=1.0,
            headroom=None,
        )
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.desired_on

    def test_infinite_headroom_always_admits(self):
        decision = _decide_one(
            _config(), _inputs(), ApplianceTracker(), surplus=10.0, headroom=INF
        )
        assert decision.status == STATUS_ON_SURPLUS

    def test_finite_headroom_boundary(self):
        # 4000 W over 3 phases = 5.797 A; exactly fitting headroom admits.
        rated_amps = 4000 / (230.0 * 3)
        decision = _decide_one(
            _config(),
            _inputs(),
            ApplianceTracker(),
            surplus=10.0,
            headroom=rated_amps,
        )
        assert decision.status == STATUS_ON_SURPLUS

        decision = _decide_one(
            _config(), _inputs(), ApplianceTracker(), surplus=10.0, headroom=5.0
        )
        assert decision.status == STATUS_BLOCKED_FUSE

    def test_single_phase_rated_amps(self):
        # 2300 W over 1 phase = 10.0 A.
        config = _config(rated_power_w=2300, phases=1)
        decision = _decide_one(
            config, _inputs(), ApplianceTracker(), surplus=10.0, headroom=9.9
        )
        assert decision.status == STATUS_BLOCKED_FUSE

        decision = _decide_one(
            config, _inputs(), ApplianceTracker(), surplus=10.0, headroom=10.0
        )
        assert decision.status == STATUS_ON_SURPLUS

    def test_intra_tick_headroom_subtraction(self):
        a = _config(subentry_id="a", priority=1)
        b = _config(subentry_id="b", priority=2, rated_power_w=2000)
        # A consumes 5.80 A of the 8 A headroom; B needs 2.90 A but only
        # 2.20 A remain despite ample surplus.
        decisions = decide_appliances(
            now_ts=T0,
            raw_surplus_kw=10.0,
            headroom_amps=8.0,
            items=[
                (a, _inputs(), ApplianceTracker()),
                (b, _inputs(), ApplianceTracker()),
            ],
        )
        assert decisions[0].status == STATUS_ON_SURPLUS
        assert decisions[1].status == STATUS_BLOCKED_FUSE

        # With 9 A both fit.
        decisions = decide_appliances(
            now_ts=T0,
            raw_surplus_kw=10.0,
            headroom_amps=9.0,
            items=[
                (a, _inputs(), ApplianceTracker()),
                (b, _inputs(), ApplianceTracker()),
            ],
        )
        assert decisions[0].status == STATUS_ON_SURPLUS
        assert decisions[1].status == STATUS_ON_SURPLUS


# ---------------------------------------------------------------------------
# External-on, disable, unavailable
# ---------------------------------------------------------------------------


class TestExternalOn:
    def test_left_alone_without_command_or_allocation(self):
        tracker = ApplianceTracker()
        decision = _decide_one(_config(), _inputs(is_on=True), tracker, surplus=10.0)
        assert decision.status == STATUS_ON_EXTERNAL
        assert not decision.desired_on
        assert not decision.should_command
        assert decision.allocated_kw == 0.0
        assert not tracker.em_commanded_on

    def test_no_credit_back_for_external_load(self):
        a = _config(subentry_id="a", priority=1, power_sensor_entity="sensor.a")
        b = _config(subentry_id="b", priority=2, rated_power_w=2000)
        # A draws 3 kW but was turned on outside EM: no credit, so the pool
        # is just the raw 2.0 kW -- below B's 2.2 kW on threshold.
        decisions = decide_appliances(
            now_ts=T0,
            raw_surplus_kw=2.0,
            headroom_amps=INF,
            items=[
                (a, _inputs(is_on=True, measured=3000.0), ApplianceTracker()),
                (b, _inputs(), ApplianceTracker()),
            ],
        )
        assert decisions[0].status == STATUS_ON_EXTERNAL
        assert decisions[1].status == STATUS_OFF_NO_SURPLUS


class TestEmControlDisable:
    def test_disable_mid_on_issues_single_release(self):
        tracker = _on_tracker()
        decision = _decide_one(
            _config(),
            _inputs(is_on=True, em_control=False),
            tracker,
            now_ts=T0 + 60,
            surplus=10.0,
        )
        assert decision.status == STATUS_DISABLED
        assert decision.should_command
        assert not decision.turn_on
        assert not tracker.em_commanded_on
        assert tracker.last_off_ts == T0 + 60

        decision = _decide_one(
            _config(),
            _inputs(em_control=False),
            tracker,
            now_ts=T0 + 90,
            surplus=10.0,
        )
        assert decision.status == STATUS_DISABLED
        assert not decision.should_command

    def test_disabled_while_off_issues_no_command(self):
        decision = _decide_one(
            _config(), _inputs(em_control=False), ApplianceTracker(), surplus=10.0
        )
        assert decision.status == STATUS_DISABLED
        assert not decision.should_command


class TestActuatorUnavailable:
    def test_no_command_and_state_retained(self):
        tracker = _on_tracker()
        tracker.deficit_since_ts = T0 + 10
        decision = _decide_one(
            _config(),
            _inputs(available=False),
            tracker,
            now_ts=T0 + 60,
            surplus=10.0,
        )
        assert decision.status == STATUS_ACTUATOR_UNAVAILABLE
        assert decision.desired_on
        assert not decision.should_command
        assert decision.allocated_kw == 0.0
        assert tracker.em_commanded_on
        assert tracker.deficit_since_ts == T0 + 10


# ---------------------------------------------------------------------------
# Declarative re-assert (observe-only / failed command)
# ---------------------------------------------------------------------------


class TestDeclarativeReassert:
    def test_failed_turn_off_reasserted_until_actuator_reads_off(self):
        config = _config()
        tracker = _on_tracker()

        # Deficit releases the load; the actuator still reads on, so the
        # release stays pending.
        decision = _decide_one(
            config, _inputs(is_on=True), tracker, now_ts=T0 + 600, surplus=-1.0
        )
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert decision.should_command
        assert not decision.turn_on
        assert tracker.release_pending

        # The command failed/was missed: the actuator still reads on next
        # tick -- EM re-asserts the turn-off instead of stranding the load
        # as on_external.
        decision = _decide_one(
            config, _inputs(is_on=True), tracker, now_ts=T0 + 630, surplus=-1.0
        )
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert decision.should_command
        assert not decision.turn_on

        # Once the actuator reads off, the release completes silently.
        decision = _decide_one(
            config, _inputs(), tracker, now_ts=T0 + 660, surplus=-1.0
        )
        assert not decision.should_command
        assert not tracker.release_pending

        # A genuinely manual turn-on after the completed release is
        # external again.
        decision = _decide_one(
            config, _inputs(is_on=True), tracker, now_ts=T0 + 690, surplus=-1.0
        )
        assert decision.status == STATUS_ON_EXTERNAL
        assert not decision.should_command

    def test_disable_release_held_while_actuator_unavailable(self):
        tracker = _on_tracker()

        # EM control switched off while the actuator is unavailable: no
        # command is sent at the dead entity, the release stays pending.
        decision = _decide_one(
            _config(),
            _inputs(available=False, em_control=False),
            tracker,
            now_ts=T0 + 60,
        )
        assert decision.status == STATUS_DISABLED
        assert not decision.should_command
        assert tracker.release_pending

        # Actuator returns still on: the turn-off is finally delivered.
        decision = _decide_one(
            _config(),
            _inputs(is_on=True, em_control=False),
            tracker,
            now_ts=T0 + 90,
        )
        assert decision.status == STATUS_DISABLED
        assert decision.should_command
        assert not decision.turn_on

        # Actuator observed off: done, no further commands.
        decision = _decide_one(
            _config(), _inputs(em_control=False), tracker, now_ts=T0 + 120
        )
        assert not decision.should_command
        assert not tracker.release_pending

    def test_turn_on_reasserted_while_actuator_reads_off(self):
        config = _config()
        tracker = ApplianceTracker()

        decision = _decide_one(config, _inputs(), tracker, now_ts=T0, surplus=5.0)
        assert decision.should_command
        assert decision.turn_on

        # Command suppressed (observe-only): actuator still off, no credit,
        # pool stays at the raw surplus -- EM re-asserts the turn-on.
        decision = _decide_one(config, _inputs(), tracker, now_ts=T0 + 30, surplus=5.0)
        assert decision.status == STATUS_ON_SURPLUS
        assert decision.desired_on
        assert decision.should_command
        assert decision.turn_on
        assert decision.allocated_kw == pytest.approx(4.0)

        # Once the actuator follows, no further command is needed.
        decision = _decide_one(
            config, _inputs(is_on=True), tracker, now_ts=T0 + 60, surplus=5.0
        )
        assert decision.status == STATUS_ON_SURPLUS
        assert not decision.should_command


# ---------------------------------------------------------------------------
# Full lifecycle and determinism
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_full_cycle_with_measured_sensor(self):
        config = _config(
            on_sustain_s=60,
            off_sustain_s=120,
            min_on_s=300,
            min_off_s=60,
            power_sensor_entity="sensor.p",
        )
        tracker = ApplianceTracker()

        # Surplus appears; on-sustain runs.
        decision = _decide_one(
            config, _inputs(measured=0.0), tracker, now_ts=T0, surplus=5.0
        )
        assert decision.status == STATUS_WAITING_ON_SUSTAIN

        # Sustained: turn on, allocating rated.
        decision = _decide_one(
            config, _inputs(measured=0.0), tracker, now_ts=T0 + 60, surplus=5.0
        )
        assert decision.should_command
        assert decision.turn_on
        assert decision.allocated_kw == pytest.approx(4.0)

        # Actuator followed; the meter dropped by the 4 kW draw and the
        # credit-back restores the pool: 1.0 + 4.0 = 5.0 kW.
        decision = _decide_one(
            config,
            _inputs(is_on=True, measured=4000.0),
            tracker,
            now_ts=T0 + 90,
            surplus=1.0,
        )
        assert decision.status == STATUS_ON_SURPLUS
        assert not decision.should_command

        # Thermostat satisfied: the load idles at 200 W and no export is
        # left. Deficit starts inside min_on.
        decision = _decide_one(
            config,
            _inputs(is_on=True, measured=200.0),
            tracker,
            now_ts=T0 + 120,
            surplus=0.0,
        )
        assert decision.status == STATUS_HOLDING_MIN_ON
        assert decision.allocated_kw == pytest.approx(0.2)

        # min_on expires with the deficit clock long past off-sustain.
        decision = _decide_one(
            config,
            _inputs(is_on=True, measured=200.0),
            tracker,
            now_ts=T0 + 360,
            surplus=0.0,
        )
        assert decision.status == STATUS_OFF_NO_SURPLUS
        assert decision.should_command
        assert not decision.turn_on

        # Surplus returns immediately, but min_off blocks the restart.
        decision = _decide_one(
            config, _inputs(measured=0.0), tracker, now_ts=T0 + 400, surplus=5.0
        )
        assert decision.status == STATUS_BLOCKED_MIN_OFF

        # After min_off, the on-sustain clock starts fresh.
        decision = _decide_one(
            config, _inputs(measured=0.0), tracker, now_ts=T0 + 420, surplus=5.0
        )
        assert decision.status == STATUS_WAITING_ON_SUSTAIN


class TestDeterminism:
    @staticmethod
    def _build_items():
        a = _config(subentry_id="a", priority=1, power_sensor_entity="sensor.a")
        b = _config(subentry_id="b", priority=2, rated_power_w=2000)
        c = _config(subentry_id="c", priority=3, on_sustain_s=300)
        return [
            (
                a,
                _inputs(is_on=True, measured=3200.0),
                ApplianceTracker(em_commanded_on=True, last_on_ts=T0 - 1200),
            ),
            (b, _inputs(), ApplianceTracker(last_off_ts=T0 - 60)),
            (c, _inputs(), ApplianceTracker()),
        ]

    def test_same_inputs_produce_identical_decisions_and_trackers(self):
        items1 = self._build_items()
        items2 = self._build_items()
        decisions1 = decide_appliances(
            now_ts=T0, raw_surplus_kw=3.0, headroom_amps=14.0, items=items1
        )
        decisions2 = decide_appliances(
            now_ts=T0, raw_surplus_kw=3.0, headroom_amps=14.0, items=items2
        )
        assert decisions1 == decisions2
        assert [t for _, _, t in items1] == [t for _, _, t in items2]
        # Sanity: the walk produced a mixed outcome, not all-identical. c's
        # remaining pool (1.0 kW) is below its 4.4 kW on threshold after a
        # and b's allocations, even though the total pool (6.2 kW) would
        # have admitted it alone -- blocked_priority, not waiting_on_sustain.
        statuses = [d.status for d in decisions1]
        assert statuses == [
            STATUS_ON_SURPLUS,
            STATUS_ON_SURPLUS,
            STATUS_BLOCKED_PRIORITY,
        ]


# ---------------------------------------------------------------------------
# clamp_hysteresis() -- APPL-05 invariant enforced at the consume side
# ---------------------------------------------------------------------------


def test_clamp_hysteresis_returns_same_config_when_band_valid() -> None:
    config = _config(on_threshold_pct=100, off_threshold_pct=40)
    assert clamp_hysteresis(config) is config


def test_clamp_hysteresis_forces_off_below_on() -> None:
    config = _config(on_threshold_pct=80, off_threshold_pct=90)
    clamped = clamp_hysteresis(config)
    assert clamped.off_threshold_pct == 79
    assert clamped.on_threshold_pct == 80


def test_clamp_hysteresis_equal_thresholds_also_clamped() -> None:
    config = _config(on_threshold_pct=100, off_threshold_pct=100)
    assert clamp_hysteresis(config).off_threshold_pct == 99

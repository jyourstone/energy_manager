"""Tests for derive_battery_status() -- the unified Battery status state.

The sensor's contract: the state never claims an action that is not
physically happening. These tests pin every branch and the honesty rules
(gated discharge and surplus-less solar slots resolve to self_consumption).
"""

from __future__ import annotations

from custom_components.energy_manager.ems_controller import derive_battery_status


def _status(**overrides: object) -> str:
    defaults: dict[str, object] = {
        "plan_state": "idle",
        "ems_mode": "max_self_consumption",
        "charge_limit_kw": 3.0,
        "pv_charging_active": False,
        "car_override_active": False,
        "export_limit_kw": None,
        "discharge_allowed": True,
    }
    defaults.update(overrides)
    return derive_battery_status(**defaults)  # type: ignore[arg-type]


def test_default_is_self_consumption() -> None:
    """Nothing active -> the battery autonomously self-consumes, not "idle"."""
    assert _status() == "self_consumption"


def test_pv_charging_wins_over_plan_idle() -> None:
    assert _status(pv_charging_active=True, ems_mode="command_charging") == (
        "solar_charging"
    )


def test_scheduled_grid_charge() -> None:
    assert _status(ems_mode="command_charging") == "grid_charging"


def test_car_priority_outranks_everything() -> None:
    assert (
        _status(
            car_override_active=True,
            pv_charging_active=True,
            ems_mode="command_charging",
        )
        == "paused_car_priority"
    )


def test_discharge_slot_with_open_gate() -> None:
    assert _status(plan_state="discharging") == "discharging"


def test_gated_discharge_is_honest() -> None:
    """A booked discharge slot with a closed gate must NOT claim discharging."""
    assert _status(plan_state="discharging", discharge_allowed=False) == (
        "self_consumption"
    )


def test_export_slot_active() -> None:
    assert _status(plan_state="exporting", export_limit_kw=3.5) == "exporting"


def test_export_slot_without_limit_not_exporting() -> None:
    """Export slot booked but reserve floor / arming blocks it -> honest."""
    assert _status(plan_state="exporting", export_limit_kw=None) == ("self_consumption")


def test_scheduled_solar_charge_without_surplus_is_honest() -> None:
    """BATT-16 solar_charging slot with no actual surplus -> self_consumption."""
    assert _status(plan_state="solar_charging") == "self_consumption"


def test_night_holding_when_battery_power_near_zero() -> None:
    """Discharge blocked, no PV, battery at ~0 W -> holding, not self_consumption."""
    assert _status(battery_power_kw=0.01) == "holding"
    assert _status(battery_power_kw=-0.04) == "holding"


def test_active_balancing_stays_self_consumption() -> None:
    assert _status(battery_power_kw=0.8) == "self_consumption"
    assert _status(battery_power_kw=-1.2) == "self_consumption"


def test_unknown_battery_power_keeps_self_consumption() -> None:
    assert _status(battery_power_kw=None) == "self_consumption"


def test_holding_never_overrides_active_states() -> None:
    assert _status(battery_power_kw=0.0, ems_mode="command_charging") == (
        "grid_charging"
    )
    assert _status(battery_power_kw=0.0, pv_charging_active=True) == "solar_charging"


def test_zero_charge_limit_never_claims_charging() -> None:
    """A fuse-clamped 0 kW limit means no authorized flow -> not charging."""
    assert _status(pv_charging_active=True, charge_limit_kw=0.0) == "self_consumption"
    assert _status(ems_mode="command_charging", charge_limit_kw=0.0) == (
        "self_consumption"
    )


def test_zero_export_limit_not_exporting() -> None:
    assert _status(plan_state="exporting", export_limit_kw=0.0) == "self_consumption"

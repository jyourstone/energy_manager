"""Tests for the Wave B EaseeCoordinator's pure/testable helper pieces.

coordinator.py cannot be fully instantiated under the HA stubs (known
limitation, DataUpdateCoordinator subclassing breaks under the stub), so
this file targets only the small pure functions and the FuseSensorReader
helper class -- neither requires instantiating EaseeCoordinator or
EMSCoordinator themselves. Covers:
- build_easee_service_call(): ChargerCommand -> easee.* service mapping,
  verified against dev/config/custom_components/easee/services.yaml.
- _derive_phase_mode(): raw config_phaseMode attribute -> single/three.
- _estimate_charger_current_amps(): power-based current-draw estimate.
- _read_entity_float() / _entity_has_value() / _read_control_enabled():
  shared sensor-read primitives.
- FuseSensorReader: shared grid-current read/fallback logic (the "shared
  fuse arbiter").
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.energy_manager.charger_state_machine import ChargerCommand
from custom_components.energy_manager.coordinator import (
    EASEE_PHASE_MODE_MAP,
    FuseSensorReader,
    _derive_phase_mode,
    _dispatch_notifications,
    _entity_has_value,
    _estimate_charger_current_amps,
    _read_control_enabled,
    _read_entity_float,
    _read_force_charging,
    _read_power_kw,
    build_easee_service_call,
)

# ---------------------------------------------------------------------------
# build_easee_service_call()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["start", "pause", "resume", "stop"])
def test_build_easee_service_call_action_commands(action: str) -> None:
    domain, service, data = build_easee_service_call(ChargerCommand(action), "dev1")
    assert domain == "easee"
    assert service == "action_command"
    assert data == {"device_id": "dev1", "action_command": action}


def test_build_easee_service_call_set_dynamic_limit() -> None:
    domain, service, data = build_easee_service_call(
        ChargerCommand("set_dynamic_limit", 12.0), "dev1"
    )
    assert domain == "easee"
    assert service == "set_charger_dynamic_limit"
    assert data == {"device_id": "dev1", "current": 12.0}


@pytest.mark.parametrize(
    ("internal_mode", "easee_mode"),
    [("single", "1_phase"), ("three", "3_phase")],
)
def test_build_easee_service_call_set_phase_mode(internal_mode, easee_mode) -> None:
    domain, service, data = build_easee_service_call(
        ChargerCommand("set_phase_mode", internal_mode), "dev1"
    )
    assert domain == "easee"
    assert service == "set_charger_phase_mode"
    assert data == {"device_id": "dev1", "phase_mode": easee_mode}


def test_build_easee_service_call_phase_mode_map_matches_easee_vocabulary() -> None:
    # Regression guard: charger_state_machine's "single"/"three" vocabulary
    # must map onto exactly the easee.set_charger_phase_mode enum values.
    assert EASEE_PHASE_MODE_MAP == {"single": "1_phase", "three": "3_phase"}


def test_build_easee_service_call_unknown_action_raises() -> None:
    with pytest.raises(ValueError, match="Unknown charger command action"):
        build_easee_service_call(ChargerCommand("reboot"), "dev1")


# ---------------------------------------------------------------------------
# _derive_phase_mode()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, "single"),
        ("1", "single"),
        (3, "three"),
        ("3", "three"),
        (2, "three"),  # auto -> treated as three (safe default)
        (None, "three"),
        ("garbage", "three"),
        ("", "three"),
    ],
)
def test_derive_phase_mode(raw, expected) -> None:
    assert _derive_phase_mode(raw) == expected


# ---------------------------------------------------------------------------
# _estimate_charger_current_amps()
# ---------------------------------------------------------------------------


def test_estimate_charger_current_amps_single_phase_uses_1phase_factor() -> None:
    result = _estimate_charger_current_amps(2.3, "single", 4.3, 1.45)
    assert result == pytest.approx(2.3 * 4.3)


def test_estimate_charger_current_amps_three_phase_uses_3phase_factor() -> None:
    result = _estimate_charger_current_amps(2.3, "three", 4.3, 1.45)
    assert result == pytest.approx(2.3 * 1.45)


def test_estimate_charger_current_amps_zero_power() -> None:
    assert _estimate_charger_current_amps(0.0, "three", 4.3, 1.45) == 0.0


def test_estimate_charger_current_amps_never_negative() -> None:
    assert _estimate_charger_current_amps(-5.0, "single", 4.3, 1.45) == 0.0


# ---------------------------------------------------------------------------
# Shared read primitives
# ---------------------------------------------------------------------------


class FakeState:
    def __init__(self, state: str, attributes: dict | None = None) -> None:
        self.state = state
        self.attributes = attributes or {}


class FakeHass:
    def __init__(self, states: dict[str, FakeState]) -> None:
        self.states = MagicMock()
        self.states.get.side_effect = lambda entity_id: states.get(entity_id)


def test_read_entity_float_missing_entity_returns_default() -> None:
    hass = FakeHass({})
    assert _read_entity_float(hass, "", 42.0) == 42.0
    assert _read_entity_float(hass, "sensor.missing", 42.0) == 42.0


def test_read_entity_float_unavailable_returns_default() -> None:
    hass = FakeHass({"sensor.x": FakeState("unavailable")})
    assert _read_entity_float(hass, "sensor.x", 7.0) == 7.0


def test_read_entity_float_valid_value() -> None:
    hass = FakeHass({"sensor.x": FakeState("3.5")})
    assert _read_entity_float(hass, "sensor.x", 0.0) == 3.5


def test_read_entity_float_unparseable_returns_default() -> None:
    hass = FakeHass({"sensor.x": FakeState("not-a-number")})
    assert _read_entity_float(hass, "sensor.x", 9.0) == 9.0


def test_entity_has_value() -> None:
    hass = FakeHass(
        {
            "sensor.ok": FakeState("1.0"),
            "sensor.unknown": FakeState("unknown"),
        }
    )
    assert _entity_has_value(hass, "sensor.ok") is True
    assert _entity_has_value(hass, "sensor.unknown") is False
    assert _entity_has_value(hass, "sensor.missing") is False
    assert _entity_has_value(hass, "") is False


def test_read_control_enabled_no_runtime_data_defaults_false() -> None:
    entry = SimpleNamespace()
    assert _read_control_enabled(entry) is False


def test_read_control_enabled_true() -> None:
    entry = SimpleNamespace(runtime_data=SimpleNamespace(control_enabled=True))
    assert _read_control_enabled(entry) is True


def test_read_control_enabled_runtime_data_missing_attr_defaults_false() -> None:
    entry = SimpleNamespace(runtime_data=SimpleNamespace())
    assert _read_control_enabled(entry) is False


def test_read_force_charging_no_runtime_data_defaults_false() -> None:
    entry = SimpleNamespace()
    assert _read_force_charging(entry) is False


def test_read_force_charging_true() -> None:
    entry = SimpleNamespace(runtime_data=SimpleNamespace(force_charging=True))
    assert _read_force_charging(entry) is True


def test_read_force_charging_runtime_data_missing_attr_defaults_false() -> None:
    entry = SimpleNamespace(runtime_data=SimpleNamespace())
    assert _read_force_charging(entry) is False


# ---------------------------------------------------------------------------
# _read_power_kw() -- EV-09 solar-surplus input reader
# ---------------------------------------------------------------------------


def test_read_power_kw_assumes_watts_by_default() -> None:
    hass = FakeHass({"sensor.pv": FakeState("2300")})
    assert _read_power_kw(hass, "sensor.pv") == pytest.approx(2.3)


def test_read_power_kw_respects_kw_unit() -> None:
    hass = FakeHass({"sensor.pv": FakeState("2.3", {"unit_of_measurement": "kW"})})
    assert _read_power_kw(hass, "sensor.pv") == pytest.approx(2.3)


def test_read_power_kw_missing_entity_defaults_zero() -> None:
    hass = FakeHass({})
    assert _read_power_kw(hass, "") == 0.0
    assert _read_power_kw(hass, "sensor.missing") == 0.0


def test_read_power_kw_unavailable_defaults_zero() -> None:
    hass = FakeHass({"sensor.pv": FakeState("unavailable")})
    assert _read_power_kw(hass, "sensor.pv") == 0.0


# ---------------------------------------------------------------------------
# FuseSensorReader (the "shared fuse arbiter")
# ---------------------------------------------------------------------------


def _reader(hass: FakeHass, **overrides) -> FuseSensorReader:
    defaults = {
        "hass": hass,
        "grid_phase_a_entity": "",
        "grid_phase_b_entity": "",
        "grid_phase_c_entity": "",
        "grid_power_entity": "",
        "sensor_fail_behavior": "assume_load",
        "assumed_load_amps": 10.0,
    }
    defaults.update(overrides)
    return FuseSensorReader(**defaults)


def test_fuse_reader_per_phase_worst_case_signed_amps() -> None:
    hass = FakeHass(
        {
            "sensor.a": FakeState("1000"),  # W -> 1000/230 ~= 4.35A
            "sensor.b": FakeState("2000"),  # ~8.70A (worst case)
            "sensor.c": FakeState("500"),  # ~2.17A
        }
    )
    reader = _reader(
        hass,
        grid_phase_a_entity="sensor.a",
        grid_phase_b_entity="sensor.b",
        grid_phase_c_entity="sensor.c",
    )
    amps, blocked = reader.read_grid_current_amps()
    assert blocked is False
    assert amps == pytest.approx(2000 / 230.0)


def test_fuse_reader_kw_unit_conversion() -> None:
    hass = FakeHass(
        {
            "sensor.a": FakeState("1.0", {"unit_of_measurement": "kW"}),
            "sensor.b": FakeState("0.5", {"unit_of_measurement": "kW"}),
            "sensor.c": FakeState("0.2", {"unit_of_measurement": "kW"}),
        }
    )
    reader = _reader(
        hass,
        grid_phase_a_entity="sensor.a",
        grid_phase_b_entity="sensor.b",
        grid_phase_c_entity="sensor.c",
    )
    amps, blocked = reader.read_grid_current_amps()
    assert blocked is False
    assert amps == pytest.approx(1000 / 230.0)


def test_fuse_reader_falls_back_to_total_grid_power_when_phases_not_configured() -> None:
    hass = FakeHass({"sensor.total": FakeState("3000")})
    reader = _reader(hass, grid_power_entity="sensor.total")
    amps, blocked = reader.read_grid_current_amps()
    assert blocked is False
    assert amps == pytest.approx(3000 / (3.0 * 230.0))


def test_fuse_reader_assume_load_fallback_when_phase_sensor_unavailable() -> None:
    hass = FakeHass(
        {
            "sensor.a": FakeState("1000"),
            "sensor.b": FakeState("unavailable"),
            "sensor.c": FakeState("500"),
        }
    )
    reader = _reader(
        hass,
        grid_phase_a_entity="sensor.a",
        grid_phase_b_entity="sensor.b",
        grid_phase_c_entity="sensor.c",
        sensor_fail_behavior="assume_load",
        assumed_load_amps=15.0,
    )
    amps, blocked = reader.read_grid_current_amps()
    assert amps == 15.0
    assert blocked is False


def test_fuse_reader_block_fallback_forces_zero_headroom() -> None:
    hass = FakeHass({})
    reader = _reader(hass, sensor_fail_behavior="block")
    amps, blocked = reader.read_grid_current_amps()
    assert blocked is True
    assert amps == 0.0


def test_fuse_reader_not_configured_applies_fallback() -> None:
    hass = FakeHass({})
    reader = _reader(hass, sensor_fail_behavior="assume_load", assumed_load_amps=7.5)
    amps, blocked = reader.read_grid_current_amps()
    assert amps == 7.5
    assert blocked is False


# ---------------------------------------------------------------------------
# _dispatch_notifications() -- the testable half of
# EaseeCoordinator._send_notifications (the coordinator itself cannot be
# instantiated under the HA stubs, see the module docstring)
# ---------------------------------------------------------------------------


def _notify_hass(*, raises: bool = False):
    hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock()))
    if raises:
        hass.services.async_call.side_effect = RuntimeError("notify backend down")
    return hass


def test_dispatch_notifications_marks_critical_messages_only() -> None:
    hass = _notify_hass()
    asyncio.run(
        _dispatch_notifications(hass, "notify.mobil", "", ("plain",), ("urgent",))
    )
    calls = hass.services.async_call.await_args_list
    assert [c.args[0] for c in calls] == ["notify", "notify"]
    assert [c.args[2]["message"] for c in calls] == ["plain", "urgent"]
    assert "data" not in calls[0].args[2]
    assert calls[1].args[2]["data"]["push"]["sound"]["critical"] == 1


def test_dispatch_notifications_applies_the_prefix() -> None:
    hass = _notify_hass()
    asyncio.run(
        _dispatch_notifications(hass, "notify.mobil", "[observe-only] ", (), ("urgent",))
    )
    call = hass.services.async_call.await_args_list[0]
    assert call.args[2]["message"] == "[observe-only] urgent"


def test_dispatch_notifications_never_raises_and_keeps_going() -> None:
    """A failing notify service must not abort the control tick -- this runs
    in the finally of the command dispatch, so raising here would mask the
    command failure the alert exists to report."""
    hass = _notify_hass(raises=True)
    asyncio.run(
        _dispatch_notifications(hass, "notify.mobil", "", ("plain",), ("urgent",))
    )
    assert hass.services.async_call.await_count == 2


def test_dispatch_notifications_ignores_a_malformed_service() -> None:
    hass = _notify_hass()
    asyncio.run(_dispatch_notifications(hass, "mobil", "", ("plain",), ()))
    hass.services.async_call.assert_not_awaited()

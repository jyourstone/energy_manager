"""Tests for the HA Repairs integration (repairs.py and its wiring).

EMSCoordinator cannot be instantiated under the HA stubs (known limitation,
DataUpdateCoordinator subclassing breaks under the stub), so the wrong-domain
report/clear wiring inside _send_charge_limit()/_send_discharge_limit() is
not exercised here. Covered instead:
- should_file_fallback_issue(): the pure continuous-fallback threshold.
- async_report_issue()/async_clear_issue(): call-arg pass-through to the
  stubbed issue_registry and the swallow-all-errors guarantee.
- FuseSensorReader: files fuse_sensor_fallback only after the threshold of
  continuous fallback, clears it on recovery, and re-files after a
  recover-then-degrade sequence (a fresh streak runs the full threshold).
- async_unload_entry(): clears all fixed issue ids.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.energy_manager import async_unload_entry
from custom_components.energy_manager import coordinator as coordinator_module
from custom_components.energy_manager.const import (
    DOMAIN,
    FUSE_FALLBACK_ISSUE_THRESHOLD_SECONDS,
)
from custom_components.energy_manager.coordinator import FuseSensorReader
from custom_components.energy_manager.ems_controller import should_file_fallback_issue
from custom_components.energy_manager.repairs import (
    ALL_ISSUE_IDS,
    ISSUE_FUSE_SENSOR_FALLBACK,
    async_clear_issue,
    async_report_issue,
)


class FakeState:
    def __init__(self, state: str, attributes: dict | None = None) -> None:
        self.state = state
        self.attributes = attributes or {}


class FakeHass:
    def __init__(self, states: dict[str, FakeState]) -> None:
        self.states = MagicMock()
        self.states.get.side_effect = lambda entity_id: states.get(entity_id)


@pytest.fixture(autouse=True)
def _reset_issue_registry_stub():
    """Reset the stubbed issue_registry mocks around every test."""
    ir.async_create_issue.reset_mock(side_effect=True)
    ir.async_delete_issue.reset_mock(side_effect=True)
    yield
    ir.async_create_issue.reset_mock(side_effect=True)
    ir.async_delete_issue.reset_mock(side_effect=True)


# ---------------------------------------------------------------------------
# should_file_fallback_issue() -- pure threshold decision
# ---------------------------------------------------------------------------


def test_should_file_fallback_issue_no_streak_is_false() -> None:
    assert should_file_fallback_issue(None, 1000.0, 300.0) is False


def test_should_file_fallback_issue_below_threshold_is_false() -> None:
    assert should_file_fallback_issue(1000.0, 1299.9, 300.0) is False


def test_should_file_fallback_issue_at_threshold_is_true() -> None:
    assert should_file_fallback_issue(1000.0, 1300.0, 300.0) is True


# ---------------------------------------------------------------------------
# async_report_issue() / async_clear_issue() -- issue_registry pass-through
# ---------------------------------------------------------------------------


def test_async_report_issue_passes_args_to_issue_registry() -> None:
    hass = MagicMock()
    async_report_issue(
        hass,
        "some_issue",
        ir.IssueSeverity.ERROR,
        "some_issue",
        {"entity_id": "sensor.bad"},
    )
    ir.async_create_issue.assert_called_once_with(
        hass,
        DOMAIN,
        "some_issue",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="some_issue",
        translation_placeholders={"entity_id": "sensor.bad"},
    )


def test_async_clear_issue_passes_args_to_issue_registry() -> None:
    hass = MagicMock()
    async_clear_issue(hass, "some_issue")
    ir.async_delete_issue.assert_called_once_with(hass, DOMAIN, "some_issue")


def test_report_and_clear_swallow_registry_errors() -> None:
    """A repairs failure must never propagate into the update cycle."""
    hass = MagicMock()
    ir.async_create_issue.side_effect = RuntimeError("registry down")
    ir.async_delete_issue.side_effect = RuntimeError("registry down")
    async_report_issue(hass, "some_issue", ir.IssueSeverity.WARNING, "some_issue")
    async_clear_issue(hass, "some_issue")


# ---------------------------------------------------------------------------
# FuseSensorReader -- fuse_sensor_fallback file/clear/re-file wiring
# ---------------------------------------------------------------------------


def test_fuse_reader_files_clears_and_refiles_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: clock["now"])
    states: dict[str, FakeState] = {}
    hass = FakeHass(states)
    reader = FuseSensorReader(
        hass=hass,
        grid_phase_a_entity="",
        grid_phase_b_entity="",
        grid_phase_c_entity="",
        grid_power_entity="sensor.total",
        sensor_fail_behavior="assume_load",
        assumed_load_amps=10.0,
    )

    # Sensor down: fallback streak starts, no issue before the threshold.
    reader.read_grid_current_amps()
    clock["now"] = FUSE_FALLBACK_ISSUE_THRESHOLD_SECONDS - 1.0
    reader.read_grid_current_amps()
    ir.async_create_issue.assert_not_called()

    # Threshold reached: issue filed.
    clock["now"] = FUSE_FALLBACK_ISSUE_THRESHOLD_SECONDS
    reader.read_grid_current_amps()
    assert ir.async_create_issue.call_count == 1
    assert ir.async_create_issue.call_args.args[2] == ISSUE_FUSE_SENSOR_FALLBACK

    # Recovery: issue cleared, streak reset.
    states["sensor.total"] = FakeState("3000")
    reader.read_grid_current_amps()
    ir.async_delete_issue.assert_called_once_with(
        hass, DOMAIN, ISSUE_FUSE_SENSOR_FALLBACK
    )

    # Degrades again: the fresh streak must run the full threshold anew,
    # then the issue is re-filed.
    del states["sensor.total"]
    clock["now"] += 10.0
    reader.read_grid_current_amps()
    assert ir.async_create_issue.call_count == 1
    clock["now"] += FUSE_FALLBACK_ISSUE_THRESHOLD_SECONDS
    reader.read_grid_current_amps()
    assert ir.async_create_issue.call_count == 2


# ---------------------------------------------------------------------------
# async_unload_entry() -- clears all fixed issue ids
# ---------------------------------------------------------------------------


def test_async_unload_entry_clears_all_issue_ids() -> None:
    hass = MagicMock()
    entry = MagicMock()
    entry.runtime_data = SimpleNamespace(forwarded_platforms=[])

    result = asyncio.run(async_unload_entry(hass, entry))

    assert result is True
    cleared = [call.args[2] for call in ir.async_delete_issue.call_args_list]
    assert cleared == list(ALL_ISSUE_IDS)

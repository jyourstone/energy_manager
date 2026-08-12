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
- FuseSensorReader: files grid_sensor_mismatch (with one warning log) after
  a sustained phase-sum vs total disagreement; the issue is sticky (never
  deleted at runtime -- it clears on entry unload), deduped across readers
  via the issue registry, and a non-numeric sensor state resets the sustain
  clock (the detection rule itself is covered in test_grid_consistency.py).
- async_unload_entry(): clears all fixed issue ids.
"""

from __future__ import annotations

import asyncio
import logging
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
from custom_components.energy_manager.grid_consistency import (
    MISMATCH_SUSTAIN_SECONDS,
)
from custom_components.energy_manager.repairs import (
    ALL_ISSUE_IDS,
    ISSUE_FUSE_SENSOR_FALLBACK,
    ISSUE_GRID_SENSOR_MISMATCH,
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


def _reset_issue_registry_mocks() -> None:
    ir.async_create_issue.reset_mock(side_effect=True)
    ir.async_delete_issue.reset_mock(side_effect=True)
    # The mismatch wiring queries the registry before filing (cross-reader
    # dedupe); default to "no issue filed" so the file path is exercised.
    ir.async_get.reset_mock()
    ir.async_get.return_value.async_get_issue.side_effect = None
    ir.async_get.return_value.async_get_issue.return_value = None


@pytest.fixture(autouse=True)
def _reset_issue_registry_stub():
    """Reset the stubbed issue_registry mocks around every test."""
    _reset_issue_registry_mocks()
    yield
    _reset_issue_registry_mocks()


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
# FuseSensorReader -- grid_sensor_mismatch wiring (sticky, deduped, strict)
# ---------------------------------------------------------------------------


def _mismatch_states() -> dict[str, FakeState]:
    """The field misconfig: phases sum to +7.0 kW, real grid total -6.2 kW.

    Values in W: no unit_of_measurement attribute means watts.
    """
    return {
        "sensor.phase_a": FakeState("2333"),
        "sensor.phase_b": FakeState("2333"),
        "sensor.phase_c": FakeState("2334"),
        "sensor.total": FakeState("-6200"),
    }


def _make_grid_reader(hass: FakeHass) -> FuseSensorReader:
    """Reader with both entity groups configured (the guard's precondition)."""
    return FuseSensorReader(
        hass=hass,
        grid_phase_a_entity="sensor.phase_a",
        grid_phase_b_entity="sensor.phase_b",
        grid_phase_c_entity="sensor.phase_c",
        grid_power_entity="sensor.total",
        sensor_fail_behavior="assume_load",
        assumed_load_amps=10.0,
    )


def test_fuse_reader_files_sticky_grid_mismatch_issue(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: clock["now"])
    states = _mismatch_states()
    hass = FakeHass(states)
    reader = _make_grid_reader(hass)

    with caplog.at_level(logging.WARNING):
        # Mismatch streak starts: no issue before the sustain window.
        reader.read_grid_current_amps()
        ir.async_create_issue.assert_not_called()

        # Window elapsed: issue filed with both entity groups + readings.
        clock["now"] = MISMATCH_SUSTAIN_SECONDS
        reader.read_grid_current_amps()
        # Still mismatched: no re-file, no second warning.
        clock["now"] += 30.0
        reader.read_grid_current_amps()

        # Consistency restored (the misconfigured sensors agree at night):
        # the issue is sticky -- never deleted at runtime, it clears when
        # the entry unloads.
        states["sensor.total"] = FakeState("7000")
        clock["now"] += 30.0
        reader.read_grid_current_amps()
        ir.async_delete_issue.assert_not_called()

        # Mismatch resumes in the morning: no second warning, no re-file.
        states["sensor.total"] = FakeState("-6200")
        clock["now"] += 30.0
        reader.read_grid_current_amps()
        clock["now"] += MISMATCH_SUSTAIN_SECONDS
        reader.read_grid_current_amps()

    assert ir.async_create_issue.call_count == 1
    assert ir.async_create_issue.call_args.args[2] == ISSUE_GRID_SENSOR_MISMATCH
    placeholders = ir.async_create_issue.call_args.kwargs["translation_placeholders"]
    assert placeholders["grid_power_entity"] == "sensor.total"
    assert "sensor.phase_a" in placeholders["phase_entities"]
    assert placeholders["phase_sum_kw"] == "7.0"
    assert placeholders["total_kw"] == "-6.2"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Grid sensor mismatch" in warnings[0].getMessage()


def test_grid_mismatch_issue_deduped_across_readers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Three coordinators share the fixed issue id: only the first files."""
    clock = {"now": 0.0}
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: clock["now"])
    # Mirror the real registry: once filed, the issue is queryable.
    filed: set[str] = set()
    ir.async_create_issue.side_effect = (
        lambda hass, domain, issue_id, **kwargs: filed.add(issue_id)
    )
    ir.async_get.return_value.async_get_issue.side_effect = (
        lambda domain, issue_id: (
            SimpleNamespace(active=True) if issue_id in filed else None
        )
    )
    hass = FakeHass(_mismatch_states())
    reader_a = _make_grid_reader(hass)
    reader_b = _make_grid_reader(hass)

    with caplog.at_level(logging.WARNING):
        reader_a.read_grid_current_amps()
        reader_b.read_grid_current_amps()
        clock["now"] = MISMATCH_SUSTAIN_SECONDS
        # First reader to cross the window files the issue and warns...
        reader_a.read_grid_current_amps()
        # ...the second finds it already in the registry and stays silent.
        reader_b.read_grid_current_amps()

    assert ir.async_create_issue.call_count == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


@pytest.mark.parametrize("stuck_entity", ["sensor.total", "sensor.phase_b"])
def test_grid_mismatch_non_numeric_state_resets_sustain_clock(
    monkeypatch: pytest.MonkeyPatch, stuck_entity: str
) -> None:
    """A sensor stuck at a non-numeric state must not count as 0.0 kW."""
    clock = {"now": 0.0}
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: clock["now"])
    states = _mismatch_states()
    hass = FakeHass(states)
    reader = _make_grid_reader(hass)

    reader.read_grid_current_amps()  # mismatch streak starts

    # Stuck sensor: the guard reads it as unavailable (clock reset), not
    # as a fabricated 0.0 kW reading sustaining a false mismatch.
    numeric_state = states[stuck_entity]
    states[stuck_entity] = FakeState("starting")
    clock["now"] = 200.0
    reader.read_grid_current_amps()

    # Recovery: the fresh streak must run the full window before filing.
    states[stuck_entity] = numeric_state
    clock["now"] = MISMATCH_SUSTAIN_SECONDS
    reader.read_grid_current_amps()
    ir.async_create_issue.assert_not_called()

    clock["now"] = 2 * MISMATCH_SUSTAIN_SECONDS
    reader.read_grid_current_amps()
    assert ir.async_create_issue.call_count == 1
    assert ir.async_create_issue.call_args.args[2] == ISSUE_GRID_SENSOR_MISMATCH


def test_grid_mismatch_reactivates_issue_after_restart(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A restored-from-storage placeholder (active=False) must not
    suppress re-filing: HA's issue registry persists even is_persistent=
    False issues to .storage and restores them inactive after a restart,
    and ir.async_create_issue is exactly what re-activates one.
    """
    clock = {"now": 0.0}
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: clock["now"])
    ir.async_get.return_value.async_get_issue.return_value = SimpleNamespace(
        active=False
    )
    hass = FakeHass(_mismatch_states())
    reader = _make_grid_reader(hass)

    with caplog.at_level(logging.WARNING):
        reader.read_grid_current_amps()
        clock["now"] = MISMATCH_SUSTAIN_SECONDS
        reader.read_grid_current_amps()

    assert ir.async_create_issue.call_count == 1
    assert ir.async_create_issue.call_args.args[2] == ISSUE_GRID_SENSOR_MISMATCH
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_grid_mismatch_failed_filing_rearms_the_guard(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A swallowed async_create_issue exception must not strand the guard.

    update_grid_consistency() sticky-flags the tracker on EVENT_RAISE
    before the registry call runs. If that call raises, no issue actually
    gets filed -- without a reset the guard would stay silently flagged
    with nothing filed until the entry reloads. It must instead re-arm
    (flagged=False, mismatch_since_ts=None) and retry after a fresh
    sustain window.
    """
    clock = {"now": 0.0}
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: clock["now"])
    hass = FakeHass(_mismatch_states())
    reader = _make_grid_reader(hass)

    ir.async_create_issue.side_effect = RuntimeError("registry down")

    with caplog.at_level(logging.WARNING):
        # First attempt: sustain window elapses, warns, filing raises and
        # is swallowed -- no issue on record, tracker re-armed.
        reader.read_grid_current_amps()
        clock["now"] = MISMATCH_SUSTAIN_SECONDS
        reader.read_grid_current_amps()
        assert ir.async_create_issue.call_count == 1
        assert reader._mismatch_tracker.flagged is False
        assert reader._mismatch_tracker.mismatch_since_ts is None

        # Registry recovers, but the re-armed guard must run a full fresh
        # sustain window before retrying -- no immediate re-file.
        ir.async_create_issue.side_effect = None
        clock["now"] += 30.0
        reader.read_grid_current_amps()
        assert ir.async_create_issue.call_count == 1

        # Second attempt: fresh window elapses, files + warns again.
        clock["now"] += MISMATCH_SUSTAIN_SECONDS
        reader.read_grid_current_amps()

    assert ir.async_create_issue.call_count == 2
    assert reader._mismatch_tracker.flagged is True
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


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

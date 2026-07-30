"""Tests for diagnostics.py (CORE-10).

diagnostics.py is a plain async function with no HA base-class
subclassing (unlike coordinator.py's coordinator classes, see
test_coordinator.py/test_easee_coordinator_helpers.py's "cannot be fully
instantiated under the HA stubs" note), so it can be exercised end-to-end
here using fake coordinator/entry objects that only need a `.data`
attribute -- diagnostics.py never touches coordinator-specific behavior.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.energy_manager import diagnostics
from custom_components.energy_manager.coordinator import (
    BatteryScheduleData,
    CarChargingData,
    EaseeData,
    EMSData,
    PriceData,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _passthrough_redact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub async_redact_data as a passthrough.

    Under the root conftest.py's homeassistant stub, the real
    homeassistant.components.diagnostics.async_redact_data is a MagicMock
    (not the real redaction helper) -- patch it to a deterministic
    passthrough so the assembled dict can be asserted on directly.
    """
    monkeypatch.setattr(diagnostics, "async_redact_data", lambda data, to_redact: data)


def _price_data() -> PriceData:
    return PriceData(
        today=[object(), object()],
        tomorrow=[object()],
        current_price=1.23,
        last_updated=datetime(2026, 2, 15, 12, 0, tzinfo=UTC),
    )


def _battery_data() -> BatteryScheduleData:
    return BatteryScheduleData(
        current_state="idle",
        schedule=[object(), object(), object()],
        next_charging_slot=None,
        next_discharging_slot=None,
        charging_slot_count=1,
        discharging_slot_count=2,
        target_ems_mode="standby",
        last_calculated=datetime(2026, 2, 15, 12, 0, tzinfo=UTC),
        solar_forecast_used=False,
        mean_consumption_kw=0.75,
        consumption_sample_count=42,
    )


def _ems_data() -> EMSData:
    return EMSData(
        current_mode="standby",
        target_mode="standby",
        charge_limit_kw=0.0,
        fuse_headroom_amps=18.0,
        override_reason=None,
        command_verified=True,
        last_command_time=None,
        car_override_active=False,
        pv_charging_active=False,
        dry_run=True,
        last_suppressed_command=None,
    )


def _easee_data() -> EaseeData:
    return EaseeData(
        mode="idle",
        target_amps=0.0,
        target_phase_mode="three",
        sequence_state="idle",
        stuck=False,
        dry_run=True,
        last_suppressed_command=None,
        notification_count=0,
        override_reason=None,
        charger_status="disconnected",
        charger_power_kw=0.0,
        fuse_headroom_amps=18.0,
        house_consumption_kw=1.5,
        solar_surplus_kw=-0.5,
    )


def _car_data() -> CarChargingData:
    return CarChargingData(
        current_action="idle",
        schedule=[object()],
        charging_slot_count=0,
        energy_needed_kwh=10.0,
        hours_needed=2.0,
        is_preliminary=False,
        car_name="Test Car",
        current_soc=60.0,
        target_soc=80.0,
        last_calculated=datetime(2026, 2, 15, 12, 0, tzinfo=UTC),
        home_and_plugged=True,
        phase_capability=3,
        max_charge_power_kw=7.4,
    )


def _fake_entry(
    *,
    battery_coordinator=None,
    ems_coordinator=None,
    easee_coordinator=None,
    car_coordinators=None,
):
    runtime_data = SimpleNamespace(
        price_coordinator=SimpleNamespace(data=_price_data()),
        battery_coordinator=battery_coordinator,
        ems_coordinator=ems_coordinator,
        easee_coordinator=easee_coordinator,
        car_coordinators=car_coordinators or {},
        control_enabled=True,
        force_charging=False,
        forwarded_platforms=["sensor", "switch"],
    )
    return SimpleNamespace(
        data={"nordpool_sensor": "sensor.np"},
        options={"battery_enabled": True},
        runtime_data=runtime_data,
    )


# ---------------------------------------------------------------------------
# _read_manifest_version()
# ---------------------------------------------------------------------------


MANIFEST_VERSION = json.loads(
    (
        Path(__file__).parent.parent
        / "custom_components"
        / "energy_manager"
        / "manifest.json"
    ).read_text()
)["version"]


def test_read_manifest_version_matches_bundled_manifest() -> None:
    assert diagnostics._read_manifest_version() == MANIFEST_VERSION


# ---------------------------------------------------------------------------
# async_get_config_entry_diagnostics() -- full snapshot
# ---------------------------------------------------------------------------


def test_diagnostics_full_snapshot() -> None:
    entry = _fake_entry(
        battery_coordinator=SimpleNamespace(data=_battery_data()),
        ems_coordinator=SimpleNamespace(data=_ems_data()),
        easee_coordinator=SimpleNamespace(data=_easee_data()),
        car_coordinators={"car1": SimpleNamespace(data=_car_data())},
    )

    result = asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(SimpleNamespace(), entry)
    )

    assert result["entry"]["data"] == {"nordpool_sensor": "sensor.np"}
    assert result["entry"]["options"] == {"battery_enabled": True}
    assert result["integration_version"] == MANIFEST_VERSION
    assert result["runtime_flags"] == {
        "control_enabled": True,
        "force_charging": False,
        "forwarded_platforms": ["sensor", "switch"],
    }

    price = result["coordinators"]["price"]
    assert price["today_slot_count"] == 2
    assert price["tomorrow_slot_count"] == 1
    assert price["current_price"] == 1.23
    assert price["last_updated"] == "2026-02-15T12:00:00+00:00"

    battery = result["coordinators"]["battery"]
    assert battery["current_state"] == "idle"
    assert battery["total_slot_count"] == 3
    assert battery["mean_consumption_kw"] == 0.75
    assert battery["consumption_sample_count"] == 42

    ems = result["coordinators"]["ems"]
    assert ems["current_mode"] == "standby"
    assert ems["last_command_time"] is None
    assert ems["dry_run"] is True

    easee = result["coordinators"]["easee"]
    assert easee["mode"] == "idle"
    assert easee["house_consumption_kw"] == 1.5
    assert easee["solar_surplus_kw"] == -0.5

    car = result["coordinators"]["cars"]["car1"]
    assert car["car_name"] == "Test Car"
    assert car["current_action"] == "idle"
    assert car["total_slot_count"] == 1


def test_diagnostics_missing_optional_coordinators_are_none() -> None:
    entry = _fake_entry()

    result = asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(SimpleNamespace(), entry)
    )

    assert result["coordinators"]["battery"] is None
    assert result["coordinators"]["ems"] is None
    assert result["coordinators"]["easee"] is None
    assert result["coordinators"]["cars"] == {}
    assert result["coordinators"]["price"] is not None


def test_diagnostics_jsonify_converts_datetime_in_ems_snapshot() -> None:
    ems = replace(
        _ems_data(), last_command_time=datetime(2026, 2, 15, 12, 30, tzinfo=UTC)
    )
    entry = _fake_entry(ems_coordinator=SimpleNamespace(data=ems))

    result = asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(SimpleNamespace(), entry)
    )

    assert result["coordinators"]["ems"]["last_command_time"] == "2026-02-15T12:30:00+00:00"

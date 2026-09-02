"""Diagnostics support for the Energy Manager integration.

Provides the config-entry diagnostics dict shown by Settings > Devices &
Services > Energy Manager > Download diagnostics: entry data/options, a
snapshot of every active coordinator, runtime control flags, the measured
per-car charge-throughput window, and the integration version from
manifest.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import (
    BatteryScheduleData,
    CarChargingData,
    EaseeData,
    EMSData,
    EnergyManagerConfigEntry,
    PriceData,
)

# No secrets are stored on this config entry (only HA entity IDs and tuning
# numbers) -- kept empty for forward hygiene, matching the standard HA
# diagnostics pattern, should a future config option ever need redaction.
TO_REDACT: list[str] = []


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EnergyManagerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for an Energy Manager config entry."""
    runtime = entry.runtime_data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        # Executor job: manifest.json is read from disk, and blocking I/O
        # in the event loop trips HA's blocking-call detector.
        "integration_version": await hass.async_add_executor_job(
            _read_manifest_version
        ),
        "runtime_flags": {
            "control_enabled": runtime.control_enabled,
            "force_charging": runtime.force_charging,
            "forwarded_platforms": runtime.forwarded_platforms,
        },
        "coordinators": {
            "price": _price_snapshot(runtime.price_coordinator.data),
            "battery": _battery_snapshot(
                runtime.battery_coordinator.data
                if runtime.battery_coordinator is not None
                else None
            ),
            "ems": _dataclass_snapshot(
                runtime.ems_coordinator.data
                if runtime.ems_coordinator is not None
                else None
            ),
            "easee": _dataclass_snapshot(
                runtime.easee_coordinator.data
                if runtime.easee_coordinator is not None
                else None
            ),
            "cars": {
                subentry_id: _car_snapshot(coordinator.data)
                for subentry_id, coordinator in runtime.car_coordinators.items()
            },
        },
        "car_throughput": _car_throughput_snapshot(runtime.easee_coordinator),
    }


def _read_manifest_version() -> str | None:
    """Read the integration version from the bundled manifest.json.

    Returns:
        The "version" field from manifest.json, or None if it cannot be
        read (should never happen for a properly installed integration).
    """
    manifest_path = Path(__file__).parent / "manifest.json"
    try:
        with manifest_path.open(encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, ValueError):
        return None
    return manifest.get("version")


def _price_snapshot(data: PriceData | None) -> dict[str, Any] | None:
    """Summarize PriceData: slot counts, current price, and last update."""
    if data is None:
        return None
    return {
        "today_slot_count": len(data.today),
        "tomorrow_slot_count": len(data.tomorrow),
        "current_price": data.current_price,
        "last_updated": data.last_updated.isoformat() if data.last_updated else None,
    }


def _battery_snapshot(data: BatteryScheduleData | None) -> dict[str, Any] | None:
    """Summarize BatteryScheduleData: schedule summary + BATT-15 consumption average."""
    if data is None:
        return None
    return {
        "current_state": data.current_state,
        "total_slot_count": len(data.schedule),
        "charging_slot_count": data.charging_slot_count,
        "discharging_slot_count": data.discharging_slot_count,
        "target_ems_mode": data.target_ems_mode,
        "next_charging_slot": data.next_charging_slot,
        "next_discharging_slot": data.next_discharging_slot,
        "solar_forecast_used": data.solar_forecast_used,
        "mean_consumption_kw": round(data.mean_consumption_kw, 3),
        "consumption_sample_count": data.consumption_sample_count,
        "last_calculated": data.last_calculated.isoformat(),
    }


def _car_snapshot(data: CarChargingData | None) -> dict[str, Any] | None:
    """Summarize CarChargingData (not the full per-slot schedule)."""
    if data is None:
        return None
    return {
        "car_name": data.car_name,
        "current_action": data.current_action,
        "total_slot_count": len(data.schedule),
        "charging_slot_count": data.charging_slot_count,
        "energy_needed_kwh": round(data.energy_needed_kwh, 2),
        "hours_needed": round(data.hours_needed, 1),
        "current_soc": data.current_soc,
        "target_soc": data.target_soc,
        "is_preliminary": data.is_preliminary,
        "home_and_plugged": data.home_and_plugged,
        "fallback_mode": data.fallback_mode,
        "phase_capability": data.phase_capability,
        "max_charge_power_kw": data.max_charge_power_kw,
        "learned_power_kw": data.learned_power_kw,
        "planning_power_kw": data.planning_power_kw,
        "last_calculated": data.last_calculated.isoformat(),
    }


def _car_throughput_snapshot(easee_coordinator: Any) -> dict[str, Any] | None:
    """Summarize the measured per-car charge-throughput window.

    The learner lives on EaseeCoordinator, so this is None on an install
    without the EV module or without a charger status entity. Read behind
    a defensive getattr rather than an isinstance check: diagnostics is
    the one handler that must still assemble when a coordinator is a
    stub, half-set-up, or predates the learner.

    Args:
        easee_coordinator: The EaseeCoordinator, or None when absent.

    Returns:
        The learner's JSON-safe snapshot (committed samples per car and
        phase bucket plus the in-flight segment), or None when there is
        no learner to read. This is what makes an otherwise opaque
        planning estimate auditable from a downloaded diagnostics file.
    """
    learner = getattr(easee_coordinator, "_throughput_learner", None)
    if learner is None:
        return None
    return learner.snapshot()


def _dataclass_snapshot(data: EMSData | EaseeData | None) -> dict[str, Any] | None:
    """Return a full-fidelity, JSON-safe snapshot of an EMSData/EaseeData instance."""
    if data is None:
        return None
    return _jsonify(asdict(data))


def _jsonify(value: Any) -> Any:
    """Recursively convert datetimes to ISO strings for diagnostics JSON."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    return value

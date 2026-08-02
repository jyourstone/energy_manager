"""Tests for merge_detected_with_current() -- the options flow's
"auto-detect only if empty" pre-fill logic (CORE-05) -- plus pure-JSON
translation-key checks for the config/options flow forms.

No voluptuous/homeassistant dependency: this module is intentionally kept
pure so it can be tested directly under the HA stubs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.energy_manager.options_flow_support import (
    merge_detected_with_current,
)


def test_empty_current_uses_detected_values() -> None:
    """When nothing is configured yet, detected values are used as-is."""
    detected = {"soc_entity": "sensor.soc", "battery_power_entity": "sensor.power"}
    current: dict[str, str] = {}

    result = merge_detected_with_current(detected, current)

    assert result == detected


def test_existing_non_empty_value_wins_over_detection() -> None:
    """An already-configured value must never be overridden by auto-detection."""
    detected = {"soc_entity": "sensor.detected_soc"}
    current = {"soc_entity": "sensor.user_chosen_soc"}

    result = merge_detected_with_current(detected, current)

    assert result == {"soc_entity": "sensor.user_chosen_soc"}


def test_empty_string_in_current_falls_back_to_detected() -> None:
    """A cleared/blank field in current options is treated as unconfigured."""
    detected = {"soc_entity": "sensor.detected_soc"}
    current = {"soc_entity": ""}

    result = merge_detected_with_current(detected, current)

    assert result == {"soc_entity": "sensor.detected_soc"}


def test_none_and_empty_list_in_current_fall_back_to_detected() -> None:
    """None and [] are also treated as unconfigured."""
    detected = {"pv_power_entity": "sensor.pv", "excluded_power_entities": ["x"]}
    current = {"pv_power_entity": None, "excluded_power_entities": []}

    result = merge_detected_with_current(detected, current)

    assert result == {
        "pv_power_entity": "sensor.pv",
        "excluded_power_entities": ["x"],
    }


def test_keys_only_in_current_are_preserved() -> None:
    """Numeric/non-detected options (e.g. capacities) pass through untouched."""
    detected: dict[str, str] = {}
    current = {"battery_capacity_kwh": 15.0}

    result = merge_detected_with_current(detected, current)

    assert result == {"battery_capacity_kwh": 15.0}


def test_keys_only_in_detected_are_included() -> None:
    """A newly detected entity not yet present in current options is added."""
    detected = {"forecast_solar_entity": "sensor.solar_forecast"}
    current = {"soc_entity": "sensor.soc"}

    result = merge_detected_with_current(detected, current)

    assert result == {
        "forecast_solar_entity": "sensor.solar_forecast",
        "soc_entity": "sensor.soc",
    }


class TestExportTranslationKeys:
    """BATT-17: export arbitrage fields are present in every translation file.

    Pure JSON check -- keeps strings.json, en.json and sv.json key-synchronized
    for the config wizard's economics step and the options flow's battery step.
    """

    _COMPONENT_DIR = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "energy_manager"
    )
    _EXPORT_KEYS = ("export_spike_threshold", "export_reserve_soc_pct")

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_export_keys_in_all_form_sections(self, filename: str) -> None:
        """Both export keys exist in data and data_description of both steps."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        sections = {
            "config.step.economics.data": content["config"]["step"]["economics"][
                "data"
            ],
            "config.step.economics.data_description": content["config"]["step"][
                "economics"
            ]["data_description"],
            "options.step.battery.data": content["options"]["step"]["battery"][
                "data"
            ],
            "options.step.battery.data_description": content["options"]["step"][
                "battery"
            ]["data_description"],
        }
        for section_name, section in sections.items():
            for key in self._EXPORT_KEYS:
                assert key in section, f"{key} missing in {filename} {section_name}"

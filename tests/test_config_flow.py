"""Tests for the module-scoped `ems` step: field-set selection, value
preservation across module toggles, and translation-key sync across the
three JSON files.

`config_flow.py` is intentionally never imported here. `voluptuous` is not
installed in this test environment (see requirements_test.txt), and even
where it is, the root conftest.py's `_HAStubFinder` (installed whenever
`homeassistant` is not importable, which is the case here and in CI) makes
`class X(MagicMock(), domain="x")` produce a MagicMock *instance*, not a
class -- so `EnergyManagerConfigFlow` and `EnergyManagerOptionsFlow` cannot
be instantiated under these stubs. Routing (`async_step_modules` reaching
`ems` for appliances-only), schema filtering by `key.schema`, the wizard's
`battery_soc_gate_pct` filter, and the detector-call-site wiring all live
in `config_flow.py` and can only be verified by walking the wizard on a
live Home Assistant instance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from custom_components.energy_manager.const import (
    CONF_ASSUMED_LOAD_AMPS,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_GATE_PCT,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMS_SELECT_ENTITY,
    CONF_ESS_INCREASE_DELAY,
    CONF_EXCLUDED_POWER_ENTITIES,
    CONF_FUSE_RATING_AMPS,
    CONF_FUSE_SAFETY_BUFFER_AMPS,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_MAX_ESS_CHARGE_AMPS,
    CONF_PV_POWER_ENTITY,
    CONF_SENSOR_FAIL_BEHAVIOR,
    DEFAULT_ASSUMED_LOAD_AMPS,
    DEFAULT_SENSOR_FAIL_BEHAVIOR,
)
from custom_components.energy_manager.options_flow_support import (
    FIELD_DEFAULTS,
    apply_step_input,
    ems_step_fields,
    merge_detected_with_current,
)

# (battery, ev, appliances) -- the 7 non-empty module combinations.
_COMBOS = [
    (True, False, False),
    (False, True, False),
    (False, False, True),
    (True, True, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),
]

# ems_step_fields() only takes (battery, ev) -- appliances never changes
# the field set, so the four combinations of the two flags cover every
# code path.
_BATTERY_EV_PAIRS = [
    (False, False),
    (True, False),
    (False, True),
    (True, True),
]

_SHARED_EMS_FIELDS = (
    CONF_FUSE_RATING_AMPS,
    CONF_FUSE_SAFETY_BUFFER_AMPS,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_SENSOR_FAIL_BEHAVIOR,
    CONF_ASSUMED_LOAD_AMPS,
)
_INVERTER_FIELDS = (
    CONF_EMS_SELECT_ENTITY,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_MAX_ESS_CHARGE_AMPS,
    CONF_ESS_INCREASE_DELAY,
)
_CONSUMPTION_FIELDS = (
    CONF_PV_POWER_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_EXCLUDED_POWER_ENTITIES,
)

# A fully-configured entry: every key the ems step can ever show, none of
# them at its FIELD_DEFAULTS value except sensor_fail_behavior (which has
# only two legal values).
_POPULATED_EMS_OPTIONS: dict[str, Any] = {
    CONF_FUSE_RATING_AMPS: 25.0,
    CONF_FUSE_SAFETY_BUFFER_AMPS: 2.0,
    CONF_GRID_POWER_ENTITY: "sensor.grid_power",
    CONF_GRID_PHASE_A_ENTITY: "sensor.grid_phase_a",
    CONF_GRID_PHASE_B_ENTITY: "sensor.grid_phase_b",
    CONF_GRID_PHASE_C_ENTITY: "sensor.grid_phase_c",
    CONF_BATTERY_POWER_ENTITY: "sensor.battery_power",
    CONF_SENSOR_FAIL_BEHAVIOR: DEFAULT_SENSOR_FAIL_BEHAVIOR,
    CONF_ASSUMED_LOAD_AMPS: 12.0,
    CONF_PV_POWER_ENTITY: "sensor.pv_power",
    CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
    CONF_EXCLUDED_POWER_ENTITIES: ["sensor.water_heater"],
    CONF_EMS_SELECT_ENTITY: "select.sigen_mode",
    CONF_CHARGE_LIMIT_ENTITY: "number.sigen_charge_limit",
    CONF_DISCHARGE_LIMIT_ENTITY: "number.sigen_discharge_limit",
    CONF_MAX_ESS_CHARGE_AMPS: 25.0,
    CONF_ESS_INCREASE_DELAY: 240.0,
}


class TestEmsStepFields:
    """ems_step_fields() must return exactly the fields the modules consume."""

    @pytest.mark.parametrize("battery,ev,appliances", _COMBOS)
    def test_shared_fields_present_for_every_combination(
        self, battery: bool, ev: bool, appliances: bool
    ) -> None:
        """The 9 always-shown fuse/grid fields appear for every module combination."""
        fields = set(ems_step_fields(battery, ev))
        assert set(_SHARED_EMS_FIELDS) <= fields

    @pytest.mark.parametrize("battery,ev", _BATTERY_EV_PAIRS)
    def test_inverter_fields_only_with_battery(self, battery: bool, ev: bool) -> None:
        """Inverter control fields are present iff the battery module is on."""
        fields = set(ems_step_fields(battery, ev))
        for field in _INVERTER_FIELDS:
            assert (field in fields) == battery

    def test_consumption_fields_hidden_for_appliances_only(self) -> None:
        """PV/house-consumption/excluded fields need battery or ev -- appliances
        alone does not unlock them (F4: appliances derive surplus from signed
        grid export, not PV)."""
        assert set(ems_step_fields(False, False)).isdisjoint(_CONSUMPTION_FIELDS)
        for battery, ev in ((True, False), (False, True), (True, True)):
            fields = set(ems_step_fields(battery, ev))
            assert set(_CONSUMPTION_FIELDS) <= fields

    def test_appliances_only_covers_every_option_appliance_coordinator_reads(
        self,
    ) -> None:
        """Regression pin for F4's field set: every option
        ApplianceCoordinator.__init__ reads (coordinator.py:3719-3760 -- the
        four grid keys, battery power, fuse rating, safety buffer, sensor
        fail behavior and assumed load) is a subset of the appliances-only
        ems field set."""
        appliance_coordinator_keys = {
            CONF_GRID_PHASE_A_ENTITY,
            CONF_GRID_PHASE_B_ENTITY,
            CONF_GRID_PHASE_C_ENTITY,
            CONF_GRID_POWER_ENTITY,
            CONF_BATTERY_POWER_ENTITY,
            CONF_FUSE_RATING_AMPS,
            CONF_FUSE_SAFETY_BUFFER_AMPS,
            CONF_SENSOR_FAIL_BEHAVIOR,
            CONF_ASSUMED_LOAD_AMPS,
        }
        assert appliance_coordinator_keys <= set(ems_step_fields(False, False))

    @pytest.mark.parametrize("battery,ev", _BATTERY_EV_PAIRS)
    def test_no_duplicate_fields(self, battery: bool, ev: bool) -> None:
        """ems_step_fields() never returns the same field twice."""
        fields = ems_step_fields(battery, ev)
        assert len(fields) == len(set(fields))


class TestValuePreservation:
    """apply_step_input() must never write a field it did not show."""

    def test_hidden_field_keeps_stored_value(self) -> None:
        """A field hidden for this module combo survives apply_step_input untouched."""
        stored = {
            CONF_EMS_SELECT_ENTITY: "select.sigen",
            CONF_MAX_ESS_CHARGE_AMPS: 25.0,
            CONF_FUSE_RATING_AMPS: 25.0,
        }
        fields = ems_step_fields(battery_enabled=False, ev_enabled=True)

        apply_step_input(
            stored, {CONF_FUSE_RATING_AMPS: 20.0, CONF_GRID_POWER_ENTITY: ""}, fields
        )

        assert stored[CONF_EMS_SELECT_ENTITY] == "select.sigen"
        assert stored[CONF_MAX_ESS_CHARGE_AMPS] == 25.0

    def test_shown_entity_field_cleared_becomes_empty_string(self) -> None:
        """A shown entity field absent from user_input (cleared in the UI) is written as ""."""
        stored = {CONF_GRID_POWER_ENTITY: "sensor.old_grid_power"}
        fields = ems_step_fields(battery_enabled=False, ev_enabled=False)

        apply_step_input(stored, {}, fields)

        assert stored[CONF_GRID_POWER_ENTITY] == ""

    def test_shown_numeric_field_absent_falls_back_to_default(self) -> None:
        """A shown numeric field absent from user_input falls back to its DEFAULT_*."""
        stored = {CONF_ASSUMED_LOAD_AMPS: 30.0}
        fields = ems_step_fields(battery_enabled=False, ev_enabled=False)

        apply_step_input(stored, {}, fields)

        assert stored[CONF_ASSUMED_LOAD_AMPS] == DEFAULT_ASSUMED_LOAD_AMPS

    def test_hidden_numeric_field_is_not_reset_to_default(self) -> None:
        """Explicit inverse of the previous test: a HIDDEN numeric field is
        never reset to its default, even though it is absent from user_input."""
        stored = {CONF_MAX_ESS_CHARGE_AMPS: 25.0}
        fields = ems_step_fields(battery_enabled=False, ev_enabled=False)

        apply_step_input(stored, {}, fields)

        assert stored[CONF_MAX_ESS_CHARGE_AMPS] == 25.0

    def test_excluded_entities_default_is_not_aliased(self) -> None:
        """Two independent cleared submissions must not share the same list
        object -- EMSCoordinator keeps the stored list by reference
        (coordinator.py:1739 `... or []`, no copy)."""
        fields = ems_step_fields(battery_enabled=True, ev_enabled=False)
        store_a: dict[str, Any] = {}
        store_b: dict[str, Any] = {}

        apply_step_input(store_a, {}, fields)
        apply_step_input(store_b, {}, fields)
        store_a[CONF_EXCLUDED_POWER_ENTITIES].append("sensor.injected")

        assert store_b[CONF_EXCLUDED_POWER_ENTITIES] == []
        assert FIELD_DEFAULTS[CONF_EXCLUDED_POWER_ENTITIES] == []

    def test_toggle_battery_off_then_on_round_trip_is_lossless(self) -> None:
        """Disabling the battery module, saving, then re-enabling it must not
        lose the tuned inverter values -- and merge_detected_with_current()
        must still pre-fill them from the surviving stored values."""
        original_options = _POPULATED_EMS_OPTIONS
        stored = dict(original_options)
        inverter_fields = (
            CONF_EMS_SELECT_ENTITY,
            CONF_CHARGE_LIMIT_ENTITY,
            CONF_DISCHARGE_LIMIT_ENTITY,
            CONF_MAX_ESS_CHARGE_AMPS,
            CONF_ESS_INCREASE_DELAY,
        )

        # Pass 1 -- battery off, ev on: the inverter fields are hidden, so
        # only the visible fields (pre-filled, unchanged, plus the fuse
        # rating the user actually edits) are submitted.
        fields_off = ems_step_fields(battery_enabled=False, ev_enabled=True)
        prefilled_off = merge_detected_with_current({}, stored)
        submission_off = {field: prefilled_off[field] for field in fields_off}
        submission_off[CONF_FUSE_RATING_AMPS] = 30.0
        apply_step_input(stored, submission_off, fields_off)

        for field in inverter_fields:
            assert stored[field] == original_options[field]

        # Pass 2 -- battery re-enabled: the ems step re-shows the inverter
        # fields, pre-filled from the values that survived pass 1, and the
        # user submits that pre-filled form unchanged.
        fields_on = ems_step_fields(battery_enabled=True, ev_enabled=True)
        prefilled_on = merge_detected_with_current({}, stored)
        for field in inverter_fields:
            assert prefilled_on[field] == original_options[field]

        submission_on = {field: prefilled_on[field] for field in fields_on}
        apply_step_input(stored, submission_on, fields_on)

        for field in inverter_fields:
            assert stored[field] == original_options[field]

    @pytest.mark.parametrize("battery,ev,appliances", _COMBOS)
    def test_shared_fuse_fields_survive_every_module_combination(
        self, battery: bool, ev: bool, appliances: bool
    ) -> None:
        """From a fully-configured entry, re-submitting whichever fields the
        combination shows leaves every stored key untouched -- neither the
        9 always-shown fuse/grid keys (a regression guard against
        ems_step_fields() gating one of them behind a module flag) nor the
        keys this combination hides."""
        stored = dict(_POPULATED_EMS_OPTIONS)
        fields = ems_step_fields(battery, ev)

        apply_step_input(stored, {field: stored[field] for field in fields}, fields)

        for field in _SHARED_EMS_FIELDS:
            assert stored[field] == _POPULATED_EMS_OPTIONS[field]
        assert stored == _POPULATED_EMS_OPTIONS

    def test_field_defaults_covers_every_field_ems_step_can_show(self) -> None:
        """FIELD_DEFAULTS must cover every field any module combination can
        show, or apply_step_input() raises KeyError."""
        shown: set[str] = set()
        for battery, ev in _BATTERY_EV_PAIRS:
            shown |= set(ems_step_fields(battery, ev))

        assert shown <= set(FIELD_DEFAULTS)


class TestFlowTranslationKeys:
    """Pure-JSON checks that the moved keys are translated on the ems steps
    and gone from the donor steps, in all three files. Mirrors the style of
    TestExportTranslationKeys in tests/test_options_flow_support.py."""

    _COMPONENT_DIR = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "energy_manager"
    )
    _ALL_EMS_FIELDS = tuple(
        sorted(
            set(ems_step_fields(True, True))
            | set(ems_step_fields(True, False))
            | set(ems_step_fields(False, True))
            | set(ems_step_fields(False, False))
        )
    )

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_every_ems_field_is_translated(self, filename: str) -> None:
        """Every field any module combination can show has a non-empty
        translation in both config.step.ems and options.step.ems."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        for flow in ("config", "options"):
            step = content[flow]["step"]["ems"]
            for field in self._ALL_EMS_FIELDS:
                assert step["data"].get(field), (
                    f"{field} missing in {filename} {flow}.step.ems.data"
                )
                assert step["data_description"].get(field), (
                    f"{field} missing in {filename} {flow}.step.ems.data_description"
                )

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_moved_fields_removed_from_donor_steps(self, filename: str) -> None:
        """battery_power_entity leaves the battery step; house_consumption_entity
        and excluded_power_entities leave the ev step -- in both flows."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        for flow in ("config", "options"):
            battery_step = content[flow]["step"]["battery"]
            for section in ("data", "data_description"):
                assert CONF_BATTERY_POWER_ENTITY not in battery_step.get(
                    section, {}
                )

            ev_step = content[flow]["step"]["ev"]
            for section in ("data", "data_description"):
                assert CONF_HOUSE_CONSUMPTION_ENTITY not in ev_step.get(section, {})
                assert CONF_EXCLUDED_POWER_ENTITIES not in ev_step.get(section, {})

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_battery_soc_gate_stays_in_wizard_ev_step(self, filename: str) -> None:
        """battery_soc_gate_pct is untouched -- still conditionally shown in
        the wizard ev step (mirrors test_options_flow_support.py:194-198,278-281)."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        assert CONF_BATTERY_SOC_GATE_PCT in content["config"]["step"]["ev"]["data"]

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_ems_step_identical_in_both_flows(self, filename: str) -> None:
        """config.step.ems and options.step.ems stay byte-identical. Deliberate
        new invariant: this change edits both steps identically; a future
        change that wants flow-specific wording should delete this test."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        assert content["config"]["step"]["ems"] == content["options"]["step"]["ems"]

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_ems_step_title_is_module_neutral(self, filename: str) -> None:
        """The old battery-flavored title is gone from both flows."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        for flow in ("config", "options"):
            title = content[flow]["step"]["ems"].get("title", "")
            assert title not in ("EMS Control", "EMS-styrning")

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_ems_step_prose_is_module_neutral(self, filename: str) -> None:
        """The step description and the fuse-rating/sensor-fail descriptions
        no longer claim this screen is only about battery charging.

        Matched on word boundaries: the approved SV description names the
        inverter fields as "ladd-/urladdningsgränser" (charge/discharge
        limits), and "laddningsgränser" is a plain substring of
        "urladdningsgränser" -- a naive `in` check would flag that
        legitimate compound word.
        """
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        forbidden = (
            "battery charging",
            "charging limits",
            "batteriladdning",
            "laddningsgränser",
        )
        for flow in ("config", "options"):
            step = content[flow]["step"]["ems"]
            text = "\n".join(
                [
                    step.get("description", ""),
                    step["data_description"].get("fuse_rating_amps", ""),
                    step["data_description"].get("sensor_fail_behavior", ""),
                ]
            )
            for phrase in forbidden:
                pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
                assert not re.search(pattern, text), (
                    f"{phrase!r} found in {filename} {flow}.step.ems prose"
                )

        block_label = content["selector"]["sensor_fail_behavior"]["options"]["block"]
        assert "charging" not in block_label
        assert "laddning" not in block_label

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_repair_issue_text_names_a_reachable_step(self, filename: str) -> None:
        """The three repairs.issues descriptions point at the renamed screen,
        not the old 'EMS options' name."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        for key in (
            "fuse_sensor_fallback",
            "charge_limit_wrong_domain",
            "discharge_limit_wrong_domain",
        ):
            description = content["issues"][key]["description"]
            assert "EMS options" not in description
            assert "EMS-inställningar" not in description

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_finish_steps_mention_appliance_subentry(self, filename: str) -> None:
        """Both finish screens name the 'Add appliance' button."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        needle = (
            "Lägg till apparat" if filename == "translations/sv.json" else "Add appliance"
        )
        for step_id in ("finish", "finish_basic"):
            description = content["config"]["step"][step_id]["description"]
            assert needle in description

    def test_strings_and_en_translations_identical(self) -> None:
        """strings.json and translations/en.json must stay byte-identical."""
        strings = json.loads(
            (self._COMPONENT_DIR / "strings.json").read_text(encoding="utf-8")
        )
        en = json.loads(
            (self._COMPONENT_DIR / "translations/en.json").read_text(encoding="utf-8")
        )
        assert strings == en

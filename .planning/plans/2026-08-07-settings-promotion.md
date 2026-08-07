# Settings Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote 12 tuning settings from the config/options flow to live number entities so changing them no longer requires a full config-entry reload.

**Architecture:** Follow the existing "seed-then-entity" pattern already used by the export knobs (`ExportSpikeThreshold` / `ExportReserveSoc` in `number.py`): a `RestoreNumber` entity restores its last state, falls back to the options/subentry seed written by the initial wizard, pushes its value onto a public coordinator attribute, and requests a refresh. The initial config-flow wizard keeps the fields as one-time seeds; the options flow and appliance subentry *reconfigure* stop showing them. Coordinators consume the attribute live instead of re-reading `entry.options` / rebuilding at `__init__`.

**Tech Stack:** Home Assistant custom integration (Python), voluptuous config flows, `RestoreNumber`, pytest with HA stubs (root `conftest.py`), ruff 0.16.0.

## Global Constraints

- Lint: `uvx ruff@0.16.0 check custom_components tests` must pass. NEVER bulk-reformat unrelated code.
- Tests: `python3 -m pytest tests/ -q` must stay green (baseline ~610 tests).
- Branch `feat/settings-promotion` off `main`. This repo merges PRs with a merge commit. No version bump (release flow bumps `manifest.json` on tag).
- Match the existing entity style in `custom_components/energy_manager/number.py` exactly (docstrings, `_attr_*` ordering, restore pattern).
- The config-flow WIZARD steps (`EnergyManagerConfigFlow.async_step_battery` / `async_step_ev` / appliance subentry `async_step_user`) keep all promoted fields — they are the seeds. Only the OPTIONS flow steps and the appliance `async_step_reconfigure` drop fields.
- `strings.json`, `translations/en.json`, `translations/sv.json` must stay key-synchronized (enforced by parity tests in `tests/test_options_flow_support.py`).
- Every removed options-flow field's stored value remains in `entry.options` untouched (the options flow copies `dict(entry.options)` and only overwrites keys present in the form), so entity seeds keep working for existing installs.

## The 12 settings

| # | Setting (conf key) | Today | New owner |
|---|---|---|---|
| 1 | `priority` (appliance) | subentry data, reload | per-appliance number |
| 2 | `on_threshold_pct` (appliance) | subentry data, reload | per-appliance number |
| 3 | `off_threshold_pct` (appliance) | subentry data, reload | per-appliance number |
| 4 | `on_sustain_minutes` (appliance) | subentry data, reload | per-appliance number |
| 5 | `off_sustain_minutes` (appliance) | subentry data, reload | per-appliance number |
| 6 | `charge_buffer_pct` | options, read per refresh | hub number |
| 7 | `production_factor` | options, read per refresh | hub number |
| 8 | `estimated_charge_power_kw` | options, read per refresh | hub number |
| 9 | `peak_gap_hours` | options, read per refresh | hub number |
| 10 | `max_grid_charge_power_kw` | options, cached in `EaseeCoordinator.__init__` | hub number |
| 11 | `solar_start_threshold_kw` | options, cached in `EaseeCoordinator.__init__` | hub number |
| 12 | `battery_soc_gate_pct` | options, cached in `EaseeCoordinator.__init__` | hub number |

Task order is deliberate: appliances first (VVB onboarding is the next project step and needs reload-free tuning during the dry-run soak), battery second (cheapest — values already read per refresh), EV last.

---

### Task 1: Appliance tuning → per-appliance number entities

**Files:**
- Modify: `custom_components/energy_manager/appliance_controller.py` (add `clamp_hysteresis`, import `replace`)
- Modify: `custom_components/energy_manager/coordinator.py` (`ApplianceCoordinator.set_appliance_tuning`, import `replace` + `clamp_hysteresis`)
- Modify: `custom_components/energy_manager/number.py` (base class + 5 entities + setup loop)
- Modify: `custom_components/energy_manager/__init__.py:184-195` (`_get_enabled_platforms`: NUMBER for appliances)
- Modify: `custom_components/energy_manager/config_flow.py:1948-2054` (appliance `async_step_reconfigure`)
- Modify: `custom_components/energy_manager/strings.json`, `translations/en.json`, `translations/sv.json`
- Test: `tests/test_appliance_controller.py`, `tests/test_options_flow_support.py`

**Interfaces:**
- Consumes: frozen `ApplianceConfig` dataclass (`appliance_controller.py:85`), `ApplianceCoordinator._configs: list[ApplianceConfig]` (`coordinator.py:3751`), `ApplianceEntity` base (`entity.py:128`, signature `__init__(coordinator, entry, subentry)` exposing `self._subentry_id`).
- Produces: `clamp_hysteresis(config: ApplianceConfig) -> ApplianceConfig` (pure, in `appliance_controller.py`); `ApplianceCoordinator.set_appliance_tuning(subentry_id: str, **updates: int) -> None`; entity classes `AppliancePriority`, `ApplianceOnThreshold`, `ApplianceOffThreshold`, `ApplianceOnSustain`, `ApplianceOffSustain`.

- [ ] **Step 1: Write failing tests for `clamp_hysteresis`**

Append to `tests/test_appliance_controller.py` (reuse the file's existing `ApplianceConfig` construction helper if one exists; otherwise add this one):

```python
# ---------------------------------------------------------------------------
# clamp_hysteresis() -- APPL-05 invariant enforced at the consume side
# ---------------------------------------------------------------------------


def _tuning_config(on_pct: int, off_pct: int) -> ApplianceConfig:
    return ApplianceConfig(
        subentry_id="sub1",
        name="VVB",
        switch_entity="switch.vvb",
        rated_power_w=3000,
        phases=1,
        priority=5,
        on_threshold_pct=on_pct,
        off_threshold_pct=off_pct,
        on_sustain_s=300,
        off_sustain_s=900,
        min_on_s=0,
        min_off_s=0,
    )


def test_clamp_hysteresis_returns_same_config_when_band_valid() -> None:
    config = _tuning_config(on_pct=100, off_pct=40)
    assert clamp_hysteresis(config) is config


def test_clamp_hysteresis_forces_off_below_on() -> None:
    config = _tuning_config(on_pct=80, off_pct=90)
    clamped = clamp_hysteresis(config)
    assert clamped.off_threshold_pct == 79
    assert clamped.on_threshold_pct == 80


def test_clamp_hysteresis_equal_thresholds_also_clamped() -> None:
    config = _tuning_config(on_pct=100, off_pct=100)
    assert clamp_hysteresis(config).off_threshold_pct == 99
```

Add `clamp_hysteresis` to the file's existing `from custom_components.energy_manager.appliance_controller import (...)` block.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_appliance_controller.py -q`
Expected: FAIL with `ImportError: cannot import name 'clamp_hysteresis'`

- [ ] **Step 3: Implement `clamp_hysteresis` in `appliance_controller.py`**

Change the dataclasses import at the top to include `replace`, then add below the `ApplianceConfig` definition:

```python
def clamp_hysteresis(config: ApplianceConfig) -> ApplianceConfig:
    """Return config with the off threshold forced below the on threshold.

    An inverted hysteresis band (off >= on) guarantees perpetual on/off
    cycling -- exactly what APPL-05 exists to prevent. The subentry add
    flow validates this, but the per-appliance number entities set each
    threshold independently, so the invariant is enforced again here at
    the consume side (same approach as the amp-delay clamp in
    EaseeCoordinator).
    """
    if config.off_threshold_pct >= config.on_threshold_pct:
        return replace(config, off_threshold_pct=config.on_threshold_pct - 1)
    return config
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_appliance_controller.py -q`
Expected: PASS

- [ ] **Step 5: Add `ApplianceCoordinator.set_appliance_tuning`**

In `coordinator.py`: add `replace` to the dataclasses import at the top of the file, add `clamp_hysteresis` to the existing `from .appliance_controller import (...)` block, then add this method to `ApplianceCoordinator` (after `__init__`):

```python
    def set_appliance_tuning(self, subentry_id: str, **updates: int) -> None:
        """Apply a live tuning override from a per-appliance number entity.

        Replaces the matching frozen ApplianceConfig snapshot in place --
        list position is preserved, so priority ties keep their insertion
        order. The hysteresis band is re-clamped after every update
        because the number entities set the on/off thresholds
        independently of each other.
        """
        for index, config in enumerate(self._configs):
            if config.subentry_id != subentry_id:
                continue
            updated = replace(config, **updates)
            clamped = clamp_hysteresis(updated)
            if clamped is not updated:
                _LOGGER.warning(
                    "Appliance %s: off threshold (%s%%) must stay below on"
                    " threshold (%s%%); clamping off threshold to %s%%",
                    config.name,
                    updated.off_threshold_pct,
                    updated.on_threshold_pct,
                    clamped.off_threshold_pct,
                )
            self._configs[index] = clamped
            return
```

- [ ] **Step 6: Add the number entities**

In `number.py`, extend the `.const` import block with `CONF_APPLIANCE_OFF_SUSTAIN_MINUTES`, `CONF_APPLIANCE_OFF_THRESHOLD_PCT`, `CONF_APPLIANCE_ON_SUSTAIN_MINUTES`, `CONF_APPLIANCE_ON_THRESHOLD_PCT`, `CONF_APPLIANCE_PRIORITY`, `DEFAULT_APPLIANCE_OFF_SUSTAIN_MINUTES`, `DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT`, `DEFAULT_APPLIANCE_ON_SUSTAIN_MINUTES`, `DEFAULT_APPLIANCE_ON_THRESHOLD_PCT`, `DEFAULT_APPLIANCE_PRIORITY`, `SUBENTRY_TYPE_APPLIANCE`; add `ApplianceEntity` to the `.entity` import. Append at the end of the file:

```python
class ApplianceTuningNumber(ApplianceEntity, RestoreNumber):
    """Base for per-appliance tuning numbers (APPL-05 knobs).

    Restores the last value, falling back to the subentry-data seed the
    add-appliance wizard wrote. Every change is pushed onto the shared
    ApplianceCoordinator via set_appliance_tuning() and applies on the
    next 30s tick -- no config-entry reload.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False

    # Set by subclasses.
    _conf_key: str
    _tuning_field: str
    _default_value: int
    # Multiplier applied when pushing to the coordinator (minutes -> s).
    _scale: int = 1

    def __init__(
        self,
        coordinator,
        entry: EnergyManagerConfigEntry,
        subentry,
    ) -> None:
        """Initialize the tuning entity.

        Args:
            coordinator: The ApplianceCoordinator shared by all appliances.
            entry: The config entry this entity belongs to.
            subentry: The appliance subentry with appliance-specific
                configuration (seed source).
        """
        super().__init__(coordinator, entry, subentry)
        self._seed = int(subentry.data.get(self._conf_key, self._default_value))
        self._attr_unique_id = f"{subentry.subentry_id}_{self._conf_key}"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the subentry seed."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = round(last_data.native_value)
        else:
            self._attr_native_value = self._seed
        self._push_value()
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the tuning value and apply it on the next tick.

        Args:
            value: New value in the entity's native unit (whole numbers).
        """
        self._attr_native_value = round(value)
        self.async_write_ha_state()
        self._push_value()
        await self.coordinator.async_request_refresh()

    def _push_value(self) -> None:
        self.coordinator.set_appliance_tuning(
            self._subentry_id,
            **{self._tuning_field: int(self._attr_native_value) * self._scale},
        )


class AppliancePriority(ApplianceTuningNumber):
    """Allocation priority for the surplus pool (1 = highest)."""

    _attr_translation_key = "appliance_priority"
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1
    _conf_key = CONF_APPLIANCE_PRIORITY
    _tuning_field = "priority"
    _default_value = DEFAULT_APPLIANCE_PRIORITY


class ApplianceOnThreshold(ApplianceTuningNumber):
    """Turn ON when the surplus pool reaches this share of rated power."""

    _attr_translation_key = "appliance_on_threshold"
    _attr_native_min_value = 50
    _attr_native_max_value = 300
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _conf_key = CONF_APPLIANCE_ON_THRESHOLD_PCT
    _tuning_field = "on_threshold_pct"
    _default_value = DEFAULT_APPLIANCE_ON_THRESHOLD_PCT


class ApplianceOffThreshold(ApplianceTuningNumber):
    """Turn OFF when the surplus pool drops below this share of rated power."""

    _attr_translation_key = "appliance_off_threshold"
    _attr_native_min_value = 0
    _attr_native_max_value = 150
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _conf_key = CONF_APPLIANCE_OFF_THRESHOLD_PCT
    _tuning_field = "off_threshold_pct"
    _default_value = DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT


class ApplianceOnSustain(ApplianceTuningNumber):
    """Surplus must persist this long before turning ON."""

    _attr_translation_key = "appliance_on_sustain"
    _attr_native_min_value = 0
    _attr_native_max_value = 720
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _conf_key = CONF_APPLIANCE_ON_SUSTAIN_MINUTES
    _tuning_field = "on_sustain_s"
    _default_value = DEFAULT_APPLIANCE_ON_SUSTAIN_MINUTES
    _scale = 60


class ApplianceOffSustain(ApplianceTuningNumber):
    """Deficit must persist this long before turning OFF."""

    _attr_translation_key = "appliance_off_sustain"
    _attr_native_min_value = 0
    _attr_native_max_value = 720
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _conf_key = CONF_APPLIANCE_OFF_SUSTAIN_MINUTES
    _tuning_field = "off_sustain_s"
    _default_value = DEFAULT_APPLIANCE_OFF_SUSTAIN_MINUTES
    _scale = 60
```

In `async_setup_entry` (`number.py:63`), after the car loop, add:

```python
    # Per-appliance tuning entities (one set per appliance subentry)
    appliance_coordinator = entry.runtime_data.appliance_coordinator
    if appliance_coordinator is not None:
        for subentry_id, subentry in entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_APPLIANCE:
                continue
            async_add_entities(
                [
                    AppliancePriority(appliance_coordinator, entry, subentry),
                    ApplianceOnThreshold(appliance_coordinator, entry, subentry),
                    ApplianceOffThreshold(appliance_coordinator, entry, subentry),
                    ApplianceOnSustain(appliance_coordinator, entry, subentry),
                    ApplianceOffSustain(appliance_coordinator, entry, subentry),
                ],
                config_subentry_id=subentry_id,
            )
```

- [ ] **Step 7: Load the NUMBER platform for appliance-only setups**

In `__init__.py` `_get_enabled_platforms` (line ~184), after the EV block add:

```python
    if entry.options.get(CONF_APPLIANCES_ENABLED):
        if Platform.NUMBER not in platforms:
            platforms.append(Platform.NUMBER)
```

(`CONF_APPLIANCES_ENABLED` is already imported at `__init__.py:19`.)

- [ ] **Step 8: Retire the 5 fields from the appliance reconfigure flow**

In `config_flow.py` appliance `async_step_reconfigure` (line ~1948):
1. Delete the `off >= on` validation block (the `errors[CONF_APPLIANCE_OFF_THRESHOLD_PCT] = "off_must_be_below_on"` branch and its comment) — both fields are leaving this form; the invariant now lives in `clamp_hysteresis`. Keep the `errors` dict wiring (it still feeds `async_show_form`).
2. Change the save call so wiring-only edits keep the stored tuning seeds:

```python
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=user_input[CONF_APPLIANCE_NAME],
                    data={**existing_data, **user_input},
                )
```

3. Delete the five `vol.Optional(...)` schema blocks for `CONF_APPLIANCE_PRIORITY`, `CONF_APPLIANCE_ON_THRESHOLD_PCT`, `CONF_APPLIANCE_OFF_THRESHOLD_PCT`, `CONF_APPLIANCE_ON_SUSTAIN_MINUTES`, `CONF_APPLIANCE_OFF_SUSTAIN_MINUTES` from the *reconfigure* schema only. The add-flow (`async_step_user`, line ~1848) keeps all fields and its validation — it writes the seeds.
4. Remove any imports that only the deleted blocks used (check with ruff — the add flow still uses all five conf keys, so imports likely all stay).

- [ ] **Step 9: Translations**

In `strings.json` under `entity.number`, add (mirror the exact structure of the existing `battery_max_soc_target` entry):

```json
"appliance_priority": {"name": "Priority"},
"appliance_on_threshold": {"name": "On threshold"},
"appliance_off_threshold": {"name": "Off threshold"},
"appliance_on_sustain": {"name": "On sustain time"},
"appliance_off_sustain": {"name": "Off sustain time"}
```

Copy the same keys into `translations/en.json`. In `translations/sv.json` use:

```json
"appliance_priority": {"name": "Prioritet"},
"appliance_on_threshold": {"name": "Tillslagströskel"},
"appliance_off_threshold": {"name": "Frånslagströskel"},
"appliance_on_sustain": {"name": "Uthållighet före tillslag"},
"appliance_off_sustain": {"name": "Uthållighet före frånslag"}
```

In all three files, delete the keys `priority`, `on_threshold_pct`, `off_threshold_pct`, `on_sustain_minutes`, `off_sustain_minutes` from `config_subentries.appliance.step.reconfigure.data` (and from its `data_description` if present). Leave `config_subentries.appliance.step.user` untouched.

- [ ] **Step 10: Write the translation-parity test (fails before Step 9, passes after)**

Append to `tests/test_options_flow_support.py`, modeled on the existing `TestExportTranslationKeys` class in the same file:

```python
class TestPromotedTuningTranslationKeys:
    """Tuning settings promoted from flow forms to number entities.

    Mirrors TestExportTranslationKeys: the promoted entities stay
    translated in every file, and the retired form fields never come
    back. The config-flow WIZARD steps deliberately keep these fields as
    one-time seeds -- only the options flow and the appliance
    reconfigure form retire them.
    """

    _COMPONENT_DIR = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "energy_manager"
    )
    _ENTITY_KEYS = (
        ("number", "appliance_priority"),
        ("number", "appliance_on_threshold"),
        ("number", "appliance_off_threshold"),
        ("number", "appliance_on_sustain"),
        ("number", "appliance_off_sustain"),
    )
    _RETIRED_APPLIANCE_RECONFIGURE_KEYS = (
        "priority",
        "on_threshold_pct",
        "off_threshold_pct",
        "on_sustain_minutes",
        "off_sustain_minutes",
    )

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_promoted_entity_names_translated(self, filename: str) -> None:
        """Every promoted entity has a translated name in each file."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        for domain, key in self._ENTITY_KEYS:
            block = content["entity"][domain]
            assert key in block, f"{key} missing in {filename} entity.{domain}"
            assert block[key].get("name"), f"{key} name empty in {filename}"

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_appliance_reconfigure_fields_retired(self, filename: str) -> None:
        """Tuning fields stay out of the appliance reconfigure form."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        step = content["config_subentries"]["appliance"]["step"]["reconfigure"]
        for section in ("data", "data_description"):
            for key in self._RETIRED_APPLIANCE_RECONFIGURE_KEYS:
                assert key not in step.get(section, {}), (
                    f"{key} should be retired from {filename}"
                )

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_appliance_add_wizard_keeps_seed_fields(self, filename: str) -> None:
        """The add-appliance wizard keeps the tuning fields as seeds."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        step = content["config_subentries"]["appliance"]["step"]["user"]
        for key in self._RETIRED_APPLIANCE_RECONFIGURE_KEYS:
            assert key in step["data"], f"{key} seed missing in {filename}"
```

- [ ] **Step 11: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q`
Expected: PASS (baseline + new tests)
Run: `uvx ruff@0.16.0 check custom_components tests`
Expected: clean

- [ ] **Step 12: Commit**

```bash
git add custom_components/energy_manager tests
git commit -m "feat: promote appliance tuning to per-appliance number entities"
```

---

### Task 2: Battery scheduling tuning → hub number entities

**Files:**
- Modify: `custom_components/energy_manager/coordinator.py` (`BatteryScheduleCoordinator.__init__` ~line 568 and refresh step 5 ~lines 715-730)
- Modify: `custom_components/energy_manager/number.py` (4 entities, register in battery block of `async_setup_entry`)
- Modify: `custom_components/energy_manager/config_flow.py:1175-1294` (OPTIONS flow `async_step_battery` only)
- Modify: `strings.json`, `translations/en.json`, `translations/sv.json`
- Test: `tests/test_options_flow_support.py`

**Interfaces:**
- Consumes: `BatteryScheduleCoordinator` public-attr pattern (`self.export_spike_threshold` at `coordinator.py:568`); constants `CONF_CHARGE_BUFFER_PCT`, `CONF_PRODUCTION_FACTOR`, `CONF_ESTIMATED_CHARGE_POWER_KW`, `CONF_PEAK_GAP_HOURS` and their `DEFAULT_*`/`MIN_*`/`MAX_*` from `const.py` (defaults: 20.0 %, 0.8, 6.0 kW, 2.0 h).
- Produces: coordinator attributes `charge_buffer_pct`, `production_factor`, `estimated_charge_power_kw`, `peak_gap_hours` (floats); entity classes `BatteryChargeBuffer`, `BatteryProductionFactor`, `BatteryEstimatedChargePower`, `BatteryPeakGapHours`.

- [ ] **Step 1: Extend the parity test (fails first)**

In `TestPromotedTuningTranslationKeys` (Task 1), extend `_ENTITY_KEYS` with:

```python
        ("number", "battery_charge_buffer"),
        ("number", "battery_production_factor"),
        ("number", "battery_estimated_charge_power"),
        ("number", "battery_peak_gap_hours"),
```

Add a class constant and test:

```python
    _RETIRED_OPTIONS_BATTERY_KEYS = (
        "charge_buffer_pct",
        "production_factor",
        "estimated_charge_power_kw",
        "peak_gap_hours",
    )

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_options_battery_fields_retired(self, filename: str) -> None:
        """Battery tuning fields stay out of the options-flow battery form."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        step = content["options"]["step"]["battery"]
        for section in ("data", "data_description"):
            for key in self._RETIRED_OPTIONS_BATTERY_KEYS:
                assert key not in step.get(section, {}), (
                    f"{key} should be retired from {filename}"
                )
        # The config-flow wizard step keeps them as seeds.
        wizard = content["config"]["step"]["battery"]
        for key in self._RETIRED_OPTIONS_BATTERY_KEYS:
            assert key in wizard["data"], f"{key} seed missing in {filename}"
```

Run: `python3 -m pytest tests/test_options_flow_support.py -q`
Expected: FAIL (entity keys missing, options fields still present)

- [ ] **Step 2: Coordinator — public attrs, consumed each refresh**

In `BatteryScheduleCoordinator.__init__`, directly after the `self.export_reserve_soc_pct` line (~569), add:

```python
        # BATT-15 tuning knobs -- set by number entities (seeded from the
        # wizard's options on first add), read on every refresh below.
        self.charge_buffer_pct: float = DEFAULT_CHARGE_BUFFER_PCT
        self.production_factor: float = DEFAULT_PRODUCTION_FACTOR
        self.estimated_charge_power_kw: float = DEFAULT_ESTIMATED_CHARGE_POWER_KW
        self.peak_gap_hours: float = DEFAULT_PEAK_GAP_HOURS
```

In refresh step 5 (~lines 718-730), replace the four `self.config_entry.options.get(...)` reads with:

```python
        charge_buffer_pct = self.charge_buffer_pct
        production_factor = self.production_factor
        estimated_charge_power_kw = self.estimated_charge_power_kw
        peak_gap_hours = self.peak_gap_hours
```

Keep the `battery_capacity_kwh` options read unchanged (capacity stays flow-owned). Update the step-5 comment ("Read battery capacity from entry options and BATT-15 tuning from the number-entity knobs"). Remove the now-unused `CONF_CHARGE_BUFFER_PCT`, `CONF_PRODUCTION_FACTOR`, `CONF_ESTIMATED_CHARGE_POWER_KW`, `CONF_PEAK_GAP_HOURS` imports from `coordinator.py` ONLY if ruff reports them unused (other coordinators in the same file may use them — verify, don't assume).

- [ ] **Step 3: Number entities**

In `number.py`, extend the `.const` import with `CONF_CHARGE_BUFFER_PCT`, `CONF_ESTIMATED_CHARGE_POWER_KW`, `CONF_PEAK_GAP_HOURS`, `CONF_PRODUCTION_FACTOR`, `DEFAULT_CHARGE_BUFFER_PCT`, `DEFAULT_ESTIMATED_CHARGE_POWER_KW`, `DEFAULT_PEAK_GAP_HOURS`, `DEFAULT_PRODUCTION_FACTOR`, `MAX_CHARGE_BUFFER_PCT`, `MAX_ESTIMATED_CHARGE_POWER_KW`, `MAX_PEAK_GAP_HOURS`, `MAX_PRODUCTION_FACTOR`, `MIN_CHARGE_BUFFER_PCT`, `MIN_ESTIMATED_CHARGE_POWER_KW`, `MIN_PEAK_GAP_HOURS`, `MIN_PRODUCTION_FACTOR`. Add the first class in full:

```python
class BatteryChargeBuffer(EnergyManagerEntity, RestoreNumber):
    """Number entity for the BATT-15 charge buffer percentage.

    Extra energy planned on top of the forecast deficit when sizing
    cheap-hour grid charging. Value persists across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "battery_charge_buffer"
    _attr_native_min_value = MIN_CHARGE_BUFFER_PCT
    _attr_native_max_value = MAX_CHARGE_BUFFER_PCT
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"

    _default_value = DEFAULT_CHARGE_BUFFER_PCT

    def __init__(
        self,
        coordinator: BatteryScheduleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the charge buffer entity."""
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charge_buffer_pct"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_CHARGE_BUFFER_PCT, self._default_value
                )
            )
        self.coordinator.charge_buffer_pct = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the charge buffer and trigger schedule recalculation.

        Args:
            value: New charge buffer in percent.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.charge_buffer_pct = value
        await self.coordinator.async_request_refresh()
```

Create `BatteryProductionFactor`, `BatteryEstimatedChargePower`, `BatteryPeakGapHours` by copying `BatteryChargeBuffer` verbatim and changing ONLY the fields in this table (docstrings adjusted to the row's description; everything else identical):

| Class | translation_key | min / max / step | unit | unique_id suffix | seed conf key | default | coordinator attr | description |
|---|---|---|---|---|---|---|---|---|
| `BatteryProductionFactor` | `battery_production_factor` | `MIN_PRODUCTION_FACTOR` / `MAX_PRODUCTION_FACTOR` / `0.05` | (none — omit the unit attr) | `_production_factor` | `CONF_PRODUCTION_FACTOR` | `DEFAULT_PRODUCTION_FACTOR` | `production_factor` | Multiplier applied to the raw solar forecast before planning |
| `BatteryEstimatedChargePower` | `battery_estimated_charge_power` | `MIN_ESTIMATED_CHARGE_POWER_KW` / `MAX_ESTIMATED_CHARGE_POWER_KW` / `0.1` | `kW` | `_estimated_charge_power_kw` | `CONF_ESTIMATED_CHARGE_POWER_KW` | `DEFAULT_ESTIMATED_CHARGE_POWER_KW` | `estimated_charge_power_kw` | Assumed charge power when converting energy need to slot count |
| `BatteryPeakGapHours` | `battery_peak_gap_hours` | `MIN_PEAK_GAP_HOURS` / `MAX_PEAK_GAP_HOURS` / `0.5` | `h` | `_peak_gap_hours` | `CONF_PEAK_GAP_HOURS` | `DEFAULT_PEAK_GAP_HOURS` | `peak_gap_hours` | Minimum gap between price peaks treated as separate peaks |

Register all four in `async_setup_entry`'s battery block, after `BatteryMaxSocTarget(battery_coordinator, entry)`:

```python
            BatteryChargeBuffer(battery_coordinator, entry),
            BatteryProductionFactor(battery_coordinator, entry),
            BatteryEstimatedChargePower(battery_coordinator, entry),
            BatteryPeakGapHours(battery_coordinator, entry),
```

- [ ] **Step 4: Retire the 4 fields from the OPTIONS flow battery step**

In `config_flow.py` `EnergyManagerOptionsFlow.async_step_battery` (line ~1175):
1. Delete the four `self._options[CONF_...] = user_input.get(...)` assignments for `CONF_CHARGE_BUFFER_PCT`, `CONF_PRODUCTION_FACTOR`, `CONF_ESTIMATED_CHARGE_POWER_KW`, `CONF_PEAK_GAP_HOURS` (lines ~1190-1200).
2. Delete their four `vol.Optional(...)` schema blocks (lines ~1232-1281).
3. Do NOT touch the wizard `async_step_battery` (line 359) or `async_step_finish*` — seeds stay.
4. Keep the `MIN_*`/`MAX_*`/`DEFAULT_*`/`CONF_*` imports — the wizard step still uses them.

- [ ] **Step 5: Translations**

`strings.json` + `translations/en.json`, under `entity.number`:

```json
"battery_charge_buffer": {"name": "Charge buffer"},
"battery_production_factor": {"name": "Solar production factor"},
"battery_estimated_charge_power": {"name": "Estimated charge power"},
"battery_peak_gap_hours": {"name": "Minimum peak gap"}
```

`translations/sv.json`:

```json
"battery_charge_buffer": {"name": "Laddbuffert"},
"battery_production_factor": {"name": "Produktionsfaktor sol"},
"battery_estimated_charge_power": {"name": "Beräknad laddeffekt"},
"battery_peak_gap_hours": {"name": "Minsta gap mellan pristoppar"}
```

In all three files, delete `charge_buffer_pct`, `production_factor`, `estimated_charge_power_kw`, `peak_gap_hours` from `options.step.battery.data` and `options.step.battery.data_description`. Leave `config.step.battery` untouched.

- [ ] **Step 6: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q` → PASS.
Run: `uvx ruff@0.16.0 check custom_components tests` → clean.

- [ ] **Step 7: Commit**

```bash
git add custom_components/energy_manager tests
git commit -m "feat: promote battery scheduling tuning to number entities"
```

---

### Task 3: EV charging tuning → hub number entities

**Files:**
- Modify: `custom_components/energy_manager/coordinator.py` (`EaseeCoordinator.__init__` lines ~3218, 3251, 3266; use sites ~3385, 3388, 3392)
- Modify: `custom_components/energy_manager/number.py` (3 entities + setup block)
- Modify: `custom_components/energy_manager/config_flow.py:1473-1726` (OPTIONS flow `async_step_ev` only)
- Modify: `strings.json`, `translations/en.json`, `translations/sv.json`
- Test: `tests/test_options_flow_support.py`

**Interfaces:**
- Consumes: `EaseeCoordinator` private attrs `_grid_power_cap_kw` / `_solar_start_threshold_kw` / `_battery_soc_gate_pct` (verified: no other use sites in the component or tests); `entry.runtime_data.easee_coordinator` (`EnergyManagerData`, `coordinator.py:4100`); constants `MIN/MAX_MAX_GRID_CHARGE_POWER_KW` (1.0/22.0), `MIN/MAX_SOLAR_START_THRESHOLD_KW` (0.0/10.0), `MIN/MAX_BATTERY_SOC_GATE_PCT` (0.0/100.0) and matching `CONF_*`/`DEFAULT_*` (12.0, 1.5, 100.0).
- Produces: public attrs `EaseeCoordinator.grid_power_cap_kw`, `.solar_start_threshold_kw`, `.battery_soc_gate_pct`; entity classes `EvMaxGridChargePower`, `EvSolarStartThreshold`, `EvBatterySocGate`.

- [ ] **Step 1: Extend the parity test (fails first)**

Extend `_ENTITY_KEYS` in `TestPromotedTuningTranslationKeys` with:

```python
        ("number", "ev_max_grid_charge_power"),
        ("number", "ev_solar_start_threshold"),
        ("number", "ev_battery_soc_gate"),
```

Add:

```python
    _RETIRED_OPTIONS_EV_KEYS = (
        "max_grid_charge_power_kw",
        "solar_start_threshold_kw",
        "battery_soc_gate_pct",
    )

    @pytest.mark.parametrize(
        "filename",
        ["strings.json", "translations/en.json", "translations/sv.json"],
    )
    def test_options_ev_fields_retired(self, filename: str) -> None:
        """EV tuning fields stay out of the options-flow EV form."""
        content = json.loads(
            (self._COMPONENT_DIR / filename).read_text(encoding="utf-8")
        )
        step = content["options"]["step"]["ev"]
        for section in ("data", "data_description"):
            for key in self._RETIRED_OPTIONS_EV_KEYS:
                assert key not in step.get(section, {}), (
                    f"{key} should be retired from {filename}"
                )
        # The config-flow wizard step keeps them as seeds.
        wizard = content["config"]["step"]["ev"]
        for key in self._RETIRED_OPTIONS_EV_KEYS:
            assert key in wizard["data"], f"{key} seed missing in {filename}"
```

Run: `python3 -m pytest tests/test_options_flow_support.py -q` → FAIL.

- [ ] **Step 2: Make the three EaseeCoordinator attrs public**

In `coordinator.py`, rename (keeping the existing options-read initialization — it covers the window before the entities restore):
- `self._grid_power_cap_kw` (line ~3218) → `self.grid_power_cap_kw`
- `self._solar_start_threshold_kw` (line ~3251) → `self.solar_start_threshold_kw`
- `self._battery_soc_gate_pct` (line ~3266) → `self.battery_soc_gate_pct`

Update the three use sites in the `ChargerInputs` construction (lines ~3385, 3388, 3392). Verify no other references: `grep -n "_grid_power_cap_kw\|_solar_start_threshold_kw\|_battery_soc_gate_pct" custom_components tests -r` must return nothing.

- [ ] **Step 3: Number entities**

In `number.py`, extend the `.const` import with `CONF_BATTERY_SOC_GATE_PCT`, `CONF_MAX_GRID_CHARGE_POWER_KW`, `CONF_SOLAR_START_THRESHOLD_KW`, `DEFAULT_BATTERY_SOC_GATE_PCT`, `DEFAULT_MAX_GRID_CHARGE_POWER_KW`, `DEFAULT_SOLAR_START_THRESHOLD_KW`, `MAX_BATTERY_SOC_GATE_PCT`, `MAX_MAX_GRID_CHARGE_POWER_KW`, `MAX_SOLAR_START_THRESHOLD_KW`, `MIN_BATTERY_SOC_GATE_PCT`, `MIN_MAX_GRID_CHARGE_POWER_KW`, `MIN_SOLAR_START_THRESHOLD_KW`. Add the first class in full:

```python
class EvMaxGridChargePower(EnergyManagerEntity, RestoreNumber):
    """Number entity for the EV grid-charging power cap.

    Ceiling on total grid power the charger may draw during scheduled
    (price-based) charging. Value persists across restarts and applies
    on the next charger tick -- no reload, so an active session's state
    machine is never torn down.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False
    _attr_translation_key = "ev_max_grid_charge_power"
    _attr_native_min_value = MIN_MAX_GRID_CHARGE_POWER_KW
    _attr_native_max_value = MAX_MAX_GRID_CHARGE_POWER_KW
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kW"

    _default_value = DEFAULT_MAX_GRID_CHARGE_POWER_KW

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the grid charge power cap entity.

        Args:
            coordinator: The EaseeCoordinator running the charger loop.
            entry: The config entry this entity belongs to.
        """
        super().__init__(coordinator, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_max_grid_charge_power_kw"

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup, or use the options seed/default."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = float(
                self._entry.options.get(
                    CONF_MAX_GRID_CHARGE_POWER_KW, self._default_value
                )
            )
        self.coordinator.grid_power_cap_kw = self._attr_native_value
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        """Update the power cap; applies on the next charger tick.

        Args:
            value: New grid charging power cap in kW.
        """
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.grid_power_cap_kw = value
        await self.coordinator.async_request_refresh()
```

Create `EvSolarStartThreshold` and `EvBatterySocGate` by copying `EvMaxGridChargePower` verbatim and changing ONLY these fields (docstrings adjusted to the row's description):

| Class | translation_key | min / max / step | unit | unique_id suffix | seed conf key | default | coordinator attr | description |
|---|---|---|---|---|---|---|---|---|
| `EvSolarStartThreshold` | `ev_solar_start_threshold` | `MIN_SOLAR_START_THRESHOLD_KW` / `MAX_SOLAR_START_THRESHOLD_KW` / `0.1` | `kW` | `_solar_start_threshold_kw` | `CONF_SOLAR_START_THRESHOLD_KW` | `DEFAULT_SOLAR_START_THRESHOLD_KW` | `solar_start_threshold_kw` | Minimum net solar surplus before solar charging starts |
| `EvBatterySocGate` | `ev_battery_soc_gate` | `MIN_BATTERY_SOC_GATE_PCT` / `MAX_BATTERY_SOC_GATE_PCT` / `1` | `%` | `_battery_soc_gate_pct` | `CONF_BATTERY_SOC_GATE_PCT` | `DEFAULT_BATTERY_SOC_GATE_PCT` | `battery_soc_gate_pct` | Minimum house-battery SOC before solar EV charging starts |

In `async_setup_entry`, after the battery block (before the car loop), add:

```python
    # EV charger tuning entities (when the EV module is enabled)
    easee_coordinator = entry.runtime_data.easee_coordinator
    if easee_coordinator is not None:
        async_add_entities([
            EvMaxGridChargePower(easee_coordinator, entry),
            EvSolarStartThreshold(easee_coordinator, entry),
            EvBatterySocGate(easee_coordinator, entry),
        ])
```

- [ ] **Step 4: Retire the 3 fields from the OPTIONS flow EV step**

In `config_flow.py` `EnergyManagerOptionsFlow.async_step_ev` (line ~1473):
1. Delete the three `self._options[CONF_...] = user_input.get(...)` assignments for `CONF_MAX_GRID_CHARGE_POWER_KW`, `CONF_SOLAR_START_THRESHOLD_KW`, `CONF_BATTERY_SOC_GATE_PCT` (lines ~1502-1512).
2. Delete their three `vol.Optional(...)` schema blocks (lines ~1587-1600, ~1644-1656, ~1686-1697).
3. All other EV options fields stay (min/max amps, amp delays, phase switch threshold, solar activation/deactivation delays, emergency margin — deliberately kept flow-owned).
4. The wizard `async_step_ev` (line 624) stays untouched.

- [ ] **Step 5: Translations**

`strings.json` + `translations/en.json`, under `entity.number`:

```json
"ev_max_grid_charge_power": {"name": "Max grid charge power"},
"ev_solar_start_threshold": {"name": "Solar start threshold"},
"ev_battery_soc_gate": {"name": "Battery SOC gate"}
```

`translations/sv.json`:

```json
"ev_max_grid_charge_power": {"name": "Max nätladdningseffekt"},
"ev_solar_start_threshold": {"name": "Tröskel för solladdning"},
"ev_battery_soc_gate": {"name": "Batterinivågräns för solladdning"}
```

In all three files, delete `max_grid_charge_power_kw`, `solar_start_threshold_kw`, `battery_soc_gate_pct` from `options.step.ev.data` and `options.step.ev.data_description`. Leave `config.step.ev` untouched.

- [ ] **Step 6: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q` → PASS.
Run: `uvx ruff@0.16.0 check custom_components tests` → clean.

- [ ] **Step 7: Commit**

```bash
git add custom_components/energy_manager tests
git commit -m "feat: promote EV charging tuning to number entities"
```

---

### Task 4: Documentation

**Files:**
- Modify: `docs/content/reference/entities.md` (add the 12 new number entities)
- Modify: `docs/content/user-guide/home-battery.md`, `docs/content/user-guide/ev-charging.md`, `docs/content/user-guide/solar-appliances.md` (settings moved from options flow to entities)
- Possibly: `README.md`, `docs/content/getting-started/*` (verify via grep)

**Interfaces:**
- Consumes: final entity names from Tasks 1-3 (English translation names above).
- Produces: docs only, no code.

- [ ] **Step 1: Find every doc mention of the 12 settings**

Run: `grep -rn -i "charge buffer\|production factor\|estimated charge power\|peak gap\|max grid charge\|solar start threshold\|soc gate\|on threshold\|off threshold\|sustain\|priority" docs/content README.md`

- [ ] **Step 2: Update the docs**

For each hit that describes one of the 12 settings as an options-flow/reconfigure field, rewrite it to point at the number entity instead ("adjust live via the `number.energy_manager_*` entity — no reload; the setup wizard still asks for an initial value"). Add all 12 entities to the entity tables in `docs/content/reference/entities.md`, following that file's existing table format. Note in each affected user-guide page that changes apply within one coordinator tick (30 s) and persist across restarts.

- [ ] **Step 3: Build check (optional, if mkdocs installed)**

Run: `python3 -m mkdocs build --strict -f docs/mkdocs.yml`
Expected: clean build. If mkdocs is not installed locally, skip — Cloudflare Workers Builds validates on push.

- [ ] **Step 4: Commit**

```bash
git add docs README.md
git commit -m "docs: settings promoted to number entities"
```

---

## Notes for the implementer

- **Why seeds stay in the wizard:** first-time setup should still ask sensible questions; the entity reads the seed once (`options.get` / `subentry.data.get` fallback in `async_added_to_hass`) and owns the value afterwards. This is the repo's established "economics step" pattern.
- **Why existing installs keep their values:** on upgrade the new entities have no restore state, so they fall back to the seed already stored in `entry.options` / subentry data — the currently tuned production values carry over automatically.
- **Deliberately NOT promoted** (audit borderline, decided to keep flow-owned): `amp_increase_delay`/`amp_decrease_delay` (paired safety invariant, clamped in `EaseeCoordinator`), solar activation/deactivation delays, `phase_switch_threshold_kw` (physics constant, wrong value causes contactor wear), `min_on_minutes`/`min_off_minutes` (anti-short-cycling protection).
- **No coordinator-instance tests:** repo convention is pure-function tests under HA stubs; coordinator wiring (attr plumbing) follows the already-proven export-knob pattern and is covered by the translation-parity tests plus live verification.

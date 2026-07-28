# Phase 1: Core Infrastructure + Price Foundation - Research

**Researched:** 2026-02-15
**Domain:** Home Assistant HACS custom integration -- config flow, Nordpool price data, device registry, module architecture
**Confidence:** HIGH

## Summary

Phase 1 delivers the integration skeleton that all future modules build on: HACS-installable package, multi-step config flow wizard with auto-detection, Nordpool price data layer (internal, no visible sensor), hub/sub-device hierarchy, and proper setup/unload/reload lifecycle. The user-locked decisions constrain this to a Nordpool-specific (not generic) price adapter supporting both HACS and native variants, a wizard-style config flow with per-module steps, and an internal-only price data layer with no user-facing price entities.

The technical patterns are well-understood and verified. The multi-step config flow uses HA's standard `async_step_<step_id>` chaining pattern. Auto-detection of installed integrations uses `entity_registry` scanning (proven in the PowerSaver integration). The Nordpool adapter from PowerSaver handles both HACS and native variants and can be ported directly. The hub/sub-device hierarchy uses `via_device` in `DeviceInfo`. Config subentries (HA 2024.12+) handle per-car configuration through `ConfigSubentryFlow`. The `entry.runtime_data` pattern with typed dataclasses replaces the fragile `hass.data[DOMAIN]` dict approach.

**Primary recommendation:** Port the proven PowerSaver Nordpool adapter and config flow patterns, extend with multi-step wizard and module toggle architecture, and use `entry.runtime_data` with a typed dataclass to store coordinators and module state.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Config flow experience
- Multi-step wizard: Step 1 (Price source), Step 2 (Home Battery config), Step 3 (EV config) -- each module gets its own step
- Auto-detected entities shown as pre-filled defaults in input fields -- user can edit before confirming
- If auto-detection finds nothing, show empty fields and let user type entity IDs manually -- no blocking
- Integration name: "Energy Manager"

#### Module toggle design
- Users choose which modules to enable (Home Battery, EV Charging) during setup wizard
- Module toggles also changeable later in options flow (Phase 6 implements full options flow, but architecture should support it)
- EV Charging module supports multi-car from start using subentry pattern -- each car added separately with its own config
- Car names auto-detected from linked integration (Skoda/VW) but editable by user

#### Price data handling
- No separate visible price sensor entity -- price data is an internal-only data layer consumed by other modules
- Users see their existing Nordpool sensor directly for dashboards
- Support both the official HA Nordpool integration AND the HACS Nordpool integration (different entity/attribute structures)
- No generic "price source" abstraction -- Nordpool-specific, supporting both variants
- When tomorrow's prices aren't available yet: empty list (`[]`)
- Raw hourly prices only -- no computed stats (average, min, max) in the price layer
- Price unit: SEK/kWh as-is from Nordpool (pass-through, no conversion)

#### Entity naming & device layout
- Hub + sub-devices hierarchy: top-level "Energy Manager" hub device, with child devices per module (Home Battery, EV Charger, each Car)
- Entity friendly names use proper casing: "Battery Schedule", "Next Charge Slot", "EMS Status"

### Claude's Discretion

#### Module toggles
- What happens to entities when a module is disabled (remove vs mark unavailable)
- Module toggle UI presentation (checkboxes vs other HA-native pattern)

#### Price data
- Internal data structure for hourly prices (optimized for scheduling algorithms)
- Update strategy (event-driven vs polling vs hybrid)
- How to detect which Nordpool variant is installed

#### Entity naming
- Entity ID naming convention (full prefix vs abbreviated)
- Exact device hierarchy implementation

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

## Standard Stack

### Core

| Library / API | Version | Purpose | Why Standard | Confidence |
|---------------|---------|---------|--------------|------------|
| `ConfigFlow` + multi-step | Built-in (HA core) | Wizard-style setup flow | HA standard for user-facing integration config. Steps chain via `async_step_<step_id>` methods. | HIGH (verified via HA dev docs + Context7) |
| `ConfigSubentryFlow` | Built-in (HA 2024.12+) | Per-car configuration | Each car added as a subentry under the main config entry. Supports add/reconfigure/delete. Modern replacement for options-flow-based sub-items. | HIGH (verified via HA architecture discussion + dev docs) |
| `DataUpdateCoordinator` | Built-in (HA core) | Price data polling | Handles retry logic, `ConfigEntryNotReady`, debouncing, entity update coordination. One coordinator for price data. | HIGH (verified via HA dev docs + Context7) |
| `async_track_state_change_event` | Built-in (HA core) | Nordpool price change detection | Triggers immediate price data refresh when Nordpool sensor updates (e.g., tomorrow's prices arrive ~13:00 CET). | HIGH (verified via HA dev docs) |
| `entry.runtime_data` | Built-in (HA 2024.x+) | Typed runtime data storage | Replaces `hass.data[DOMAIN]` dict soup. Use `type EnergyManagerConfigEntry = ConfigEntry[EnergyManagerData]` pattern. | HIGH (verified via Context7 + HA dev docs blog post) |
| `voluptuous` + `homeassistant.helpers.selector` | Built-in (HA dependency) | Config flow form schemas | `EntitySelector`, `SelectSelector`, `TextSelector` render proper UI widgets. Required by HA for all config/options schemas. | HIGH |
| `entity_registry` (via `er.async_get`) | Built-in (HA core) | Auto-detection of installed integrations | Scan registry for entities by platform/domain to find Nordpool, SigenStor, Easee, Skoda/VW sensors. Proven pattern in PowerSaver. | HIGH (verified via PowerSaver source code) |
| `device_registry` (via `dr.async_get`) | Built-in (HA core) | Hub + sub-device creation | Create "Energy Manager" hub device, link module devices via `via_device`. | HIGH (verified via Context7 + HA dev docs) |
| `OptionsFlowWithReload` | Built-in (HA core) | Options flow with auto-reload | Subclass from this instead of `OptionsFlow` to automatically reload integration when options change. | HIGH (verified via HA dev docs) |
| `helpers.storage.Store` | Built-in (HA core) | Persistent state | Not needed in Phase 1, but architecture should support it for future phases. | HIGH |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `ruff` | Latest (0.8+) | Linting + formatting | Single tool replacing flake8+black+isort. Use from day one. |
| `pytest-homeassistant-custom-component` | Latest | HA test fixtures | Provides `hass` fixture, `MockConfigEntry`. Use for integration tests. |
| `pytest` + `pytest-asyncio` | Latest | Test runner + async support | All tests need async fixtures. |
| GitHub Actions (`hacs/action` + `home-assistant/actions/hassfest`) | Latest | CI validation | Run HACS validation + manifest validation on every PR. |

### Alternatives Considered

| Instead of | Could Use | Why Not |
|------------|-----------|---------|
| `entry.runtime_data` | `hass.data[DOMAIN]` dict | Dict soup has no type safety. `runtime_data` is the modern pattern, verified in HA dev docs. |
| `ConfigSubentryFlow` for cars | Multi-step options flow with dynamic sections | Subentries are purpose-built for "one main config + N sub-items." Cleaner UX, independent lifecycle per car. |
| `OptionsFlowWithReload` | Manual `entry.add_update_listener` | `OptionsFlowWithReload` eliminates boilerplate listener registration. |
| Scanning entity_registry for auto-detect | `hass.config_entries.async_entries(domain)` for config entries | Entity registry scan catches both HACS and native variants by looking at platform + attributes. Config entry scan only works for native. Use both approaches. |

## Architecture Patterns

### Recommended Project Structure (Phase 1)

```
custom_components/energy_manager/
  __init__.py              # Entry setup: create runtime_data, forward platforms, register listeners
  config_flow.py           # ConfigFlow (multi-step wizard) + stub OptionsFlow + ConfigSubentryFlow
  const.py                 # DOMAIN, config keys, module IDs, defaults
  coordinator.py           # PriceCoordinator (DataUpdateCoordinator for Nordpool)
  entity.py                # EnergyManagerEntity base class (CoordinatorEntity + common device_info)
  manifest.json            # Integration manifest
  strings.json             # UI translation strings
  translations/
    en.json                # English translations (copy of strings.json)
  nordpool_adapter.py      # Nordpool variant detection + price fetching (port from PowerSaver)
  auto_detect.py           # Integration auto-detection (find Nordpool, SigenStor, Easee, Skoda/VW)
```

Note: No sensor.py, binary_sensor.py, or other platform files in Phase 1 because there are no user-visible entities in this phase (price data is internal-only per user decision).

### Pattern 1: Multi-Step Config Flow Wizard

**What:** Config flow with progressive steps where each step collects different data and can be conditionally shown based on module selection.
**When to use:** Integration with multiple optional modules that each need configuration.
**Verified:** HA dev docs confirm steps chain via `return await self.async_step_<next>()`

```python
# Source: HA Developer Docs config_entries_config_flow_handler + PowerSaver patterns
class EnergyManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Nordpool sensor selection."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Validate Nordpool sensor exists
            nordpool_entity = user_input[CONF_NORDPOOL_SENSOR]
            nordpool_type = detect_nordpool_type(self.hass, nordpool_entity)
            if nordpool_type == "unknown":
                errors["base"] = "nordpool_not_found"
            else:
                self._data[CONF_NORDPOOL_SENSOR] = nordpool_entity
                self._data[CONF_NORDPOOL_TYPE] = nordpool_type
                return await self.async_step_modules()

        # Auto-detect Nordpool sensors
        all_sensors = find_all_nordpool_sensors(self.hass)
        # Build schema with auto-detected defaults...
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_modules(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Enable/disable modules."""
        if user_input is not None:
            self._data[CONF_BATTERY_ENABLED] = user_input.get(CONF_BATTERY_ENABLED, False)
            self._data[CONF_EV_ENABLED] = user_input.get(CONF_EV_ENABLED, False)
            if self._data[CONF_BATTERY_ENABLED]:
                return await self.async_step_battery()
            if self._data[CONF_EV_ENABLED]:
                return await self.async_step_ev()
            return self._create_entry()

        # Show checkboxes for module selection
        return self.async_show_form(step_id="modules", data_schema=modules_schema)

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Home Battery configuration (auto-detected SigenStor entities)."""
        if user_input is not None:
            self._data.update(user_input)
            if self._data.get(CONF_EV_ENABLED):
                return await self.async_step_ev()
            return self._create_entry()

        # Auto-detect SigenStor entities, pre-fill defaults
        detected = auto_detect_sigenstor(self.hass)
        return self.async_show_form(step_id="battery", data_schema=battery_schema)

    async def async_step_ev(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: EV Charging configuration (auto-detected Easee entities)."""
        if user_input is not None:
            self._data.update(user_input)
            return self._create_entry()

        detected = auto_detect_easee(self.hass)
        return self.async_show_form(step_id="ev", data_schema=ev_schema)

    def _create_entry(self) -> ConfigFlowResult:
        """Create the config entry with collected data."""
        # Separate immutable data from mutable options
        data = {
            CONF_NORDPOOL_SENSOR: self._data[CONF_NORDPOOL_SENSOR],
            CONF_NORDPOOL_TYPE: self._data[CONF_NORDPOOL_TYPE],
        }
        options = {
            CONF_BATTERY_ENABLED: self._data.get(CONF_BATTERY_ENABLED, False),
            CONF_EV_ENABLED: self._data.get(CONF_EV_ENABLED, False),
            # Battery config, EV config...
        }
        return self.async_create_entry(
            title="Energy Manager",
            data=data,
            options=options,
        )
```

### Pattern 2: Typed Runtime Data with Dataclass

**What:** Store coordinators, module state, and cleanup callbacks in a typed dataclass on `entry.runtime_data` instead of `hass.data[DOMAIN]`.
**When to use:** Always -- this is the modern HA pattern (2024.x+).
**Verified:** HA dev docs blog post (2024-04-30) + Context7 examples

```python
# Source: HA Developer Docs blog/2024-04-30-store-runtime-data-inside-config-entry
from dataclasses import dataclass, field

@dataclass
class EnergyManagerData:
    """Runtime data for the Energy Manager integration."""
    price_coordinator: PriceCoordinator
    modules_enabled: dict[str, bool] = field(default_factory=dict)
    # Future phases add: battery_coordinator, ems_coordinator, etc.

# Type alias for typed config entry
type EnergyManagerConfigEntry = ConfigEntry[EnergyManagerData]

async def async_setup_entry(
    hass: HomeAssistant, entry: EnergyManagerConfigEntry
) -> bool:
    price_coordinator = PriceCoordinator(hass, entry)
    await price_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EnergyManagerData(
        price_coordinator=price_coordinator,
        modules_enabled={
            "battery": entry.options.get(CONF_BATTERY_ENABLED, False),
            "ev": entry.options.get(CONF_EV_ENABLED, False),
        },
    )

    # Forward platforms conditionally based on enabled modules
    platforms = _get_enabled_platforms(entry)
    if platforms:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True
```

### Pattern 3: Hub + Sub-Device Hierarchy

**What:** Create a top-level "Energy Manager" hub device, then child devices per module linked via `via_device`.
**When to use:** When integration manages multiple logical device types.
**Verified:** Context7 device registry docs + DeviceInfo TypedDict definition

```python
# Source: HA Developer Docs device_registry_index + Context7

# Hub device (created in __init__.py during setup)
from homeassistant.helpers import device_registry as dr

device_registry = dr.async_get(hass)
hub_device = device_registry.async_get_or_create(
    config_entry_id=entry.entry_id,
    identifiers={(DOMAIN, entry.entry_id)},
    name="Energy Manager",
    manufacturer="Energy Manager",
    model="Hub",
    entry_type=dr.DeviceEntryType.SERVICE,  # SERVICE type for virtual/hub devices
)

# Sub-device example (created by entities via device_info property)
class BatteryScheduleSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_battery")},
            name="Home Battery",
            manufacturer="Energy Manager",
            model="Battery Module",
            via_device=(DOMAIN, self._entry_id),  # Links to hub
        )
```

### Pattern 4: Auto-Detection via Entity Registry

**What:** Scan HA entity registry to find installed integrations by platform name and entity attributes.
**When to use:** Config flow needs to auto-populate entity selectors.
**Verified:** PowerSaver nordpool_adapter.py (production code)

```python
# Source: PowerSaver nordpool_adapter.py (proven pattern)
from homeassistant.helpers import entity_registry as er

def find_all_nordpool_sensors(hass: HomeAssistant) -> list[tuple[str, str, str]]:
    """Find HACS and native Nordpool sensors."""
    registry = er.async_get(hass)
    found = []

    # HACS Nordpool: platform="nordpool", has raw_today attribute
    for entity_entry in registry.entities.values():
        if entity_entry.domain != "sensor" or entity_entry.platform != "nordpool":
            continue
        state = hass.states.get(entity_entry.entity_id)
        if state is not None and state.attributes.get("raw_today") is not None:
            found.append((entity_entry.entity_id, "hacs", ...))

    # Native Nordpool: config entries with domain "nordpool"
    for config_entry in hass.config_entries.async_entries("nordpool"):
        entity_entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
        for entity_entry in entity_entries:
            if entity_entry.unique_id and entity_entry.unique_id.endswith("-current_price"):
                found.append((entity_entry.entity_id, "native", ...))

    return found

def find_sigenstor_entities(hass: HomeAssistant) -> dict[str, str]:
    """Find SigenStor battery entities by scanning for the integration."""
    registry = er.async_get(hass)
    detected = {}
    # Scan for entities with platform containing "sigen" or from sigen config entries
    for config_entry in hass.config_entries.async_entries():
        if "sigen" in config_entry.domain.lower():
            entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
            for entity in entities:
                # Match by unique_id patterns or entity_id patterns
                if "battery_state_of_charge" in (entity.entity_id or ""):
                    detected["soc_entity"] = entity.entity_id
                # ... more pattern matching
    return detected
```

### Pattern 5: Internal-Only Price Data Coordinator

**What:** PriceCoordinator fetches and stores Nordpool prices in coordinator.data but creates NO user-visible entities. Other coordinators (future phases) access price data via `entry.runtime_data.price_coordinator.data`.
**When to use:** Shared data layer consumed by downstream modules.
**Verified:** PowerSaver coordinator pattern + user decision (no visible price sensor)

```python
# Source: Adapted from PowerSaver coordinator.py + project CONTEXT.md decisions
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class PriceSlot:
    """A single hourly price slot."""
    start: datetime    # UTC timezone-aware
    end: datetime      # UTC timezone-aware
    price: float       # SEK/kWh (pass-through from Nordpool)

@dataclass
class PriceData:
    """Internal price data structure optimized for scheduling."""
    today: list[PriceSlot] = field(default_factory=list)
    tomorrow: list[PriceSlot] = field(default_factory=list)
    current_price: float | None = None
    last_updated: datetime | None = None

class PriceCoordinator(DataUpdateCoordinator[PriceData]):
    """Fetches Nordpool prices as internal data layer (no entities)."""

    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER,
            name="Energy Manager Prices",
            config_entry=entry,
            update_interval=timedelta(minutes=5),
            always_update=False,  # Only notify listeners when data actually changes
        )
        self._nordpool_entity = entry.data[CONF_NORDPOOL_SENSOR]
        self._nordpool_type = entry.data[CONF_NORDPOOL_TYPE]

    async def _async_setup(self) -> None:
        """Register Nordpool state change listener on first refresh."""
        self.config_entry.async_on_unload(
            async_track_state_change_event(
                self.hass,
                [self._nordpool_entity],
                self._on_nordpool_update,
            )
        )

    @callback
    def _on_nordpool_update(self, event: Event) -> None:
        """React immediately when Nordpool sensor updates (e.g., tomorrow's prices arrive)."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> PriceData:
        """Fetch prices from Nordpool adapter."""
        raw_today, raw_tomorrow = await async_get_prices(
            self.hass, self._nordpool_entity, self._nordpool_type
        )
        if not raw_today:
            raise UpdateFailed("No price data available for today")

        today = [PriceSlot(start=..., end=..., price=...) for slot in raw_today]
        tomorrow = [PriceSlot(...) for slot in raw_tomorrow] if raw_tomorrow else []

        state = self.hass.states.get(self._nordpool_entity)
        current_price = float(state.state) if state and state.state not in ("unavailable", "unknown") else None

        return PriceData(
            today=today,
            tomorrow=tomorrow,
            current_price=current_price,
            last_updated=dt_util.utcnow(),
        )
```

### Pattern 6: Config Entry Lifecycle (Setup / Unload / Reload)

**What:** Every resource allocated in `async_setup_entry` must be cleaned up in `async_unload_entry`. Use `entry.async_on_unload()` to register cleanup at creation time.
**When to use:** Always -- this is mandatory for HA integrations.
**Verified:** HA dev docs config_entries_index + Context7

```python
# Source: HA Developer Docs + Context7 examples
async def async_setup_entry(
    hass: HomeAssistant, entry: EnergyManagerConfigEntry
) -> bool:
    """Set up Energy Manager from a config entry."""
    price_coordinator = PriceCoordinator(hass, entry)
    await price_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EnergyManagerData(
        price_coordinator=price_coordinator,
        modules_enabled={...},
    )

    # Register hub device
    _register_hub_device(hass, entry)

    # Forward platforms
    platforms = _get_enabled_platforms(entry)
    if platforms:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)

    return True

async def async_unload_entry(
    hass: HomeAssistant, entry: EnergyManagerConfigEntry
) -> bool:
    """Unload Energy Manager config entry."""
    platforms = _get_enabled_platforms(entry)
    if platforms:
        return await hass.config_entries.async_unload_platforms(entry, platforms)
    return True

async def async_migrate_entry(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> bool:
    """Migrate old config entry format."""
    _LOGGER.debug(
        "Migrating configuration from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )
    if config_entry.version > 1:
        return False  # Downgrade from future version
    # Migration logic here
    return True
```

### Anti-Patterns to Avoid

- **`hass.data[DOMAIN]` dict soup:** Use `entry.runtime_data` with typed dataclass instead. No type safety, no IDE support, easy to leak memory.
- **Blocking calls in async context:** All I/O must be `await`-ed. Never use `time.sleep()` -- use `asyncio.sleep()` or `async_call_later()`.
- **`hass.states.async_set()` for integration entities:** Always use proper Entity subclasses with `async_setup_entry` platform pattern.
- **`async_forward_entry_setup` (singular):** Deprecated. Use `async_forward_entry_setups` (plural).
- **Hardcoded entity names without `has_entity_name`:** Must use `has_entity_name = True` with `translation_key` for proper device name prefixing.
- **Skipping `async_unload_entry`:** Causes memory leaks, ghost entities on reload.
- **No `VERSION` / `MINOR_VERSION` on ConfigFlow:** Makes future config migration impossible.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Nordpool data fetching | Custom HTTP client or sensor scraping | `nordpool_adapter.py` from PowerSaver | Proven adapter handles both HACS (`raw_today` attribute) and native (service call `get_prices_for_date`) variants. Edge cases already solved. |
| Integration auto-detection | String matching on entity IDs | `entity_registry.async_get()` + `config_entries.async_entries()` | Registry-based detection is the HA-blessed approach. Entity IDs can be renamed by users. |
| Periodic data updates | `asyncio.create_task()` with manual timers | `DataUpdateCoordinator` | Handles retry, debouncing, error states, entity notification, `ConfigEntryNotReady`. |
| Config form UI widgets | Raw `vol.Schema` with string fields | HA Selectors (`EntitySelector`, `SelectSelector`, `NumberSelector`) | Selectors render proper HA-native UI widgets (entity pickers, dropdowns, sliders). |
| Cleanup on unload | Manual tracking of listeners/timers | `entry.async_on_unload()` callbacks | Guarantees cleanup runs even if unload is triggered by error. |
| Config entry versioning | Ad-hoc migration checks | `ConfigFlow.VERSION` + `async_migrate_entry()` | HA's built-in migration framework handles version comparison and triggers migration. |

**Key insight:** Phase 1 has ZERO external dependencies. Everything is built on HA core APIs. The only "custom" code is the Nordpool adapter (ported from PowerSaver) and the auto-detection logic.

## Common Pitfalls

### Pitfall 1: Nordpool Variant Mismatch
**What goes wrong:** Code assumes HACS Nordpool (reads `raw_today` attribute) but user has native HA Nordpool, or vice versa. Native uses a service call `nordpool.get_prices_for_date` and returns prices in Currency/MWh (must divide by 1000 for kWh).
**Why it happens:** Two completely different APIs for the same data. HACS Nordpool stores hourly prices as sensor attributes (`raw_today`, `raw_tomorrow` as lists of `{start, end, value}` dicts with price in kWh). Native Nordpool requires a service call with config_entry_id and returns prices grouped by area in MWh.
**How to avoid:** Use the PowerSaver `nordpool_adapter.py` which already handles both variants. Store `nordpool_type` ("hacs" or "native") in `entry.data` during config flow. Detection logic: (1) check if entity has `raw_today` attribute -> HACS, (2) check if entity platform is "nordpool" in entity registry and has config entry -> native.
**Warning signs:** Prices show as `None`, empty schedules, or prices 1000x too high (MWh vs kWh confusion).
**Confidence:** HIGH (verified via PowerSaver production code + official Nord Pool docs)

### Pitfall 2: Config Flow Step Data Loss
**What goes wrong:** Multi-step config flow loses data between steps because each step handler only receives its own step's `user_input`, not previous steps' data.
**Why it happens:** `user_input` in `async_step_X` only contains form data from step X. Data from step 1 is not automatically passed to step 3.
**How to avoid:** Store accumulated data in `self._data` dict (instance variable on the flow handler). Each step merges its input into `self._data`. Final step reads everything from `self._data` to create the entry. This is the standard HA multi-step pattern.
**Warning signs:** Config entry is missing expected keys. Integration fails on reload because required config is absent.
**Confidence:** HIGH (verified via HA example config flows)

### Pitfall 3: Module Toggle Without Platform Forwarding Guard
**What goes wrong:** Module is disabled but its platform entities still get set up because `async_forward_entry_setups` was called with all platforms regardless of module state.
**Why it happens:** Platform forwarding happens once during setup. If module toggles are not checked before forwarding, disabled modules still create entities.
**How to avoid:** Build the platform list dynamically based on `entry.options`:
```python
platforms = []
if entry.options.get(CONF_BATTERY_ENABLED):
    platforms.extend([Platform.SENSOR, Platform.NUMBER])
if entry.options.get(CONF_EV_ENABLED):
    platforms.extend([Platform.SWITCH])
```
On reload (triggered by options change via `OptionsFlowWithReload`), old platforms are unloaded and new platform set is forwarded.
**Warning signs:** Entities appear for disabled modules. Errors about missing coordinator data for disabled modules.
**Confidence:** HIGH (documented HA pattern)

### Pitfall 4: Missing `_async_setup` for Coordinator Listeners
**What goes wrong:** State change listener for Nordpool sensor is registered in `__init__` instead of `_async_setup`, causing the listener to be registered before the coordinator's first refresh.
**Why it happens:** `DataUpdateCoordinator._async_setup()` is called once during `async_config_entry_first_refresh()`. It is the correct place to register listeners that should exist for the coordinator's lifetime.
**How to avoid:** Override `_async_setup()` in the coordinator to register state change listeners. Use `self.config_entry.async_on_unload()` to ensure cleanup.
**Warning signs:** Listener fires before coordinator has data. Race condition on startup.
**Confidence:** HIGH (verified via PowerSaver coordinator.py which uses this pattern)

### Pitfall 5: Subentry Config Not Persisted Correctly
**What goes wrong:** Subentry data (car configuration) is stored in `entry.options` instead of the subentry's own data store. When a car subentry is deleted, its config lingers in options. When two cars are configured, their settings overwrite each other.
**Why it happens:** Subentries are relatively new (HA 2024.12+). Developers used to the options-flow-only approach may store subentry data in the wrong place.
**How to avoid:** Each `ConfigSubentryFlow` creates its own subentry with `self.async_create_entry(data=car_data)`. Access via `entry.subentries` (a dict of subentry_id -> ConfigSubentry). Each subentry has its own `.data` dict.
**Warning signs:** Adding a second car overwrites the first car's config. Deleting a car does not remove its settings. Car entities survive subentry deletion.
**Confidence:** MEDIUM (subentry API verified via docs, but limited real-world examples of this specific failure mode)

### Pitfall 6: Auto-Detection Fails Silently When Integration Not Loaded
**What goes wrong:** Config flow tries to auto-detect Nordpool/SigenStor entities, but the integration is still loading or was just installed. Entity registry query returns nothing even though the integration is configured.
**Why it happens:** When HA starts, integrations load in dependency order. The Energy Manager config flow may run before Nordpool entities are registered. Also, entities might not have state yet (state is None, not "unavailable").
**How to avoid:** (1) In config flow, scan both entity registry AND config entries (config entries exist even if entities haven't loaded yet). (2) Handle the case where auto-detection finds nothing gracefully -- show empty fields and let user type manually. (3) In `async_setup_entry`, use `ConfigEntryNotReady` if the Nordpool sensor is not available, triggering HA's automatic retry mechanism.
**Warning signs:** Auto-detection shows no results even though the integration is installed. Setup fails on restart but works after manual reload.
**Confidence:** HIGH (common HA integration issue, documented in setup_failures docs)

## Code Examples

### strings.json Structure for Multi-Step Config Flow

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Price Source",
        "description": "Select your Nordpool electricity price sensor.",
        "data": {
          "nordpool_sensor": "Nordpool sensor"
        },
        "data_description": {
          "nordpool_sensor": "Select the Nordpool sensor that provides your electricity prices."
        }
      },
      "modules": {
        "title": "Modules",
        "description": "Choose which energy management modules to enable.",
        "data": {
          "battery_enabled": "Home Battery",
          "ev_enabled": "EV Charging"
        },
        "data_description": {
          "battery_enabled": "Enable home battery charge/discharge scheduling.",
          "ev_enabled": "Enable EV charging optimization."
        }
      },
      "battery": {
        "title": "Home Battery",
        "description": "Configure your home battery system. Entities are auto-detected from SigenStor.",
        "data": {
          "soc_entity": "Battery SOC sensor",
          "battery_power_entity": "Battery power sensor"
        }
      },
      "ev": {
        "title": "EV Charging",
        "description": "Configure your EV charger. Entities are auto-detected from Easee.",
        "data": {
          "charger_status_entity": "Charger status sensor",
          "charger_power_entity": "Charger power sensor"
        }
      }
    },
    "error": {
      "nordpool_not_found": "Could not find a Nordpool sensor. Please install the Nordpool integration first."
    },
    "abort": {
      "already_configured": "Energy Manager is already configured."
    }
  },
  "config_subentries": {
    "car": {
      "step": {
        "user": {
          "title": "Add Car",
          "description": "Configure a car for charging optimization.",
          "data": {
            "car_name": "Car name",
            "battery_capacity_kwh": "Battery capacity (kWh)",
            "battery_level_entity": "Battery level sensor",
            "home_plugged_entity": "Home & plugged in sensor"
          }
        }
      }
    }
  },
  "entity": {
    "sensor": {
      "battery_schedule": {
        "name": "Battery Schedule"
      },
      "next_charge_slot": {
        "name": "Next Charge Slot"
      }
    }
  }
}
```

### manifest.json

```json
{
  "domain": "energy_manager",
  "name": "Energy Manager",
  "codeowners": ["@jyourstone"],
  "config_flow": true,
  "dependencies": [],
  "documentation": "https://github.com/jyourstone/energy_manager",
  "integration_type": "hub",
  "iot_class": "local_polling",
  "issue_tracker": "https://github.com/jyourstone/energy_manager/issues",
  "requirements": [],
  "version": "0.1.0",
  "min_version": "2024.12.0"
}
```

Notes:
- `integration_type: "hub"` -- manages multiple device types (battery, charger, EMS)
- `iot_class: "local_polling"` -- polls local HA entities (Nordpool sensor), does not make external API calls
- `min_version: "2024.12.0"` -- required for ConfigSubentryFlow support
- `dependencies: []` -- reads other integrations via state machine, no Python imports
- `requirements: []` -- no external PyPI dependencies

### hacs.json

```json
{
  "name": "Energy Manager",
  "render_readme": true,
  "homeassistant": "2024.12.0"
}
```

### ConfigSubentryFlow for Car Configuration

```python
# Source: HA Developer Docs config_entries_config_flow_handler (subentry section)
class CarSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying a car."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to add a new car."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_CAR_NAME],
                data=user_input,
            )

        # Auto-detect car integrations (Skoda, VW)
        detected_cars = auto_detect_car_integrations(self.hass)

        schema = vol.Schema({
            vol.Required(CONF_CAR_NAME): TextSelector(),
            vol.Optional(CONF_BATTERY_CAPACITY): NumberSelector(
                NumberSelectorConfig(min=10, max=200, step=1, unit_of_measurement="kWh")
            ),
            vol.Optional(CONF_BATTERY_LEVEL_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_HOME_PLUGGED_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "binary_sensor"])
            ),
        })

        # Pre-fill with detected values if available
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to modify an existing car."""
        config_subentry = self._get_reconfigure_subentry()
        # Show form pre-filled with existing subentry data
        ...

# Register in config flow:
class EnergyManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        if config_entry.options.get(CONF_EV_ENABLED):
            return {"car": CarSubentryFlowHandler}
        return {}
```

## Discretion Recommendations

### Module Toggle: Disable Behavior (Recommendation: Remove entities)

**Recommendation: Remove entities when module is disabled, not mark unavailable.**

Rationale:
- When a user disables the Home Battery module, they do not want to see "unavailable" battery entities cluttering their dashboard. They want them gone.
- `OptionsFlowWithReload` triggers a full unload/reload cycle. On reload, `async_setup_entry` only forwards platforms for enabled modules. Disabled module entities are naturally not re-created.
- If the user re-enables the module, entities are re-created on the next reload with fresh state.
- This is cleaner than marking entities unavailable, which leaves ghost entries in the entity registry.
- HA automatically cleans up device registry entries that have no associated entities.

### Module Toggle: UI Presentation (Recommendation: BooleanSelector checkboxes)

**Recommendation: Use `BooleanSelector` for module toggles in the config flow.**

Rationale:
- HA's `BooleanSelector` renders as a proper checkbox/toggle in the config flow UI.
- This is the standard HA pattern for optional features (used by many core integrations).
- Each module gets its own `vol.Optional(CONF_X_ENABLED, default=False): BooleanSelector()` in the modules step.
- Alternative (`SelectSelector` with multi-select) is heavier than needed for 2 boolean toggles.

### Price Data: Internal Data Structure (Recommendation: Typed dataclass with datetime + float)

**Recommendation: Use `PriceSlot` dataclass with timezone-aware `datetime` and `float` price.**

```python
@dataclass(frozen=True, slots=True)
class PriceSlot:
    start: datetime  # UTC, timezone-aware
    end: datetime    # UTC, timezone-aware
    price: float     # SEK/kWh, pass-through from Nordpool

@dataclass
class PriceData:
    today: list[PriceSlot]
    tomorrow: list[PriceSlot]  # Empty list if not available
    current_price: float | None
    last_updated: datetime
```

Rationale:
- `frozen=True` prevents accidental mutation of shared price data across modules.
- `slots=True` reduces memory footprint (many price slot instances).
- Typed datetime fields (not ISO strings) enable direct comparison and arithmetic in scheduling algorithms (Phase 2+) without repeated parsing.
- `list[PriceSlot]` is simpler to iterate and filter than `dict` or nested structures.
- Empty `tomorrow` list (`[]`) matches the user's decision for unavailable tomorrow prices.

### Price Data: Update Strategy (Recommendation: Hybrid -- polling + event-driven)

**Recommendation: 5-minute polling baseline + immediate refresh on Nordpool state change.**

Rationale:
- Polling interval of 5 minutes catches any missed events or state changes.
- `async_track_state_change_event` on the Nordpool sensor triggers immediate refresh when prices update (typically ~13:00 CET for tomorrow's prices).
- `always_update=False` on the coordinator ensures entities only update when price data actually changes.
- This hybrid approach is exactly what PowerSaver uses (proven in production).
- 5 minutes is low enough to not miss price hour transitions, high enough to avoid unnecessary load.

### Price Data: Nordpool Variant Detection (Recommendation: Attribute check + registry check)

**Recommendation: Two-stage detection (proven in PowerSaver).**

1. Check if entity has `raw_today` attribute (non-None) -> HACS variant
2. Check entity registry: if `entity_entry.platform == "nordpool"` and entity has a config entry -> native variant

Store the detected type as `nordpool_type` in `entry.data` (immutable after config flow). This avoids re-detection on every price fetch.

### Entity ID Naming (Recommendation: Full domain prefix)

**Recommendation: Entity IDs follow `energy_manager_<module>_<data_point>` pattern.**

Examples:
- `sensor.energy_manager_battery_schedule`
- `sensor.energy_manager_ems_status`
- `number.energy_manager_charge_threshold`
- `sensor.energy_manager_enyaq_next_charge` (per-car, name from subentry)

Rationale:
- Full prefix avoids collisions with other integrations.
- Module name in entity ID makes it clear which module owns the entity.
- Per-car entities use the car name from the subentry for human readability.
- `has_entity_name = True` means HA generates entity IDs from device name + entity name automatically. The examples above show what HA would generate given our device names.

### Device Hierarchy (Recommendation: Hub + module devices)

**Recommendation:**

```
Energy Manager (hub, entry_type=SERVICE)
  |-- Home Battery (via_device -> hub)
  |-- EV Charger (via_device -> hub)
  |-- Enyaq (via_device -> hub, linked to car subentry)
  |-- Family Car (via_device -> hub, linked to car subentry)
```

Rationale:
- Hub device uses `DeviceEntryType.SERVICE` since it is virtual (no physical hardware).
- Module devices (`identifiers={(DOMAIN, f"{entry_id}_battery")}`) group related entities.
- Car devices use subentry_id in identifiers: `identifiers={(DOMAIN, subentry.subentry_id)}`.
- `via_device=(DOMAIN, entry.entry_id)` links all sub-devices to the hub.
- When a car subentry is deleted, its device and entities are auto-cleaned by HA.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `hass.data[DOMAIN]` dict | `entry.runtime_data` typed dataclass | HA 2024.x | Type safety, IDE autocomplete, cleaner code |
| `OptionsFlowWithConfigEntry` | `OptionsFlowWithReload` | HA 2024.x (renamed) | Same functionality, clearer name. PowerSaver uses try/except import for backward compatibility. |
| `async_forward_entry_setup` (singular) | `async_forward_entry_setups` (plural) | HA 2023.x | Bulk platform setup in one call, deprecated singular form |
| Options flow for sub-items | Config subentries (`ConfigSubentryFlow`) | HA 2024.12 | Purpose-built for N sub-items (cars, locations, zones) with independent lifecycles |
| `er.async_get_registry()` | `er.async_get()` | HA 2023.5 | Simplified API, old form deprecated |
| Hardcoded entity `name` | `has_entity_name = True` + `translation_key` | HA 2023.x | Device name auto-prefixed, supports localization |

**Deprecated/outdated:**
- `OptionsFlowWithConfigEntry`: Still works but `OptionsFlowWithReload` is the canonical name. Use try/except import for HA version compatibility.
- `async_forward_entry_setup` (singular): Deprecated, must use plural form.
- `hass.data[DOMAIN]` dict: Works but considered legacy. Use `entry.runtime_data`.

## Open Questions

1. **ConfigSubentryFlow minimum HA version for custom integrations**
   - What we know: Subentries were introduced in HA 2024.12. Architecture discussion approved January 2025, implementation completed February 2025.
   - What's unclear: Whether `ConfigSubentryFlow` is available for custom (HACS) integrations or only core integrations. The API exists in `homeassistant.config_entries` which custom integrations can import.
   - Recommendation: Set `min_version: "2024.12.0"` in manifest.json. Test during implementation. If subentries are not available in custom integration context, fall back to a multi-step options flow for car management.
   - Confidence: MEDIUM -- API exists but limited HACS integration examples using it.

2. **OptionsFlowWithReload import compatibility**
   - What we know: PowerSaver uses try/except import: `OptionsFlowWithReload` (new) falling back to `OptionsFlowWithConfigEntry` (old).
   - What's unclear: Which HA version introduced the rename.
   - Recommendation: Copy the PowerSaver try/except pattern for maximum compatibility.
   - Confidence: HIGH -- proven pattern in production.

3. **Platform forwarding for modules with no entities yet**
   - What we know: Phase 1 creates no user-visible entities (price data is internal). The hub device exists but has no entity platforms.
   - What's unclear: Whether `async_forward_entry_setups` should be called with an empty list, or not called at all.
   - Recommendation: Do not call `async_forward_entry_setups` if there are no platforms to forward. The coordinator and hub device are created directly in `async_setup_entry`.
   - Confidence: HIGH -- calling with empty list is a no-op but unnecessary.

## Sources

### Primary (HIGH confidence)
- HA Developer Docs: Config Flow Handler -- multi-step flows, subentry flows, `ConfigSubentryFlow`, `async_get_supported_subentry_types` (https://developers.home-assistant.io/docs/config_entries_config_flow_handler) -- verified via Context7 + WebFetch 2026-02-15
- HA Developer Docs: Device Registry -- `via_device`, `DeviceEntryType`, `DeviceInfo`, identifiers format (https://developers.home-assistant.io/docs/device_registry_index) -- verified via Context7 + WebFetch 2026-02-15
- HA Developer Docs: Config Entries -- lifecycle states, `runtime_data`, `async_on_unload`, platform forwarding (https://developers.home-assistant.io/docs/config_entries_index) -- verified via Context7 + WebFetch 2026-02-15
- HA Developer Docs: Options Flow -- `OptionsFlowWithReload`, `add_suggested_values_to_schema` (https://developers.home-assistant.io/docs/config_entries_options_flow_handler) -- verified via WebFetch 2026-02-15
- HA Developer Docs: Backend Localization -- `strings.json` structure, `config_subentries` translation, `translation_key` (https://developers.home-assistant.io/docs/internationalization/core) -- verified via WebFetch 2026-02-15
- HA Architecture Discussion #1070: Config Subentries -- specification, device/entity cascading, approved January 2025 (https://github.com/home-assistant/architecture/discussions/1070) -- verified via WebFetch 2026-02-15
- PowerSaver HACS Integration: `nordpool_adapter.py`, `config_flow.py`, `coordinator.py`, `const.py` -- proven production code at `/Users/johan.yourstone/Git/power_saver/custom_components/power_saver/` -- HIGH confidence (direct source code analysis)
- Existing AppDaemon codebase: `home_battery_manager.py`, `car_charging_manager.py` -- Nordpool attribute usage (`raw_today`, `raw_tomorrow`) -- HIGH confidence (production code)
- HA Developer Docs: `entry.runtime_data` blog post (2024-04-30) -- typed dataclass pattern -- verified via Context7

### Secondary (MEDIUM confidence)
- Official Nord Pool Integration docs -- entities, services (`get_prices_for_date`), data format (Currency/MWh), polling frequency (https://www.home-assistant.io/integrations/nordpool/) -- verified via WebFetch 2026-02-15
- HACS Nordpool (custom-components/nordpool) -- sensor attributes (`raw_today`, `raw_tomorrow`, `current_price`), version 0.0.18 (https://github.com/custom-components/nordpool) -- verified via WebFetch 2026-02-15
- HACS Publishing Requirements -- `hacs.json` keys, `manifest.json` requirements, validation checks (https://hacs.xyz/docs/publish/integration) -- verified via WebFetch 2026-02-15

### Tertiary (LOW confidence)
- ConfigSubentryFlow availability in custom integrations -- verified API exists, but limited examples of HACS integrations using it. Needs validation during implementation.
- Exact HA version where `OptionsFlowWithReload` replaced `OptionsFlowWithConfigEntry` -- not found in docs, using PowerSaver's try/except pattern as workaround.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all HA built-in APIs, verified via official docs and Context7
- Architecture: HIGH -- patterns verified via HA dev docs, existing PowerSaver code, and Context7
- Config flow: HIGH -- multi-step pattern documented, subentry pattern verified
- Nordpool adapter: HIGH -- proven production code in PowerSaver, both variants documented
- Pitfalls: HIGH -- concrete issues identified from codebase analysis and HA documentation
- Subentry API for HACS: MEDIUM -- API exists, spec approved, but limited custom integration examples

**Research date:** 2026-02-15
**Valid until:** 2026-03-15 (30 days -- HA API is stable, subentry spec is finalized)

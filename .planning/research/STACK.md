# Technology Stack

**Project:** Unified Energy Manager (HACS Custom Integration)
**Researched:** 2026-02-15
**Mode:** Ecosystem (brownfield conversion from AppDaemon)

## Recommended Stack

### Core Framework

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Home Assistant Core APIs | 2025.1+ (target 2024.12 minimum) | Integration runtime | This IS a HA integration -- no choice here. Target `min_version` of 2024.12 in manifest to get subentry flows and modern config flow. | HIGH (verified via HA dev docs) |
| Python | 3.12+ | Runtime | HA 2024.12+ requires Python 3.12. Use modern typing (PEP 695 `type` aliases, `X | Y` union syntax). | HIGH (HA docs specify Python version per release) |
| HACS | 2.0+ | Distribution channel | Standard distribution for custom integrations. Avoids the multi-year core review process while maintaining discoverability. | HIGH |

### Data Coordination

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `DataUpdateCoordinator` | Built-in (HA core) | Periodic data polling | Handles retry logic, `ConfigEntryNotReady`, debouncing, and entity update coordination out of the box. Use one coordinator per module (battery, EV, EMS). | HIGH (verified via HA dev docs) |
| `async_track_state_change_event` | Built-in (HA core) | Event-driven updates | For real-time reactions to price changes, battery SOC updates, and charging state changes. Complement the coordinator for push-style events. | HIGH (verified via HA dev docs) |
| `async_track_time_interval` / `async_track_time_change` | Built-in (HA core) | Scheduled operations | For hourly price-based schedule recalculations and scheduled charge/discharge commands. Prefer over coordinator for operations that need exact timing (e.g., "at the start of each hour"). | HIGH |

### Configuration

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `ConfigFlow` + `OptionsFlowWithConfigEntry` | Built-in (HA core) | User configuration | ConfigFlow for initial setup (which modules to enable, device connections). OptionsFlow for runtime tuning (thresholds, schedules, margins). `OptionsFlowWithConfigEntry` auto-reloads the entry on save. | HIGH (verified via HA dev docs) |
| Subentry Flows | Built-in (HA 2024.12+) | Per-module configuration | Each module (battery, EV, EMS) gets its own subentry. Allows adding/removing/reconfiguring modules independently without touching the main config entry. This is the modern replacement for YAML sub-configs. | MEDIUM (verified pattern exists, but need to validate fit for our use case) |
| `voluptuous` | Built-in (HA dependency) | Schema validation | Required by HA for all config/options schemas. Use `vol.Schema`, `vol.Optional`, `vol.Required` with selectors for the UI. | HIGH |
| `homeassistant.helpers.selector` | Built-in (HA core) | UI form controls | Selectors like `EntitySelector`, `DeviceSelector`, `NumberSelector`, `SelectSelector` render proper UI widgets in the config flow. Use entity selectors for Nordpool sensor selection and device selectors for battery/charger discovery. | HIGH |

### Entity Platforms

| Platform | Purpose | Why | Confidence |
|----------|---------|-----|------------|
| `sensor` | Energy prices, SOC, power readings, cost tracking | Core data exposure. Use `SensorDeviceClass.ENERGY`, `SensorDeviceClass.POWER`, `SensorDeviceClass.MONETARY` for HA Energy Dashboard compatibility. | HIGH |
| `binary_sensor` | Active schedule, grid export active, charging active | Simple on/off state indicators. | HIGH |
| `switch` | Module enable/disable, automation enable/disable | User-facing toggles that replace the current HA helper input_booleans. | HIGH |
| `number` | Thresholds, margins, SOC targets, price limits | Replaces input_number helpers. Use `NumberDeviceClass` and proper min/max/step. | HIGH |
| `select` | Operating modes (e.g., "auto", "manual", "off") | Replaces input_select helpers. Cleaner than switch for multi-state options. | HIGH |
| `button` | Force recalculate, force charge now | One-shot actions. Simpler than services for user-facing operations. | HIGH |
| `calendar` | Charge/discharge schedule visualization | Optional but valuable -- shows upcoming schedule as calendar events. HA has native calendar entity support. | MEDIUM |
| `diagnostics` | Debug data export | Required by HA quality scale for bronze tier. Expose coordinator data, schedule state, config. | HIGH |

### Storage and Persistence

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `homeassistant.helpers.storage.Store` | Built-in (HA core) | Persistent state across restarts | For storing computed schedules, activity history, and learned patterns. Survives restarts. Uses JSON under `.storage/`. Prefer over entity attributes for large data. | HIGH |
| Entity `extra_state_attributes` | Built-in (HA core) | Exposing schedule details | For attaching schedule arrays, price forecasts, and optimization results to sensor entities. Keep under ~16KB per entity for recorder performance. | HIGH |
| `entry.data` (immutable) | Built-in (HA core) | Connection config | Credentials, device identifiers, module selection. Set during ConfigFlow, changed only via reconfigure flow. | HIGH |
| `entry.options` (mutable) | Built-in (HA core) | Runtime settings | Thresholds, margins, modes. Changed via OptionsFlow, triggers automatic reload. | HIGH |

### Testing

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `pytest` | Latest (8.x) | Test runner | HA standard. Use `pytest-homeassistant-custom-component` for fixtures. | HIGH |
| `pytest-homeassistant-custom-component` | Latest | HA test fixtures | Provides `hass` fixture, `MockConfigEntry`, state helpers. The standard way to test custom integrations outside core. | HIGH |
| `pytest-asyncio` | Latest | Async test support | HA is fully async; all tests need async fixtures. | HIGH |
| `unittest.mock` / `pytest-mock` | Built-in / Latest | Mocking | Mock external API calls (Easee, Nordpool sensor reads). | HIGH |
| `syrupy` | Latest | Snapshot testing | HA core uses this for entity state snapshots. Good for regression testing sensor output. | MEDIUM (used in core, optional for custom) |

### Code Quality

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `ruff` | Latest (0.8+) | Linting + formatting | Replaces flake8, isort, black, and pylint for HA projects. HA core uses ruff. Single tool, fast. | HIGH |
| `mypy` | Latest | Type checking | HA core has strict typing. Use `--strict` mode. Catches integration bugs before runtime. | MEDIUM (recommended but not enforced for custom integrations) |
| `pre-commit` | Latest | Git hooks | Run ruff + mypy on commit. Prevents lint failures in CI. | HIGH |

### CI/CD

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| GitHub Actions | N/A | CI pipeline | Standard for HACS integrations. Run tests, lint, validate manifest, HACS validation. | HIGH |
| `hacs/action` | Latest | HACS validation | Official HACS GitHub Action. Validates manifest, structure, and HACS requirements. Run in CI. | HIGH |
| `home-assistant/actions/hassfest` | Latest | HA manifest validation | Validates manifest.json against HA requirements. | HIGH |

### Supporting Libraries (External)

| Library | Version | Purpose | When to Use | Confidence |
|---------|---------|---------|-------------|------------|
| `pyeasee` | Latest | Easee charger API | EV charging module -- wrap in a library or use existing. Check if already on PyPI with adequate coverage. | MEDIUM (need to verify current state of pyeasee) |
| `numpy` | DO NOT USE | Numerical operations | NEVER -- too heavy for HA. Use pure Python for schedule optimization. HA actively discourages numpy. | HIGH |

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Config approach | ConfigFlow + OptionsFlow | YAML configuration | YAML config is deprecated for new integrations. ConfigFlow is required for HACS and provides UI setup. |
| Data coordination | DataUpdateCoordinator | Manual polling with async_track_time_interval | Coordinator handles error retry, entity updates, and availability automatically. Manual polling reinvents this. |
| Module architecture | Subentry flows per module | Separate config entries per module | Subentries keep modules under one integration entry while allowing independent config. Multiple entries would require cross-entry coordination. |
| Module architecture (alt) | Subentry flows per module | Single flat config entry | Flat config becomes unmanageable with 5 modules. Cannot add/remove modules without full reconfigure. |
| State persistence | `helpers.storage.Store` | Writing to files directly | Store handles atomic writes, migrations, and lives in HA's managed `.storage/` directory. |
| Testing | pytest-homeassistant-custom-component | Testing against HA core directly | The custom-component package provides the right fixtures without pulling all of HA core as a dev dependency. |
| Linting | ruff | flake8 + black + isort | ruff replaces all three, runs 10-100x faster, and is what HA core now uses. |
| Distribution | HACS | Manual / custom_components copy | HACS provides update notifications, discoverability, and standardized install UX. |
| Entity creation | Platform-based (sensor.py, switch.py) | Dynamic entity creation in __init__.py | Platform-based is the HA standard. Each platform file handles its own entity type. |
| Scheduling algorithm | Pure Python with datetime/timedelta | APScheduler or similar | External schedulers conflict with HA's event loop. Use HA's native `async_track_*` helpers instead. |

## Architecture-Critical Stack Decisions

### 1. One Integration, Multiple Coordinators (not multiple integrations)

Use a single `custom_components/energy_manager/` domain with module-specific coordinators:
- `EnergyManagerCoreCoordinator` -- price data, grid state, overall optimization
- `BatteryCoordinator` -- SigenStor battery state and scheduling
- `EVChargingCoordinator` -- Easee charger state and charging optimization
- `EMSCoordinator` -- cross-module power balancing

Each coordinator runs independently. Modules register with core coordinator for cross-module optimization.

**Why not separate integrations?** Cross-module optimization (e.g., "don't charge car while battery is discharging to grid") requires shared state. Separate integrations would need entity-based communication (like the current AppDaemon approach) which is exactly what we are moving away from.

### 2. Subentries for Module Management

Each module (battery, EV, EMS) is a subentry under the main config entry. This means:
- Users add/remove modules without touching the main config
- Each module has its own config flow steps
- Modules can be reconfigured independently
- The main entry just holds core settings (Nordpool sensor, region, basic preferences)

**Why not a monolithic options flow?** With 5 modules and 24+ settings, a single options flow becomes overwhelming. Subentries provide natural grouping.

### 3. Entity-Based Settings (not HA helpers)

All 24 manual HA helpers (input_boolean, input_number, input_select) become native entities:
- `switch.*` replaces `input_boolean.*`
- `number.*` replaces `input_number.*`
- `select.*` replaces `input_select.*`

These entities are owned by the integration and tied to the config entry lifecycle. When a module subentry is removed, its entities are automatically cleaned up.

**Why not keep using helpers?** Helpers are user-managed, fragile (renamed/deleted accidentally), and require manual setup. Integration-owned entities are created/destroyed with the config entry.

### 4. Store for Schedule Persistence

Use `helpers.storage.Store` with a versioned schema for persisting:
- Computed charge/discharge schedules
- Activity history (for the activity log sensor)
- Learned patterns (if applicable)

Include a `_async_migrate` method for schema evolution between versions.

## manifest.json Template

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
  "version": "0.1.0"
}
```

Notes:
- `integration_type: "hub"` because we manage multiple device types (battery, charger, EMS)
- `iot_class: "local_polling"` because we poll local HA entities (Nordpool, device sensors). If Easee API calls are made directly, this becomes `"cloud_polling"`
- `dependencies: []` because we read other integrations' entities via state machine, not via direct Python imports. If we import from `nordpool` or `easee` packages, add them here
- `requirements: []` unless we add PyPI dependencies (like `pyeasee`)

## hacs.json Template

```json
{
  "name": "Energy Manager",
  "render_readme": true,
  "homeassistant": "2024.12.0"
}
```

## Directory Structure

```
custom_components/energy_manager/
  __init__.py              # Entry setup, coordinator creation
  config_flow.py           # ConfigFlow + OptionsFlow + SubentryFlows
  const.py                 # Constants, defaults, domain
  coordinator.py           # Base coordinator + CoreCoordinator
  diagnostics.py           # Diagnostic data export
  entity.py                # Base entity class (extends CoordinatorEntity)
  manifest.json
  strings.json             # UI strings for config flow
  translations/
    en.json                # English translations

  # Platforms
  sensor.py
  binary_sensor.py
  switch.py
  number.py
  select.py
  button.py
  calendar.py              # Optional: schedule visualization

  # Modules (business logic, not HA-specific)
  modules/
    __init__.py
    battery/
      __init__.py
      coordinator.py       # BatteryCoordinator
      scheduler.py         # Charge/discharge scheduling algorithm
      entities.py          # Battery-specific entity descriptions
    ev_charging/
      __init__.py
      coordinator.py       # EVChargingCoordinator
      scheduler.py         # Charging optimization algorithm
      entities.py          # EV-specific entity descriptions
    ems/
      __init__.py
      coordinator.py       # EMSCoordinator
      balancer.py          # Power balancing logic
      entities.py          # EMS-specific entity descriptions
    price/
      __init__.py
      coordinator.py       # PriceCoordinator (Nordpool data)
      analyzer.py          # Price analysis, cheap hour detection
```

## Installation Commands

```bash
# Dev environment setup
python -m venv venv
source venv/bin/activate

# Core dev dependencies
pip install homeassistant  # For type stubs and local testing
pip install pytest pytest-homeassistant-custom-component pytest-asyncio pytest-mock
pip install ruff mypy pre-commit

# Optional: snapshot testing
pip install syrupy
```

## What NOT to Use

| Technology | Why Not |
|------------|---------|
| `numpy` / `scipy` / `pandas` | Too heavy for HA. Causes memory issues and installation failures on ARM (RPi). Use pure Python. |
| `APScheduler` | Conflicts with HA's async event loop. Use HA's native `async_track_*` helpers. |
| YAML configuration | Deprecated for new integrations. Required to use ConfigFlow for HACS. |
| `async_setup_platform` (legacy) | Deprecated. Use `async_setup_entry` + `async_forward_entry_setups`. |
| `hass.data[DOMAIN]` dict soup | Fragile, no type safety. Use a typed dataclass or `NamedTuple` stored in `entry.runtime_data` (HA 2024.x+). |
| Direct file I/O for persistence | Use `helpers.storage.Store` which handles atomic writes and lives in managed `.storage/`. |
| `async_forward_entry_setup` (singular) | Deprecated. Use `async_forward_entry_setups` (plural) to set up all platforms in one call. |
| `entity_platform.async_get_platforms` for cross-module comms | Fragile internal API. Use coordinator references or HA events for inter-module communication. |

## Sources

- Home Assistant Developer Documentation: Integration Manifest (https://developers.home-assistant.io/docs/creating_integration_manifest) -- verified via WebFetch 2026-02-15
- Home Assistant Developer Documentation: Config Flow (https://developers.home-assistant.io/docs/config_entries_config_flow_handler) -- verified via WebFetch 2026-02-15
- Home Assistant Developer Documentation: Data Fetching / DataUpdateCoordinator (https://developers.home-assistant.io/docs/integration_fetching_data) -- verified via WebFetch 2026-02-15
- Home Assistant Developer Documentation: Setup Failures (https://developers.home-assistant.io/docs/integration_setup_failures) -- verified via WebFetch 2026-02-15
- Home Assistant Developer Documentation: Platform Code Review (https://developers.home-assistant.io/docs/creating_platform_code_review) -- verified via WebFetch 2026-02-15
- Project memory notes on existing patterns (MEMORY.md) -- entry.data vs entry.options, OptionsFlowWithConfigEntry, DataUpdateCoordinator + async_track_state_change_event pattern
- Training data for: pytest-homeassistant-custom-component, ruff adoption in HA, entry.runtime_data pattern, subentry flows, HACS action -- MEDIUM confidence, recommend verification during implementation

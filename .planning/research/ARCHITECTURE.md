# Architecture Patterns

**Domain:** Home Assistant HACS energy management integration (brownfield conversion from AppDaemon)
**Researched:** 2026-02-15

## Recommended Architecture

**Single config entry, multi-coordinator, subentry-based modular integration.**

One HACS integration (`unified_energy_manager` or similar) with a single config entry per installation. Modules are activated/deactivated through options flow. Each car is a config subentry (HA 2025.x+ pattern). Each module has its own DataUpdateCoordinator. All modules register entities under their own HA devices via the device registry.

```
custom_components/unified_energy_manager/
  __init__.py              # Entry point, coordinator setup, platform forwarding
  const.py                 # DOMAIN, platform list, defaults
  config_flow.py           # Config flow + options flow + car subentry flows
  coordinator.py           # Base coordinator + per-module coordinators
  helpers.py               # Shared utilities (price calcs, fuse protection math)
  strings.json             # UI translations
  manifest.json            # HACS metadata
  sensor.py                # Sensor platform (all modules' sensors)
  number.py                # Number platform (configurable thresholds)
  select.py                # Select platform (mode selections)
  binary_sensor.py         # Binary sensor platform (charging states)
  switch.py                # Switch platform (enable/disable features)
  diagnostics.py           # Diagnostics dump for debugging
  translations/
    en.json
```

### Why This Structure

**Single config entry, not one per module:** HA best practice is one config entry per physical integration point. All modules share the same Nordpool price source and communicate through the same coordinator hub. Multiple config entries would force users to configure the same price sensor repeatedly and make inter-module coordination awkward.

**Subentries for cars:** The HA subentry pattern (documented since 2025) is purpose-built for "one main config + N sub-items." Each car has its own battery capacity, departure time, charge target, and home sensor -- exactly what subentries model. Subentries are created/deleted from the UI without reconfiguring the whole integration.

**Separate coordinators per module:** Different modules have different update cadences. The EMS controller polls every 5 seconds. The battery scheduler recalculates every 5 minutes. Car charging recalculates every 15 minutes. Separate coordinators prevent fast modules from triggering unnecessary updates in slow modules.

### Component Boundaries

| Component | Responsibility | Data Sources | Produces | Communicates With |
|-----------|---------------|--------------|----------|-------------------|
| **Core/Price Coordinator** | Fetch and normalize Nordpool prices, provide price data to all modules | Nordpool sensor entity (state + attributes `raw_today`, `raw_tomorrow`) | Normalized price slots with timestamps | Battery Scheduler, Car Scheduler |
| **Battery Scheduler** | Calculate charge/discharge schedule based on prices, SOC, production forecasts | Core prices, SigenStor battery sensors, solar forecast sensors, sun sensors | Schedule sensor (state + `schedule` attribute), `target_ems_mode` | EMS Controller (via entity state) |
| **Car Scheduler** (per car) | Calculate cheapest charging slots before departure | Core prices, car battery sensor, departure input, target input | Per-car schedule sensor (state + `schedule` attribute) | EMS Controller (via entity state), Easee Controller (via entity state) |
| **EMS Controller** | Execute battery mode changes, fuse protection, PV opportunistic charging | Battery Schedule sensor, Easee status, L-current sensor, PV power, battery SOC | EMS status sensor, service calls to SigenStor (select.select_option, number.set_value) | SigenStor integration (service calls) |
| **Easee Controller** | Execute car charging amp adjustments, solar charging, phase switching | Car Schedule sensors, Easee status/power/limit sensors, PV sensors, L-current | Easee status sensor, service calls to Easee integration | Easee integration (service calls) |

### Data Flow

```
                    Nordpool Sensor (external)
                           |
                    [Price Coordinator]
                     /              \
            [Battery Scheduler]   [Car Scheduler(s)]
                    |                    |
            schedule entity        schedule entity(s)
                    |                    |
                    v                    v
              [EMS Controller]    [Easee Controller]
                    |                    |
              SigenStor entities    Easee entities
              (service calls)      (service calls)
```

**Key data flow principle:** Modules communicate via HA entity state, exactly as the AppDaemon apps do today. This is a deliberate architectural choice:

1. **Entity state is the HA-native IPC mechanism.** Using `hass.data[DOMAIN]` for inter-module data would work but creates tight coupling. Entity state keeps modules decoupled -- the EMS Controller reads `sensor.battery_charge_schedule` the same way whether it comes from this integration or an external source.

2. **Preserves the existing communication contract.** The current AppDaemon apps already use entity state (`sensor.battery_charge_schedule_py`, `sensor.enyaq_car_charging_manager_py`) as their interface. Keeping this pattern means the migration can be done module-by-module with AppDaemon and the new integration coexisting during transition.

3. **Exception: In-memory coordinator data for tightly-coupled pairs.** The Price Coordinator provides data to schedulers via `hass.data[DOMAIN]["price_coordinator"].data` because prices are read-only shared data, not a control signal. This avoids each scheduler independently parsing the same Nordpool entity.

### Internal Data Store

```python
# In __init__.py async_setup_entry:
hass.data[DOMAIN] = {
    "config_entry": entry,
    "price_coordinator": PriceCoordinator,          # Always present
    "battery_coordinator": BatteryScheduleCoordinator,  # If battery module enabled
    "ems_coordinator": EMSCoordinator,               # If battery module enabled
    "car_coordinators": {                            # If EV module enabled
        "car_subentry_id_1": CarScheduleCoordinator,
        "car_subentry_id_2": CarScheduleCoordinator,
    },
    "easee_coordinator": EaseeCoordinator,           # If EV module enabled
    "modules_enabled": {
        "battery": True/False,
        "ev_charging": True/False,
    }
}
```

## Component Detail: Coordinators

### PriceCoordinator (always present)

```python
class PriceCoordinator(DataUpdateCoordinator):
    """Fetches and normalizes Nordpool price data."""

    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER,
            name="Energy Price Data",
            update_interval=timedelta(minutes=5),
            always_update=False,  # Only trigger entities when prices actually change
        )
        self._nordpool_entity = entry.data["nordpool_sensor"]

    async def _async_update_data(self):
        """Read Nordpool sensor and normalize to 15-minute slots."""
        state = self.hass.states.get(self._nordpool_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            raise UpdateFailed(f"Nordpool sensor {self._nordpool_entity} unavailable")

        raw_today = state.attributes.get("raw_today", [])
        raw_tomorrow = state.attributes.get("raw_tomorrow", [])

        return {
            "raw_today": raw_today,
            "raw_tomorrow": raw_tomorrow,
            "current_price": float(state.state),
        }
```

**Update cadence:** Every 5 minutes. Also listens for Nordpool entity state changes via `async_track_state_change_event` for immediate updates when tomorrow's prices arrive (typically around 13:00).

### BatteryScheduleCoordinator

```python
class BatteryScheduleCoordinator(DataUpdateCoordinator):
    """Calculates home battery charge/discharge schedule."""

    def __init__(self, hass, entry, price_coordinator):
        super().__init__(
            hass, _LOGGER,
            name="Battery Schedule",
            update_interval=timedelta(minutes=5),
        )
        self._price_coordinator = price_coordinator
        # Config from entry.options
        self._charge_threshold = entry.options.get("charge_threshold", 2.0)
        # ... etc

    async def _async_update_data(self):
        """Build charge/discharge schedule using prices + battery state."""
        prices = self._price_coordinator.data
        if not prices:
            raise UpdateFailed("No price data available")

        # Read battery sensors from HA state
        soc = self._get_float_state("sensor.sigen_battery_battery_state_of_charge")
        # ... build schedule (port from HomeBatteryManager.build_schedule)
        return schedule
```

**Update cadence:** Every 5 minutes (aligned with current HomeBatteryManager). Also triggers on price coordinator updates.

### EMSCoordinator

```python
class EMSCoordinator(DataUpdateCoordinator):
    """Executes EMS mode changes and fuse protection."""

    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER,
            name="EMS Controller",
            update_interval=timedelta(seconds=5),  # Fast loop for fuse protection
        )
```

**Update cadence:** Every 5 seconds (matches current EMS Controller). This is the critical real-time component -- fuse protection and mode switching must be responsive.

**Important:** The EMS coordinator is event-driven in addition to polling. It uses `async_track_state_change_event` on:
- `sensor.highest_l_current` (fuse protection)
- `sensor.easee_home_25562_status` (charger state changes)
- The battery schedule entity (mode changes)

### CarScheduleCoordinator (one per car)

```python
class CarScheduleCoordinator(DataUpdateCoordinator):
    """Calculates car charging schedule for one vehicle."""

    def __init__(self, hass, entry, subentry, price_coordinator):
        super().__init__(
            hass, _LOGGER,
            name=f"Car Schedule - {subentry.data['car_name']}",
            update_interval=timedelta(minutes=15),
        )
        self._subentry = subentry
        self._price_coordinator = price_coordinator
```

**Update cadence:** Every 15 minutes (matches current CarChargingManager). Also listens for departure time, battery level, and target changes.

### EaseeCoordinator

```python
class EaseeCoordinator(DataUpdateCoordinator):
    """Controls Easee charger amp settings and solar charging."""

    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER,
            name="Easee Controller",
            update_interval=timedelta(seconds=30),  # Match current 30s interval
        )
```

**Update cadence:** Every 30 seconds (matches current EaseeController).

## Component Detail: Config Flow

### Initial Setup Flow

```
Step 1: "user" - Select Nordpool sensor (auto-detect sensor.nordpool_*)
Step 2: "modules" - Enable/disable Battery Module, EV Charging Module
Step 3: "battery" (if enabled) - Select SigenStor entities (auto-detect sensor.sigen_*)
Step 4: "ev_charging" (if enabled) - Select Easee entities (auto-detect sensor.easee_*)
  -> Creates initial config entry
```

### Options Flow (post-setup)

```
Step 1: "init" - Choose what to configure
  -> "modules" - Enable/disable modules
  -> "battery_settings" - Thresholds, timings, SOC limits
  -> "ev_settings" - Fuse limits, solar charging params
  -> "ems_settings" - EMS modes, fuse protection params
```

**Key pattern:** Use `OptionsFlowWithReload` so the integration automatically reloads when options change. This replaces the 24 manual input_number/input_boolean helpers with proper options.

### Car Subentry Flow

```
Subentry type: "car"
Step 1: "user" - Car name, battery capacity sensor/value, departure time entity,
         target entity, battery level sensor, home/plugged sensor
  -> Auto-detect car integrations (Skoda Connect, VW Connect, etc.)
```

Each car subentry creates:
- A CarScheduleCoordinator instance
- A device in the device registry
- Sensor entities under that device (schedule, next charge, energy needed)

## Component Detail: Devices and Entities

### Device Registry Structure

```
Integration: Unified Energy Manager
  |
  +-- Device: "Energy Price Monitor"         (always present)
  |     +-- sensor.energy_current_price
  |     +-- sensor.energy_prices_today        (attribute: raw prices)
  |     +-- binary_sensor.energy_tomorrow_available
  |
  +-- Device: "Home Battery Manager"         (if battery module enabled)
  |     +-- sensor.home_battery_schedule      (state: idle/grid_charging/discharging/solar_charging)
  |     +-- sensor.home_battery_next_charge
  |     +-- sensor.home_battery_next_discharge
  |     +-- sensor.home_battery_charging_slots
  |     +-- sensor.home_battery_discharging_slots
  |     +-- number.home_battery_charge_threshold    (replaces input_number helper)
  |     +-- number.home_battery_discharge_threshold (replaces input_number helper)
  |     +-- number.home_battery_max_charge_power    (replaces input_number helper)
  |
  +-- Device: "EMS Controller"               (if battery module enabled)
  |     +-- sensor.ems_controller_status
  |     +-- sensor.ems_current_mode
  |     +-- sensor.ems_fuse_headroom           (diagnostic)
  |     +-- binary_sensor.ems_fuse_protection_active (diagnostic)
  |
  +-- Device: "Easee Charger Controller"     (if EV module enabled)
  |     +-- sensor.easee_controller_status
  |     +-- sensor.easee_target_amps
  |     +-- binary_sensor.easee_solar_charging_active
  |     +-- switch.easee_force_charging        (replaces input_boolean helper)
  |
  +-- Device: "Enyaq Car Charging"           (subentry: car 1)
  |     +-- sensor.enyaq_charging_schedule
  |     +-- sensor.enyaq_next_charge_slot
  |     +-- sensor.enyaq_energy_needed
  |     +-- number.enyaq_departure_time        (replaces input_datetime helper)
  |     +-- number.enyaq_charge_target         (replaces input_number helper)
  |
  +-- Device: "ID.3 Car Charging"            (subentry: car 2)
        +-- sensor.id3_charging_schedule
        +-- sensor.id3_next_charge_slot
        +-- sensor.id3_energy_needed
        +-- number.id3_departure_time
        +-- number.id3_charge_target
```

### Entity Unique IDs

Follow the HA pattern: `{config_entry_id}_{module}_{entity_type}`

```python
# Examples:
f"{entry.entry_id}_battery_schedule"          # Battery schedule sensor
f"{entry.entry_id}_ems_status"                # EMS status sensor
f"{subentry.subentry_id}_schedule"            # Car schedule sensor (scoped to subentry)
f"{subentry.subentry_id}_next_charge"         # Car next charge sensor
```

### Entity Categories

| Entity | Category | Rationale |
|--------|----------|-----------|
| Schedule sensors | None (primary) | Core user-facing data |
| Threshold numbers | CONFIG | User-adjustable settings |
| Fuse headroom sensor | DIAGNOSTIC | Debugging/monitoring only |
| Fuse protection active | DIAGNOSTIC | Debugging/monitoring only |
| Force charging switch | None (primary) | User actively toggles this |

## Patterns to Follow

### Pattern 1: Coordinator Chaining

**What:** One coordinator consumes another coordinator's data.
**When:** Derived calculations depend on a shared data source.
**Example:**

```python
class BatteryScheduleCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, price_coordinator):
        super().__init__(hass, _LOGGER, name="Battery Schedule",
                         update_interval=timedelta(minutes=5))
        self._price_coordinator = price_coordinator
        # Re-trigger when prices update
        self._price_coordinator.async_add_listener(self._handle_price_update)

    @callback
    def _handle_price_update(self):
        """Schedule an immediate update when prices change."""
        self.async_set_updated_data(None)  # Force re-fetch

    async def _async_update_data(self):
        prices = self._price_coordinator.data
        if not prices:
            raise UpdateFailed("Price data not available")
        # ... build schedule using prices
```

### Pattern 2: Event-Driven + Polling Hybrid

**What:** Use DataUpdateCoordinator for baseline polling, augmented with `async_track_state_change_event` for real-time response.
**When:** Components need both periodic checks and immediate reaction to external changes.
**Example:**

```python
async def async_setup_entry(hass, entry):
    ems_coordinator = EMSCoordinator(hass, entry)
    await ems_coordinator.async_config_entry_first_refresh()

    # Also react immediately to fuse-critical state changes
    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            [entry.options["highest_l_current_sensor"]],
            ems_coordinator.handle_l_current_change,
        )
    )
```

### Pattern 3: Module-Conditional Platform Setup

**What:** Only forward platform setup for enabled modules.
**When:** Integration has optional modules that may not all be configured.
**Example:**

```python
# In __init__.py
PLATFORMS_ALWAYS = [Platform.SENSOR]
PLATFORMS_BATTERY = [Platform.NUMBER]  # threshold numbers
PLATFORMS_EV = [Platform.SWITCH]       # force charging switch

async def async_setup_entry(hass, entry):
    platforms = list(PLATFORMS_ALWAYS)
    if entry.options.get("battery_enabled"):
        platforms.extend(PLATFORMS_BATTERY)
    if entry.options.get("ev_enabled"):
        platforms.extend(PLATFORMS_EV)

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
```

### Pattern 4: Sensor with Schedule Attribute

**What:** Primary state is a human-readable status; full schedule is in attributes.
**When:** Entity needs to expose both a quick status and detailed structured data.
**Example:**

```python
class BatteryScheduleSensor(CoordinatorEntity, SensorEntity):
    @property
    def native_value(self):
        """Return current schedule state: idle/grid_charging/discharging/solar_charging."""
        return self.coordinator.data.get("current_state", "idle")

    @property
    def extra_state_attributes(self):
        return {
            "schedule": self.coordinator.data.get("schedule", []),
            "next_charging_slot": self.coordinator.data.get("next_charging_slot"),
            "next_discharging_slot": self.coordinator.data.get("next_discharging_slot"),
            "target_ems_mode": self.coordinator.data.get("target_ems_mode"),
            "charging_slots": self.coordinator.data.get("charging_slots", 0),
            "discharging_slots": self.coordinator.data.get("discharging_slots", 0),
        }
```

### Pattern 5: Options Replace Helpers

**What:** Replace `input_number`, `input_boolean`, `input_datetime` helpers with integration-native entities.
**When:** Converting external manual helpers to proper integration config.
**Example mapping:**

| Current Helper | Replacement | How |
|---------------|-------------|-----|
| `input_number.battery_charge_price_threshold` | `number.home_battery_charge_threshold` | NumberEntity with options flow default |
| `input_number.battery_max_charging_power` | `number.home_battery_max_charge_power` | NumberEntity, persisted in entry.options |
| `input_boolean.easee_force_charging` | `switch.easee_force_charging` | SwitchEntity calling Easee service |
| `input_datetime.johans_car_temporary_departure_time` | `datetime.enyaq_departure_time` | DateTimeEntity in car subentry |
| `input_number.johans_car_temporary_charging_target` | `number.enyaq_charge_target` | NumberEntity in car subentry |

**Implementation:** These entities store their value in the coordinator or entry options (not external helpers), and changes trigger schedule recalculation via coordinator listener.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Direct Inter-Module Function Calls

**What:** One coordinator directly calls methods on another coordinator.
**Why bad:** Creates tight coupling; makes modules non-independent. If battery module is disabled, EV module calling battery methods crashes.
**Instead:** Communicate via entity state (read sensor attributes) or coordinator listener callbacks. The EMS Controller reads `sensor.home_battery_schedule` state/attributes rather than calling `battery_coordinator.get_schedule()`.

### Anti-Pattern 2: Single Monolithic Coordinator

**What:** One DataUpdateCoordinator that handles all modules in one `_async_update_data()`.
**Why bad:** 5-second EMS fuse protection loop would trigger 5-second recalculation of battery schedule (which only needs 5-minute updates). Wasteful and error-prone.
**Instead:** Separate coordinators with appropriate intervals. Chain them with listeners where needed.

### Anti-Pattern 3: Storing Runtime State in Config Entry Options

**What:** Using `entry.options` to persist ephemeral state like "current EMS mode" or "timer handles."
**Why bad:** `entry.options` writes to `.storage/` on disk. Writing every 5 seconds would wear flash storage and trigger unnecessary config entry reload.
**Instead:** Store runtime state in coordinator instance variables. Use `entry.options` only for user-configurable settings that survive restarts.

### Anti-Pattern 4: Multiple Config Entries for One Installation

**What:** Requiring users to add the integration multiple times (once for battery, once for EV).
**Why bad:** User has to configure Nordpool sensor twice; no shared context; car-battery priority coordination becomes cross-entry communication (hard).
**Instead:** Single config entry with modules enabled/disabled via options flow. Cars added via subentries.

### Anti-Pattern 5: Polling External Entities When Events Available

**What:** Reading `sensor.highest_l_current` every 5 seconds in the coordinator update loop.
**Why bad:** Misses changes between polls (safety issue for fuse protection). Polls even when value hasn't changed.
**Instead:** Use `async_track_state_change_event` for real-time entities. Coordinator polling is backup, not primary.

## Migration Strategy: AppDaemon Coexistence

During phased migration, both AppDaemon and the new integration may run simultaneously. The architecture supports this:

1. **Phase 1 (Core + Battery Scheduler):** New integration creates `sensor.home_battery_schedule_v2` (or similar). AppDaemon EMS Controller continues reading the old `sensor.battery_charge_schedule_py`. Users can compare outputs.

2. **Phase 2 (EMS Controller):** New integration's EMS reads the new battery schedule entity. AppDaemon EMS is disabled. AppDaemon battery scheduler can be disabled (or kept as fallback).

3. **Phase 3 (Car + Easee):** New integration's car schedulers create new entities. Easee controller migrates last since it coordinates with both battery and car modules.

**Entity naming:** Use a `_v2` suffix or different entity IDs during migration to avoid conflicts. After migration completes, a one-time entity rename can match the old IDs if dashboards/automations depend on them.

## Scalability Considerations

| Concern | Current (5 apps) | At 10 cars | At multi-site |
|---------|-------------------|------------|---------------|
| Coordinator count | 6 (price + battery + ems + easee + 2 cars) | 14 (adds 8 car coordinators) | Not applicable (one HA instance per site) |
| Update frequency | Mixed 5s-15min | Same (car coordinators are independent) | Same |
| Entity count | ~25 | ~45 (adds ~20 car entities) | Same per site |
| Memory | Minimal | Minimal (schedule data is small) | Same |
| Config complexity | Subentries for cars | Same pattern, more subentries | Same |

**The architecture scales horizontally for cars** because each car is a subentry with its own coordinator. Adding a car means adding one subentry and one coordinator instance -- no changes to battery or EMS modules.

## Suggested Build Order

Based on dependency analysis and risk assessment:

### Phase 1: Core Infrastructure + Price Coordinator
**Build:** `__init__.py`, `const.py`, `manifest.json`, `config_flow.py` (initial step), `coordinator.py` (PriceCoordinator), `sensor.py` (price sensor)
**Rationale:** Everything depends on prices. This is the foundation. Low complexity, validates the integration skeleton.
**Dependencies:** None
**Deliverable:** Working HACS integration that shows current/future energy prices.

### Phase 2: Battery Schedule Module
**Build:** BatteryScheduleCoordinator, battery schedule sensors, number entities for thresholds
**Rationale:** Port `HomeBatteryManager.build_schedule()` -- the most complex scheduling algorithm. Gets the hardest scheduling logic done early.
**Dependencies:** Phase 1 (prices)
**Deliverable:** Battery schedule that matches AppDaemon output. Can run alongside AppDaemon for validation.

### Phase 3: EMS Controller Module
**Build:** EMSCoordinator, EMS sensors, event listeners for fuse protection
**Rationale:** This is the real-time control component. Must be rock-solid before disabling AppDaemon.
**Dependencies:** Phase 2 (battery schedule entity to read)
**Deliverable:** Battery mode control + fuse protection. AppDaemon EMS can be disabled.

### Phase 4: Car Charging Module
**Build:** Car subentry flow, CarScheduleCoordinator, per-car sensors and number entities
**Rationale:** Port `CarChargingManager` with multi-car support via subentries.
**Dependencies:** Phase 1 (prices)
**Deliverable:** Car schedules matching AppDaemon output. Can run alongside.

### Phase 5: Easee Controller Module
**Build:** EaseeCoordinator, Easee sensors, solar charging logic, fuse protection for charger
**Rationale:** Depends on both car schedules and battery state. Most external service calls (Easee API).
**Dependencies:** Phase 3 (EMS coordination), Phase 4 (car schedules)
**Deliverable:** Full Easee control. AppDaemon Easee controller can be disabled.

### Phase 6: Polish and Helper Migration
**Build:** Options flow for all settings, migrate remaining input_number/input_boolean helpers, diagnostics, documentation
**Rationale:** All core logic works. Now make it user-friendly and eliminate external helper dependencies.
**Dependencies:** Phases 1-5
**Deliverable:** Self-contained integration with no external helper dependencies.

## Sources

- [HA Developer Docs: Creating a Component](https://developers.home-assistant.io/docs/creating_component_index) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Config Entries](https://developers.home-assistant.io/docs/config_entries_index) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Config Flow Handler + Subentries](https://developers.home-assistant.io/docs/config_entries_config_flow_handler) -- MEDIUM confidence (official docs, subentries verified)
- [HA Developer Docs: DataUpdateCoordinator](https://developers.home-assistant.io/docs/integration_fetching_data) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Device Registry](https://developers.home-assistant.io/docs/device_registry_index) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Entity Registry](https://developers.home-assistant.io/docs/entity_registry_index) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Event Listening](https://developers.home-assistant.io/docs/integration_listen_events) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Options Flow](https://developers.home-assistant.io/docs/config_entries_options_flow_handler) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Setup Failures](https://developers.home-assistant.io/docs/integration_setup_failures) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Sensor Entity](https://developers.home-assistant.io/docs/core/entity/sensor) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Quality Scale](https://developers.home-assistant.io/docs/integration_quality_scale_index) -- MEDIUM confidence (official docs)
- [HA Developer Docs: Code Review Requirements](https://developers.home-assistant.io/docs/creating_component_code_review) -- MEDIUM confidence (official docs)
- Existing AppDaemon source code at `/Volumes/addon_configs/a0d7b954_appdaemon/apps/` -- HIGH confidence (primary source)
- Existing PowerSaver integration patterns (from project memory) -- HIGH confidence (known working code)

**Confidence note:** All HA developer docs were fetched live from the official site on 2026-02-15. Rated MEDIUM (not HIGH) because some pages may have been summarized by the fetch tool, and the subentries feature is relatively new (should be verified against the exact HA version in production).

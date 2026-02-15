# Phase 2: Home Battery Schedule - Research

**Researched:** 2026-02-15
**Domain:** Battery charge/discharge scheduling algorithm, HA coordinator chaining, number entities, Forecast.Solar integration
**Confidence:** HIGH

## Summary

Phase 2 builds a multi-cycle battery charge/discharge scheduler that consumes PriceCoordinator data from Phase 1 and produces a schedule sensor that Phase 3 (EMS Controller) will consume. The core algorithm -- peak grouping, virtual energy tracking, and multi-cycle scheduling -- is proven production code from the AppDaemon HomeBatteryManager. The HA integration patterns (BatteryScheduleCoordinator chained to PriceCoordinator, NumberEntity with RestoreNumber for user-adjustable thresholds, schedule sensor with attributes) are well-documented in HA developer docs and validated by the Phase 1 implementation.

The scheduler module should be a pure-Python module with zero HA dependencies (like PowerSaver's `scheduler.py`), enabling independent unit testing. The coordinator wraps it with HA lifecycle management, Forecast.Solar data fetching, and event-driven recalculation. Three NumberEntity instances replace what were previously `input_number` helpers (charge threshold, discharge threshold, max charging power).

**Primary recommendation:** Port the AppDaemon HomeBatteryManager scheduling algorithm into a pure `battery_scheduler.py` module, wrap it with a `BatteryScheduleCoordinator` chained to the existing `PriceCoordinator`, and expose results through sensor + number entity platforms. Design the schedule data structure so Phase 3 (EMS Controller) can cleanly consume it via entity state.

## Standard Stack

### Core

| Library / API | Version | Purpose | Why Standard | Confidence |
|---------------|---------|---------|--------------|------------|
| `DataUpdateCoordinator` | HA core | BatteryScheduleCoordinator orchestration | Phase 1 already uses this for PriceCoordinator; proven pattern | HIGH (verified via Phase 1 code + HA dev docs) |
| `NumberEntity` + `RestoreNumber` | HA core | User-adjustable thresholds (charge/discharge price, max power) | Official HA entity platform for numeric config; RestoreNumber preserves values across restarts | HIGH (verified via Context7 HA dev docs) |
| `CoordinatorEntity` | HA core | Base class for all battery module entities | Phase 1's EnergyManagerEntity extends this | HIGH (verified via Phase 1 code) |
| `async_track_state_change_event` | HA core | React to Forecast.Solar and SOC sensor changes | Phase 1 already uses this for Nordpool state changes in PriceCoordinator | HIGH (verified via Phase 1 code) |
| `EnergyManagerEntity` | Local (Phase 1) | Base entity providing device_info and naming | Already exists in `entity.py` | HIGH (source code verified) |

### Supporting

| Library / API | Version | Purpose | When to Use | Confidence |
|---------------|---------|---------|-------------|------------|
| `Forecast.Solar` sensors | HA native integration | Solar production estimates for schedule optimization | When user has Forecast.Solar configured; accessed by reading entity state | MEDIUM (sensor entities documented; exact attribute names need runtime verification) |
| `homeassistant.helpers.storage.Store` | HA core | Optional: persist schedule state across restarts | If schedule reconstruction from scratch is too slow or loses context | HIGH (PowerSaver uses this for activity_history) |
| `voluptuous` | HA dependency | Schema validation for config flow additions | Config flow step for Forecast.Solar auto-detection | HIGH (Phase 1 already uses this) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| NumberEntity for thresholds | Options flow only | NumberEntity gives real-time adjustment in UI without reconfigure; options flow is Phase 6 |
| Pure Python scheduler module | Algorithm inline in coordinator | Pure module enables unit testing without HA mocking; PowerSaver proves this works |
| Reading Forecast.Solar entity state | Direct forecast_solar coordinator access | Entity state is the HA-native IPC; keeps modules decoupled per architecture decisions |

## Architecture Patterns

### Recommended Project Structure

```
custom_components/energy_manager/
  __init__.py              # Extended: create BatteryScheduleCoordinator, forward Platform.NUMBER
  const.py                 # Extended: battery module constants
  coordinator.py           # Extended: BatteryScheduleCoordinator + BatteryScheduleData
  battery_scheduler.py     # NEW: pure scheduling algorithm (no HA deps)
  sensor.py                # Extended: BatteryScheduleSensor, NextChargeSensor, NextDischargeSensor
  number.py                # NEW: threshold number entities (charge/discharge/power)
  entity.py                # Unchanged (base entity)
  config_flow.py           # Extended: Forecast.Solar auto-detection step
  auto_detect.py           # Extended: find_forecast_solar_entities()
  nordpool_adapter.py      # Unchanged
  strings.json             # Extended: battery sensor + number translations
  translations/en.json     # Extended: same
```

### Pattern 1: Coordinator Chaining (PriceCoordinator -> BatteryScheduleCoordinator)

**What:** BatteryScheduleCoordinator listens to PriceCoordinator updates and recalculates the schedule.
**When to use:** When a downstream coordinator depends on an upstream coordinator's data.
**Source:** HA dev docs (DataUpdateCoordinator) + Phase 1 architecture decisions
**Example:**

```python
class BatteryScheduleCoordinator(DataUpdateCoordinator[BatteryScheduleData]):
    def __init__(self, hass, entry, price_coordinator):
        super().__init__(
            hass, _LOGGER,
            name="Battery Schedule",
            config_entry=entry,
            update_interval=timedelta(minutes=5),
        )
        self._price_coordinator = price_coordinator
        # Chain: recalculate when prices update
        self._unsub_price = price_coordinator.async_add_listener(
            self._handle_price_update
        )
        entry.async_on_unload(lambda: self._unsub_price())

    @callback
    def _handle_price_update(self):
        """Trigger schedule recalculation when prices change."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> BatteryScheduleData:
        price_data = self._price_coordinator.data
        if not price_data or not price_data.today:
            raise UpdateFailed("No price data available")
        # ... call pure scheduler, read SOC sensor, etc.
```

### Pattern 2: Pure Scheduler Module (No HA Dependencies)

**What:** Battery scheduling algorithm in a standalone Python module, callable by both the coordinator and unit tests.
**When to use:** Complex algorithms that benefit from isolated testing.
**Source:** PowerSaver's `scheduler.py` (verified in production)
**Example:**

```python
# battery_scheduler.py -- zero HA imports
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class ScheduleSlot:
    start: datetime
    end: datetime
    price: float
    action: str  # "charge", "discharge", "idle", "solar_charge"

def build_battery_schedule(
    price_slots: list[dict],
    charge_threshold: float,
    discharge_threshold: float,
    max_charge_power_w: float,
    battery_capacity_kwh: float,
    current_soc_pct: float,
    solar_forecast: dict[datetime, float] | None = None,
    peak_gap_hours: float = 2.0,
) -> list[ScheduleSlot]:
    """Build multi-cycle charge/discharge schedule.

    Algorithm:
    1. Identify discharge windows using peak grouping
    2. For each peak, calculate energy needed to fill/discharge
    3. Virtual energy tracking simulates battery through schedule
    4. Assign charge/discharge/idle per slot
    """
    ...
```

### Pattern 3: NumberEntity with RestoreNumber for User Thresholds

**What:** User-adjustable numeric values that persist across restarts, stored as integration-owned entities.
**When to use:** Replacing `input_number` helpers with integration-native entities.
**Source:** HA dev docs NumberEntity + RestoreNumber (verified via Context7)
**Example:**

```python
from homeassistant.components.number import NumberEntity, RestoreNumber

class BatteryChargeThreshold(EnergyManagerEntity, RestoreNumber):
    _attr_translation_key = "charge_price_threshold"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
        else:
            self._attr_native_value = self._default_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        # Trigger schedule recalculation
        await self.coordinator.async_request_refresh()
```

### Pattern 4: Schedule Sensor with Structured Attributes

**What:** Sensor whose state is the current battery mode, with full schedule in attributes.
**When to use:** Primary user-facing sensor that both the UI and Phase 3 EMS consume.
**Source:** Architecture doc (Pattern 4: Sensor with Schedule Attribute) + PowerSaver sensor.py
**Example:**

```python
class BatteryScheduleSensor(EnergyManagerEntity, SensorEntity):
    _attr_translation_key = "battery_schedule"

    @property
    def native_value(self) -> str:
        """Current state: idle, grid_charging, discharging, solar_charging."""
        if self.coordinator.data is None:
            return "idle"
        return self.coordinator.data.current_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "schedule": data.schedule_as_dicts,  # list of slot dicts
            "next_charging_slot": data.next_charging_slot,
            "next_discharging_slot": data.next_discharging_slot,
            "charging_slots": data.charging_slot_count,
            "discharging_slots": data.discharging_slot_count,
            "target_ems_mode": data.target_ems_mode,  # Phase 3 reads this
        }
```

### Anti-Patterns to Avoid

- **Algorithm in coordinator:** Do NOT put scheduling logic directly in `_async_update_data()`. Keep it in a pure module for testability.
- **Direct EMS control from scheduler:** Phase 2 only produces a schedule. Phase 3 consumes it. Do NOT send control commands from the scheduler.
- **Polling Forecast.Solar sensors in coordinator:** Use `async_track_state_change_event` instead. The coordinator's 5-min interval is the fallback, not the primary trigger.
- **Storing schedule in entry.options:** Schedule is ephemeral runtime data. Store it on the coordinator data object, not in persistent config. (Architecture doc Anti-Pattern 3)
- **Oversized sensor attributes:** Keep attribute data within HA's soft limit. A 48-slot schedule (2 days x 24 hours) is fine. Avoid storing raw price arrays redundantly (they are already on PriceCoordinator).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Peak grouping algorithm | Custom peak detection | Port from AppDaemon HomeBatteryManager `_group_into_peaks()` | Proven in production; handles gap threshold, multi-peak detection |
| Virtual energy tracking | Custom battery simulation | Port from AppDaemon HomeBatteryManager (lines 452-521) | Handles multi-cycle charge/discharge with buffer allocation |
| Number entity state restore | Custom file persistence | `RestoreNumber` base class | HA built-in; handles edge cases (corrupt state, first boot, migration) |
| Coordinator lifecycle | Manual listener management | `DataUpdateCoordinator` + `entry.async_on_unload()` | HA handles subscriber management, error retry, and cleanup |
| Forecast.Solar data parsing | Custom HTTP client for forecast.solar API | Read HA entity state (`sensor.energy_production_today`, etc.) | The Forecast.Solar HA integration already handles API calls, caching, rate limits |
| Schedule slot time logic | Custom datetime math | Use `PriceSlot` from Phase 1 coordinator (already has UTC-aware start/end) | Consistent time handling; avoids timezone bugs |

**Key insight:** The scheduling algorithm is the core intellectual property of this phase. It already exists in AppDaemon -- port it, don't reinvent it. Everything else (coordinator lifecycle, entity persistence, event handling) is standard HA infrastructure.

## Common Pitfalls

### Pitfall 1: Coordinator Update Race Between Price and Schedule

**What goes wrong:** PriceCoordinator updates, BatteryScheduleCoordinator immediately recalculates, but NumberEntity threshold hasn't been read yet (stale value).
**Why it happens:** Coordinator listener fires synchronously before entities have processed the update.
**How to avoid:** Read threshold values directly from the NumberEntity instances or from a shared state object at the start of `_async_update_data()`, not from cached values. The coordinator should always read the latest values at calculation time.
**Warning signs:** Schedule doesn't change when user adjusts a threshold in the UI.
**Confidence:** HIGH (standard HA coordinator timing concern)

### Pitfall 2: Schedule Attribute Size Exceeds HA Limits

**What goes wrong:** Sensor attributes that are too large get silently truncated or cause performance issues in the frontend.
**Why it happens:** Including redundant data (full price arrays alongside schedule) or too many slots.
**How to avoid:** Schedule attribute should contain only the schedule slots (max 48 for 2 days of hourly data). Do NOT duplicate price data that is already available on PriceCoordinator. Keep per-slot data minimal: `{"start": "ISO", "end": "ISO", "price": float, "action": str}`.
**Warning signs:** Frontend displays `...` for attribute values; recorder warnings about large states.
**Confidence:** HIGH (Phase 1 already encountered this -- `01-05-PLAN.md` removed oversized attributes)

### Pitfall 3: Forecast.Solar Integration Not Present

**What goes wrong:** Scheduler crashes when trying to read Forecast.Solar entity state that doesn't exist.
**Why it happens:** Forecast.Solar is optional (BATT-07 says "auto-detects... optional"). Not all users have it.
**How to avoid:** Always check if the Forecast.Solar entity_id is configured AND the entity state is not `None`/`unavailable`/`unknown` before using it. Fall back to no solar adjustment (charge fully from grid). The scheduler function should accept `solar_forecast: dict | None` and handle `None` gracefully.
**Warning signs:** `UpdateFailed` errors when user doesn't have Forecast.Solar.
**Confidence:** HIGH (explicit requirement BATT-07; PROJECT.md key decision: "Forecast.Solar optional")

### Pitfall 4: NumberEntity Default Values on First Installation

**What goes wrong:** Number entities start with `None` value on fresh install; scheduler crashes on `None * something`.
**Why it happens:** `RestoreNumber` returns `None` if no previous state exists (first boot).
**How to avoid:** Always provide sensible defaults in `async_added_to_hass()`. If `last_number_data` is `None` or `last_number_data.native_value` is `None`, set to the default. The scheduler must also handle `None` thresholds defensively.
**Warning signs:** `TypeError: unsupported operand type(s) for *: 'NoneType' and 'float'`
**Confidence:** HIGH (verified via Context7 RestoreNumber docs)

### Pitfall 5: Timezone Handling in Schedule Slots

**What goes wrong:** Schedule slots appear at wrong times, or "current slot" lookup fails to find a match.
**Why it happens:** Mixing naive and aware datetimes, or comparing UTC slots with local-time "now".
**How to avoid:** All PriceSlots from Phase 1 are already UTC-aware. The scheduler should work entirely in UTC internally. Convert to local time only at the presentation layer (sensor attributes). Use `dt_util.utcnow()` consistently.
**Warning signs:** Schedule shows correct prices but "current state" is wrong or always "idle".
**Confidence:** HIGH (Phase 1 already enforces UTC-aware datetimes in PriceSlot)

### Pitfall 6: BatteryScheduleCoordinator Not Created When Module Disabled

**What goes wrong:** Integration crashes on startup because `entry.runtime_data.battery_coordinator` is `None` but sensor.py tries to use it.
**Why it happens:** Battery module is disabled in options; coordinator was never created.
**How to avoid:** Check `modules_enabled[MODULE_BATTERY]` in `__init__.py` before creating BatteryScheduleCoordinator. Only forward `Platform.NUMBER` when battery module is enabled. Sensor platform must check which module sensors to create. Follow the existing pattern in `_get_enabled_platforms()`.
**Warning signs:** `AttributeError: 'NoneType' object has no attribute 'data'` at startup with battery disabled.
**Confidence:** HIGH (existing code pattern in `__init__.py`)

## Code Examples

### Schedule Data Structure (Phase 3 Contract)

The schedule data structure is the API contract between Phase 2 (scheduler) and Phase 3 (EMS Controller). Design it carefully.

```python
# Source: Architecture doc component boundaries + AppDaemon schedule format

@dataclass
class BatteryScheduleData:
    """Output of the battery schedule coordinator."""

    # Current state for sensor display
    current_state: str  # "idle" | "grid_charging" | "discharging" | "solar_charging"

    # Full schedule (list of slots)
    schedule: list[ScheduleSlot]

    # Convenience lookups
    next_charging_slot: ScheduleSlot | None
    next_discharging_slot: ScheduleSlot | None

    # Counts for UI
    charging_slot_count: int
    discharging_slot_count: int

    # Phase 3 interface: what EMS mode should be active NOW
    target_ems_mode: str  # "command_charging" | "max_self_consumption" | "standby"

    # Metadata
    last_calculated: datetime
    solar_forecast_used: bool

@dataclass(frozen=True)
class ScheduleSlot:
    """A single time slot in the battery schedule."""
    start: datetime  # UTC-aware
    end: datetime    # UTC-aware
    price: float     # SEK/kWh
    action: str      # "charge" | "discharge" | "idle" | "solar_charge"
```

### Coordinator Initialization in __init__.py

```python
# Source: Phase 1 __init__.py pattern + Architecture doc coordinator chaining

async def async_setup_entry(hass, entry):
    # Phase 1: Price coordinator (always present)
    price_coordinator = PriceCoordinator(hass, entry)
    await price_coordinator.async_config_entry_first_refresh()

    # Phase 2: Battery schedule coordinator (if battery module enabled)
    battery_coordinator = None
    if entry.options.get(CONF_BATTERY_ENABLED):
        battery_coordinator = BatteryScheduleCoordinator(
            hass, entry, price_coordinator
        )
        await battery_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EnergyManagerData(
        price_coordinator=price_coordinator,
        battery_coordinator=battery_coordinator,  # NEW field
        modules_enabled={...},
    )
```

### Reading Forecast.Solar Entity State

```python
# Source: Forecast.Solar integration docs + PROJECT.md "accept as optional input"

def _get_solar_forecast(hass: HomeAssistant, entity_id: str | None) -> dict[datetime, float] | None:
    """Read solar production forecast from Forecast.Solar entity.

    Returns hourly production estimate dict, or None if unavailable.
    """
    if not entity_id:
        return None

    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None

    # Forecast.Solar exposes watt_hours as entity attribute or via
    # separate sensors for today/tomorrow energy production
    # Use the energy_production sensor values to estimate hourly production
    try:
        today_kwh = float(state.state)  # energy_production_today in Wh
        # Simplified: distribute across daylight hours
        # More sophisticated: use multiple Forecast.Solar sensors
        return {"today_total_wh": today_kwh}
    except (ValueError, TypeError):
        return None
```

### Platform Forwarding for Battery Module

```python
# Source: Phase 1 _get_enabled_platforms pattern

def _get_enabled_platforms(entry):
    platforms = [Platform.SENSOR]  # Always: price sensor

    if entry.options.get(CONF_BATTERY_ENABLED):
        platforms.append(Platform.NUMBER)  # Threshold entities

    return platforms
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `input_number` helpers for thresholds | `NumberEntity` with `RestoreNumber` | HA 2022+ | Users don't need to create manual helpers; values survive restarts via built-in restore |
| `OptionsFlowWithConfigEntry` | `OptionsFlowWithReload` | HA 2024.x | Phase 1 already handles this with try/except import |
| Storing runtime state in `entry.options` | Coordinator data + RestoreNumber | Architectural best practice | Avoids disk writes on every update cycle |
| `RestoreEntity` for numbers | `RestoreNumber` | HA 2022.6+ | Saves `native_value` not `state`; handles unit conversion properly |
| Single monolithic coordinator | Per-module coordinators with chaining | Architectural decision | Battery scheduler at 5min doesn't interfere with EMS at 5sec (Phase 3) |

**Deprecated/outdated:**
- `NumberEntity._attr_value`, `._attr_min_value`, `._attr_step`: Replaced by `native_*` variants since HA 2023.1. Always use `_attr_native_value`, `_attr_native_min_value`, etc.
- `RestoreEntity` for number entities: Use `RestoreNumber` instead (saves native value, not state).

## Open Questions

1. **Exact Forecast.Solar data access pattern**
   - What we know: Forecast.Solar provides `energy_production_today`, `energy_production_tomorrow`, `power_production_now` etc. as separate sensor entities. The underlying `forecast_solar` library has `watt_hours` and `watt_hours_period` data structures.
   - What's unclear: Whether the per-hour breakdown is accessible as an entity attribute, or only through the aggregate sensors. Community posts suggest the hourly data is available but the exact attribute key is not documented in official HA docs.
   - Recommendation: For Phase 2 v1, use the aggregate `energy_production_today` and `energy_production_tomorrow` sensors (simple, reliable). Distribute production estimate across daylight hours using a simple model. If per-hour data proves accessible, enhance later. This keeps the scheduler functional without Forecast.Solar complexity.
   - Confidence: MEDIUM

2. **How NumberEntity values reach the coordinator**
   - What we know: NumberEntity can call `coordinator.async_request_refresh()` when its value changes (Pattern 3 above). The coordinator reads the current value at calculation time.
   - What's unclear: The cleanest way to give the coordinator access to the NumberEntity's current value. Options: (a) coordinator reads entity state from `hass.states.get()`, (b) coordinator holds reference to NumberEntity instance, (c) shared state dict in runtime_data.
   - Recommendation: Store threshold values on the coordinator instance directly. NumberEntity's `async_set_native_value` updates `coordinator.charge_threshold = value` then calls `coordinator.async_request_refresh()`. This avoids hass.states round-trip and keeps it simple.
   - Confidence: HIGH (PowerSaver uses a similar pattern -- options are read in coordinator's `_async_update_data()`)

3. **Battery capacity and characteristics -- where do they come from?**
   - What we know: The scheduler needs battery capacity (kWh), current SOC (%), and max charge power (W) to calculate charge/discharge durations. SOC and power entity are configured in Phase 1 config flow. Max charge power is a NumberEntity (BATT-10).
   - What's unclear: Whether battery capacity should come from the SigenStor integration automatically, from config flow, or from a NumberEntity. The AppDaemon version reads it from a config parameter.
   - Recommendation: Battery capacity is a configuration value (set once in config flow or options), not a NumberEntity (it doesn't change at runtime). Max charge power IS a NumberEntity because users may want to limit it dynamically. SOC comes from the entity configured in Phase 1 config flow (`CONF_SOC_ENTITY`).
   - Confidence: HIGH

## Sources

### Primary (HIGH confidence)
- Phase 1 source code at `/Users/johan.yourstone/Git/energy_manager/custom_components/energy_manager/` -- coordinator.py, __init__.py, sensor.py, entity.py, const.py, config_flow.py patterns
- PowerSaver `scheduler.py` at `/Users/johan.yourstone/Git/power_saver/` (git HEAD) -- pure scheduling algorithm pattern, build_schedule(), find_current_slot(), find_next_change()
- PowerSaver `coordinator.py` at `/Users/johan.yourstone/Git/power_saver/` (git HEAD) -- coordinator wrapping scheduler, activity_history persistence, emergency mode handling
- PowerSaver `sensor.py` at `/Users/johan.yourstone/Git/power_saver/` (git HEAD) -- schedule sensor, next change sensor patterns
- AppDaemon codebase architecture at `.planning/codebase/ARCHITECTURE.md` -- peak grouping, virtual energy tracking, schedule object format, data flow
- AppDaemon codebase concerns at `.planning/codebase/CONCERNS.md` -- tech debt to avoid, fragile areas to improve
- HA Developer Docs: NumberEntity + RestoreNumber (verified via Context7 `/websites/developers_home-assistant_io`) -- native_value, async_set_native_value, async_get_last_number_data
- HA Developer Docs: DataUpdateCoordinator (verified via Context7) -- async_add_listener, async_request_refresh, _async_update_data

### Secondary (MEDIUM confidence)
- HA Developer Docs: Forecast.Solar integration page (https://www.home-assistant.io/integrations/forecast_solar/) -- sensor list, general capabilities
- Architecture doc at `.planning/research/ARCHITECTURE.md` -- BatteryScheduleCoordinator pattern, component boundaries, data flow diagram
- Project doc at `.planning/PROJECT.md` -- key algorithms to port, Forecast.Solar optional decision

### Tertiary (LOW confidence)
- Forecast.Solar Python library (https://github.com/home-assistant-libs/forecast_solar) -- estimate object structure, watt_hours_period format (not fully documented in public README)
- Community posts on battery scheduling algorithms -- general patterns, not specific to this project

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all HA APIs verified via Context7 and Phase 1 source code
- Architecture: HIGH -- patterns validated by Phase 1 implementation and PowerSaver production code
- Scheduling algorithm: HIGH -- AppDaemon source documented in `.planning/codebase/ARCHITECTURE.md`; PowerSaver `scheduler.py` provides pure-module pattern
- Forecast.Solar: MEDIUM -- sensor entities known; exact per-hour data access pattern unclear from docs
- Pitfalls: HIGH -- Phase 1 already hit attribute size issue; timezone, restore, and module-gating patterns are well understood

**Research date:** 2026-02-15
**Valid until:** 2026-03-15 (stable domain; HA core APIs unlikely to change in 30 days)

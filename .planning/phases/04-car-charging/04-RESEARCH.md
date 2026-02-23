# Phase 4: Car Charging - Research

**Researched:** 2026-02-23
**Domain:** Per-car EV charging schedule optimization, HA subentry-based devices, datetime/number entities, price-sorted slot selection with departure constraints, solar-surplus EV charging, dynamic phase switching
**Confidence:** HIGH

## Summary

Phase 4 builds a per-car price-optimized charging schedule system that consumes PriceCoordinator data from Phase 1 and produces per-car schedule sensors. Each car is represented as a separate HA device (via config subentry), with its own schedule sensor, departure time entity, and target SOC entity. The core algorithm -- price-sorted slot selection constrained by departure deadline and target SOC -- is conceptually simpler than the Phase 2 battery scheduler (no peak grouping or virtual energy tracking needed), but requires careful handling of per-car state isolation, fallback charging for unknown vehicles, and solar-surplus routing with hysteresis.

The architecture follows the proven Phase 2/3 pattern: a pure-Python `car_charging_scheduler.py` module with zero HA imports for independent testability, wrapped by a `CarChargingCoordinator` (one per car) that chains to PriceCoordinator. Each car's entities (schedule sensor, departure time, target SOC, battery capacity number) are associated with a car-specific device registered under the car's subentry. The subentry system already exists from Phase 1 -- `CarSubentryFlowHandler` creates subentries with `car_name`, `battery_capacity`, `battery_level_entity`, and `home_plugged_entity` data.

The key algorithmic challenge is: given price slots, departure time, current SOC, target SOC, and battery capacity, select the cheapest N hours before departure to reach the target. Fallback mode (EV-08) selects off-peak hours when no specific car is recognized. Solar-surplus charging (EV-09) reuses the PVHysteresisTracker pattern from Phase 3's ems_controller.py but applies it to charger power allocation. Dynamic phase switching (EV-10) requires understanding Easee's 1-phase vs 3-phase modes and the 6A minimum per-phase constraint.

**Primary recommendation:** Create a pure `car_charging_scheduler.py` with `build_car_charging_schedule()` for the slot-selection algorithm. Create one `CarChargingCoordinator` per subentry, storing them in `EnergyManagerData.car_coordinators` dict keyed by subentry_id. Each car gets its own device in the device registry (via `config_subentry_id`), own schedule sensor, departure TimeEntity (with RestoreEntity), and target SOC NumberEntity (with RestoreNumber). The coordinator chains to PriceCoordinator and listens for car battery level entity changes.

## Standard Stack

### Core

| Library / API | Version | Purpose | Why Standard | Confidence |
|---------------|---------|---------|--------------|------------|
| `DataUpdateCoordinator` | HA core | CarChargingCoordinator per car | Phase 1-3 all use this; proven pattern for periodic + event-driven updates | HIGH (Phase 1-3 verified) |
| `ConfigSubentryFlow` | HA core | Per-car configuration via subentry | Phase 1 already implemented `CarSubentryFlowHandler`; subentries are the HA-standard way to model per-device configs | HIGH (Phase 1 code verified) |
| `TimeEntity` + `RestoreEntity` | HA core | Departure time entity per car (HH:MM:SS) | HA-native time input; departure time is time-of-day not full datetime; RestoreEntity preserves value across restarts | HIGH (HA dev docs verified) |
| `RestoreNumber` | HA core | Target SOC number entity and battery capacity per car | Phase 2 already uses RestoreNumber for battery thresholds; proven persist-across-restart pattern | HIGH (Phase 2 code verified) |
| `SensorEntity` + `CoordinatorEntity` | HA core | Per-car schedule sensor | Phase 2-3 use this exact pattern for battery schedule and EMS status | HIGH (Phase 2-3 code verified) |
| `DeviceInfo` with `config_subentry_id` | HA core | Register each car as a separate HA device | WAQI integration demonstrates the pattern; each subentry gets its own device | MEDIUM (WAQI example verified, not yet used in this integration) |
| `PVHysteresisTracker` | Local (Phase 3) | Solar-surplus charging hysteresis for EV | Already implemented in ems_controller.py; reusable for EV solar charging | HIGH (Phase 3 code verified) |
| Pure Python module (no HA deps) | Python 3.x | `car_charging_scheduler.py` -- schedule algorithm | Phase 2-3 proved this pattern; enables independent unit testing | HIGH (Phase 2-3 pattern verified) |

### Supporting

| Library / API | Version | Purpose | When to Use | Confidence |
|---------------|---------|---------|-------------|------------|
| `async_track_state_change_event` | HA core | React to car battery level and charger status changes | Same as Phase 2-3 for SOC and grid power listeners | HIGH (Phase 1-3 verified) |
| `voluptuous` | HA dep | Config flow schema for car subentry fields | Phase 1 already uses this in CarSubentryFlowHandler | HIGH (Phase 1 verified) |
| `EntityCategory.CONFIG` | HA core | Mark departure time and target SOC as config entities | Phase 2 uses this for threshold numbers | HIGH (Phase 2 verified) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| TimeEntity for departure | DateTimeEntity | Departure is a recurring daily time (07:30), not a specific date+time; TimeEntity is simpler and more appropriate |
| One coordinator per car | Single coordinator for all cars | Per-car coordinators isolate update cadence and failure domains; one car's sensor going unavailable does not block others |
| RestoreEntity mixin for TimeEntity | Store in config entry options | RestoreEntity is the HA-standard persistence pattern; config entry options would require options flow (Phase 6) |
| Per-car device via subentry | All entities on hub device | Subentry devices are cleaner UX -- each car appears separately in the device registry with its own entities |

## Architecture Patterns

### Recommended Project Structure

```
custom_components/energy_manager/
  __init__.py              # Extended: create CarChargingCoordinators per subentry
  const.py                 # Extended: car charging constants
  coordinator.py           # Extended: CarChargingCoordinator + CarChargingData
  car_charging_scheduler.py # NEW: pure scheduling algorithm (no HA deps)
  sensor.py                # Extended: CarScheduleSensor per car
  number.py                # Extended: car target SOC + battery capacity numbers per car
  time.py                  # NEW: car departure time entity per car
  entity.py                # Extended: CarEntity base with per-car DeviceInfo
  config_flow.py           # Potentially extended: add max_charge_power_kw to car subentry
  auto_detect.py           # Unchanged (car detection already exists)
  battery_scheduler.py     # Unchanged
  ems_controller.py        # Unchanged
  nordpool_adapter.py      # Unchanged
  strings.json             # Extended: car entity translations
  translations/en.json     # Extended: same
```

### Pattern 1: Per-Car Coordinator from Subentries

**What:** Create one CarChargingCoordinator per car subentry, stored in `EnergyManagerData.car_coordinators` dict.
**When to use:** When each subentry needs its own update cycle and data pipeline.
**Source:** WAQI integration pattern (verified via GitHub), adapted for this integration's subentry model.

```python
# In __init__.py async_setup_entry:
car_coordinators: dict[str, CarChargingCoordinator] = {}
if entry.options.get(CONF_EV_ENABLED):
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TYPE_CAR:
            coordinator = CarChargingCoordinator(
                hass, entry, subentry, price_coordinator
            )
            await coordinator.async_config_entry_first_refresh()
            car_coordinators[subentry_id] = coordinator

entry.runtime_data = EnergyManagerData(
    price_coordinator=price_coordinator,
    battery_coordinator=battery_coordinator,
    ems_coordinator=ems_coordinator,
    car_coordinators=car_coordinators,
    modules_enabled={...},
)
```

### Pattern 2: Per-Car Device Registration via Subentry

**What:** Each car subentry gets its own device in the HA device registry.
**When to use:** When subentry entities should be grouped under a distinct device.
**Source:** HA architecture discussion #1070, WAQI integration pattern.

```python
class CarEntity(CoordinatorEntity):
    """Base entity for per-car entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, subentry):
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._subentry_id = subentry.subentry_id
        self._car_name = subentry.data.get(CONF_CAR_NAME, "Unknown Car")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._subentry_id)},
            name=self._car_name,
            manufacturer="Energy Manager",
            model="Car",
            via_device=(DOMAIN, self._entry_id),  # Link to hub device
        )
```

### Pattern 3: Entities Added with config_subentry_id

**What:** When adding entities for a specific car, pass `config_subentry_id` to `async_add_entities`.
**When to use:** Required for proper subentry-device association in HA.
**Source:** WAQI integration (verified via GitHub).

```python
# In sensor.py async_setup_entry:
for subentry_id, coordinator in entry.runtime_data.car_coordinators.items():
    subentry = entry.subentries[subentry_id]
    async_add_entities(
        [CarScheduleSensor(coordinator, entry, subentry)],
        config_subentry_id=subentry_id,
    )
```

### Pattern 4: TimeEntity with RestoreEntity for Departure Time

**What:** Departure time uses TimeEntity (time-of-day, not full datetime) with RestoreEntity mixin for persistence.
**When to use:** When the entity represents a recurring daily time that should survive restarts.
**Source:** HA dev docs for TimeEntity + RestoreEntity pattern from Phase 2's RestoreNumber.

```python
from datetime import time
from homeassistant.components.time import TimeEntity
from homeassistant.helpers.restore_state import RestoreEntity

class CarDepartureTime(CarEntity, TimeEntity, RestoreEntity):
    _attr_translation_key = "departure_time"

    def __init__(self, coordinator, entry, subentry):
        super().__init__(coordinator, entry, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_departure_time"
        self._attr_native_value = time(7, 0)  # Default: 07:00

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = time.fromisoformat(last_state.state)
            except (ValueError, TypeError):
                pass
        self.coordinator.departure_time = self._attr_native_value

    async def async_set_value(self, value: time) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.departure_time = value
        await self.coordinator.async_request_refresh()
```

### Anti-Patterns to Avoid

- **Shared mutable state between car coordinators:** Each coordinator must have its own independent state. Do not share a single schedule dict across cars.
- **Using DateTimeEntity for departure time:** Departure time is a daily recurring event (07:30 every day), not a one-time datetime. TimeEntity is the correct abstraction.
- **Coupling car schedule algorithm to HA imports:** The pure scheduler module must remain HA-free for testability.
- **Polling car battery level frequently:** Cloud-based car integrations (Skoda, VW) update infrequently. Do not create a fast polling loop -- listen for state changes and accept the cloud's update frequency.
- **Phase switching in the scheduler:** Phase switching (1-phase vs 3-phase) is a Phase 5 concern (Easee charger control). The Phase 4 scheduler should calculate energy/hours needed; Phase 5 translates that to amp limits and phase modes.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Departure time persistence | Manual file/store-based save | TimeEntity + RestoreEntity | HA handles serialization, migration, and UI automatically |
| Per-car device grouping | Manual device registry calls | DeviceInfo with subentry identifiers | HA links entities to devices automatically via DeviceInfo |
| Price-sorted slot selection | Complex optimization algorithm | Simple sorted + slice | The problem is: pick N cheapest hours before deadline. Sort by price, take first N. No need for LP solvers or complex optimization |
| PV hysteresis for EV solar charging | New hysteresis implementation | Reuse PVHysteresisTracker from ems_controller.py | Already tested and proven; same activate/deactivate threshold pattern |
| Entity state restoration | Custom storage layer | RestoreEntity / RestoreNumber | HA's built-in restore mechanism handles all edge cases |

**Key insight:** The car charging schedule algorithm is fundamentally simpler than the battery scheduler. It is: (1) calculate energy_needed_kwh from SOC gap and battery capacity, (2) calculate hours_needed from energy and charge rate, (3) filter price slots to those before departure, (4) sort by price ascending, (5) select cheapest N slots. The complexity is in the HA wiring (per-car devices, subentries, entity lifecycle), not the algorithm.

## Common Pitfalls

### Pitfall 1: Subentry Entity Lifecycle

**What goes wrong:** Entities created for a subentry are not properly cleaned up when the subentry is removed, or new entities are not created when a subentry is added at runtime.
**Why it happens:** The integration must handle subentry add/remove events, not just the initial setup.
**How to avoid:** Listen for `entry.async_on_unload()` per coordinator. When a subentry is removed, HA should automatically remove associated entities if they are properly linked via `config_subentry_id`. Test add/remove/re-add cycles.
**Warning signs:** Ghost entities after removing a car; missing entities after adding a car without restart.

### Pitfall 2: Cloud Car Integration Update Lag

**What goes wrong:** Car battery level entity from Skoda/VW integrations updates infrequently (every 15-60 minutes or only when the car is active). The scheduler recalculates with stale SOC data.
**Why it happens:** Cloud-based car integrations poll at their own pace; some only update when the car's ignition is on.
**How to avoid:** Design the algorithm to be robust with stale SOC. Use the most recent value and log when it is older than a threshold (e.g., 2 hours). Consider a "last_updated" check. Do NOT attempt to force-refresh the car integration.
**Warning signs:** Schedule shows "needs 4 hours of charging" when the car is already at 80% because the sensor last updated 3 hours ago.

### Pitfall 3: Tomorrow's Prices Not Yet Available

**What goes wrong:** Departure is at 07:00 tomorrow, but tomorrow's Nordpool prices do not arrive until ~13:00 CET today. The scheduler has no prices for the overnight period.
**Why it happens:** Nordpool publishes day-ahead prices around noon. If the user sets departure for the next morning early in the day, the scheduler lacks data.
**How to avoid:** When tomorrow's prices are unavailable, use today's prices as a fallback estimate for the overnight period. Alternatively, schedule a recalculation when tomorrow's prices arrive (PriceCoordinator already fires updates). Mark the schedule as "preliminary" when based on incomplete data.
**Warning signs:** Empty schedule before 13:00 CET; sudden schedule change at 13:00 when tomorrow's prices arrive.

### Pitfall 4: No Car Recognized but Charger Shows Connected

**What goes wrong:** Easee charger reports a car is connected (status = "awaiting_start"), but no configured car's battery level entity is updating. This is the fallback scenario (EV-08).
**Why it happens:** A different car (guest, rental) is plugged in, or the configured car's cloud integration is down.
**How to avoid:** Implement fallback charging mode that activates off-peak-priced hours when a car is connected but no configured car is detected. Use a simple heuristic: if charger says "car connected" but no subentry car's SOC has changed in the last hour, activate fallback.
**Warning signs:** Car connected but no charging scheduled; user expects charging to happen.

### Pitfall 5: Departure Time Timezone Handling

**What goes wrong:** TimeEntity stores time as HH:MM:SS without timezone. The scheduler needs to convert departure time to a UTC datetime for comparison with price slots.
**Why it happens:** TimeEntity uses `datetime.time` which is timezone-naive. The scheduler operates in UTC. The user thinks in local time.
**How to avoid:** When building the schedule, combine today's (or tomorrow's) date with the departure time in the user's configured HA timezone, then convert to UTC. Use `homeassistant.util.dt.now()` for local time and `as_utc()` for conversion. If departure time is earlier than current local time, it means tomorrow.
**Warning signs:** Schedule is off by 1 or 2 hours; charging finishes too early or too late.

### Pitfall 6: Concurrent Subentry Operations

**What goes wrong:** Two car coordinators both try to update at the same time, or a subentry is being removed while its coordinator is updating.
**Why it happens:** DataUpdateCoordinator updates are async; multiple can be in-flight.
**How to avoid:** Each coordinator is independent with its own data. No shared mutable state. The coordinator's `_async_update_data` only reads shared PriceCoordinator data (immutable snapshots) and its own car's sensor state. Removal is handled by HA's unload mechanism.
**Warning signs:** KeyError on subentry_id during iteration; coordinator referencing removed entities.

## Code Examples

### Car Charging Schedule Algorithm (Pure Python)

```python
# car_charging_scheduler.py -- Pure Python, no HA imports

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timezone

@dataclass(frozen=True)
class CarScheduleSlot:
    start: datetime
    end: datetime
    price: float
    action: str  # "charge" or "idle"

@dataclass
class CarScheduleResult:
    schedule: list[CarScheduleSlot]
    charging_slot_count: int
    energy_needed_kwh: float
    hours_needed: float
    current_action: str
    is_preliminary: bool  # True when tomorrow's prices not yet available

def build_car_charging_schedule(
    price_slots: list[dict],
    departure_time_utc: datetime,
    current_soc_pct: float,
    target_soc_pct: float,
    battery_capacity_kwh: float,
    max_charge_power_kw: float,
    now: datetime | None = None,
    fallback_mode: bool = False,
) -> CarScheduleResult:
    """Build a price-optimized charging schedule for one car.

    Algorithm:
        1. Calculate energy needed: (target - current) / 100 * capacity
        2. Calculate hours needed: energy / charge_power
        3. Filter slots to those between now and departure
        4. Sort by price ascending
        5. Select cheapest N slots to cover hours_needed
        6. In fallback mode, select all off-peak slots instead
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Calculate energy and hours needed
    soc_gap = max(0, target_soc_pct - current_soc_pct)
    energy_needed_kwh = (soc_gap / 100.0) * battery_capacity_kwh
    if max_charge_power_kw <= 0:
        return _empty_result(energy_needed_kwh)
    hours_needed = energy_needed_kwh / max_charge_power_kw

    # Filter to valid window
    available = [
        s for s in _parse_slots(price_slots)
        if s.start >= now and s.end <= departure_time_utc
    ]
    if not available:
        return _empty_result(energy_needed_kwh)

    if fallback_mode:
        # EV-08: select off-peak hours (cheapest half)
        available.sort(key=lambda s: s.price)
        midpoint = len(available) // 2
        charge_slots = set(id(s) for s in available[:midpoint])
    else:
        # Normal: select cheapest N hours
        slots_needed = _ceil_slots(hours_needed, available)
        available_sorted = sorted(available, key=lambda s: s.price)
        charge_slots = set(id(s) for s in available_sorted[:slots_needed])

    # Build final schedule (in chronological order)
    # ... (build CarScheduleSlot list, derive current_action)
```

### Departure Time to UTC Conversion

```python
# In CarChargingCoordinator._async_update_data():
from homeassistant.util import dt as dt_util

def _departure_to_utc(departure_time: time) -> datetime:
    """Convert a local departure time to UTC datetime.

    If departure is earlier than now (local), it means tomorrow.
    """
    local_now = dt_util.now()
    local_departure = local_now.replace(
        hour=departure_time.hour,
        minute=departure_time.minute,
        second=0,
        microsecond=0,
    )
    if local_departure <= local_now:
        local_departure += timedelta(days=1)
    return dt_util.as_utc(local_departure)
```

### Fallback Charging Detection

```python
def _detect_fallback_needed(
    charger_connected: bool,
    car_coordinators: dict[str, CarChargingCoordinator],
    stale_threshold_minutes: int = 60,
) -> bool:
    """Detect if fallback charging should activate.

    Returns True when charger reports a car connected but no
    configured car's SOC has recently updated.
    """
    if not charger_connected:
        return False

    now = dt_util.utcnow()
    for coordinator in car_coordinators.values():
        if coordinator.data and coordinator.data.soc_last_updated:
            age = (now - coordinator.data.soc_last_updated).total_seconds()
            if age < stale_threshold_minutes * 60:
                return False  # At least one car is recently active
    return True  # Charger connected but no car recognized
```

## State of the Art

| Old Approach (AppDaemon) | Current Approach (HA Integration) | Impact |
|--------------------------|-----------------------------------|--------|
| Two separate CarChargingManager instances in apps.yaml | Per-car subentry with independent coordinators | Eliminates config duplication; cars added/removed via UI |
| `input_datetime` helpers for departure time | TimeEntity with RestoreEntity | No manual helper creation; persists automatically |
| `input_number` helpers for target SOC | RestoreNumber entity per car | Same pattern as Phase 2 battery thresholds |
| Template sensor for "car home and plugged in" | Auto-detected from car/charger integration entities | Computed internally from configured entities |
| Manual entity ID strings in apps.yaml | Config flow with auto-detection and entity selectors | Type-safe; validated at config time |
| Single charger power calculation | Per-car max charge power as configurable parameter | Different cars charge at different rates |

**Deprecated/outdated:**
- AppDaemon's `run_minutely()` pattern -- replaced by DataUpdateCoordinator polling + event listeners
- Manual `input_boolean` for force charging -- will be integration-owned switch (Phase 5)

## Open Questions

1. **Subentry add/remove at runtime without restart**
   - What we know: HA supports adding subentries at runtime. The `async_setup_entry` is called once at startup.
   - What's unclear: Whether adding a new car subentry after initial setup automatically triggers entity creation, or if a reload is needed.
   - Recommendation: Implement the initial setup path first. Test runtime add/remove. If reload is needed, document it. HA's subentry system may handle this automatically since the subentry flow creates entities via the platform setup.

2. **Max charge power per car**
   - What we know: Different cars charge at different max rates (e.g., Enyaq 11 kW 3-phase, ID.3 7.4 kW 1-phase). The AppDaemon version reads this from a sensor (`sensor.johans_car_calculated_charging_power`).
   - What's unclear: Whether to add max_charge_power_kw as a config flow field, a RestoreNumber entity, or read from a configurable sensor.
   - Recommendation: Add as a NumberEntity (RestoreNumber) per car with a sensible default (7.4 kW). This lets users adjust without reconfiguring. The subentry already has `battery_capacity` -- add `max_charge_power_kw` alongside it or as a separate number entity.

3. **Scope boundary with Phase 5 (Easee Charger Control)**
   - What we know: Phase 4 produces charging schedules. Phase 5 sends commands to the Easee charger. EV-09 (solar surplus) and EV-10 (phase switching) require charger interaction.
   - What's unclear: Whether solar-surplus and phase-switching logic belongs in the schedule (Phase 4) or the charger controller (Phase 5).
   - Recommendation: Phase 4 handles the **schedule** (when to charge, how much energy). Phase 5 handles the **execution** (amp limits, phase modes, start/stop). EV-09 solar surplus is split: Phase 4 can mark "solar_charge" slots in the schedule (like battery scheduler does), but actual PV-to-charger power routing is Phase 5. EV-10 phase switching is entirely Phase 5 -- the scheduler just outputs energy/hours needed; the charger controller decides 1-phase vs 3-phase based on available power.

4. **Fallback charging trigger mechanism**
   - What we know: EV-08 says "when an unrecognized vehicle is connected." The charger status entity tells us a car is connected.
   - What's unclear: How to reliably distinguish "unrecognized car connected" from "recognized car with stale SOC sensor."
   - Recommendation: Use a staleness heuristic: if charger says connected AND no configured car's SOC has updated within the last 60 minutes, activate fallback. This covers both unknown cars and offline car integrations. Make the staleness threshold configurable.

## Sources

### Primary (HIGH confidence)
- Phase 1-3 source code (config_flow.py, coordinator.py, battery_scheduler.py, ems_controller.py, entity.py, sensor.py, number.py, const.py, auto_detect.py, __init__.py, strings.json) -- all verified in current codebase
- .planning/codebase/ documentation (ARCHITECTURE.md, INTEGRATIONS.md, CONVENTIONS.md, CONCERNS.md, STRUCTURE.md) -- verified reference documentation
- .planning/ROADMAP.md, REQUIREMENTS.md, PROJECT.md -- verified project context

### Secondary (MEDIUM confidence)
- [HA Developer Docs: TimeEntity](https://developers.home-assistant.io/docs/core/entity/time/) -- official docs for TimeEntity (native_value is `time`, set_value method)
- [HA Developer Docs: DateTimeEntity](https://developers.home-assistant.io/docs/core/entity/datetime/) -- official docs (confirmed TimeEntity is better fit for departure time)
- [HA Developer Blog: Config Subentries](https://developers.home-assistant.io/blog/2025/02/16/config-subentries/) -- official announcement of subentry feature
- [HA Core: WAQI sensor.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/waqi/sensor.py) -- reference implementation for per-subentry entity creation with `config_subentry_id`
- [HA Core: Kitchen Sink subentry PR #136755](https://github.com/home-assistant/core/pull/136755) -- reference implementation for subentry support pattern
- [HA Architecture Discussion #1070](https://github.com/home-assistant/architecture/discussions/1070) -- subentry architecture discussion
- [Easee Developer: Current Limits](https://developer.easee.com/docs/current-limits-and-control) -- dynamicChargerCurrent is the only safe frequent-write setting; 6A minimum per phase
- [Easee HA Integration](https://github.com/nordicopen/easee_hass) -- set_charger_phase_mode service: "1_phase", "auto_phase", "3_phase"

### Tertiary (LOW confidence)
- [ev_smart_charging](https://github.com/jonasbkarlsson/ev_smart_charging) -- referenced in PROJECT.md as prior art; confirms price-sorted slot selection is the standard algorithm for EV charging optimization

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries are either already used in Phase 1-3 or verified in official HA docs
- Architecture: HIGH -- follows exact same coordinator/pure-module/entity pattern established in Phase 2-3
- Algorithm: HIGH -- price-sorted slot selection is proven in AppDaemon reference and is the standard approach for deadline-constrained cost minimization
- Subentry device pattern: MEDIUM -- verified in WAQI/kitchen-sink examples but not yet used in this integration; needs implementation validation
- Pitfalls: HIGH -- based on known issues from AppDaemon reference (CONCERNS.md) and Phase 2-3 UAT experience

**Research date:** 2026-02-23
**Valid until:** 2026-03-23 (stable domain; HA core subentry API is established)

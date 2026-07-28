# Phase 3: EMS Controller - Research

**Researched:** 2026-02-17
**Domain:** Real-time battery EMS mode control, fuse protection, command verification, HA service calls for device control
**Confidence:** HIGH

## Summary

Phase 3 transforms the integration from a passive schedule-display system into an active device controller. The EMS Controller reads the battery schedule produced by Phase 2's BatteryScheduleCoordinator, determines the correct SigenStor EMS mode (command_charging, max_self_consumption, standby), and sends control commands via HA service calls (`select.select_option` for mode, `number.set_value` for charging limits). It also implements fuse protection by reading real-time phase current (`sensor.highest_l_current`) and dynamically limiting battery charging power when household load approaches the configured fuse rating.

The core design follows the architecture established in Phase 1-2: a new `EMSCoordinator` (DataUpdateCoordinator with 30-second polling + event-driven state change listeners) that chains to `BatteryScheduleCoordinator`. The EMS coordinator's `_async_update_data()` reads the schedule, reads real-time sensor data, computes the target EMS mode and safe charging limit, sends commands via `hass.services.async_call()`, and verifies the command took effect by reading back entity state. A pure-Python `ems_controller.py` module handles all calculations (fuse headroom, amp clamping, car-priority logic) with zero HA imports for independent testability -- following the Phase 2 pattern with `battery_scheduler.py`.

The fuse protection algorithm is the most safety-critical component. It must: (1) read current phase load in amps, (2) subtract from configured fuse rating, (3) account for the battery's own contribution to load, (4) clamp the result to [0, max_safe_amps], and (5) never produce negative values or exceed the fuse rating. All calculated amp values pass through a hard-clamp function before being sent to any device.

**Primary recommendation:** Build a pure-Python `ems_controller.py` module for all EMS calculations (mode selection, fuse headroom, amp clamping, car-priority override). Wrap it with an `EMSCoordinator` that polls every 30 seconds (with event-driven listeners for immediate response to fuse-critical changes). Send control commands via `hass.services.async_call("select", "select_option", ...)` and `hass.services.async_call("number", "set_value", ...)`. Verify commands by reading back entity state after a short delay. Add fuse rating as a required config field.

## Standard Stack

### Core

| Library / API | Version | Purpose | Why Standard | Confidence |
|---------------|---------|---------|--------------|------------|
| `DataUpdateCoordinator` | HA core | EMSCoordinator orchestration with 30s polling | Proven in Phase 1/2; provides listener management, error retry, cleanup on unload | HIGH (Phase 1-2 code verified) |
| `hass.services.async_call()` | HA core | Send control commands to SigenStor select/number entities | Official HA API for inter-integration service calls; async, non-blocking | HIGH (HA dev docs verified) |
| `async_track_state_change_event` | HA core | React immediately to fuse-critical sensor changes (L-current, charger status) | Phase 1-2 already uses this for Nordpool/SOC state changes | HIGH (Phase 1-2 code verified) |
| `async_call_later` | HA core helpers | Schedule delayed command verification readback | HA-native async timer; replaces AppDaemon's `run_in()` for delayed operations | HIGH (HA dev docs) |
| `CoordinatorEntity` / `EnergyManagerEntity` | Local (Phase 1) | Base entity for EMS status sensor | Already exists in `entity.py`; provides device_info and naming | HIGH (source verified) |
| `SensorEntity` | HA core | EMS status sensor showing current mode and fuse headroom | Phase 2 already creates sensors this way | HIGH (Phase 2 code verified) |
| Pure Python module (no HA deps) | Python 3.x | `ems_controller.py` -- all calculations (mode selection, fuse math, clamping) | Phase 2 proved this pattern with `battery_scheduler.py`; enables independent unit testing | HIGH (Phase 2 pattern verified) |

### Supporting

| Library / API | Version | Purpose | When to Use | Confidence |
|---------------|---------|---------|-------------|------------|
| `voluptuous` | HA dependency | Validate fuse rating in config flow (required field, numeric bounds) | Config flow step for EMS configuration | HIGH (Phase 1 uses this) |
| `EntityCategory.DIAGNOSTIC` | HA core | Mark fuse headroom sensor as diagnostic entity | For monitoring/debug entities not core user-facing | HIGH (HA dev docs) |
| `NumberEntity` / `RestoreNumber` | HA core | Fuse rating number entity (configurable, persists) | If fuse rating is user-adjustable at runtime; alternatively config-flow-only | HIGH (Phase 2 verified) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| 30s DataUpdateCoordinator polling | 5s polling (matching AppDaemon) | 30s is sufficient when combined with event-driven listeners; 5s would work but wastes cycles since HA fires state change events for L-current changes |
| `hass.services.async_call()` for SigenStor | Direct modbus writes via pymodbus | Service calls are the HA-standard way; direct modbus bypasses the SigenStor integration's state tracking and could cause state conflicts |
| Separate EMSCoordinator | EMS logic inside BatteryScheduleCoordinator | Separate coordinator respects different update cadences (schedule recalc every 5 min vs EMS enforcement every 30s); prevents schedule recalculation on every fuse check |
| Pure Python ems_controller module | All logic in coordinator | Pure module enables TDD with no HA mocking; coordinator just orchestrates I/O |

## Architecture Patterns

### Recommended Project Structure

```
custom_components/energy_manager/
  __init__.py              # Extended: create EMSCoordinator, forward no new platforms (sensor already forwarded)
  const.py                 # Extended: EMS constants (fuse config keys, default values, EMS modes)
  coordinator.py           # Extended: EMSCoordinator + EMSData dataclass
  ems_controller.py        # NEW: pure EMS calculation module (mode selection, fuse math, clamping)
  sensor.py                # Extended: EMSStatusSensor
  config_flow.py           # Extended: fuse rating config field in battery step
  auto_detect.py           # Unchanged
  battery_scheduler.py     # Unchanged
  entity.py                # Unchanged
  nordpool_adapter.py      # Unchanged
  number.py                # Potentially extended: fuse rating NumberEntity (if runtime-adjustable)
  strings.json             # Extended: EMS sensor + fuse config translations
  translations/en.json     # Extended: same
```

### Pattern 1: EMSCoordinator Chaining to BatteryScheduleCoordinator

**What:** EMSCoordinator listens to BatteryScheduleCoordinator updates and re-evaluates EMS mode.
**When to use:** When a downstream controller depends on an upstream scheduler's output.
**Source:** Phase 2 coordinator chaining pattern (verified in production).

```python
class EMSCoordinator(DataUpdateCoordinator[EMSData]):
    def __init__(self, hass, entry, battery_coordinator):
        super().__init__(
            hass, _LOGGER,
            name="Energy Manager EMS",
            config_entry=entry,
            update_interval=timedelta(seconds=30),
            always_update=False,
        )
        self._battery_coordinator = battery_coordinator
        self._fuse_rating_amps = entry.options.get(CONF_FUSE_RATING, 20)

    async def _async_setup(self):
        # Chain: re-evaluate when battery schedule updates
        unsub = self._battery_coordinator.async_add_listener(
            self._handle_schedule_update
        )
        self.config_entry.async_on_unload(lambda: unsub())

        # Event-driven: react immediately to fuse-critical changes
        l_current_entity = self.config_entry.options.get(CONF_L_CURRENT_ENTITY)
        if l_current_entity:
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, [l_current_entity], self._handle_fuse_update
                )
            )

    async def _async_update_data(self) -> EMSData:
        # 1. Read schedule data from battery coordinator
        schedule_data = self._battery_coordinator.data
        if schedule_data is None:
            return EMSData(mode="standby", ...)

        # 2. Read real-time sensor values
        l_current = self._read_float_state(l_current_entity)
        battery_soc = self._read_float_state(soc_entity)

        # 3. Call pure module for calculations
        result = compute_ems_state(
            target_ems_mode=schedule_data.target_ems_mode,
            current_l_amps=l_current,
            fuse_rating_amps=self._fuse_rating_amps,
            # ... other inputs
        )

        # 4. Send commands if mode changed
        if result.mode != self._last_sent_mode:
            await self._send_ems_mode(result.mode)
            self._last_sent_mode = result.mode

        # 5. Update charging limit if changed
        if result.charge_limit_kw != self._last_charge_limit:
            await self._send_charge_limit(result.charge_limit_kw)
            self._last_charge_limit = result.charge_limit_kw

        return result
```

### Pattern 2: Service Call with Command Verification

**What:** Send a control command via HA service call, then read back the actual state to verify it took effect.
**When to use:** Any time the integration controls physical hardware (EMS mode, charging limits).
**Source:** EMS-05 requirement + AppDaemon CONCERNS.md (state consistency issue).

```python
async def _send_ems_mode(self, target_mode: str) -> bool:
    """Send EMS mode change and verify it took effect."""
    ems_select_entity = self.config_entry.options.get(CONF_EMS_SELECT_ENTITY)
    if not ems_select_entity:
        return False

    # Map internal mode to SigenStor option string
    option_map = {
        "command_charging": "Command Charging (PV First)",
        "max_self_consumption": "Maximum Self Consumption",
        "standby": "Standby",
    }
    option = option_map.get(target_mode)
    if not option:
        _LOGGER.warning("Unknown EMS mode: %s", target_mode)
        return False

    # Send the command
    await self.hass.services.async_call(
        "select", "select_option",
        {"entity_id": ems_select_entity, "option": option},
        blocking=True,
    )

    # Schedule verification readback after short delay
    self._pending_verification = {
        "entity": ems_select_entity,
        "expected": option,
        "sent_at": dt_util.utcnow(),
    }
    return True
```

### Pattern 3: Pure Python EMS Calculation Module

**What:** All fuse math, mode selection, and safety clamping in a standalone module with zero HA imports.
**When to use:** Safety-critical calculations that must be exhaustively unit-tested.
**Source:** Phase 2 `battery_scheduler.py` pattern (proven).

```python
# ems_controller.py -- zero HA imports
from dataclasses import dataclass

@dataclass(frozen=True)
class EMSDecision:
    """Result of EMS state computation."""
    target_mode: str          # "command_charging" | "max_self_consumption" | "standby"
    charge_limit_kw: float    # Clamped safe charging limit
    fuse_headroom_amps: float # Available headroom on fuse
    override_reason: str | None  # Why mode was overridden (e.g., "car_charging_priority")

def compute_ems_state(
    target_ems_mode: str,
    current_l_amps: float,
    fuse_rating_amps: float,
    max_charge_power_kw: float,
    battery_soc_pct: float,
    car_scheduled: bool,
    car_plugged_in: bool,
    pv_power_w: float,
    battery_capacity_kwh: float,
    max_soc_pct: float = 95.0,
    safety_buffer_amps: float = 2.0,
    voltage: float = 230.0,
) -> EMSDecision:
    """Compute EMS mode and safe charging limit.

    All values clamped to safe range. Never returns negative amps.
    """
    # Fuse headroom calculation
    headroom = fuse_rating_amps - current_l_amps - safety_buffer_amps
    headroom = max(0.0, headroom)  # Never negative

    # Car priority override (EMS-03)
    if car_scheduled and car_plugged_in and target_ems_mode == "command_charging":
        return EMSDecision(
            target_mode="standby",
            charge_limit_kw=0.0,
            fuse_headroom_amps=headroom,
            override_reason="car_charging_priority",
        )

    # Fuse-limited charging power (EMS-02)
    headroom_kw = (headroom * voltage) / 1000.0
    safe_charge_kw = min(max_charge_power_kw, headroom_kw)
    safe_charge_kw = max(0.0, safe_charge_kw)  # Clamp to zero minimum

    # PV opportunistic charging (EMS-08)
    if target_ems_mode == "standby" and pv_power_w > 500:
        if battery_soc_pct < max_soc_pct:
            return EMSDecision(
                target_mode="command_charging",
                charge_limit_kw=min(pv_power_w / 1000.0, safe_charge_kw),
                fuse_headroom_amps=headroom,
                override_reason="pv_opportunistic",
            )

    return EMSDecision(
        target_mode=target_ems_mode,
        charge_limit_kw=safe_charge_kw if target_ems_mode == "command_charging" else 0.0,
        fuse_headroom_amps=headroom,
        override_reason=None,
    )

def clamp_amps(value: float, min_amps: float = 0.0, max_amps: float = 32.0) -> float:
    """Hard-clamp amp value to safe range. Never negative, never exceeds max."""
    return max(min_amps, min(value, max_amps))
```

### Pattern 4: Config Flow Extension for Fuse Rating (EMS-06)

**What:** Add fuse rating as a required config field in the battery config step.
**When to use:** When the EMS controller needs a hardware-specific value that varies per installation.

```python
# In config_flow.py battery step, add:
vol.Required(CONF_FUSE_RATING, default=20): NumberSelector(
    NumberSelectorConfig(
        min=10, max=63, step=1, unit_of_measurement="A"
    )
),
# Also add EMS select entity and L-current sensor:
vol.Optional(CONF_EMS_SELECT_ENTITY): EntitySelector(
    EntitySelectorConfig(domain="select")
),
vol.Optional(CONF_L_CURRENT_ENTITY): EntitySelector(
    EntitySelectorConfig(domain="sensor")
),
```

### Anti-Patterns to Avoid

- **Direct modbus writes to SigenStor:** Always use HA service calls (`select.select_option`, `number.set_value`). Direct modbus bypasses the SigenStor integration's state tracking.
- **Blocking service calls in coordinator:** Use `blocking=True` with `hass.services.async_call()` which is async-safe; never use synchronous service calls.
- **Polling for state changes at 5-second intervals:** Use `async_track_state_change_event` for fuse-critical sensors (L-current). The 30-second coordinator poll is the fallback, not the primary trigger.
- **Unclamped math results sent to devices:** Every calculated amp/kW value must pass through `clamp_amps()` or equivalent before being sent to any service call. The AppDaemon code had a bug where `math.ceil()` on negative values produced incorrect results.
- **Storing EMS runtime state in entry.options:** EMS mode, fuse headroom, and command history are ephemeral. Store on coordinator instance variables. `entry.options` is for user config only.
- **EMS logic inside BatteryScheduleCoordinator:** Keep EMS enforcement separate. The schedule recalculates every 5 minutes; EMS enforcement runs every 30 seconds. Different cadences, different coordinators.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Service calls to other integrations | Custom HTTP/modbus client | `hass.services.async_call("select", "select_option", ...)` | HA standard; handles entity resolution, state updates, error propagation |
| Fuse amp calculations | Inline math in coordinator | Pure `ems_controller.py` module with `compute_ems_state()` | Testable without HA; safety-critical code must be unit-tested exhaustively |
| Delayed command verification | `asyncio.sleep()` in coordinator | `async_call_later(hass, delay, callback)` | HA-native timer; properly cleaned up on unload; does not block event loop |
| Event-driven sensor monitoring | Manual polling loop | `async_track_state_change_event()` | HA fires events on every state change; zero latency vs polling; no wasted cycles |
| EMS mode state machine | Ad-hoc if/elif chains | Explicit mode enum + transition table in pure module | Testable; prevents invalid transitions; logs every change with reason |
| Coordinator lifecycle | Manual listener cleanup | `DataUpdateCoordinator` + `entry.async_on_unload()` | HA handles subscriber management, error retry, cleanup |

**Key insight:** The EMS controller's value is in the coordination logic and safety guards, not in the plumbing. Use HA's built-in infrastructure for all I/O (service calls, state tracking, timers) and put all intelligence in the pure Python module where it can be tested without HA.

## Common Pitfalls

### Pitfall 1: Service Call to Unavailable Entity

**What goes wrong:** `hass.services.async_call("select", "select_option", {"entity_id": "select.sigen_...", "option": "..."})` silently succeeds even when the entity is unavailable (GitHub issue #97029). The EMS mode does not actually change but no error is raised.
**Why it happens:** HA service calls do not validate entity availability before execution for some entity types.
**How to avoid:** Before sending a command, check `hass.states.get(entity_id)` is not `None` and state is not `"unavailable"`. After sending, verify the state changed within a timeout window (command verification pattern). If verification fails, log a warning and retry once.
**Warning signs:** EMS mode sensor shows "command_charging" but SigenStor entity shows a different mode.
**Confidence:** HIGH (verified via GitHub issue #97029)

### Pitfall 2: Fuse Calculation Produces Negative or NaN Values

**What goes wrong:** `available_amps = fuse_rating - current_load + battery_contribution` can produce negative values when current_load is high, or NaN when sensor returns "unavailable" parsed as 0.
**Why it happens:** AppDaemon code identified in CONCERNS.md: "fuse calculation can produce negative values." Sensors returning "unavailable" get parsed as 0 or cause float conversion errors.
**How to avoid:** Hard-clamp ALL calculated values: `max(0.0, min(calculated, absolute_max))`. Parse sensor values with explicit "unavailable"/"unknown" checks returning safe defaults. The pure module's `clamp_amps()` function must be the single exit point for all amp values.
**Warning signs:** Negative charging limit sent to SigenStor. Battery charges at unexpected power. `ValueError` in logs.
**Confidence:** HIGH (directly documented in CONCERNS.md)

### Pitfall 3: Race Condition Between Schedule Update and EMS Enforcement

**What goes wrong:** BatteryScheduleCoordinator updates the schedule, firing a listener on EMSCoordinator. EMSCoordinator reads the new schedule and sends a mode change. Meanwhile, the old mode's charging limit is still active for a brief period with the new mode's parameters.
**Why it happens:** Coordinator chaining is asynchronous. The mode change and limit change are two separate service calls.
**How to avoid:** Always send mode change AND limit change together in the same update cycle. If mode changes to "command_charging", set the safe limit first, then change mode. If mode changes to "standby", change mode first, then zero the limit. Order matters for safety.
**Warning signs:** Brief period of high-power charging at transition points. Fuse trips during mode transitions.
**Confidence:** MEDIUM (architectural concern from AppDaemon EMS controller; mitigated by correct command ordering)

### Pitfall 4: Car Plugged-In Detection is Stale

**What goes wrong:** The EMS controller checks if a car is "scheduled and plugged in" to pause battery charging (EMS-03). But the plugged-in sensor might update slowly (car integrations poll every 5-30 minutes), so the EMS acts on stale data.
**Why it happens:** Car integrations (Skoda, VW) poll their cloud APIs infrequently. The plugged-in state may lag real-world by minutes.
**How to avoid:** Use the Easee charger status sensor (local, updates in seconds) as the primary indicator of "car plugged in" rather than the car integration's sensor. The Easee integration reports "awaiting_start" or "charging" when a cable is connected. If Easee reports a car connected AND a car is scheduled, trigger the priority override.
**Warning signs:** Battery continues charging when car is plugged in. Fuse trips because both charge simultaneously.
**Confidence:** MEDIUM (depends on user's specific sensor configuration; mitigated by using charger-side detection)

### Pitfall 5: PV Opportunistic Charging Oscillation

**What goes wrong:** Solar production fluctuates rapidly (clouds), causing the EMS to rapidly switch between "standby" and "command_charging" for PV opportunistic charging (EMS-08). Each mode switch takes seconds to take effect on the inverter, causing oscillation.
**Why it happens:** No hysteresis in PV power threshold. Every update cycle re-evaluates from scratch.
**How to avoid:** Add hysteresis: activate PV charging when solar exceeds 500W for 2+ consecutive checks; deactivate when solar drops below 300W for 2+ checks. Track PV state as a mini state machine (off -> pending_on -> on -> pending_off -> off).
**Warning signs:** Rapid mode switching in logs. Inverter error states from frequent mode changes.
**Confidence:** HIGH (standard control systems pattern; AppDaemon code had similar cycling issues)

### Pitfall 6: EMSCoordinator Created When Battery Module Disabled

**What goes wrong:** EMSCoordinator depends on BatteryScheduleCoordinator. If battery module is disabled, battery_coordinator is None, and EMSCoordinator crashes.
**Why it happens:** EMS is only meaningful when battery module is enabled, but the code might not guard this properly.
**How to avoid:** Only create EMSCoordinator when `battery_coordinator is not None`. Follow the Phase 2 pattern: `if battery_coordinator is not None: ems_coordinator = EMSCoordinator(...)`. The EMS sensor should only be created when ems_coordinator exists.
**Warning signs:** `AttributeError: 'NoneType' object has no attribute 'data'` at startup with battery disabled.
**Confidence:** HIGH (Phase 2 already handles this pattern for battery sensors)

## Code Examples

### SigenStor Control Entities Reference

Based on the SigenStor/Sigenergy integration analysis, these are the control entities the EMS coordinator targets:

```python
# SigenStor EMS control entities (from INTEGRATIONS.md)
EMS_MODE_ENTITY = "select.sigen_plant_remote_ems_control_mode"
CHARGE_LIMIT_ENTITY = "number.sigen_plant_ess_max_charging_limit"

# EMS mode options (SigenStor select entity options)
EMS_MODE_OPTIONS = {
    "command_charging": "Command Charging (PV First)",
    "max_self_consumption": "Maximum Self Consumption",
    "standby": "Standby",
}

# SigenStor Modbus register reference (for documentation):
# Register 40031: Remote EMS Control Mode
#   0 = PCS remote control
#   1 = Standby
#   2 = Maximum self-consumption (default)
#   3 = Command charging (grid first)
#   4 = Command charging (PV first) -- THIS IS WHAT WE USE
#   5 = Command discharging (PV first)
#   6 = Command discharging (battery first)

# Register 40032: ESS Max Charging Limit (kW)
# Register 40034: ESS Max Discharging Limit (kW)
```

### Sending EMS Mode Command via HA Service Call

```python
# Source: HA developer docs + INTEGRATIONS.md service call analysis
async def _send_ems_mode(self, mode: str) -> None:
    """Send EMS mode change to SigenStor via HA service call."""
    entity_id = self.config_entry.options.get(CONF_EMS_SELECT_ENTITY)
    if not entity_id:
        _LOGGER.warning("EMS select entity not configured")
        return

    # Check entity is available before sending
    state = self.hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        _LOGGER.warning("EMS entity %s is unavailable, skipping command", entity_id)
        return

    option = EMS_MODE_MAP.get(mode)
    if option is None:
        _LOGGER.error("Unknown EMS mode: %s", mode)
        return

    _LOGGER.info("Setting EMS mode to %s (%s)", mode, option)
    await self.hass.services.async_call(
        "select", "select_option",
        {"entity_id": entity_id, "option": option},
        blocking=True,
    )
```

### Sending Charging Limit via HA Service Call

```python
async def _send_charge_limit(self, limit_kw: float) -> None:
    """Send charging limit to SigenStor via HA service call."""
    entity_id = self.config_entry.options.get(CONF_CHARGE_LIMIT_ENTITY)
    if not entity_id:
        return

    state = self.hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        _LOGGER.warning("Charge limit entity %s unavailable", entity_id)
        return

    # Clamp to safe range before sending
    clamped = max(0.0, min(limit_kw, MAX_CHARGE_LIMIT_KW))
    _LOGGER.info("Setting charge limit to %.1f kW (clamped from %.1f)", clamped, limit_kw)
    await self.hass.services.async_call(
        "number", "set_value",
        {"entity_id": entity_id, "value": clamped},
        blocking=True,
    )
```

### Command Verification Pattern (EMS-05)

```python
async def _verify_command(self, entity_id: str, expected_state: str, timeout_s: float = 10.0) -> bool:
    """Verify a command took effect by reading back entity state.

    Returns True if entity state matches expected within timeout.
    """
    import asyncio
    deadline = dt_util.utcnow() + timedelta(seconds=timeout_s)
    check_interval = 2.0  # seconds between checks

    while dt_util.utcnow() < deadline:
        state = self.hass.states.get(entity_id)
        if state is not None and state.state == expected_state:
            return True
        await asyncio.sleep(check_interval)

    _LOGGER.warning(
        "Command verification failed: %s expected '%s' but got '%s'",
        entity_id,
        expected_state,
        state.state if state else "None",
    )
    return False
```

### EMSData Dataclass (Coordinator Output)

```python
@dataclass(frozen=True, slots=True)
class EMSData:
    """Output of the EMS coordinator."""
    current_mode: str             # Active EMS mode
    target_mode: str              # What mode the schedule wants
    charge_limit_kw: float        # Current safe charging limit
    fuse_headroom_amps: float     # Available fuse headroom
    override_reason: str | None   # Why mode differs from schedule (car priority, PV, etc.)
    command_verified: bool        # Whether last command was verified
    last_command_time: datetime | None  # When last command was sent
    car_override_active: bool     # Whether car priority paused battery charging
    pv_charging_active: bool      # Whether PV opportunistic charging is active
```

### EMS Status Sensor

```python
class EMSStatusSensor(EnergyManagerEntity, SensorEntity):
    """Sensor showing EMS controller status."""
    _attr_translation_key = "ems_status"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ems_status"

    @property
    def native_value(self) -> str:
        data: EMSData | None = self.coordinator.data
        if data is None:
            return "unknown"
        return data.current_mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data: EMSData | None = self.coordinator.data
        if data is None:
            return {}
        return {
            "target_mode": data.target_mode,
            "charge_limit_kw": round(data.charge_limit_kw, 2),
            "fuse_headroom_amps": round(data.fuse_headroom_amps, 1),
            "override_reason": data.override_reason,
            "command_verified": data.command_verified,
            "car_override_active": data.car_override_active,
            "pv_charging_active": data.pv_charging_active,
        }
```

### Config Flow: New EMS Config Fields

The battery step in config_flow.py needs these additional fields:

```python
# New constants for const.py
CONF_FUSE_RATING = "fuse_rating"
CONF_EMS_SELECT_ENTITY = "ems_select_entity"
CONF_CHARGE_LIMIT_ENTITY = "charge_limit_entity"
CONF_L_CURRENT_ENTITY = "l_current_entity"
CONF_PV_POWER_ENTITY = "pv_power_entity"
CONF_CHARGER_STATUS_ENTITY_EMS = "charger_status_entity"  # Already exists from Phase 1

# Defaults
DEFAULT_FUSE_RATING = 20  # Amps
DEFAULT_SAFETY_BUFFER_AMPS = 2.0
DEFAULT_PV_THRESHOLD_W = 500
MAX_FUSE_RATING = 63
MIN_FUSE_RATING = 10
```

## State of the Art

| Old Approach (AppDaemon) | Current Approach (HA Integration) | Impact |
|--------------------------|----------------------------------|--------|
| 5-second polling loop for state detection | `async_track_state_change_event` + 30s coordinator fallback | Zero latency for fuse-critical changes; 6x less CPU usage |
| `self.call_service("select/select_option", ...)` | `await hass.services.async_call("select", "select_option", ..., blocking=True)` | Async-safe; proper error propagation; no event loop blocking |
| `set_state()` for output sensor | Proper SensorEntity with CoordinatorEntity base | Entity registry managed; device grouping; survives restart |
| Manual `previous_states` dict for change detection | DataUpdateCoordinator with `always_update=False` | HA handles change detection; entities only notified on actual changes |
| Bare `except:` blocks | Specific exception types + structured logging | Debuggable; no silent failures |
| No command verification | Read-back verification after mode change | Catches silent failures; EMS-05 requirement |
| Math on unvalidated sensor values | Hard-clamp all calculated values through `clamp_amps()` | Prevents negative/overflow values from reaching hardware; EMS-04 |

**Deprecated/outdated:**
- AppDaemon's `self.call_service()`: Replaced by `hass.services.async_call()` in native integrations.
- Polling for state changes via `check_state_changes()`: Replaced by event-driven `async_track_state_change_event`.
- `set_state()` for EMS output: Replaced by proper SensorEntity with coordinator.

## Open Questions

1. **Exact SigenStor select option strings**
   - What we know: The Modbus register values are documented (0-6). The INTEGRATIONS.md lists select options like "Command Charging (PV First)", "Maximum Self Consumption", "Standby". The SigenStor HA integration exposes these as a `select` entity.
   - What's unclear: The exact string values may differ between SigenStor integration versions (HACS vs newer Sigenergy-Local-Modbus). Need to verify at runtime.
   - Recommendation: Store the option-string mapping as constants that can be adjusted. On first command, read the entity's `options` attribute to validate our mapping is correct. If the expected option is not in the entity's available options, log an error and do not send the command.
   - Confidence: MEDIUM (the strings come from INTEGRATIONS.md analysis of existing AppDaemon code; real entities may differ)

2. **Whether to use 30s or shorter EMS coordinator interval**
   - What we know: AppDaemon used 5s polling because it polled for state changes manually. HA provides event-driven listeners that fire immediately. The coordinator's polling is a fallback/catch-up mechanism.
   - What's unclear: Whether 30s is responsive enough for fuse protection after adding event-driven listeners.
   - Recommendation: Start with 30 seconds. Event-driven listeners on `sensor.highest_l_current` and charger status provide sub-second response for critical changes. The 30s poll catches anything the listeners miss. Can reduce to 15s if testing shows gaps.
   - Confidence: HIGH (event-driven + polling hybrid is the documented HA best practice)

3. **How fuse rating should be configured -- config flow vs NumberEntity**
   - What we know: EMS-06 says "fuse rating is a required config field with validation." The AppDaemon version reads it from apps.yaml. It rarely changes (hardware constant).
   - What's unclear: Whether users should be able to adjust it at runtime (NumberEntity) or only during config (config flow / options flow).
   - Recommendation: Add fuse rating to the battery config flow step as a required NumberSelector field. Store in `entry.options`. Do NOT make it a NumberEntity -- it is a hardware property that should not change casually. Changing it requires going through options flow (Phase 6). For Phase 3, it lives in `entry.options` set during initial config.
   - Confidence: HIGH (fuse rating is a hardware constant; config-time is the right place)

4. **Command ordering safety for mode transitions**
   - What we know: When switching TO command_charging, the limit should be set first (so charging starts at the safe rate). When switching FROM command_charging, the mode should change first (to stop charging) then the limit can be zeroed.
   - What's unclear: Whether the SigenStor integration handles this ordering internally, or whether the EMS controller must enforce it.
   - Recommendation: Always enforce safe ordering in the EMS controller regardless of SigenStor's internal behavior. When switching to charge: set limit, wait 1s, set mode. When switching away: set mode, wait 1s, zero limit. This is defense-in-depth.
   - Confidence: MEDIUM (depends on SigenStor implementation; defense-in-depth is always correct)

5. **Which config flow step to add EMS fields to**
   - What we know: Current battery step has SOC entity, battery power entity, capacity, and solar forecast entity. EMS control entities (select, charge limit number, L-current sensor, fuse rating) are also battery-related.
   - What's unclear: Whether to add them to the existing battery step (making it longer) or create a new "ems" config flow step.
   - Recommendation: Create a new "ems" step in the config flow that follows the battery step. This keeps the battery step focused on scheduling inputs and the EMS step focused on control/safety inputs. Auto-detect SigenStor control entities the same way Phase 1 auto-detects sensor entities.
   - Confidence: HIGH (separation of concerns; consistent with multi-step config flow pattern)

## Sources

### Primary (HIGH confidence)
- Phase 1-2 source code at `/Users/johan.yourstone/Git/energy_manager/custom_components/energy_manager/` -- coordinator.py (coordinator chaining pattern), __init__.py (module-conditional setup), sensor.py (entity patterns), battery_scheduler.py (pure module pattern)
- AppDaemon EMS architecture at `.planning/codebase/ARCHITECTURE.md` -- EMS Controller entry point, data flow, key abstractions (EMS Mode State Machine, Fuse Protection Constraint)
- AppDaemon EMS concerns at `.planning/codebase/CONCERNS.md` -- fuse calculation bugs (negative values), state consistency issues, no retry logic, bare exception handlers
- AppDaemon integrations at `.planning/codebase/INTEGRATIONS.md` -- exact service calls (`select/select_option`, `number/set_value`), SigenStor entity IDs, Easee entity IDs, `sensor.highest_l_current` for fuse protection
- Domain pitfalls at `.planning/research/PITFALLS.md` -- Pitfall 6 (unsafe device control), Pitfall 9 (state machine without formal model), Pitfall 5 (polling intervals)
- Phase 2 research at `.planning/phases/02-home-battery-schedule/02-RESEARCH.md` -- coordinator chaining, pure module, BatteryScheduleData contract (target_ems_mode field)

### Secondary (MEDIUM confidence)
- HA Developer Docs: DataUpdateCoordinator (Context7 `/websites/developers_home-assistant_io`) -- update_interval, _async_update_data, async_add_listener
- HA Developer Docs: Service calls -- `hass.services.async_call()` with blocking=True
- SigenStor Modbus register documentation (https://github.com/TypQxQ/Sigenergy-Home-Assistant-Integration) -- EMS mode register 40031, charging limit register 40032, mode option strings
- homeassistant-charging-control (https://github.com/mikrohard/homeassistant-charging-control) -- fuse protection patterns, safety margins, min 6A threshold, hysteresis control
- GitHub issue #97029 -- `select.select_option` silently succeeds on unavailable entities

### Tertiary (LOW confidence)
- Community posts on dynamic EV charging and fuse protection patterns -- general approach validation
- SigenStor HA integration select option string values -- may vary between versions; needs runtime verification

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all HA APIs verified via Phase 1-2 code and Context7/dev docs
- Architecture: HIGH -- follows proven Phase 1-2 patterns (coordinator chaining, pure module, event-driven + polling hybrid)
- Fuse protection algorithm: HIGH -- documented in AppDaemon INTEGRATIONS.md and CONCERNS.md; safety clamping is well-understood control systems pattern
- SigenStor control interface: MEDIUM -- entity IDs and service calls documented in INTEGRATIONS.md from existing AppDaemon code; exact option strings need runtime verification
- Command verification: MEDIUM -- pattern is straightforward but timing/retry policy needs empirical tuning
- Pitfalls: HIGH -- directly sourced from CONCERNS.md documented bugs and PITFALLS.md phase-specific warnings

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (stable domain; HA core APIs unlikely to change in 30 days)

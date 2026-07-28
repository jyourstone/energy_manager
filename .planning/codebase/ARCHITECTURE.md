# Architecture

**Analysis Date:** 2025-02-15

## Pattern Overview

**Overall:** Distributed multi-app microservices pattern with event-driven state synchronization

**Key Characteristics:**
- Five independent AppDaemon apps communicating exclusively through Home Assistant entity state
- No direct inter-app method calls or shared databases
- Periodic polling combined with event-driven state listeners
- Configuration-driven behavior via `apps.yaml`
- Shared logging infrastructure for observability

## Layers

**Presentation/UI Layer:**
- Purpose: Sensor entities representing system state and schedules
- Location: Home Assistant entity state
- Contains: Output sensor entities (`sensor.battery_charge_schedule_py`, `sensor.ems_controller_status`, etc.)
- Depends on: Core calculation engines
- Used by: Home Assistant automations, dashboards, and user interfaces

**Domain/Business Logic Layer:**
- Purpose: Battery scheduling, EMS control, car charging optimization
- Location: `apps/home_battery_manager.py`, `apps/ems_controller.py`, `apps/car_charging_manager.py`, `apps/easee_controller.py`
- Contains: Complex scheduling algorithms, energy calculations, state machines
- Depends on: Input sensors from Home Assistant, shared logging utilities
- Used by: Presentation layer (via state updates), cross-app coordination (via entity listeners)

**Infrastructure/Support Layer:**
- Purpose: Logging, rotation, configuration management
- Location: `apps/logging_utils.py`, `apps/log_rotation_helper.py`
- Contains: File logger setup, log rotation management, configuration helpers
- Depends on: Python stdlib
- Used by: All domain logic apps

**Integration/Data Layer:**
- Purpose: Sensor data acquisition from Home Assistant
- Location: Home Assistant HASS API (via AppDaemon)
- Contains: Entity state retrieval, sensor listeners, service calls
- Depends on: Home Assistant instance
- Used by: All domain apps

## Data Flow

**Charge Schedule Calculation Flow:**

1. **Home Battery Manager** reads Nordpool electricity prices from `sensor.nordpool_kwh_se4_sek_2_10_025`
2. Fetches real-time battery state from 15+ input sensors (SOC, capacity, consumption, production)
3. Calculates multi-cycle charge/discharge schedule using peak grouping algorithm
4. Writes complete schedule (with time slots, prices, actions) to `sensor.battery_charge_schedule_py` attributes
5. **EMS Controller** listens to battery schedule state changes and acts on it
6. EMS sets battery EMS mode based on current scheduled action via `select.sigen_plant_remote_ems_control_mode`

**Car Charging Optimization Flow:**

1. **Car Charging Manager** instances (one per car) read Nordpool prices and car state
2. Fetch car battery level, departure time, charging target from Home Assistant inputs
3. Calculate optimal charging windows based on price and available charging power
4. Write schedule to output sensors (`sensor.enyaq_car_charging_manager_py`, `sensor.id_3_car_charging_manager_py`)
5. **Easee Controller** monitors car schedule sensors and adjusts charger limits accordingly
6. Easee reads available solar power from `sensor.solar_net_available_power_adjusted_easee`
7. Adjusts Easee dynamic limit via service call to match schedule and PV availability

**EMS Coordination Flow:**

1. **EMS Controller** monitors multiple inputs simultaneously:
   - Battery charge schedule (from home_battery_manager)
   - Car charging schedules (from both car_charging_manager instances)
   - PV power, battery SOC, fuse current, Easee status
2. Applies logic: If car is scheduled AND plugged in, pause home battery charging to reserve fuse capacity
3. Calculates safe battery charging limit considering fuse constraints
4. Updates battery EMS mode every 5 seconds (configurable `main_check_interval`)
5. Easee Controller independently checks if battery/car charging fits within fuse limits

**State Management:**

- **State Source of Truth:** Home Assistant entity state (immutable state database)
- **Stateful Tracking:** Each app maintains internal state only during runtime (discarded on restart)
- **Schedule Persistence:** Historical schedule data recovered from entity attributes on app restart
- **Calculation Caching:** Apps use entity attribute maps to preserve charge/discharge decisions across slots

## Key Abstractions

**Schedule Object:**
- Purpose: Represents 15-minute time slot with price and action flags
- Examples: `home_battery_manager.py` line 405-412, `car_charging_manager.py` (similar pattern)
- Pattern: Dict with keys: `time` (ISO), `price` (float), `charge` (bool), `discharge` (bool)
- Used by: All scheduling apps to build and compare schedules

**Peak Grouping Algorithm:**
- Purpose: Identifies separate discharge opportunity windows based on price clusters
- Examples: `home_battery_manager.py` method `_group_into_peaks()` (line 182-204)
- Pattern: Groups profitable discharge slots with configurable gap threshold (`peak_gap_hours`)
- Enables: Multi-cycle scheduling (charge → discharge → repeat)

**EMS Mode State Machine:**
- Purpose: Determines correct battery operating mode based on current conditions
- Examples: `home_battery_manager.py` line 152-158 (target mode calculation), `ems_controller.py` (mode enforcement)
- Pattern: Three states - "command_charging", "max_self_consumption", "standby"
- Condition logic: Current interval action (charging/discharging/idle) determines mode

**Virtual Energy Tracking:**
- Purpose: Simulates battery state through schedule to enable multi-cycle charging decisions
- Examples: `home_battery_manager.py` line 452-521 (peak processing loop)
- Pattern: Tracks energy level as schedule is processed, not actual battery state
- Enables: Smart buffer allocation and fair energy distribution across peaks

**Fuse Protection Constraint:**
- Purpose: Limits simultaneous battery + car charging to stay within electrical capacity
- Examples: `ems_controller.py` fuse calculation logic, `easee_controller.py` amp-based limits
- Pattern: Converts power/current values to amps, applies buffer, enforces max
- Enabled by: Coordination between EMS and Easee via dynamic limits

## Entry Points

**Home Battery Manager:**
- Location: `apps/home_battery_manager.py` class `HomeBatteryManager`
- Triggers:
  - Scheduled minutely callback (runs every minute, filters to 5-min intervals)
  - Startup trigger `run_in(recalc, 1)`
  - Manual invocation via `recalc()` method
- Responsibilities:
  - Calculate battery charge/discharge schedule
  - Analyze price peaks and production/consumption balance
  - Emit schedule to sensor entity with all metadata

**Car Charging Manager:**
- Location: `apps/car_charging_manager.py` class `CarChargingManager` (instantiated twice in config)
- Triggers:
  - Scheduled 15-minute callback
  - State changes on departure time, battery level, or charging target inputs
  - Max charge power sensor changes
- Responsibilities:
  - Calculate optimal charging windows for each car
  - Account for price, target SOC, and available power
  - Handle fallback charging during off-peak hours

**EMS Controller:**
- Location: `apps/ems_controller.py` class `EMSController`
- Triggers:
  - Main check loop every 5 seconds (configurable)
  - State listeners on multiple sensor inputs
- Responsibilities:
  - Enforce battery EMS mode based on schedule and fuse constraints
  - Coordinate battery vs car charging priority
  - Apply fuse protection limiting algorithm

**Easee Controller:**
- Location: `apps/easee_controller.py` class `EaseeController`
- Triggers:
  - Scheduled callback on state changes
  - Listeners on car schedule, PV power, and Easee status
- Responsibilities:
  - Set dynamic charging limits on Easee charger
  - Switch between solar and grid charging modes
  - Protect fuse by limiting charger amps

## Error Handling

**Strategy:** Try-catch with graceful degradation, fallbacks to sensible defaults

**Patterns:**

- **Price/SOC Parsing:** Lines like `try: float(value)` with `except (ValueError, TypeError)` → logs warning and uses fallback default
- **Sensor Read Failures:** Check entity state with `or 0` default values (e.g., `home_battery_manager.py` line 113-118)
- **Production Validation:** Dawn/dusk sensor comparison to detect day/night without production sensor (line 280-298)
- **Logger Setup Failures:** If file logger initialization fails, fall back to AppDaemon logger only (line 26-28)
- **Log Rotation Errors:** Failed rotation attempts fall back to regular file handler (line 57-60)
- **Schedule Build Exceptions:** Return empty list rather than crash, logged as ERROR (line 535-538)

## Cross-Cutting Concerns

**Logging:**
- Unified setup via `logging_utils.setup_app_logger()` called in every app's `initialize()`
- Hybrid logging: Both AppDaemon console + file with optional rotation
- File logs in `logs/` directory (auto-created) with configurable rotation (size-based)
- Log levels controlled via level parameter (default DEBUG)

**Validation:**
- Entity existence checked implicitly via state retrieval (returns None if missing)
- Numeric bounds validated with `float()` try-catch pattern
- Time/date parsing with ISO format and timezone handling
- Sum calculations protected against division by zero (e.g., line 371-378)

**Authentication:**
- Handled entirely by Home Assistant HASS plugin (AppDaemon token auth)
- AppDaemon configuration: `appdaemon.yaml` line 10 uses `!env_var SUPERVISOR_TOKEN`
- No direct auth in app code

**Scheduling:**
- Minutely/scheduled callbacks use `run_minutely()` with minute-level filtering for reliability
- Prevents `run_every()` which can stop working (documented bug workaround)
- Duplicate execution prevention via `last_run_minute` tracking
- 5-minute intervals for battery, 15-minute for cars, 5-second for EMS

**Coordination:**
- State-based, not event-based: Apps read entity state on their schedule
- No race conditions because HASS state updates are atomic
- Loose coupling: Each app can fail independently without cascading
- Ordering implicit: Schedule creation (battery) → consumption (EMS/Easee) one schedule period later

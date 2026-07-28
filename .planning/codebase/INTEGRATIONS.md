# External Integrations

**Analysis Date:** 2025-02-15

## APIs & External Services

**Home Assistant Services:**
- `easee/action_command` - Control Easee charger (start/stop charging)
  - Called in `easee_controller.py` (lines 439, 444, 456, 480, 577, 1006, 1151, 1342, 1362, 1453, 1503)
  - Parameters: `entity_id`, command type (e.g., start/stop)

- `easee/set_charger_dynamic_limit` - Set Easee charger power limit
  - Called in `easee_controller.py` (lines 463, 820, 1136, 1145, 1381, 1516)
  - Parameters: `entity_id`, `limit` (in amps)

- `easee/set_charger_phase_mode` - Switch between 1-phase and 3-phase charging
  - Called in `easee_controller.py` (line 1394)
  - Parameters: `entity_id`, `phase_mode`

- `select/select_option` - Set Sigen EMS control mode
  - Called in `ems_controller.py` (lines 346, 628, 645, 733)
  - Entity: `select.sigen_plant_remote_ems_control_mode`
  - Options: "Command Charging (PV First)", "Maximum Self Consumption", "Standby"

- `number/set_value` - Set numeric values (battery charge limits, power limits)
  - Called in `ems_controller.py` (lines 382, 581, 822)
  - Entity: `number.sigen_plant_ess_max_charging_limit`

- `input_boolean/turn_off` - Turn off force charging boolean
  - Called in `easee_controller.py` (line 528)
  - Entity: `input_boolean.easee_force_charging`

- `notify.mobile_app_johans_iphone` - Send mobile notifications
  - Called in `easee_controller.py` (lines 572, 1000)
  - Used for charging alerts and warnings

**Home Assistant Sensors (Monitoring):**
- `sensor.nordpool_kwh_se4_sek_2_10_025` - Electricity prices
  - Nordpool integration sensor
  - Used in: `home_battery_manager.py`, `car_charging_manager.py`
  - Attributes: `raw_today`, `raw_tomorrow`

**Sigen Battery Integration:**
- Status sensors:
  - `sensor.sigen_battery_battery_state_of_charge` - Battery SOC (%)
  - `sensor.sigen_battery_battery_power` - Battery power (kW, negative=discharging)
  - `sensor.sigen_battery_pv_power` - PV production (kW)
  - `sensor.sigen_battery_ess_rated_charging_power` - Max charge rate
  - `sensor.sigen_battery_ess_rated_discharging_power` - Max discharge rate
  - `binary_sensor.sigen_battery_battery_charging` - Charging status

- Control entities:
  - `select.sigen_plant_remote_ems_control_mode` - EMS mode selection
  - `number.sigen_plant_ess_max_charging_limit` - Charge limit (kW)
  - `sensor.sigen_plant_available_max_charging_capacity` - Available capacity
  - `sensor.sigen_plant_available_max_charging_power` - Available power
  - `sensor.sigen_plant_available_max_discharging_capacity` - Available discharge capacity
  - `sensor.sigen_plant_available_max_discharging_power` - Available discharge power

**Easee Charger Integration:**
- Status sensors:
  - `sensor.easee_home_25562_status` - Charger status (charging, idle, completed, etc.)
  - `sensor.easee_home_25562_power` - Current charging power (kW)
  - `sensor.easee_home_25562_dynamic_charger_limit` - Current power limit (A)

**Vehicle Integrations:**
- Skoda Enyaq:
  - `sensor.skoda_enyaq_85_battery_percentage` - Battery level (%)
  - `sensor.johans_car_calculated_charging_power` - Max charge rate (kW)
  - `sensor.johans_car_home_and_plugged_in` - Plugged in status

- VW ID.3:
  - `sensor.id_3_battery_level` - Battery level (%)
  - `sensor.cissis_car_calculated_charging_power` - Max charge rate (kW)
  - `sensor.cissis_car_home_and_plugged_in` - Plugged in status

**Solar Production:**
- `sensor.energy_production_today` - Daily solar production (kWh)
- `sensor.energy_production_today_2` - Secondary solar production sensor (optional)
- `sensor.energy_production_today_remaining` - Remaining daily production (kWh)
- `sensor.energy_production_today_remaining_2` - Secondary remaining sensor (optional)
- `sensor.solar_net_available_power_adjusted_easee` - Available solar power for charger (kW)

**Home Consumption:**
- `sensor.house_average_consumed_power_filtered` - Average home consumption (kW)

**Grid Protection:**
- `sensor.highest_l_current` - Highest phase current (Amps)
  - Monitored by: `ems_controller.py`, `easee_controller.py`
  - Used for fuse protection logic

**Sun Position:**
- `sensor.sun_next_dawn` - Next sunrise time
- `sensor.sun_next_dusk` - Next sunset time

## Data Storage

**State Storage:**
- Home Assistant entity state database
- Sensor attributes for persisting schedules and history

**File Storage:**
- Local filesystem only
- Log files: `/config/appdaemon/logs/` (with optional rotation)
  - `home_battery.log`
  - `car_charging_manager.log`
  - `ems_controller.log`
  - `easee_controller.log`
  - Log rotation: Configurable per-app in `apps.yaml`

**Caching:**
- None detected - relies on Home Assistant state caching

## Authentication & Identity

**Auth Provider:**
- Home Assistant Supervisor token
- Passed via environment variable: `SUPERVISOR_TOKEN`
- Used for AppDaemon to authenticate with Home Assistant API

**Implementation:**
- Token-based authentication in `appdaemon.yaml`
- No user credentials stored in app code

## Monitoring & Observability

**Error Tracking:**
- File-based logging to disk
- Logs written by: `logging_utils.py`, `log_rotation_helper.py`
- Log level: DEBUG by default (configurable per app)

**Logs:**
- Dual logging: AppDaemon console + file
- File handler with automatic rotation support
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
- Log directory created at: `/config/appdaemon/logs/`

**Status Sensors:**
- `sensor.battery_charge_schedule_py` - Battery schedule output
- `sensor.enyaq_car_charging_manager_py` - Enyaq charging schedule
- `sensor.id_3_car_charging_manager_py` - ID.3 charging schedule
- `sensor.ems_controller_status` - EMS controller status with attributes
- `sensor.easee_controller_status` - Easee controller status with attributes

## CI/CD & Deployment

**Hosting:**
- Home Assistant with AppDaemon add-on
- Deployment: Docker container via Home Assistant ecosystem

**CI Pipeline:**
- None detected - scripts deployed directly via Home Assistant file system

## Environment Configuration

**Required env vars:**
- `SUPERVISOR_TOKEN` - Home Assistant API token (passed by supervisor)

**Secrets location:**
- `appdaemon.yaml` - Uses `!env_var SUPERVISOR_TOKEN` syntax
- No `.env` files; configuration via YAML and Home Assistant secrets

## Webhooks & Callbacks

**Incoming:**
- No incoming webhooks detected
- Apps communicate via Home Assistant entity state changes

**Outgoing:**
- Mobile notifications to: `notify.mobile_app_johans_iphone`
- Home Assistant service calls for device control
- No external webhook endpoints

## Data Flow Integration

**Price Data:**
- Nordpool sensor → Home Battery Manager → Battery schedule output
- Nordpool sensor → Car Charging Manager → Car schedule output

**Schedule Execution:**
- Home Battery Manager output → EMS Controller → Sigen battery control
- Car Charging Manager output → Easee Controller → Easee charger control
- Both controllers monitor `sensor.highest_l_current` for fuse protection

**Coordination:**
- EMS Controller and Easee Controller coordinate via:
  - Shared car charging schedule sensors
  - Shared fuse limit monitoring
  - Shared car home/plugged status sensors
  - No direct app-to-app communication; all via entity state

---

*Integration audit: 2025-02-15*

"""Constants for the Energy Manager integration."""

# Integration domain
DOMAIN = "energy_manager"

# Config entry versioning
CONFIG_VERSION = 1
CONFIG_MINOR_VERSION = 1

# --- Nordpool configuration ---

# Config keys
CONF_NORDPOOL_SENSOR = "nordpool_sensor"
CONF_NORDPOOL_TYPE = "nordpool_type"

# Nordpool variant types
NORDPOOL_TYPE_HACS = "hacs"
NORDPOOL_TYPE_NATIVE = "native"

# Price update interval (minutes)
PRICE_UPDATE_INTERVAL_MINUTES = 5

# --- Module toggles ---

CONF_BATTERY_ENABLED = "battery_enabled"
CONF_EV_ENABLED = "ev_enabled"
CONF_APPLIANCES_ENABLED = "appliances_enabled"

# Module identifiers
MODULE_BATTERY = "battery"
MODULE_EV = "ev"
MODULE_APPLIANCES = "appliances"

# --- Home Battery configuration ---

CONF_SOC_ENTITY = "soc_entity"
CONF_BATTERY_POWER_ENTITY = "battery_power_entity"

# --- EV Charging configuration ---

CONF_CHARGER_STATUS_ENTITY = "charger_status_entity"
CONF_CHARGER_POWER_ENTITY = "charger_power_entity"

# --- Car subentry configuration ---

SUBENTRY_TYPE_CAR = "car"

CONF_CAR_NAME = "car_name"
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_BATTERY_LEVEL_ENTITY = "battery_level_entity"
CONF_CHARGER_CONNECTED_ENTITY = "charger_connected_entity"
CONF_LOCATION_ENTITY = "location_entity"

# Per-car phase capability (EV-12): how many phases this car actually draws
# on when the charger is in 3-phase mode. Stored as a string ("1"/"2"/"3")
# since it is a SelectSelector option value.
CONF_PHASE_CAPABILITY = "phase_capability"
DEFAULT_PHASE_CAPABILITY = "3"

# --- Battery schedule configuration ---

# Config keys for battery schedule
CONF_FORECAST_SOLAR_ENTITY = "forecast_solar_entity"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"

# Battery schedule update interval (minutes) -- fallback polling
BATTERY_SCHEDULE_UPDATE_INTERVAL_MINUTES = 5

# Number entity defaults
DEFAULT_CHARGE_THRESHOLD = 1.0  # SEK/kWh
DEFAULT_DISCHARGE_THRESHOLD = 0.50  # SEK/kWh
DEFAULT_MAX_CHARGE_POWER_KW = 8.0  # kW

# Number entity limits
MIN_PRICE_THRESHOLD = 0.0
MAX_PRICE_THRESHOLD = 10.0
PRICE_THRESHOLD_STEP = 0.01
# Export spikes can sit far above normal price levels -- own, higher cap
MAX_EXPORT_SPIKE_THRESHOLD = 20.0

MIN_CHARGE_POWER_KW = 0.0
MAX_CHARGE_POWER_KW = 15.0
CHARGE_POWER_STEP_KW = 0.1

# Peak grouping
DEFAULT_PEAK_GAP_HOURS = 2.0
CONF_PEAK_GAP_HOURS = "peak_gap_hours"
MIN_PEAK_GAP_HOURS = 0.5
MAX_PEAK_GAP_HOURS = 12.0

# SOC constraints
DEFAULT_MIN_SOC_PCT = 10.0
DEFAULT_MAX_SOC_PCT = 95.0

# Default battery capacity if not configured
DEFAULT_BATTERY_CAPACITY_KWH = 10.0

# --- BATT-15 algorithm tuning options (config + options flow battery step) ---

CONF_CHARGE_BUFFER_PCT = "charge_buffer_pct"
DEFAULT_CHARGE_BUFFER_PCT = 20.0  # %
MIN_CHARGE_BUFFER_PCT = 0.0
MAX_CHARGE_BUFFER_PCT = 100.0

CONF_PRODUCTION_FACTOR = "production_factor"
DEFAULT_PRODUCTION_FACTOR = 0.8
MIN_PRODUCTION_FACTOR = 0.1
MAX_PRODUCTION_FACTOR = 1.0

CONF_ESTIMATED_CHARGE_POWER_KW = "estimated_charge_power_kw"
DEFAULT_ESTIMATED_CHARGE_POWER_KW = 6.0  # kW
MIN_ESTIMATED_CHARGE_POWER_KW = 0.5
MAX_ESTIMATED_CHARGE_POWER_KW = 22.0

# Rolling house-consumption average used to size BATT-15 energy needs
MEAN_CONSUMPTION_WINDOW_HOURS = 48.0
DEFAULT_MEAN_CONSUMPTION_KW = 0.5
# Minimum gap between samples -- refreshes are event-driven (SOC/price/
# Forecast.Solar updates), not fixed-cadence, so without this the rolling
# mean is skewed toward chatty sensors and the sample list grows unbounded.
MIN_CONSUMPTION_SAMPLE_INTERVAL_MINUTES = 1.0
# Consumption-sample persistence (one HA Store per config entry) -- restored
# at setup so the first refresh after a restart already uses the rolling
# mean; saves are delayed to batch appends instead of writing every cycle.
CONSUMPTION_STORAGE_VERSION = 1
CONSUMPTION_STORAGE_SAVE_DELAY_SECONDS = 30.0

# Stage-1 forecast-accuracy telemetry (observe-only) -- daily
# forecast-vs-actual records plus the in-flight day persisted in their own
# Store (separate file from the consumption samples by design; written at
# the local-midnight rollover, with delayed batched saves for the in-flight
# day so a mid-day restart or reload does not lose it).
FORECAST_ACCURACY_STORAGE_VERSION = 1
FORECAST_ACCURACY_SAVE_DELAY_SECONDS = 30.0

# Solar-activation latch persistence (EaseeCoordinator) -- without it a
# config-entry reload or HA restart during active solar-only charging
# resets the latch, so the in-progress charge is stopped as an
# unauthorized session and the charger is locked out for the full
# activation delay. Delayed batched saves like the two stores above; a
# clean unload flushes, so the delay only costs data on a hard crash.
SOLAR_TRACKER_STORAGE_VERSION = 1
SOLAR_TRACKER_SAVE_DELAY_SECONDS = 30.0

# --- BATT-14 economics number entities (RestoreNumber, hub-level) ---
# Reuses MIN_PRICE_THRESHOLD/MAX_PRICE_THRESHOLD/PRICE_THRESHOLD_STEP above
# (same SEK/kWh range as the charge/discharge thresholds).
DEFAULT_BATTERY_CYCLE_COST = 0.0  # SEK/kWh -- 0 disables the BATT-14 derivation
DEFAULT_GRID_TRANSFER_FEE = 0.0  # SEK/kWh
DEFAULT_ELECTRICITY_COMPANY_FEE = 0.0  # SEK/kWh -- actual-price sensor only

# Economics wizard step -- seed values for the tunable number entities
CONF_BATTERY_CYCLE_COST = "battery_cycle_cost"
CONF_GRID_TRANSFER_FEE = "grid_transfer_fee"
CONF_ELECTRICITY_COMPANY_FEE = "electricity_company_fee"
CONF_MAX_CHARGE_POWER = "max_charge_power"

# --- BATT-17 export arbitrage (opt-in; threshold unset/0 = feature off) ---
CONF_EXPORT_SPIKE_THRESHOLD = "export_spike_threshold"
CONF_EXPORT_RESERVE_SOC_PCT = "export_reserve_soc_pct"
DEFAULT_EXPORT_RESERVE_SOC_PCT = 20.0

# --- EMS Controller configuration ---

CONF_FUSE_RATING_AMPS = "fuse_rating_amps"
CONF_FUSE_SAFETY_BUFFER_AMPS = "fuse_safety_buffer_amps"
CONF_EMS_SELECT_ENTITY = "ems_select_entity"
CONF_CHARGE_LIMIT_ENTITY = "charge_limit_entity"
CONF_DISCHARGE_LIMIT_ENTITY = "discharge_limit_entity"
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_GRID_PHASE_A_ENTITY = "grid_phase_a_entity"
CONF_GRID_PHASE_B_ENTITY = "grid_phase_b_entity"
CONF_GRID_PHASE_C_ENTITY = "grid_phase_c_entity"
CONF_PV_POWER_ENTITY = "pv_power_entity"

# Sensor-unavailable fallback behavior for fuse-critical current sensors
CONF_SENSOR_FAIL_BEHAVIOR = "sensor_fail_behavior"
SENSOR_FAIL_BEHAVIOR_ASSUME_LOAD = "assume_load"
SENSOR_FAIL_BEHAVIOR_BLOCK = "block"
DEFAULT_SENSOR_FAIL_BEHAVIOR = SENSOR_FAIL_BEHAVIOR_ASSUME_LOAD
CONF_ASSUMED_LOAD_AMPS = "assumed_load_amps"
DEFAULT_ASSUMED_LOAD_AMPS = 10.0
MIN_ASSUMED_LOAD_AMPS = 0
MAX_ASSUMED_LOAD_AMPS = 63
# Continuous-fallback duration before a Repairs issue is filed -- the
# rate-limited log warning fires immediately; the issue is reserved for
# persistent sensor outages, not transient blips
FUSE_FALLBACK_ISSUE_THRESHOLD_SECONDS = 300.0

# ESS (battery) charging current cap and self-consumption add-back
CONF_MAX_ESS_CHARGE_AMPS = "max_ess_charge_amps"
DEFAULT_MAX_ESS_CHARGE_AMPS = 16.0
MIN_MAX_ESS_CHARGE_AMPS = 0
MAX_MAX_ESS_CHARGE_AMPS = 63

# Asymmetric ESS-limit timing: decreases apply immediately, increases are
# delayed until the higher value has been stable for this many seconds
CONF_ESS_INCREASE_DELAY = "ess_increase_delay"
DEFAULT_ESS_INCREASE_DELAY_SECONDS = 180.0
MIN_ESS_INCREASE_DELAY_SECONDS = 0
MAX_ESS_INCREASE_DELAY_SECONDS = 3600

# EMS defaults
DEFAULT_FUSE_RATING_AMPS = 20  # Amps
DEFAULT_SAFETY_BUFFER_AMPS = 1.0
MIN_SAFETY_BUFFER_AMPS = 0
MAX_SAFETY_BUFFER_AMPS = 10
DEFAULT_PV_THRESHOLD_W = 500
MIN_FUSE_RATING_AMPS = 6
MAX_FUSE_RATING_AMPS = 100

# 3-phase 400V watts-to-amps divisor (sqrt(3) * 400V), used to convert a
# single total power reading (e.g. battery charging power) to an equivalent
# per-phase current contribution
WATTS_TO_AMPS_3PHASE_DIVISOR = 692.8

# EMS update interval (seconds) -- faster than schedule for real-time control
EMS_UPDATE_INTERVAL_SECONDS = 30

# EMS mode mapping: internal mode -> SigenStor select option string
EMS_MODE_MAP = {
    "command_charging": "Command Charging (PV First)",
    "command_discharging": "Command Discharging (ESS First)",
    "max_self_consumption": "Maximum Self Consumption",
    "standby": "Standby",
}

# Maximum safe charging limit (kW) -- hardware absolute limit
MAX_CHARGE_LIMIT_KW = 15.0

# --- Car charging configuration ---

# Car charge power limits (kW)
DEFAULT_CAR_MAX_CHARGE_POWER_KW = 7.4  # 1-phase default (32A * 230V)
MIN_CAR_MAX_CHARGE_POWER_KW = 1.4  # Minimum ~6A single phase
MAX_CAR_MAX_CHARGE_POWER_KW = 22.0  # Max 3-phase
CAR_CHARGE_POWER_STEP_KW = 0.1

# Target SOC limits (%)
DEFAULT_TARGET_SOC_PCT = 80.0
MIN_TARGET_SOC_PCT = 10.0
MAX_TARGET_SOC_PCT = 100.0
TARGET_SOC_STEP_PCT = 1.0
DEFAULT_CAR_SOLAR_TARGET_SOC_PCT = 100.0

# Car schedule update interval (minutes) -- polling fallback
CAR_SCHEDULE_UPDATE_INTERVAL_MINUTES = 5

# Fallback charging detection: SOC stale threshold (minutes)
FALLBACK_STALE_THRESHOLD_MINUTES = 60

# --- Easee charger control configuration (Phase 5) ---

# Charger addressing: the HA device_id (device registry, NOT the raw Easee
# charger_id) that easee.* service calls target via their "device_id" field.
# Auto-detected from the configured charger_status_entity's entity registry
# entry; this key stores the manual/auto-filled text override.
CONF_CHARGER_DEVICE_ID = "charger_device_id"

# EMS-13 solar-surplus inputs (EV-09, wired in Wave C)
CONF_HOUSE_CONSUMPTION_ENTITY = "house_consumption_entity"
CONF_EXCLUDED_POWER_ENTITIES = "excluded_power_entities"

# EASE-08 safety notifications
CONF_NOTIFY_SERVICE = "notify_service"

# Charger amp limits (Easee minimum is 6A per phase)
CONF_MIN_CHARGE_AMPS = "min_charge_amps"
CONF_MAX_CHARGE_AMPS = "max_charge_amps"
DEFAULT_MIN_CHARGE_AMPS = 6.0
DEFAULT_MAX_CHARGE_AMPS = 16.0
MIN_MIN_CHARGE_AMPS = 6
MAX_MIN_CHARGE_AMPS = 32
MIN_MAX_CHARGE_AMPS = 6
MAX_MAX_CHARGE_AMPS = 32

# Grid charging power ceiling
CONF_MAX_GRID_CHARGE_POWER_KW = "max_grid_charge_power_kw"
DEFAULT_MAX_GRID_CHARGE_POWER_KW = 12.0
MIN_MAX_GRID_CHARGE_POWER_KW = 1.0
MAX_MAX_GRID_CHARGE_POWER_KW = 22.0

# Amp hysteresis: asymmetric increase/decrease delays (decrease must never
# be lengthened -- it is a safety property, not a tuning preference)
CONF_AMP_INCREASE_DELAY = "amp_increase_delay"
CONF_AMP_DECREASE_DELAY = "amp_decrease_delay"
DEFAULT_AMP_INCREASE_DELAY_SECONDS = 120.0
DEFAULT_AMP_DECREASE_DELAY_SECONDS = 5.0
MIN_AMP_DELAY_SECONDS = 0
MAX_AMP_INCREASE_DELAY_SECONDS = 3600
MAX_AMP_DECREASE_DELAY_SECONDS = 600

# 1/3-phase switching threshold (3 * 6A * 230V)
CONF_PHASE_SWITCH_THRESHOLD_KW = "phase_switch_threshold_kw"
DEFAULT_PHASE_SWITCH_THRESHOLD_KW = 4.1
MIN_PHASE_SWITCH_THRESHOLD_KW = 0.0
MAX_PHASE_SWITCH_THRESHOLD_KW = 20.0

# Solar-surplus opportunistic charging (EV-09, wave C)
CONF_SOLAR_START_THRESHOLD_KW = "solar_start_threshold_kw"
DEFAULT_SOLAR_START_THRESHOLD_KW = 1.5
MIN_SOLAR_START_THRESHOLD_KW = 0.0
MAX_SOLAR_START_THRESHOLD_KW = 10.0

CONF_SOLAR_ACTIVATION_DELAY = "solar_activation_delay"
CONF_SOLAR_DEACTIVATION_DELAY = "solar_deactivation_delay"
DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS = 300.0
DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS = 60.0
MIN_SOLAR_DELAY_SECONDS = 0
MAX_SOLAR_DELAY_SECONDS = 3600

CONF_BATTERY_SOC_GATE_PCT = "battery_soc_gate_pct"
DEFAULT_BATTERY_SOC_GATE_PCT = 100.0
MIN_BATTERY_SOC_GATE_PCT = 0.0
MAX_BATTERY_SOC_GATE_PCT = 100.0

# Fuse Layer 1: emergency overload pause margin
CONF_EMERGENCY_MARGIN_AMPS = "emergency_margin_amps"
DEFAULT_EMERGENCY_MARGIN_AMPS = 2.0
MIN_EMERGENCY_MARGIN_AMPS = 0
MAX_EMERGENCY_MARGIN_AMPS = 20

# Tuned constants not exposed as options (passed explicitly, never relying
# on the ChargerInputs dataclass fallbacks -- see 05-EXECUTION.md Wave B)
DEFAULT_CHARGER_CONVERSION_FACTOR_1PHASE = 4.3
DEFAULT_CHARGER_CONVERSION_FACTOR_2PHASE = 2.5
DEFAULT_CHARGER_CONVERSION_FACTOR_3PHASE = 1.45
DEFAULT_GRID_POWER_SAFETY_BUFFER_KW = 0.5
DEFAULT_SOLAR_SAFETY_BUFFER_KW = 0.5
DEFAULT_SOC_ROUND_UP = True
DEFAULT_PHASE_SEQUENCE_STEP_TIMEOUT_SECONDS = 15.0
DEFAULT_COMMAND_STUCK_TIMEOUT_SECONDS = 60.0
# Heartbeat for re-asserting an unchanged dynamic limit (belief-gated
# command emission -- without it, a limit the charger silently dropped
# would never be re-written)
DEFAULT_LIMIT_REASSERT_INTERVAL_SECONDS = 600.0

# Easee coordinator update interval (seconds)
EASEE_UPDATE_INTERVAL_SECONDS = 30

# --- Appliance subentry configuration (APPL solar-surplus module) ---

SUBENTRY_TYPE_APPLIANCE = "appliance"

CONF_APPLIANCE_NAME = "name"
CONF_APPLIANCE_SWITCH_ENTITY = "switch_entity"
CONF_APPLIANCE_RATED_POWER_W = "rated_power_w"
CONF_APPLIANCE_PHASES = "phases"
CONF_APPLIANCE_POWER_SENSOR_ENTITY = "power_sensor_entity"
CONF_APPLIANCE_PRIORITY = "priority"
CONF_APPLIANCE_ON_THRESHOLD_PCT = "on_threshold_pct"
CONF_APPLIANCE_OFF_THRESHOLD_PCT = "off_threshold_pct"
CONF_APPLIANCE_ON_SUSTAIN_MINUTES = "on_sustain_minutes"
CONF_APPLIANCE_OFF_SUSTAIN_MINUTES = "off_sustain_minutes"
CONF_APPLIANCE_MIN_ON_MINUTES = "min_on_minutes"
CONF_APPLIANCE_MIN_OFF_MINUTES = "min_off_minutes"

DEFAULT_APPLIANCE_PHASES = 3
DEFAULT_APPLIANCE_PRIORITY = 5
DEFAULT_APPLIANCE_ON_THRESHOLD_PCT = 110
DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT = 90
DEFAULT_APPLIANCE_ON_SUSTAIN_MINUTES = 5
# Asymmetric on purpose -- slow release keeps resistive loads from flapping
DEFAULT_APPLIANCE_OFF_SUSTAIN_MINUTES = 15
DEFAULT_APPLIANCE_MIN_ON_MINUTES = 15
DEFAULT_APPLIANCE_MIN_OFF_MINUTES = 5

# Appliance coordinator update interval (seconds)
APPLIANCE_UPDATE_INTERVAL_SECONDS = 30

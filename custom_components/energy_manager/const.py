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

# Module identifiers
MODULE_BATTERY = "battery"
MODULE_EV = "ev"

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
CONF_HOME_PLUGGED_ENTITY = "home_plugged_entity"

# --- Battery schedule configuration ---

# Config keys for battery schedule
CONF_FORECAST_SOLAR_ENTITY = "forecast_solar_entity"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"

# Battery schedule update interval (minutes) -- fallback polling
BATTERY_SCHEDULE_UPDATE_INTERVAL_MINUTES = 5

# Number entity defaults
DEFAULT_CHARGE_THRESHOLD = 1.0  # SEK/kWh
DEFAULT_DISCHARGE_THRESHOLD = 0.50  # SEK/kWh
DEFAULT_MAX_CHARGE_POWER_KW = 5.0  # kW

# Number entity limits
MIN_PRICE_THRESHOLD = 0.0
MAX_PRICE_THRESHOLD = 10.0
PRICE_THRESHOLD_STEP = 0.01

MIN_CHARGE_POWER_KW = 0.0
MAX_CHARGE_POWER_KW = 15.0
CHARGE_POWER_STEP_KW = 0.1

# Peak grouping
DEFAULT_PEAK_GAP_HOURS = 2.0

# SOC constraints
DEFAULT_MIN_SOC_PCT = 10.0
DEFAULT_MAX_SOC_PCT = 95.0

# Default battery capacity if not configured
DEFAULT_BATTERY_CAPACITY_KWH = 10.0

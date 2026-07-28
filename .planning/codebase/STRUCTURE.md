# Codebase Structure

**Analysis Date:** 2025-02-15

## Directory Layout

```
/Volumes/addon_configs/a0d7b954_appdaemon/
├── appdaemon.yaml          # Global AppDaemon configuration (host, plugins, API)
├── apps/                   # All application code
│   ├── apps.yaml           # Application definitions and configuration for all 5 apps
│   ├── home_battery_manager.py      # Battery scheduling engine
│   ├── car_charging_manager.py      # Car charging optimization
│   ├── ems_controller.py            # Energy management system coordination
│   ├── easee_controller.py          # EV charger dynamic limit control
│   ├── logging_utils.py             # Shared logging setup helper
│   ├── log_rotation_helper.py       # Log file rotation utility
│   └── __pycache__/        # Python bytecode cache (generated)
├── logs/                   # Application log files (auto-created by apps)
├── dashboards/             # HADashboard definitions (minimal, legacy)
├── compiled/               # Build artifacts from dashboards
├── namespaces/             # AppDaemon namespace configurations (empty)
└── www/                    # Static web assets (empty)
```

## Directory Purposes

**`apps/`:**
- Purpose: Contains all Python application code
- Contains: 5 main app classes + 2 utility modules
- Key files: `apps.yaml` (config), `home_battery_manager.py`, `ems_controller.py`, `easee_controller.py`, `car_charging_manager.py`

**`logs/`:**
- Purpose: Runtime log files from all apps
- Contains: Files like `home_battery_manager.log`, `ems_controller.log`, `ems_controller.1.log` (rotated)
- Generated: Yes (auto-created by logging setup if not present)
- Committed: No (ignored)

**`dashboards/`:**
- Purpose: HADashboard frontend definitions
- Contains: `Hello.dash` (minimal)
- Generated: No
- Committed: Yes

**`compiled/`:**
- Purpose: Build artifacts from dashboard compilation
- Contains: `css/`, `javascript/` subdirectories
- Generated: Yes (during dashboard build)
- Committed: No

**`namespaces/`:**
- Purpose: AppDaemon namespace data storage
- Contains: Empty (not used in current setup)
- Generated: Potentially
- Committed: No

**`www/`:**
- Purpose: Static web assets for dashboards
- Contains: Empty
- Generated: Potentially
- Committed: No

## Key File Locations

**Configuration:**
- `appdaemon.yaml`: Global AppDaemon settings (latitude, timezone, plugins, port 5050)
- `apps/apps.yaml`: App definitions with 90+ configuration parameters for all 5 apps

**Core Entry Points:**
- `apps/home_battery_manager.py`: Battery scheduling - entry point is `HomeBatteryManager` class, `initialize()` method
- `apps/car_charging_manager.py`: Car charging - entry point is `CarChargingManager` class, instantiated twice in config
- `apps/ems_controller.py`: EMS coordination - entry point is `EMSController` class
- `apps/easee_controller.py`: Charger control - entry point is `EaseeController` class

**Shared Utilities:**
- `apps/logging_utils.py`: `setup_app_logger()` function used by all 5 apps (line 77-113)
- `apps/log_rotation_helper.py`: `LogRotationHelper` class and `create_rotating_file_handler()` function

**Runtime Artifacts:**
- `logs/home_battery_manager.log`: Battery app debug logs
- `logs/ems_controller.log`: EMS coordination logs
- `logs/easee_controller.log`: Charger control logs
- `.planning/codebase/`: Documentation directory (ARCHITECTURE.md, STRUCTURE.md, etc.)

## Naming Conventions

**Files:**
- `{app_name}_manager.py`: App classes (e.g., `home_battery_manager.py`, `car_charging_manager.py`)
- `{app_name}_controller.py`: Specialist controllers (e.g., `ems_controller.py`, `easee_controller.py`)
- `{utility}_utils.py`: Utility/helper modules (e.g., `logging_utils.py`)
- `{utility}_helper.py`: Helper classes (e.g., `log_rotation_helper.py`)
- `logs/{app_name}.log`: Log files (use app name from `self.name` in config)
- `.yaml` or `.yml`: Configuration files

**Directories:**
- `{component}/`: Feature areas in snake_case (apps, logs, dashboards)
- `compiled/`: Build output (standard)
- `namespaces/`: AppDaemon specific (standard)

**Classes:**
- `{CamelCase}Manager` or `{CamelCase}Controller` for app classes
- Inherit from `hass.Hass` (AppDaemon HASS API)
- Example: `HomeBatteryManager`, `CarChargingManager`, `EMSController`

**Methods:**
- `initialize()`: App startup entry point (called by AppDaemon)
- `recalc()` or `_scheduled_recalc()`: Main calculation logic
- `log()`: Overridden method for dual (AppDaemon + file) logging
- Private methods prefix with `_` (e.g., `_group_into_peaks()`, `_scheduled_recalc()`)

**Configuration Keys** (in `apps.yaml`):
- snake_case for all config parameters
- Suffixes for type hints: `_entity` (Home Assistant entity), `_sensor`, `_input`, `_select`, `_boolean`
- Example: `charge_price_threshold_entity`, `battery_soc_sensor`, `ems_mode_select`

**Home Assistant Entities** (referenced in config):
- Input sensors: `input_number.`, `input_datetime.`, `input_boolean.`
- Data sensors: `sensor.` (mostly from integrations like Sigen, Nordpool, Easee)
- Control selects/numbers: `select.`, `number.` for settings
- Output sensors: `sensor.{app}_output_entity` (created by apps)

## Where to Add New Code

**New Feature (e.g., additional battery scheduling strategy):**
- Primary code: `apps/home_battery_manager.py` in `build_schedule()` method (line 206-533)
- Configuration: Add new parameters to `apps.yaml` under `home_battery:` section
- Testing: Add test cases for new pricing logic or constraint handling
- Logging: Use existing `self.log()` method with DEBUG level for algorithm traces

**New AppDaemon App (e.g., for water heater management):**
- Implementation file: `apps/water_heater_manager.py` with class `WaterHeaterManager(hass.Hass)`
- Initialize setup: `initialize()` method calling `setup_app_logger()` and registering callbacks
- Configuration: Add new app section to `apps/apps.yaml` with module/class and parameters
- Entry point: Instantiate via AppDaemon config loader (automatic based on `apps.yaml`)
- Output entity: Create `sensor.water_heater_status` or similar in your app's `set_state()` call

**New Utility/Helper (e.g., calculation library):**
- File location: `apps/{utility}_utils.py` or `apps/{utility}_helper.py`
- Pattern: Create functions/classes, import in apps using `from .utility_utils import function_name`
- Fallback import: Include try/except for standalone execution (see `logging_utils.py` line 5-9)
- Shared usage: Import in multiple apps via their `initialize()` or module scope

**New Log Category:**
- Setup: Call `setup_app_logger()` in `initialize()` with custom logger name (default uses `self.name`)
- Config: Add `log_rotation_enabled`, `log_max_file_size_mb`, `log_max_files` to app section if needed
- File location: Logs go to `logs/{log_file_name}` (default `{app_name}.log`)
- Rotation: Automatically handled by `LogRotationHelper` if enabled in config

## Special Directories

**`logs/`:**
- Purpose: Output directory for app log files with rotation
- Generated: Yes, auto-created by logging setup if missing
- Committed: No, add `logs/` to `.gitignore`
- Rotation: Files rotate when size exceeds `log_max_file_size_mb` (e.g., 10MB)
- Retention: Keep `log_max_files` versions (e.g., last 3 = main + 2 rotated)

**`.planning/codebase/`:**
- Purpose: Project planning documentation (architecture, structure, concerns)
- Generated: No, manually maintained
- Committed: Yes, part of version control
- Used by: `gsd` planning tools and code generation

**`apps/__pycache__/`:**
- Purpose: Python bytecode cache for performance
- Generated: Yes, automatically by Python
- Committed: No, add to `.gitignore`
- Cleanup: Safe to delete anytime, will regenerate

## Configuration Architecture

**Config Layers:**

1. **System Level** (`appdaemon.yaml`):
   - Timezone, lat/long, plugin setup
   - HTTP/admin API ports
   - AppDaemon runtime settings

2. **App Registration** (`apps/apps.yaml`):
   - Maps app names to Python classes
   - 5 app definitions: `home_battery`, `enyaq_car`, `id3_car`, `ems_controller`, `easee_controller`
   - Each app has 10-50+ config parameters

3. **App Instance Parameters** (in each app definition):
   - Input sensor entity references (e.g., `ha_sensor: sensor.nordpool...`)
   - Output entity names (e.g., `output_entity: sensor.battery_charge_schedule_py`)
   - Tuning parameters (e.g., `peak_gap_hours: 2`, `charge_buffer_percent: 20`)
   - Feature flags and thresholds (e.g., `log_rotation_enabled: true`)

4. **Runtime Config** (in app code):
   - Accessed via `self.args` dict (AppDaemon pattern)
   - Example: `self.args.get("peak_gap_hours", 2)` with fallback default

## Module Dependencies

**Import Pattern:**

```python
# Standard library
import logging
from datetime import datetime
from math import ceil

# AppDaemon API
import appdaemon.plugins.hass.hassapi as hass

# Local utilities (with fallback)
try:
    from .logging_utils import setup_app_logger
except ImportError:
    from logging_utils import setup_app_logger
```

**Fallback pattern** (line 5-9 in logging_utils, line 7-11 in each app):
- Try relative import first (for normal AppDaemon execution)
- Fall back to absolute import (for standalone/testing)
- Allows modules to run independently or in AppDaemon container

**No third-party dependencies:**
- Uses only Python stdlib and AppDaemon built-in HASS API
- Makes deployment simple (no pip requirements.txt needed)

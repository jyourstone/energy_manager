# Coding Conventions

**Analysis Date:** 2025-02-15

## Naming Patterns

**Files:**
- Lowercase with underscores: `home_battery_manager.py`, `car_charging_manager.py`, `logging_utils.py`
- Descriptive names indicating purpose: `easee_controller.py`, `ems_controller.py`, `log_rotation_helper.py`

**Classes:**
- PascalCase: `HomeBatteryManager`, `CarChargingManager`, `EMSController`, `EaseeController`, `LogRotationHelper`
- Always inherit from `hass.Hass` for AppDaemon apps or descriptive base classes for utilities
- All classes include docstrings with purpose description

**Functions:**
- snake_case: `setup_app_logger()`, `setup_file_logger()`, `_scheduled_recalc()`, `_fetch_sensor_data()`
- Private functions prefixed with underscore: `_scheduled_recalc()`, `_parse_departure_time()`, `_check_fallback_mode()`
- Public methods used by AppDaemon/HomeAssistant: `initialize()`, `recalc()`, `log()`, `main_check_loop()`
- State change callbacks: `on_force_charging_changed()`, `on_car_schedule_changed()`, `easee_status_changed()`

**Variables:**
- snake_case: `self.file_logger`, `self.last_run_minute`, `self.output_entity`, `charging_needed`
- Entity IDs remain as configured: `sensor.nordpool_kwh_se4_sek_2_10_025`, `input_boolean.easee_force_charging`
- Temporary calculation variables: `energy_needed`, `discharging_capacity`, `virtual_energy`
- Configuration parameters from `self.args` maintain original naming

**Constants:**
- UPPERCASE with underscores where appropriate (stored as instance variables)
- Example: `self.easee_power_threshold` (0.5 kW), `self.main_check_interval` (5 seconds)

## Code Style

**Formatting:**
- 4-space indentation (Python standard)
- Line length practical (90-100 characters observed)
- No automatic formatter detected; style is manually consistent

**Linting:**
- No `.pylintrc` or linting config detected
- Pyright type ignore comments used: `# pyright: ignore[reportMissingImports]`
- Type hints used in function signatures for log rotation helper: `def __init__(self, log_file_path: str, max_file_size_mb: float = 10.0, max_files: int = 5)`

## Import Organization

**Order:**
1. Standard library imports: `import logging`, `import os`, `import glob`, `import math`, `from typing import Optional`
2. Framework imports: `import appdaemon.plugins.hass.hassapi as hass`
3. Local imports: `from .logging_utils import setup_app_logger` (with fallback)
4. Type ignore comments for untyped imports

**Path Aliases:**
- No path aliases detected; relative imports used: `from .logging_utils import setup_app_logger`
- Fallback pattern for standalone execution: `try: from .logging_utils import X except ImportError: from logging_utils import X`

**Import Pattern (All Apps):**
```python
import appdaemon.plugins.hass.hassapi as hass # pyright: ignore[reportMissingImports]
from datetime import datetime, timedelta
from math import ceil
import logging
try:
    from .logging_utils import setup_app_logger
except ImportError:
    from logging_utils import setup_app_logger
```

## Error Handling

**Patterns:**
- Try-except blocks wrap sensor state reads and type conversions
- Specific exception types caught: `ValueError`, `TypeError`, `OSError`, `Exception`
- Fallback values provided on error: sensor reads default to `0` or empty values
- Non-blocking errors logged with `level="WARNING"` rather than raising

**Common Error Scenarios:**
```python
# Type conversion with fallback
try:
    battery_level = float(battery_level_str) if battery_level_str not in ["unknown", "unavailable", None] else 0
except (ValueError, TypeError):
    self.log(f"Cannot convert battery level '{battery_level_str}' to float, using 0", level="WARNING")
    battery_level = 0

# Entity state reads with fallback
try:
    dt = datetime.fromisoformat(s.get('start')).astimezone(now.tzinfo).replace(microsecond=0)
except Exception as e:
    self.log(f"Error parsing date {s.get('start')}: {str(e)}", level="WARNING")
    continue  # Skip this slot

# Division by zero protection
if mean_cons <= 0:
    discharging_needed = 0
    self.log("Mean consumption is <= 0, setting discharging needed to 0", level="WARNING")
else:
    discharging_needed = ceil(discharging_capacity / (mean_cons * 0.25))
```

**Recovery Strategy:**
- Errors in schedule building return empty list rather than raising: `return []`
- Errors in sensor reading use safe defaults (0, empty list, None)
- Never crash; always log and continue with degraded functionality

## Logging

**Framework:** Python `logging` module + AppDaemon's `super().log()` method

**Dual Logging Pattern:**
- All apps override `log()` method to write to both AppDaemon and file simultaneously
- File logging set up in `initialize()` via `setup_app_logger()`
- File rotation handled by `LogRotationHelper` class

```python
def log(self, message, level="INFO"):
    """Override the log method to log to both AppDaemon and file."""
    super().log(message, level=level)  # AppDaemon logger
    if hasattr(self, 'file_logger') and self.file_logger:
        log_level = logging.getLevelName(level.upper())
        if isinstance(log_level, int):
            self.file_logger.log(log_level, message)
        else:
            self.file_logger.info(f"[{level}] {message}")
```

**Log Levels:**
- `INFO`: State changes, schedule decisions, mode changes
- `DEBUG`: Detailed calculations, sensor reads, interim values (suppressed in normal operation)
- `WARNING`: Configuration issues, sensor unavailable, fallback behavior, error recovery
- `ERROR`: Critical failures in initialization

**Logging at Key Points:**
- Initialization completion: `self.log(f"{self.name} initialization complete")`
- Method entry/exit: `self.log(f"{self.name} recalc triggered")`
- Decision points: `self.log(f"Current interval status: {state_str} at price {current.get('price', 'unknown')}")`
- Warnings before fallback: `self.log(f"Cannot convert battery level '{battery_level_str}' to float, using 0", level="WARNING")`

## Comments

**When to Comment:**
- Complex calculations with multiple steps use inline comments explaining logic
- Configuration defaults documented in comments
- Bug fixes and design decisions documented with date and explanation
- State machine transitions documented
- Workarounds for AppDaemon quirks explained

**Comment Style:**
```python
# Track last run to prevent duplicate runs within same minute
self.last_run_minute = None

# Use run_minutely for more reliable scheduling
# This runs every minute, but we filter to only execute on :00, :05, :10, :15, etc. (every 5 minutes)
self.run_minutely(self._scheduled_recalc, self.get_now())
```

**JSDoc/Docstrings:**
- Functions include docstrings with Args, Returns, and description
- Classes include docstring with purpose
- Multi-line descriptions use triple quotes

```python
def _build_schedule(self, raw_today, raw_tomorrow, now, dep_dt, charging_needed, old_map, fallback_mode=False):
    if fallback_mode:
        self.log(f"Building schedule in FALLBACK MODE (all available slots will be marked)", level="DEBUG")
```

## Function Design

**Size:**
- Methods range from 5 lines (simple callbacks) to 150+ lines (complex schedule builders)
- Large methods like `build_schedule()` (330+ lines) use helper methods and clear section comments
- Break at logical boundaries: data fetch, calculation, publishing

**Parameters:**
- Use configuration dictionary (`self.args`) for app settings rather than many parameters
- State fetching via `self.get_state()` rather than passing state objects
- Helper methods receive parsed/derived data, not raw configs
- Keyword arguments used for optional modes: `fallback_mode=False`

**Return Values:**
- Explicit return types: lists of dicts for schedules, dicts for sensor data, bools for checks
- None or empty collections on error (not raising exceptions)
- Always return consistently (don't return `None` sometimes and dict other times)

## Module Design

**Exports:**
- Each file contains one main class (e.g., `HomeBatteryManager` in `home_battery_manager.py`)
- Utility functions exported at module level: `setup_app_logger()`, `setup_file_logger()`, `create_rotating_file_handler()`
- No `__all__` declarations; public by convention

**Barrel Files:**
- No barrel/index files; apps import directly from source modules
- Relative imports with fallback: `from .logging_utils import setup_app_logger`

**File Structure Pattern:**
1. Module docstring (if complex logic documented)
2. Imports (standard, framework, local)
3. Class definition with docstring
4. `initialize()` method (setup)
5. Public methods (ordered by logical flow)
6. Private helper methods (prefixed with `_`)
7. Callbacks/event handlers at end

## Configuration & State Management

**Config Source:**
- All configuration read from `self.args` (loaded from `apps.yaml`)
- Dynamic config values read from Home Assistant entities (sensors, input_number, etc.)
- No hardcoded values; all configurable via args with sensible defaults

**State Access:**
- Read-only via `self.get_state(entity_id)` or `self.get_state(entity_id, attribute="key")`
- Write via `self.set_state(entity_id, state=value, attributes={})`
- State changes detected via `self.listen_state()` callback registration
- Polling via `self.run_minutely()` or `self.run_every()`

**Attribute Storage:**
- Complex data (schedules, metadata) stored as entity attributes, not in separate files
- Historical data preserved via attribute updates: `old_schedule = self.get_state(output, attribute="schedule")`

---

*Convention analysis: 2025-02-15*

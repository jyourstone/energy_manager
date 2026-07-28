# Testing Patterns

**Analysis Date:** 2025-02-15

## Test Framework

**Status:** No automated testing framework configured

**Current State:**
- No pytest, unittest, or other test framework in use
- No test files found in codebase (no `*_test.py`, `test_*.py`, or `tests/` directory)
- No test configuration files (pytest.ini, setup.cfg, tox.ini)
- No test dependencies in configuration

**Testing Approach:**
- Manual testing in Home Assistant development environment
- AppDaemon supports direct testing via state manipulation in HA UI
- Validation via sensor state attributes and log output review

## Recommended Testing Strategy

Given the AppDaemon framework and Home Assistant integration context, future test implementation should consider:

**Unit Testing (Recommended Framework: pytest):**
- Mock `hass.Hass` base class methods: `get_state()`, `set_state()`, `run_minutely()`, `listen_state()`
- Test calculation functions in isolation: `_group_into_peaks()`, `build_schedule()`, `_calculate_energy_requirements()`
- Test error handling and fallback behavior
- Use fixture-based state mocking

**Integration Testing:**
- Mock Home Assistant entity states
- Simulate state change events
- Verify schedule generation against known sensor data
- Test timer/callback chains without actual time delays

**Current Validation Methods:**
- State inspection: Check `sensor.ems_controller_status_py` attributes
- Log file review: Examine `logs/app_name.log` for decision tracing
- Manual HA UI testing: Set input entities and observe schedule updates

## Test File Organization

**Recommended Structure (if implemented):**
```
/Volumes/addon_configs/a0d7b954_appdaemon/
├── apps/
│   ├── home_battery_manager.py
│   └── car_charging_manager.py
├── tests/
│   ├── conftest.py                      # pytest fixtures and mocks
│   ├── test_home_battery_manager.py
│   ├── test_car_charging_manager.py
│   ├── test_logging_utils.py
│   └── fixtures/
│       ├── sensor_data.py               # Mock sensor responses
│       └── schedule_data.py             # Test schedule fixtures
└── pytest.ini                            # Test config
```

**Naming Convention (if implemented):**
- Test file: `test_{module_name}.py`
- Test class: `Test{ClassName}`
- Test method: `test_{method_name}_{scenario}` (e.g., `test_build_schedule_with_surplus_production`)

## Key Code Areas That Need Testing

**High Priority (Complex Logic):**
- `build_schedule()` in `home_battery_manager.py` (330+ lines)
  - Peak detection algorithm: `_group_into_peaks()`
  - Multi-cycle charge/discharge scheduling
  - Production surplus/deficit calculation

- `_build_schedule()` in `car_charging_manager.py`
  - Cheapest slot selection
  - Departure time validation
  - Fallback mode activation

- `calculate_charge_limit()` in `easee_controller.py`
  - Fuse-based capacity calculation
  - Phase mode conversion factor application
  - Power-to-amps conversion

**Medium Priority (State Management):**
- `_check_fallback_mode()` in `car_charging_manager.py`
  - Conditional logic for car home/plugged states
  - Time window validation

- `check_state_changes()` in `ems_controller.py`
  - State change detection and history tracking
  - Loop prevention counters

- `is_scheduled_charging_active()` in `easee_controller.py`
  - Car schedule sensor reading
  - Home sensor validation

**Low Priority (Utility/Setup):**
- `setup_app_logger()` and `setup_file_logger()` in `logging_utils.py`
- `LogRotationHelper` in `log_rotation_helper.py`
- Logger initialization in app constructors

## Current Test Approach (Manual)

**Validation Via State Attributes:**

Apps publish detailed state attributes that serve as validation checkpoints:

`home_battery_manager.py` publishes to sensor state:
```python
attrs = {
    'friendly_name': '...',
    'next_charging_slot': datetime_or_none,
    'next_discharging_slot': datetime_or_none,
    'charging_slots': count,
    'discharging_slots': count,
    'target_ems_mode': mode_string,
    'schedule': [list of slot dicts with time, price, charge, discharge]
}
```

Manual validation steps:
1. Set Nordpool sensor data via automation
2. Observe sensor state and attributes in HA UI
3. Check schedule attribute structure and values
4. Review app log files for decision rationale

**Log File Inspection:**

Apps write detailed DEBUG logs that trace execution:
- Sensor value reads with timestamps
- Calculation steps and interim values
- Decision points and mode selections
- Error recovery with fallback values

Example log inspection:
```
# Check if build_schedule() ran
grep "Multi-cycle schedule" logs/home_battery_manager.log

# Verify error handling
grep "ERROR\|WARNING" logs/home_battery_manager.log

# Trace peak detection
grep "Peak\|profitable_discharge" logs/home_battery_manager.log
```

## Mock Strategy (If Testing Framework Implemented)

**AppDaemon Mocking Pattern:**

```python
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    mock = Mock()
    mock.get_state = Mock(return_value="50")  # Default state
    mock.set_state = Mock()
    mock.run_minutely = Mock()
    mock.listen_state = Mock()
    mock.log = Mock()
    mock.get_now = Mock(return_value=datetime.now(timezone.utc))
    mock.config_dir = "/tmp/test_config"
    mock.name = "test_app"
    return mock

@pytest.fixture
def mock_args():
    """Default app configuration arguments."""
    return {
        "ha_sensor": "sensor.nordpool_kwh_se4_sek_2_10_025",
        "output_entity": "sensor.battery_schedule",
        "soc_entity": "sensor.battery_soc",
        "default_max_soc": 100.0,
        "default_min_soc": 5.0,
        "default_charge_threshold": 2.0,
        "default_discharge_threshold": 2.0,
    }
```

**Sensor Data Mocking:**

```python
@pytest.fixture
def nordpool_data():
    """Mock Nordpool sensor raw_today and raw_tomorrow data."""
    return [
        {"start": "2025-02-15T12:00:00+01:00", "value": 1.50},
        {"start": "2025-02-15T12:15:00+01:00", "value": 1.55},
        {"start": "2025-02-15T12:30:00+01:00", "value": 1.60},
        {"start": "2025-02-15T12:45:00+01:00", "value": 2.80},  # Peak
        {"start": "2025-02-15T13:00:00+01:00", "value": 2.85},  # Peak
    ]

def test_build_schedule_identifies_peaks(mock_hass, mock_args, nordpool_data):
    """Test that build_schedule correctly identifies price peaks."""
    # Setup mock to return test data
    mock_hass.get_state.side_effect = lambda entity, attribute=None: {
        "sensor.nordpool_kwh_se4_sek_2_10_025": nordpool_data if attribute == "raw_today" else [],
        "sensor.battery_soc": "75",
        "input_number.charge_threshold": "2.0",
    }.get(entity, {}).get(attribute, "0")

    # Create instance and test
    manager = HomeBatteryManager(mock_hass, mock_hass, mock_args)
    schedule = manager.build_schedule(nordpool_data, [], datetime.now(), {})

    # Verify
    peaks = [s for s in schedule if s.get("discharge")]
    assert len(peaks) > 0, "Should identify discharge peaks"
    assert all(s["price"] > 2.0 for s in peaks), "Peaks should be expensive slots"
```

## Error Testing Patterns

**Current Error Handling (Observable in Code):**

```python
# Type conversion with graceful fallback
try:
    soc = float(self.get_state(self.args["soc_entity"]) or 0)
except (ValueError, TypeError):
    self.log(f"Cannot convert battery level to float, using 0", level="WARNING")
    soc = 0

# Date parsing with skip-on-error
try:
    dt = datetime.fromisoformat(s.get("start")).astimezone(now.tzinfo)
except Exception as e:
    self.log(f"Error parsing date {s.get('start')}: {str(e)}", level="WARNING")
    continue  # Skip malformed slot
```

**Error Test Pattern (if framework implemented):**

```python
def test_build_schedule_handles_invalid_price_gracefully():
    """Test that invalid price values are handled without crashing."""
    bad_data = [
        {"start": "2025-02-15T12:00:00+01:00", "value": "invalid_price"},  # Bad price
    ]
    manager = HomeBatteryManager(mock_hass, mock_hass, mock_args)
    schedule = manager.build_schedule(bad_data, [], datetime.now(), {})
    # Should return schedule with price defaulted to 0, not crash
    assert len(schedule) > 0
    assert schedule[0]["price"] == 0

def test_calculate_energy_with_unavailable_sensors():
    """Test energy calculation when sensors return 'unavailable'."""
    mock_hass.get_state.return_value = "unavailable"
    result = manager._calculate_energy_requirements(...)
    assert result['energy_needed'] >= 0, "Should handle unavailable sensors"
```

## Coverage Gaps (Current State)

**Not Covered by Manual Testing:**
- Edge case scenarios (leap seconds, DST transitions, timezone changes)
- Rapid state changes and callback ordering
- Long-running stability (multi-day schedules)
- Configuration validation (missing required args, invalid types)
- Concurrent app execution (multiple apps interacting)
- Memory leaks with large schedules (10,000+ slots)

**Testing These Requires Automated Framework:**
- Unit tests for individual functions with edge cases
- State machine testing with sequence verification
- Load testing with synthetic sensor data
- Configuration validation tests

---

*Testing analysis: 2025-02-15*

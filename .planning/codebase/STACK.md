# Technology Stack

**Analysis Date:** 2025-02-15

## Languages

**Primary:**
- Python 3.x - All application logic in `apps/` directory

## Runtime

**Environment:**
- AppDaemon (Home Assistant add-on) - Automation and script execution platform

**Package Manager:**
- pip (Python package manager)
- Lockfile: Not present in repository (managed via AppDaemon add-on)

## Frameworks

**Core:**
- AppDaemon - Home automation framework with Home Assistant integration
  - `appdaemon.plugins.hass.hassapi` - Primary API for Home Assistant interaction
  - Used in: `home_battery_manager.py`, `car_charging_manager.py`, `ems_controller.py`, `easee_controller.py`

**Logging:**
- Python `logging` module - Standard library logging with file rotation support
  - Custom rotating file handler in `log_rotation_helper.py`
  - Shared logging utilities in `logging_utils.py`

**Build/Dev:**
- None detected - Scripts are interpreted directly by AppDaemon

## Key Dependencies

**Critical:**
- AppDaemon - Provides Home Assistant integration via `hass.Hass` base class
- Python standard library: `datetime`, `math`, `os`, `glob`, `logging`, `typing`

**Infrastructure:**
- No external third-party packages required
- All functionality uses AppDaemon's Home Assistant API and Python stdlib

## Configuration

**Environment:**
- Configured via YAML files:
  - `appdaemon.yaml` - AppDaemon daemon configuration
  - `apps.yaml` - Individual app configurations (parameters, thresholds, entity mappings)
- Supervisor token passed via environment variable: `SUPERVISOR_TOKEN`
- No `.env` file usage; configuration is YAML-based

**Build:**
- No build system - scripts run directly as AppDaemon apps
- Configuration files: `appdaemon.yaml`, `apps/apps.yaml`

## Platform Requirements

**Development:**
- Python 3.x runtime
- AppDaemon add-on for Home Assistant
- Access to Home Assistant instance with required integrations:
  - Nordpool (electricity prices)
  - Sigen battery integration
  - Easee charger integration
  - Skoda/VW car integrations

**Production:**
- Home Assistant with AppDaemon add-on
- Deployment: Docker container via Home Assistant add-on system
- Log files written to: `/config/appdaemon/logs/`
- Configuration directory: `/config/appdaemon/`

---

*Stack analysis: 2025-02-15*

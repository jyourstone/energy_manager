# Installation &amp; Requirements

What you need before installing Energy Manager, and the two ways to get it onto your Home Assistant instance.

## Requirements

- **Home Assistant 2025.3.0 or newer** — Energy Manager relies on config subentries (for cars and appliances), which need this version or later.
- **A [Nordpool](https://www.home-assistant.io/integrations/nordpool/) sensor**, configured and providing prices. Either the official Home Assistant Nordpool integration or the HACS Nordpool integration works — both are auto-detected during setup.
- **Recommended: [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/)** for solar-aware battery scheduling. This is a separate integration you install and configure yourself.

!!! note "Forecast.Solar depends on the Sun integration"
    Forecast.Solar relies on Home Assistant's built-in [Sun](https://www.home-assistant.io/integrations/sun/) integration (`sun.sun`), which ships as part of `default_config` and is enabled on standard installs. If `sun.sun` has been removed from your setup, Forecast.Solar's data is ignored and Energy Manager's scheduling still works — just without solar awareness.

No specific battery, inverter, or charger is required to install Energy Manager. Which hardware gets controlled directly versus through your own automations depends on what you own — see [Two ways to use it](../index.md#two-ways-to-use-it) and the [Bring Your Own Hardware](../bring-your-own-hardware/command-sensors.md) section.

## HACS (Recommended)

Energy Manager is in the HACS default store.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jyourstone&repository=energy_manager&category=integration)

1. Click the button above, or search for **Energy Manager** in HACS
2. Click **Download**
3. Restart Home Assistant

## Manual Installation

1. Copy the `custom_components/energy_manager` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Next Steps

With the integration installed and Home Assistant restarted, continue to the [Setup Wizard](setup-wizard.md) to add Energy Manager and configure your first modules.

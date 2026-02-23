"""Config flow for the Energy Manager integration.

Multi-step wizard:
  Step 1 (user)    — Nordpool sensor selection with auto-detection
  Step 2 (modules) — Enable/disable Home Battery and EV Charging modules
  Step 3 (battery) — Home Battery entity config (conditional, auto-detected SigenStor)
  Step 3b (ems)    — EMS control config: fuse rating + control entities (after battery)
  Step 4 (ev)      — EV Charging entity config (conditional, auto-detected Easee)

Plus:
  - Car subentry flow for per-car EV configuration
  - Stub options flow (full options flow is Phase 6)
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)

try:
    from homeassistant.config_entries import OptionsFlowWithReload

    _LEGACY_OPTIONS_FLOW = False
except ImportError:
    from homeassistant.config_entries import (
        OptionsFlowWithConfigEntry as OptionsFlowWithReload,
    )

    _LEGACY_OPTIONS_FLOW = True

from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    TextSelector,
)

from .auto_detect import (
    find_car_integrations,
    find_easee_entities,
    find_forecast_solar_entities,
    find_sigenstor_ems_entities,
    find_sigenstor_entities,
)
from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_LEVEL_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_CAR_NAME,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGER_POWER_ENTITY,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMS_SELECT_ENTITY,
    CONF_EV_ENABLED,
    CONF_FORECAST_SOLAR_ENTITY,
    CONF_FUSE_RATING,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_HOME_PLUGGED_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_NORDPOOL_SENSOR,
    CONF_NORDPOOL_TYPE,
    CONF_PV_POWER_ENTITY,
    CONF_SOC_ENTITY,
    CONFIG_MINOR_VERSION,
    CONFIG_VERSION,
    DEFAULT_FUSE_RATING,
    DOMAIN,
    MAX_FUSE_RATING,
    MIN_FUSE_RATING,
    SUBENTRY_TYPE_CAR,
)
from .nordpool_adapter import detect_nordpool_type, find_all_nordpool_sensors

_LOGGER = logging.getLogger(__name__)


def _add_suggested_values(
    schema: vol.Schema,
    suggested: dict[str, Any],
) -> vol.Schema:
    """Return a copy of *schema* with suggested_value descriptions injected.

    For each key in *suggested* that exists in the schema, a
    ``description.suggested_value`` is set so the UI pre-fills the field.
    """
    new_schema: dict[vol.Optional | vol.Required, Any] = {}
    for key, validator in schema.schema.items():
        if isinstance(key, (vol.Optional, vol.Required)):
            key_name = key.schema
        else:
            key_name = key

        if key_name in suggested and suggested[key_name] is not None:
            description = dict(key.description or {}) if hasattr(key, "description") and key.description else {}
            description["suggested_value"] = suggested[key_name]
            # Rebuild the key with the description
            if isinstance(key, vol.Required):
                new_key = vol.Required(key_name, description=description)
            else:
                new_key = vol.Optional(key_name, description=description)
            new_schema[new_key] = validator
        else:
            new_schema[key] = validator

    return vol.Schema(new_schema)


class EnergyManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Energy Manager."""

    VERSION = CONFIG_VERSION
    MINOR_VERSION = CONFIG_MINOR_VERSION

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> EnergyManagerOptionsFlow:
        """Get the options flow for this handler."""
        if _LEGACY_OPTIONS_FLOW:
            return EnergyManagerOptionsFlow(config_entry)
        return EnergyManagerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration.

        Car subentry is only available when the EV module is enabled.
        """
        if config_entry.options.get(CONF_EV_ENABLED):
            return {SUBENTRY_TYPE_CAR: CarSubentryFlowHandler}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Price Source — select the Nordpool sensor."""
        # Enforce single instance
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            nordpool_entity = user_input.get(CONF_NORDPOOL_SENSOR)
            if not nordpool_entity:
                errors["base"] = "nordpool_not_found"
            else:
                nordpool_type = detect_nordpool_type(self.hass, nordpool_entity)
                if nordpool_type == "unknown":
                    errors["base"] = "nordpool_not_found"
                else:
                    self._data[CONF_NORDPOOL_SENSOR] = nordpool_entity
                    self._data[CONF_NORDPOOL_TYPE] = nordpool_type
                    return await self.async_step_modules()

        # Auto-detect available Nordpool sensors
        all_sensors = find_all_nordpool_sensors(self.hass)

        schema = vol.Schema(
            {
                vol.Required(CONF_NORDPOOL_SENSOR): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        # Pre-fill with first detected sensor if available
        if all_sensors:
            suggested = {CONF_NORDPOOL_SENSOR: all_sensors[0][0]}
            schema = _add_suggested_values(schema, suggested)

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_modules(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Module Selection — enable/disable Home Battery and EV Charging."""
        if user_input is not None:
            self._data[CONF_BATTERY_ENABLED] = user_input.get(
                CONF_BATTERY_ENABLED, False
            )
            self._data[CONF_EV_ENABLED] = user_input.get(CONF_EV_ENABLED, False)

            if self._data[CONF_BATTERY_ENABLED]:
                return await self.async_step_battery()
            if self._data[CONF_EV_ENABLED]:
                return await self.async_step_ev()
            return self._create_entry()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_BATTERY_ENABLED, default=False
                ): BooleanSelector(),
                vol.Optional(CONF_EV_ENABLED, default=False): BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="modules",
            data_schema=schema,
        )

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Home Battery Config — conditional, with auto-detected SigenStor entities."""
        if user_input is not None:
            self._data[CONF_SOC_ENTITY] = user_input.get(CONF_SOC_ENTITY, "")
            self._data[CONF_BATTERY_POWER_ENTITY] = user_input.get(
                CONF_BATTERY_POWER_ENTITY, ""
            )
            self._data[CONF_BATTERY_CAPACITY_KWH] = user_input.get(
                CONF_BATTERY_CAPACITY_KWH, 10.0
            )
            self._data[CONF_FORECAST_SOLAR_ENTITY] = user_input.get(
                CONF_FORECAST_SOLAR_ENTITY, ""
            )

            # Route to EMS step (battery is enabled, so EMS config is relevant)
            return await self.async_step_ems()

        # Auto-detect SigenStor entities and Forecast.Solar
        detected = find_sigenstor_entities(self.hass)
        solar_detected = find_forecast_solar_entities(self.hass)
        detected.update(solar_detected)

        schema = vol.Schema(
            {
                vol.Optional(CONF_SOC_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_BATTERY_POWER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_BATTERY_CAPACITY_KWH): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=100, step=0.1, unit_of_measurement="kWh"
                    )
                ),
                vol.Optional(CONF_FORECAST_SOLAR_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        # Pre-fill with auto-detected values
        if detected:
            schema = _add_suggested_values(schema, detected)

        return self.async_show_form(
            step_id="battery",
            data_schema=schema,
        )

    async def async_step_ems(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3b: EMS Control Config — fuse rating and control entity selection.

        Appears after the battery step and before the EV step.
        Auto-detects SigenStor EMS control entities.
        """
        if user_input is not None:
            self._data[CONF_FUSE_RATING] = user_input.get(
                CONF_FUSE_RATING, DEFAULT_FUSE_RATING
            )
            self._data[CONF_EMS_SELECT_ENTITY] = user_input.get(
                CONF_EMS_SELECT_ENTITY, ""
            )
            self._data[CONF_CHARGE_LIMIT_ENTITY] = user_input.get(
                CONF_CHARGE_LIMIT_ENTITY, ""
            )
            self._data[CONF_DISCHARGE_LIMIT_ENTITY] = user_input.get(
                CONF_DISCHARGE_LIMIT_ENTITY, ""
            )
            self._data[CONF_GRID_POWER_ENTITY] = user_input.get(
                CONF_GRID_POWER_ENTITY, ""
            )
            self._data[CONF_GRID_PHASE_A_ENTITY] = user_input.get(
                CONF_GRID_PHASE_A_ENTITY, ""
            )
            self._data[CONF_GRID_PHASE_B_ENTITY] = user_input.get(
                CONF_GRID_PHASE_B_ENTITY, ""
            )
            self._data[CONF_GRID_PHASE_C_ENTITY] = user_input.get(
                CONF_GRID_PHASE_C_ENTITY, ""
            )
            self._data[CONF_PV_POWER_ENTITY] = user_input.get(
                CONF_PV_POWER_ENTITY, ""
            )

            if self._data.get(CONF_EV_ENABLED):
                return await self.async_step_ev()
            return self._create_entry()

        # Auto-detect SigenStor EMS entities
        detected = find_sigenstor_ems_entities(self.hass)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_FUSE_RATING, default=DEFAULT_FUSE_RATING
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_FUSE_RATING,
                        max=MAX_FUSE_RATING,
                        step=1,
                        unit_of_measurement="A",
                    )
                ),
                vol.Optional(CONF_EMS_SELECT_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="select")
                ),
                vol.Optional(CONF_CHARGE_LIMIT_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain=["sensor", "number"])
                ),
                vol.Optional(CONF_DISCHARGE_LIMIT_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain=["sensor", "number"])
                ),
                vol.Optional(CONF_GRID_POWER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_GRID_PHASE_A_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_GRID_PHASE_B_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_GRID_PHASE_C_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_PV_POWER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        # Pre-fill with auto-detected values
        if detected:
            schema = _add_suggested_values(schema, detected)

        return self.async_show_form(
            step_id="ems",
            data_schema=schema,
        )

    async def async_step_ev(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: EV Charging Config — conditional, with auto-detected Easee entities."""
        if user_input is not None:
            self._data[CONF_CHARGER_STATUS_ENTITY] = user_input.get(
                CONF_CHARGER_STATUS_ENTITY, ""
            )
            self._data[CONF_CHARGER_POWER_ENTITY] = user_input.get(
                CONF_CHARGER_POWER_ENTITY, ""
            )
            return self._create_entry()

        # Auto-detect Easee entities
        detected = find_easee_entities(self.hass)

        schema = vol.Schema(
            {
                vol.Optional(CONF_CHARGER_STATUS_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_CHARGER_POWER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        # Pre-fill with auto-detected values
        if detected:
            schema = _add_suggested_values(schema, detected)

        return self.async_show_form(
            step_id="ev",
            data_schema=schema,
        )

    def _create_entry(self) -> ConfigFlowResult:
        """Create the config entry with collected data.

        Immutable data: Nordpool sensor + type (set during initial config).
        Mutable options: module toggles + entity configs (changeable later).
        """
        data = {
            CONF_NORDPOOL_SENSOR: self._data[CONF_NORDPOOL_SENSOR],
            CONF_NORDPOOL_TYPE: self._data[CONF_NORDPOOL_TYPE],
        }
        options = {
            CONF_BATTERY_ENABLED: self._data.get(CONF_BATTERY_ENABLED, False),
            CONF_EV_ENABLED: self._data.get(CONF_EV_ENABLED, False),
            CONF_SOC_ENTITY: self._data.get(CONF_SOC_ENTITY, ""),
            CONF_BATTERY_POWER_ENTITY: self._data.get(
                CONF_BATTERY_POWER_ENTITY, ""
            ),
            CONF_BATTERY_CAPACITY_KWH: self._data.get(
                CONF_BATTERY_CAPACITY_KWH, 10.0
            ),
            CONF_FORECAST_SOLAR_ENTITY: self._data.get(
                CONF_FORECAST_SOLAR_ENTITY, ""
            ),
            CONF_FUSE_RATING: self._data.get(CONF_FUSE_RATING, DEFAULT_FUSE_RATING),
            CONF_EMS_SELECT_ENTITY: self._data.get(CONF_EMS_SELECT_ENTITY, ""),
            CONF_CHARGE_LIMIT_ENTITY: self._data.get(
                CONF_CHARGE_LIMIT_ENTITY, ""
            ),
            CONF_DISCHARGE_LIMIT_ENTITY: self._data.get(
                CONF_DISCHARGE_LIMIT_ENTITY, ""
            ),
            CONF_GRID_POWER_ENTITY: self._data.get(CONF_GRID_POWER_ENTITY, ""),
            CONF_GRID_PHASE_A_ENTITY: self._data.get(CONF_GRID_PHASE_A_ENTITY, ""),
            CONF_GRID_PHASE_B_ENTITY: self._data.get(CONF_GRID_PHASE_B_ENTITY, ""),
            CONF_GRID_PHASE_C_ENTITY: self._data.get(CONF_GRID_PHASE_C_ENTITY, ""),
            CONF_PV_POWER_ENTITY: self._data.get(CONF_PV_POWER_ENTITY, ""),
            CONF_CHARGER_STATUS_ENTITY: self._data.get(
                CONF_CHARGER_STATUS_ENTITY, ""
            ),
            CONF_CHARGER_POWER_ENTITY: self._data.get(
                CONF_CHARGER_POWER_ENTITY, ""
            ),
        }
        return self.async_create_entry(
            title="Energy Manager",
            data=data,
            options=options,
        )


class EnergyManagerOptionsFlow(OptionsFlowWithReload):
    """Stub options flow — full implementation in Phase 6."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show placeholder settings form."""
        if user_input is not None:
            return self.async_create_entry(data=self.config_entry.options)

        # Empty form — serves as a placeholder until Phase 6
        schema = vol.Schema({})

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )


class CarSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying a car."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to add a new car."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_CAR_NAME],
                data=user_input,
            )

        # Auto-detect car integrations (Skoda, VW)
        detected_cars = find_car_integrations(self.hass)

        schema = vol.Schema(
            {
                vol.Required(CONF_CAR_NAME): TextSelector(),
                vol.Optional(CONF_BATTERY_CAPACITY): NumberSelector(
                    NumberSelectorConfig(
                        min=10, max=200, step=1, unit_of_measurement="kWh"
                    )
                ),
                vol.Optional(CONF_BATTERY_LEVEL_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_HOME_PLUGGED_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain=["sensor", "binary_sensor"])
                ),
            }
        )

        # Pre-fill with first detected car if available
        if detected_cars:
            first_car = detected_cars[0]
            suggested: dict[str, Any] = {}
            if first_car.get("name"):
                suggested[CONF_CAR_NAME] = first_car["name"]
            if first_car.get("battery_level_entity"):
                suggested[CONF_BATTERY_LEVEL_ENTITY] = first_car[
                    "battery_level_entity"
                ]
            if suggested:
                schema = _add_suggested_values(schema, suggested)

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to modify an existing car."""
        subentry = self._get_reconfigure_subentry()
        existing_data = dict(subentry.data)

        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_CAR_NAME],
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_CAR_NAME): TextSelector(),
                vol.Optional(CONF_BATTERY_CAPACITY): NumberSelector(
                    NumberSelectorConfig(
                        min=10, max=200, step=1, unit_of_measurement="kWh"
                    )
                ),
                vol.Optional(CONF_BATTERY_LEVEL_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_HOME_PLUGGED_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain=["sensor", "binary_sensor"])
                ),
            }
        )

        # Pre-fill with existing subentry data
        schema = _add_suggested_values(schema, existing_data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
        )

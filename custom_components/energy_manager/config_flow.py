"""Config flow for the Energy Manager integration.

Multi-step wizard:
  Step 1 (user)    — Nordpool sensor selection with auto-detection
  Step 2 (modules) — Enable/disable Home Battery, EV Charging and Appliances modules
  Step 3 (battery) — Home Battery entity config (conditional, auto-detected SigenStor)
  Step 3b (ems)    — Grid & fuse protection: the shared fuse/grid-sensor config
                     every module needs, plus the inverter control entities when
                     the Home Battery module is enabled
  Step 4 (ev)      — EV Charging entity config (conditional, auto-detected Easee)

Plus:
  - Car subentry flow for per-car EV configuration
  - Appliance subentry flow for per-appliance solar-surplus control
  - Options flow (CORE-05): mirrors the config flow's step structure
    (init/price -> modules -> battery -> ems -> ev) so any setting can be
    revisited later. Car subentries have their own reconfigure flow and are
    not duplicated here.
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
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .auto_detect import (
    find_car_integrations,
    find_easee_charger_device_id,
    find_easee_entities,
    find_forecast_solar_entities,
    find_house_consumption_entity,
    find_sigenstor_ems_entities,
    find_sigenstor_entities,
)
from .const import (
    CHARGE_POWER_STEP_KW,
    CONF_AMP_DECREASE_DELAY,
    CONF_AMP_INCREASE_DELAY,
    CONF_APPLIANCE_MIN_OFF_MINUTES,
    CONF_APPLIANCE_MIN_ON_MINUTES,
    CONF_APPLIANCE_NAME,
    CONF_APPLIANCE_OFF_SUSTAIN_MINUTES,
    CONF_APPLIANCE_OFF_THRESHOLD_PCT,
    CONF_APPLIANCE_ON_SUSTAIN_MINUTES,
    CONF_APPLIANCE_ON_THRESHOLD_PCT,
    CONF_APPLIANCE_PHASES,
    CONF_APPLIANCE_POWER_SENSOR_ENTITY,
    CONF_APPLIANCE_PRIORITY,
    CONF_APPLIANCE_RATED_POWER_W,
    CONF_APPLIANCE_SWITCH_ENTITY,
    CONF_APPLIANCES_ENABLED,
    CONF_ASSUMED_LOAD_AMPS,
    CONF_AVAILABLE_CHARGE_POWER_ENTITY,
    CONF_AVAILABLE_DISCHARGE_POWER_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CYCLE_COST,
    CONF_BATTERY_ENABLED,
    CONF_BATTERY_LEVEL_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_GATE_PCT,
    CONF_CAR_NAME,
    CONF_CHARGE_BUFFER_PCT,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGER_CONNECTED_ENTITY,
    CONF_CHARGER_DEVICE_ID,
    CONF_CHARGER_POWER_ENTITY,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_ELECTRICITY_COMPANY_FEE,
    CONF_EMERGENCY_MARGIN_AMPS,
    CONF_EMS_SELECT_ENTITY,
    CONF_ESS_INCREASE_DELAY,
    CONF_ESTIMATED_CHARGE_POWER_KW,
    CONF_EV_ENABLED,
    CONF_EXCLUDED_POWER_ENTITIES,
    CONF_FORECAST_SOLAR_ENTITY,
    CONF_FUSE_RATING_AMPS,
    CONF_FUSE_SAFETY_BUFFER_AMPS,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_TRANSFER_FEE,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_LOCATION_ENTITY,
    CONF_MAX_CHARGE_AMPS,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_ESS_CHARGE_AMPS,
    CONF_MAX_GRID_CHARGE_POWER_KW,
    CONF_MIN_CHARGE_AMPS,
    CONF_NORDPOOL_SENSOR,
    CONF_NORDPOOL_TYPE,
    CONF_NOTIFY_SERVICE,
    CONF_PEAK_GAP_HOURS,
    CONF_PHASE_CAPABILITY,
    CONF_PHASE_SWITCH_THRESHOLD_KW,
    CONF_PRODUCTION_FACTOR,
    CONF_PV_POWER_ENTITY,
    CONF_RATED_CHARGE_POWER_ENTITY,
    CONF_RATED_DISCHARGE_POWER_ENTITY,
    CONF_SENSOR_FAIL_BEHAVIOR,
    CONF_SOC_ENTITY,
    CONF_SOLAR_ACTIVATION_DELAY,
    CONF_SOLAR_DEACTIVATION_DELAY,
    CONF_SOLAR_START_THRESHOLD_KW,
    CONFIG_MINOR_VERSION,
    CONFIG_VERSION,
    DEFAULT_AMP_DECREASE_DELAY_SECONDS,
    DEFAULT_AMP_INCREASE_DELAY_SECONDS,
    DEFAULT_APPLIANCE_MIN_OFF_MINUTES,
    DEFAULT_APPLIANCE_MIN_ON_MINUTES,
    DEFAULT_APPLIANCE_OFF_SUSTAIN_MINUTES,
    DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT,
    DEFAULT_APPLIANCE_ON_SUSTAIN_MINUTES,
    DEFAULT_APPLIANCE_ON_THRESHOLD_PCT,
    DEFAULT_APPLIANCE_PHASES,
    DEFAULT_APPLIANCE_PRIORITY,
    DEFAULT_ASSUMED_LOAD_AMPS,
    DEFAULT_BATTERY_CYCLE_COST,
    DEFAULT_BATTERY_SOC_GATE_PCT,
    DEFAULT_CHARGE_BUFFER_PCT,
    DEFAULT_ELECTRICITY_COMPANY_FEE,
    DEFAULT_EMERGENCY_MARGIN_AMPS,
    DEFAULT_ESS_INCREASE_DELAY_SECONDS,
    DEFAULT_ESTIMATED_CHARGE_POWER_KW,
    DEFAULT_FUSE_RATING_AMPS,
    DEFAULT_GRID_TRANSFER_FEE,
    DEFAULT_MAX_CHARGE_AMPS,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DEFAULT_MAX_ESS_CHARGE_AMPS,
    DEFAULT_MAX_GRID_CHARGE_POWER_KW,
    DEFAULT_MIN_CHARGE_AMPS,
    DEFAULT_PEAK_GAP_HOURS,
    DEFAULT_PHASE_CAPABILITY,
    DEFAULT_PHASE_SWITCH_THRESHOLD_KW,
    DEFAULT_PRODUCTION_FACTOR,
    DEFAULT_SAFETY_BUFFER_AMPS,
    DEFAULT_SENSOR_FAIL_BEHAVIOR,
    DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS,
    DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS,
    DEFAULT_SOLAR_START_THRESHOLD_KW,
    DOMAIN,
    MAX_AMP_DECREASE_DELAY_SECONDS,
    MAX_AMP_INCREASE_DELAY_SECONDS,
    MAX_ASSUMED_LOAD_AMPS,
    MAX_BATTERY_SOC_GATE_PCT,
    MAX_CHARGE_BUFFER_PCT,
    MAX_CHARGE_POWER_KW,
    MAX_EMERGENCY_MARGIN_AMPS,
    MAX_ESS_INCREASE_DELAY_SECONDS,
    MAX_ESTIMATED_CHARGE_POWER_KW,
    MAX_FUSE_RATING_AMPS,
    MAX_MAX_CHARGE_AMPS,
    MAX_MAX_ESS_CHARGE_AMPS,
    MAX_MAX_GRID_CHARGE_POWER_KW,
    MAX_MIN_CHARGE_AMPS,
    MAX_PEAK_GAP_HOURS,
    MAX_PHASE_SWITCH_THRESHOLD_KW,
    MAX_PRICE_THRESHOLD,
    MAX_PRODUCTION_FACTOR,
    MAX_SAFETY_BUFFER_AMPS,
    MAX_SOLAR_DELAY_SECONDS,
    MAX_SOLAR_START_THRESHOLD_KW,
    MIN_AMP_DELAY_SECONDS,
    MIN_ASSUMED_LOAD_AMPS,
    MIN_BATTERY_SOC_GATE_PCT,
    MIN_CHARGE_BUFFER_PCT,
    MIN_CHARGE_POWER_KW,
    MIN_EMERGENCY_MARGIN_AMPS,
    MIN_ESS_INCREASE_DELAY_SECONDS,
    MIN_ESTIMATED_CHARGE_POWER_KW,
    MIN_FUSE_RATING_AMPS,
    MIN_MAX_CHARGE_AMPS,
    MIN_MAX_ESS_CHARGE_AMPS,
    MIN_MAX_GRID_CHARGE_POWER_KW,
    MIN_MIN_CHARGE_AMPS,
    MIN_PEAK_GAP_HOURS,
    MIN_PHASE_SWITCH_THRESHOLD_KW,
    MIN_PRODUCTION_FACTOR,
    MIN_SAFETY_BUFFER_AMPS,
    MIN_SOLAR_DELAY_SECONDS,
    MIN_SOLAR_START_THRESHOLD_KW,
    SENSOR_FAIL_BEHAVIOR_ASSUME_LOAD,
    SENSOR_FAIL_BEHAVIOR_BLOCK,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_CAR,
)
from .nordpool_adapter import detect_nordpool_type, find_all_nordpool_sensors
from .options_flow_support import (
    apply_step_input,
    ems_step_fields,
    merge_detected_with_current,
)

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
        return EnergyManagerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration.

        Car subentry is only available when the EV module is enabled;
        appliance subentry only when the appliances module is enabled.
        """
        types: dict[str, type[ConfigSubentryFlow]] = {}
        if config_entry.options.get(CONF_EV_ENABLED):
            types[SUBENTRY_TYPE_CAR] = CarSubentryFlowHandler
        if config_entry.options.get(CONF_APPLIANCES_ENABLED):
            types[SUBENTRY_TYPE_APPLIANCE] = ApplianceSubentryFlowHandler
        return types

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
        """Step 2: Module Selection — enable/disable Home Battery, EV Charging and Appliances."""
        if user_input is not None:
            self._data[CONF_BATTERY_ENABLED] = user_input.get(
                CONF_BATTERY_ENABLED, False
            )
            self._data[CONF_EV_ENABLED] = user_input.get(CONF_EV_ENABLED, False)
            self._data[CONF_APPLIANCES_ENABLED] = user_input.get(
                CONF_APPLIANCES_ENABLED, False
            )

            if self._data[CONF_BATTERY_ENABLED]:
                return await self.async_step_battery()
            if self._data[CONF_EV_ENABLED] or self._data[CONF_APPLIANCES_ENABLED]:
                # EV control and appliance admission both need the shared
                # fuse/grid-sensor config, not just the battery module.
                return await self.async_step_ems()
            return await self.async_step_economics()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_BATTERY_ENABLED, default=False
                ): BooleanSelector(),
                vol.Optional(CONF_EV_ENABLED, default=False): BooleanSelector(),
                vol.Optional(
                    CONF_APPLIANCES_ENABLED, default=False
                ): BooleanSelector(),
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
            self._data[CONF_BATTERY_CAPACITY_KWH] = user_input.get(
                CONF_BATTERY_CAPACITY_KWH, 10.0
            )
            self._data[CONF_FORECAST_SOLAR_ENTITY] = user_input.get(
                CONF_FORECAST_SOLAR_ENTITY, []
            )
            self._data[CONF_CHARGE_BUFFER_PCT] = user_input.get(
                CONF_CHARGE_BUFFER_PCT, DEFAULT_CHARGE_BUFFER_PCT
            )
            self._data[CONF_PRODUCTION_FACTOR] = user_input.get(
                CONF_PRODUCTION_FACTOR, DEFAULT_PRODUCTION_FACTOR
            )
            self._data[CONF_ESTIMATED_CHARGE_POWER_KW] = user_input.get(
                CONF_ESTIMATED_CHARGE_POWER_KW, DEFAULT_ESTIMATED_CHARGE_POWER_KW
            )
            self._data[CONF_PEAK_GAP_HOURS] = user_input.get(
                CONF_PEAK_GAP_HOURS, DEFAULT_PEAK_GAP_HOURS
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
                vol.Optional(CONF_BATTERY_CAPACITY_KWH): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=100, step=0.1, unit_of_measurement="kWh"
                    )
                ),
                vol.Optional(CONF_FORECAST_SOLAR_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor", multiple=True)
                ),
                vol.Optional(
                    CONF_CHARGE_BUFFER_PCT, default=DEFAULT_CHARGE_BUFFER_PCT
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_CHARGE_BUFFER_PCT,
                        max=MAX_CHARGE_BUFFER_PCT,
                        step=1,
                        unit_of_measurement="%",
                    )
                ),
                vol.Optional(
                    CONF_PRODUCTION_FACTOR, default=DEFAULT_PRODUCTION_FACTOR
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_PRODUCTION_FACTOR,
                        max=MAX_PRODUCTION_FACTOR,
                        step=0.05,
                    )
                ),
                vol.Optional(
                    CONF_ESTIMATED_CHARGE_POWER_KW,
                    default=DEFAULT_ESTIMATED_CHARGE_POWER_KW,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_ESTIMATED_CHARGE_POWER_KW,
                        max=MAX_ESTIMATED_CHARGE_POWER_KW,
                        step=0.1,
                        unit_of_measurement="kW",
                    )
                ),
                vol.Optional(
                    CONF_PEAK_GAP_HOURS, default=DEFAULT_PEAK_GAP_HOURS
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_PEAK_GAP_HOURS,
                        max=MAX_PEAK_GAP_HOURS,
                        step=0.5,
                        unit_of_measurement="h",
                    )
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
        """Step 3b: Grid & Fuse Protection — shared fuse, grid and control config.

        Appears for every non-empty module combination: the fuse rating,
        safety buffer and grid sensors feed the fuse-headroom arbitration
        all three coordinators use. The inverter control entities are only
        shown when the battery module is enabled. Auto-detects the SigenStor
        entities the step can pre-fill.
        """
        fields = ems_step_fields(
            bool(self._data.get(CONF_BATTERY_ENABLED)),
            bool(self._data.get(CONF_EV_ENABLED)),
        )

        if user_input is not None:
            apply_step_input(self._data, user_input, fields)

            if self._data.get(CONF_EV_ENABLED):
                return await self.async_step_ev()
            return await self.async_step_economics()

        # Auto-detect the SigenStor entities this step can pre-fill
        detected = find_sigenstor_ems_entities(self.hass)
        detected.update(find_sigenstor_entities(self.hass))
        detected.update(find_house_consumption_entity(self.hass))

        schema_dict = {
            vol.Required(
                CONF_FUSE_RATING_AMPS, default=DEFAULT_FUSE_RATING_AMPS
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_FUSE_RATING_AMPS,
                    max=MAX_FUSE_RATING_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(
                CONF_FUSE_SAFETY_BUFFER_AMPS, default=DEFAULT_SAFETY_BUFFER_AMPS
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SAFETY_BUFFER_AMPS,
                    max=MAX_SAFETY_BUFFER_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(CONF_EMS_SELECT_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="select")
            ),
            vol.Optional(CONF_CHARGE_LIMIT_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="number")
            ),
            vol.Optional(CONF_DISCHARGE_LIMIT_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="number")
            ),
            vol.Optional(CONF_AVAILABLE_DISCHARGE_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])
            ),
            vol.Optional(CONF_RATED_DISCHARGE_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])
            ),
            vol.Optional(CONF_AVAILABLE_CHARGE_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])
            ),
            vol.Optional(CONF_RATED_CHARGE_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])
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
            vol.Optional(CONF_BATTERY_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_PV_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_HOUSE_CONSUMPTION_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_EXCLUDED_POWER_ENTITIES): EntitySelector(
                EntitySelectorConfig(domain="sensor", multiple=True)
            ),
            vol.Optional(
                CONF_SENSOR_FAIL_BEHAVIOR, default=DEFAULT_SENSOR_FAIL_BEHAVIOR
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SENSOR_FAIL_BEHAVIOR_ASSUME_LOAD,
                        SENSOR_FAIL_BEHAVIOR_BLOCK,
                    ],
                    translation_key="sensor_fail_behavior",
                )
            ),
            vol.Optional(
                CONF_ASSUMED_LOAD_AMPS, default=DEFAULT_ASSUMED_LOAD_AMPS
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_ASSUMED_LOAD_AMPS,
                    max=MAX_ASSUMED_LOAD_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(
                CONF_MAX_ESS_CHARGE_AMPS, default=DEFAULT_MAX_ESS_CHARGE_AMPS
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_MAX_ESS_CHARGE_AMPS,
                    max=MAX_MAX_ESS_CHARGE_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(
                CONF_ESS_INCREASE_DELAY,
                default=DEFAULT_ESS_INCREASE_DELAY_SECONDS,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_ESS_INCREASE_DELAY_SECONDS,
                    max=MAX_ESS_INCREASE_DELAY_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                )
            ),
        }
        schema = vol.Schema(
            {key: val for key, val in schema_dict.items() if key.schema in fields}
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
            self._data[CONF_CHARGER_DEVICE_ID] = user_input.get(
                CONF_CHARGER_DEVICE_ID, ""
            )
            self._data[CONF_NOTIFY_SERVICE] = user_input.get(
                CONF_NOTIFY_SERVICE, ""
            )
            self._data[CONF_MIN_CHARGE_AMPS] = user_input.get(
                CONF_MIN_CHARGE_AMPS, DEFAULT_MIN_CHARGE_AMPS
            )
            self._data[CONF_MAX_CHARGE_AMPS] = user_input.get(
                CONF_MAX_CHARGE_AMPS, DEFAULT_MAX_CHARGE_AMPS
            )
            self._data[CONF_MAX_GRID_CHARGE_POWER_KW] = user_input.get(
                CONF_MAX_GRID_CHARGE_POWER_KW, DEFAULT_MAX_GRID_CHARGE_POWER_KW
            )
            self._data[CONF_PHASE_SWITCH_THRESHOLD_KW] = user_input.get(
                CONF_PHASE_SWITCH_THRESHOLD_KW, DEFAULT_PHASE_SWITCH_THRESHOLD_KW
            )
            self._data[CONF_SOLAR_START_THRESHOLD_KW] = user_input.get(
                CONF_SOLAR_START_THRESHOLD_KW, DEFAULT_SOLAR_START_THRESHOLD_KW
            )
            self._data[CONF_BATTERY_SOC_GATE_PCT] = user_input.get(
                CONF_BATTERY_SOC_GATE_PCT, DEFAULT_BATTERY_SOC_GATE_PCT
            )
            self._data[CONF_AMP_INCREASE_DELAY] = user_input.get(
                CONF_AMP_INCREASE_DELAY, DEFAULT_AMP_INCREASE_DELAY_SECONDS
            )
            self._data[CONF_AMP_DECREASE_DELAY] = user_input.get(
                CONF_AMP_DECREASE_DELAY, DEFAULT_AMP_DECREASE_DELAY_SECONDS
            )
            self._data[CONF_SOLAR_ACTIVATION_DELAY] = user_input.get(
                CONF_SOLAR_ACTIVATION_DELAY, DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS
            )
            self._data[CONF_SOLAR_DEACTIVATION_DELAY] = user_input.get(
                CONF_SOLAR_DEACTIVATION_DELAY, DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS
            )
            self._data[CONF_EMERGENCY_MARGIN_AMPS] = user_input.get(
                CONF_EMERGENCY_MARGIN_AMPS, DEFAULT_EMERGENCY_MARGIN_AMPS
            )
            return await self.async_step_economics()

        # Auto-detect Easee entities + charger device_id
        detected = find_easee_entities(self.hass)
        if detected.get(CONF_CHARGER_STATUS_ENTITY):
            charger_device_id = find_easee_charger_device_id(
                self.hass, detected[CONF_CHARGER_STATUS_ENTITY]
            )
            if charger_device_id:
                detected[CONF_CHARGER_DEVICE_ID] = charger_device_id

        schema_dict = {
            vol.Optional(CONF_CHARGER_STATUS_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_CHARGER_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_CHARGER_DEVICE_ID): TextSelector(),
            vol.Optional(CONF_NOTIFY_SERVICE): TextSelector(),
            vol.Optional(
                CONF_MIN_CHARGE_AMPS, default=DEFAULT_MIN_CHARGE_AMPS
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_MIN_CHARGE_AMPS,
                    max=MAX_MIN_CHARGE_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(
                CONF_MAX_CHARGE_AMPS, default=DEFAULT_MAX_CHARGE_AMPS
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_MAX_CHARGE_AMPS,
                    max=MAX_MAX_CHARGE_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(
                CONF_MAX_GRID_CHARGE_POWER_KW,
                default=DEFAULT_MAX_GRID_CHARGE_POWER_KW,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_MAX_GRID_CHARGE_POWER_KW,
                    max=MAX_MAX_GRID_CHARGE_POWER_KW,
                    step=0.1,
                    unit_of_measurement="kW",
                )
            ),
            # -- Advanced (mirrors the EMS step's grouping) --
            vol.Optional(
                CONF_AMP_INCREASE_DELAY,
                default=DEFAULT_AMP_INCREASE_DELAY_SECONDS,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_AMP_DELAY_SECONDS,
                    max=MAX_AMP_INCREASE_DELAY_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                )
            ),
            vol.Optional(
                CONF_AMP_DECREASE_DELAY,
                default=DEFAULT_AMP_DECREASE_DELAY_SECONDS,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_AMP_DELAY_SECONDS,
                    max=MAX_AMP_DECREASE_DELAY_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                )
            ),
            vol.Optional(
                CONF_PHASE_SWITCH_THRESHOLD_KW,
                default=DEFAULT_PHASE_SWITCH_THRESHOLD_KW,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_PHASE_SWITCH_THRESHOLD_KW,
                    max=MAX_PHASE_SWITCH_THRESHOLD_KW,
                    step=0.1,
                    unit_of_measurement="kW",
                )
            ),
            vol.Optional(
                CONF_SOLAR_START_THRESHOLD_KW,
                default=DEFAULT_SOLAR_START_THRESHOLD_KW,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SOLAR_START_THRESHOLD_KW,
                    max=MAX_SOLAR_START_THRESHOLD_KW,
                    step=0.1,
                    unit_of_measurement="kW",
                )
            ),
            vol.Optional(
                CONF_SOLAR_ACTIVATION_DELAY,
                default=DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SOLAR_DELAY_SECONDS,
                    max=MAX_SOLAR_DELAY_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                )
            ),
            vol.Optional(
                CONF_SOLAR_DEACTIVATION_DELAY,
                default=DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SOLAR_DELAY_SECONDS,
                    max=MAX_SOLAR_DELAY_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                )
            ),
            vol.Optional(
                CONF_BATTERY_SOC_GATE_PCT,
                default=DEFAULT_BATTERY_SOC_GATE_PCT,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_BATTERY_SOC_GATE_PCT,
                    max=MAX_BATTERY_SOC_GATE_PCT,
                    step=1,
                    unit_of_measurement="%",
                )
            ),
            vol.Optional(
                CONF_EMERGENCY_MARGIN_AMPS,
                default=DEFAULT_EMERGENCY_MARGIN_AMPS,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_EMERGENCY_MARGIN_AMPS,
                    max=MAX_EMERGENCY_MARGIN_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
        }
        if not self._data.get(CONF_BATTERY_ENABLED):
            # The gate compares against the house battery SOC; without the
            # Home Battery module EaseeCoordinator reads no SOC at all
            # (CONF_BATTERY_ENABLED guard in coordinator.py) and the number
            # entity is not created (number.py), so the field is inert.
            schema_dict = {
                key: val
                for key, val in schema_dict.items()
                if key.schema != CONF_BATTERY_SOC_GATE_PCT
            }
        schema = vol.Schema(schema_dict)

        # Pre-fill with auto-detected values
        if detected:
            schema = _add_suggested_values(schema, detected)

        return self.async_show_form(
            step_id="ev",
            data_schema=schema,
        )

    async def async_step_economics(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 5: Economics -- fees and battery charge power.

        Values seed the tunable number entities on first setup; they stay
        adjustable at runtime from the device page afterward. Skipped when
        the battery module is disabled -- all values are battery-bound.
        """
        if not self._data.get(CONF_BATTERY_ENABLED):
            return await self._async_route_finish()
        if user_input is not None:
            self._data[CONF_BATTERY_CYCLE_COST] = user_input.get(
                CONF_BATTERY_CYCLE_COST, DEFAULT_BATTERY_CYCLE_COST
            )
            self._data[CONF_GRID_TRANSFER_FEE] = user_input.get(
                CONF_GRID_TRANSFER_FEE, DEFAULT_GRID_TRANSFER_FEE
            )
            self._data[CONF_ELECTRICITY_COMPANY_FEE] = user_input.get(
                CONF_ELECTRICITY_COMPANY_FEE, DEFAULT_ELECTRICITY_COMPANY_FEE
            )
            self._data[CONF_MAX_CHARGE_POWER] = user_input.get(
                CONF_MAX_CHARGE_POWER, DEFAULT_MAX_CHARGE_POWER_KW
            )
            return await self._async_route_finish()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_BATTERY_CYCLE_COST, default=DEFAULT_BATTERY_CYCLE_COST
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0, max=MAX_PRICE_THRESHOLD, step=0.01
                    )
                ),
                vol.Optional(
                    CONF_GRID_TRANSFER_FEE, default=DEFAULT_GRID_TRANSFER_FEE
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0, max=MAX_PRICE_THRESHOLD, step=0.01
                    )
                ),
                vol.Optional(
                    CONF_ELECTRICITY_COMPANY_FEE,
                    default=DEFAULT_ELECTRICITY_COMPANY_FEE,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0, max=MAX_PRICE_THRESHOLD, step=0.01
                    )
                ),
                vol.Optional(
                    CONF_MAX_CHARGE_POWER, default=DEFAULT_MAX_CHARGE_POWER_KW
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_CHARGE_POWER_KW,
                        max=MAX_CHARGE_POWER_KW,
                        step=CHARGE_POWER_STEP_KW,
                        unit_of_measurement="kW",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="economics", data_schema=schema)

    async def _async_route_finish(self) -> ConfigFlowResult:
        """Pick the finish variant: car guidance only when EV is enabled."""
        if self._data.get(CONF_EV_ENABLED):
            return await self.async_step_finish()
        return await self.async_step_finish_basic()

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 6: Setup-complete note -- next steps (add cars, enable control)."""
        if user_input is not None:
            return self._create_entry()
        return self.async_show_form(step_id="finish", data_schema=vol.Schema({}))

    async def async_step_finish_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 6 (no EV): Setup-complete note without car guidance."""
        if user_input is not None:
            return self._create_entry()
        return self.async_show_form(
            step_id="finish_basic", data_schema=vol.Schema({})
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
            CONF_APPLIANCES_ENABLED: self._data.get(CONF_APPLIANCES_ENABLED, False),
            CONF_SOC_ENTITY: self._data.get(CONF_SOC_ENTITY, ""),
            CONF_BATTERY_POWER_ENTITY: self._data.get(
                CONF_BATTERY_POWER_ENTITY, ""
            ),
            CONF_BATTERY_CAPACITY_KWH: self._data.get(
                CONF_BATTERY_CAPACITY_KWH, 10.0
            ),
            CONF_FORECAST_SOLAR_ENTITY: self._data.get(
                CONF_FORECAST_SOLAR_ENTITY, []
            ),
            CONF_CHARGE_BUFFER_PCT: self._data.get(
                CONF_CHARGE_BUFFER_PCT, DEFAULT_CHARGE_BUFFER_PCT
            ),
            CONF_PRODUCTION_FACTOR: self._data.get(
                CONF_PRODUCTION_FACTOR, DEFAULT_PRODUCTION_FACTOR
            ),
            CONF_ESTIMATED_CHARGE_POWER_KW: self._data.get(
                CONF_ESTIMATED_CHARGE_POWER_KW, DEFAULT_ESTIMATED_CHARGE_POWER_KW
            ),
            CONF_PEAK_GAP_HOURS: self._data.get(
                CONF_PEAK_GAP_HOURS, DEFAULT_PEAK_GAP_HOURS
            ),
            CONF_FUSE_RATING_AMPS: self._data.get(
                CONF_FUSE_RATING_AMPS, DEFAULT_FUSE_RATING_AMPS
            ),
            CONF_FUSE_SAFETY_BUFFER_AMPS: self._data.get(
                CONF_FUSE_SAFETY_BUFFER_AMPS, DEFAULT_SAFETY_BUFFER_AMPS
            ),
            CONF_EMS_SELECT_ENTITY: self._data.get(CONF_EMS_SELECT_ENTITY, ""),
            CONF_CHARGE_LIMIT_ENTITY: self._data.get(
                CONF_CHARGE_LIMIT_ENTITY, ""
            ),
            CONF_DISCHARGE_LIMIT_ENTITY: self._data.get(
                CONF_DISCHARGE_LIMIT_ENTITY, ""
            ),
            CONF_AVAILABLE_DISCHARGE_POWER_ENTITY: self._data.get(
                CONF_AVAILABLE_DISCHARGE_POWER_ENTITY, ""
            ),
            CONF_RATED_DISCHARGE_POWER_ENTITY: self._data.get(
                CONF_RATED_DISCHARGE_POWER_ENTITY, ""
            ),
            CONF_AVAILABLE_CHARGE_POWER_ENTITY: self._data.get(
                CONF_AVAILABLE_CHARGE_POWER_ENTITY, ""
            ),
            CONF_RATED_CHARGE_POWER_ENTITY: self._data.get(
                CONF_RATED_CHARGE_POWER_ENTITY, ""
            ),
            CONF_GRID_POWER_ENTITY: self._data.get(CONF_GRID_POWER_ENTITY, ""),
            CONF_GRID_PHASE_A_ENTITY: self._data.get(CONF_GRID_PHASE_A_ENTITY, ""),
            CONF_GRID_PHASE_B_ENTITY: self._data.get(CONF_GRID_PHASE_B_ENTITY, ""),
            CONF_GRID_PHASE_C_ENTITY: self._data.get(CONF_GRID_PHASE_C_ENTITY, ""),
            CONF_PV_POWER_ENTITY: self._data.get(CONF_PV_POWER_ENTITY, ""),
            CONF_SENSOR_FAIL_BEHAVIOR: self._data.get(
                CONF_SENSOR_FAIL_BEHAVIOR, DEFAULT_SENSOR_FAIL_BEHAVIOR
            ),
            CONF_ASSUMED_LOAD_AMPS: self._data.get(
                CONF_ASSUMED_LOAD_AMPS, DEFAULT_ASSUMED_LOAD_AMPS
            ),
            CONF_MAX_ESS_CHARGE_AMPS: self._data.get(
                CONF_MAX_ESS_CHARGE_AMPS, DEFAULT_MAX_ESS_CHARGE_AMPS
            ),
            CONF_ESS_INCREASE_DELAY: self._data.get(
                CONF_ESS_INCREASE_DELAY, DEFAULT_ESS_INCREASE_DELAY_SECONDS
            ),
            CONF_CHARGER_STATUS_ENTITY: self._data.get(
                CONF_CHARGER_STATUS_ENTITY, ""
            ),
            CONF_CHARGER_POWER_ENTITY: self._data.get(
                CONF_CHARGER_POWER_ENTITY, ""
            ),
            CONF_CHARGER_DEVICE_ID: self._data.get(CONF_CHARGER_DEVICE_ID, ""),
            CONF_HOUSE_CONSUMPTION_ENTITY: self._data.get(
                CONF_HOUSE_CONSUMPTION_ENTITY, ""
            ),
            CONF_EXCLUDED_POWER_ENTITIES: self._data.get(
                CONF_EXCLUDED_POWER_ENTITIES, []
            ),
            CONF_NOTIFY_SERVICE: self._data.get(CONF_NOTIFY_SERVICE, ""),
            CONF_MIN_CHARGE_AMPS: self._data.get(
                CONF_MIN_CHARGE_AMPS, DEFAULT_MIN_CHARGE_AMPS
            ),
            CONF_MAX_CHARGE_AMPS: self._data.get(
                CONF_MAX_CHARGE_AMPS, DEFAULT_MAX_CHARGE_AMPS
            ),
            CONF_MAX_GRID_CHARGE_POWER_KW: self._data.get(
                CONF_MAX_GRID_CHARGE_POWER_KW, DEFAULT_MAX_GRID_CHARGE_POWER_KW
            ),
            CONF_PHASE_SWITCH_THRESHOLD_KW: self._data.get(
                CONF_PHASE_SWITCH_THRESHOLD_KW, DEFAULT_PHASE_SWITCH_THRESHOLD_KW
            ),
            CONF_SOLAR_START_THRESHOLD_KW: self._data.get(
                CONF_SOLAR_START_THRESHOLD_KW, DEFAULT_SOLAR_START_THRESHOLD_KW
            ),
            CONF_BATTERY_SOC_GATE_PCT: self._data.get(
                CONF_BATTERY_SOC_GATE_PCT, DEFAULT_BATTERY_SOC_GATE_PCT
            ),
            CONF_AMP_INCREASE_DELAY: self._data.get(
                CONF_AMP_INCREASE_DELAY, DEFAULT_AMP_INCREASE_DELAY_SECONDS
            ),
            CONF_AMP_DECREASE_DELAY: self._data.get(
                CONF_AMP_DECREASE_DELAY, DEFAULT_AMP_DECREASE_DELAY_SECONDS
            ),
            CONF_SOLAR_ACTIVATION_DELAY: self._data.get(
                CONF_SOLAR_ACTIVATION_DELAY, DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS
            ),
            CONF_SOLAR_DEACTIVATION_DELAY: self._data.get(
                CONF_SOLAR_DEACTIVATION_DELAY,
                DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS,
            ),
            CONF_EMERGENCY_MARGIN_AMPS: self._data.get(
                CONF_EMERGENCY_MARGIN_AMPS, DEFAULT_EMERGENCY_MARGIN_AMPS
            ),
            CONF_BATTERY_CYCLE_COST: self._data.get(
                CONF_BATTERY_CYCLE_COST, DEFAULT_BATTERY_CYCLE_COST
            ),
            CONF_GRID_TRANSFER_FEE: self._data.get(
                CONF_GRID_TRANSFER_FEE, DEFAULT_GRID_TRANSFER_FEE
            ),
            CONF_ELECTRICITY_COMPANY_FEE: self._data.get(
                CONF_ELECTRICITY_COMPANY_FEE, DEFAULT_ELECTRICITY_COMPANY_FEE
            ),
            CONF_MAX_CHARGE_POWER: self._data.get(
                CONF_MAX_CHARGE_POWER, DEFAULT_MAX_CHARGE_POWER_KW
            ),
        }
        return self.async_create_entry(
            title="Energy Manager",
            data=data,
            options=options,
        )


class EnergyManagerOptionsFlow(OptionsFlow):
    """Options flow mirroring the config flow's step structure.

    Steps: init (price source) -> modules -> battery -> ems -> ev, matching
    the config flow wizard so any part of the initial setup can be revisited
    later. Car subentries have their own reconfigure flow (see
    CarSubentryFlowHandler) and are intentionally not duplicated here.

    Every field is pre-filled from the current entry data/options via
    suggested_value: it can be left untouched, changed, or (for optional
    entity fields) cleared. Auto-detection only fills fields that are
    currently empty -- it never overrides an existing choice.

    Note on reload: deliberately plain OptionsFlow, NOT
    OptionsFlowWithReload. The update listener registered in __init__.py
    is the single reload mechanism -- it fires on options saves AND on car
    subentry changes (which never pass through this flow). HA raises
    ValueError at flow finish when OptionsFlowWithReload is combined with
    an update listener, so the two must never be mixed again.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the options flow."""
        super().__init__(*args, **kwargs)
        self._options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Price Source — change the Nordpool sensor."""
        if not self._options:
            self._options = dict(self.config_entry.options)

        errors: dict[str, str] = {}
        current_sensor = self.config_entry.data.get(CONF_NORDPOOL_SENSOR, "")

        if user_input is not None:
            new_sensor = user_input.get(CONF_NORDPOOL_SENSOR, current_sensor)
            if new_sensor != current_sensor:
                nordpool_type = detect_nordpool_type(self.hass, new_sensor)
                if nordpool_type == "unknown":
                    errors["base"] = "nordpool_not_found"
                else:
                    new_data = dict(self.config_entry.data)
                    new_data[CONF_NORDPOOL_SENSOR] = new_sensor
                    new_data[CONF_NORDPOOL_TYPE] = nordpool_type
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
            if not errors:
                return await self.async_step_modules()

        schema = vol.Schema(
            {
                vol.Required(CONF_NORDPOOL_SENSOR): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
            }
        )
        if current_sensor:
            schema = _add_suggested_values(
                schema, {CONF_NORDPOOL_SENSOR: current_sensor}
            )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_modules(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Module Selection — enable/disable Home Battery, EV Charging and Appliances."""
        if user_input is not None:
            self._options[CONF_BATTERY_ENABLED] = user_input.get(
                CONF_BATTERY_ENABLED, False
            )
            self._options[CONF_EV_ENABLED] = user_input.get(CONF_EV_ENABLED, False)
            self._options[CONF_APPLIANCES_ENABLED] = user_input.get(
                CONF_APPLIANCES_ENABLED, False
            )

            if self._options[CONF_BATTERY_ENABLED]:
                return await self.async_step_battery()
            if self._options[CONF_EV_ENABLED] or self._options[CONF_APPLIANCES_ENABLED]:
                # EV control and appliance admission both need the shared
                # fuse/grid-sensor config, not just the battery module.
                return await self.async_step_ems()
            return self.async_create_entry(data=self._options)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_BATTERY_ENABLED,
                    default=self._options.get(CONF_BATTERY_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_EV_ENABLED,
                    default=self._options.get(CONF_EV_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_APPLIANCES_ENABLED,
                    default=self._options.get(CONF_APPLIANCES_ENABLED, False),
                ): BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="modules",
            data_schema=schema,
        )

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Home Battery Config — conditional, auto-detected SigenStor entities."""
        if user_input is not None:
            self._options[CONF_SOC_ENTITY] = user_input.get(CONF_SOC_ENTITY, "")
            self._options[CONF_BATTERY_CAPACITY_KWH] = user_input.get(
                CONF_BATTERY_CAPACITY_KWH, 10.0
            )
            self._options[CONF_FORECAST_SOLAR_ENTITY] = user_input.get(
                CONF_FORECAST_SOLAR_ENTITY, []
            )

            # Route to EMS step (battery is enabled, so EMS config is relevant)
            return await self.async_step_ems()

        # Auto-detect SigenStor entities and Forecast.Solar; only fills
        # fields that are not already configured (see merge_detected_with_current)
        detected = find_sigenstor_entities(self.hass)
        detected.update(find_forecast_solar_entities(self.hass))
        suggested = merge_detected_with_current(detected, self._options)

        schema = vol.Schema(
            {
                vol.Optional(CONF_SOC_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(
                    CONF_BATTERY_CAPACITY_KWH,
                    default=self._options.get(CONF_BATTERY_CAPACITY_KWH, 10.0),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=100, step=0.1, unit_of_measurement="kWh"
                    )
                ),
                vol.Optional(CONF_FORECAST_SOLAR_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor", multiple=True)
                ),
            }
        )

        if suggested:
            schema = _add_suggested_values(schema, suggested)

        return self.async_show_form(
            step_id="battery",
            data_schema=schema,
        )

    async def async_step_ems(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3b: Grid & Fuse Protection — shared fuse, grid and control config.

        Appears for every non-empty module combination: the fuse rating,
        safety buffer and grid sensors feed the fuse-headroom arbitration
        all three coordinators use. The inverter control entities are only
        shown when the battery module is enabled. Auto-detects the SigenStor
        entities the step can pre-fill, for fields that are not already
        configured.
        """
        fields = ems_step_fields(
            bool(self._options.get(CONF_BATTERY_ENABLED)),
            bool(self._options.get(CONF_EV_ENABLED)),
        )

        if user_input is not None:
            apply_step_input(self._options, user_input, fields)

            if self._options.get(CONF_EV_ENABLED):
                return await self.async_step_ev()
            return self.async_create_entry(data=self._options)

        auto = find_sigenstor_ems_entities(self.hass)
        auto.update(find_sigenstor_entities(self.hass))
        auto.update(find_house_consumption_entity(self.hass))
        detected = merge_detected_with_current(auto, self._options)

        schema_dict = {
            vol.Required(
                CONF_FUSE_RATING_AMPS,
                default=self._options.get(
                    CONF_FUSE_RATING_AMPS, DEFAULT_FUSE_RATING_AMPS
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_FUSE_RATING_AMPS,
                    max=MAX_FUSE_RATING_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(
                CONF_FUSE_SAFETY_BUFFER_AMPS,
                default=self._options.get(
                    CONF_FUSE_SAFETY_BUFFER_AMPS, DEFAULT_SAFETY_BUFFER_AMPS
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SAFETY_BUFFER_AMPS,
                    max=MAX_SAFETY_BUFFER_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(CONF_EMS_SELECT_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="select")
            ),
            vol.Optional(CONF_CHARGE_LIMIT_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="number")
            ),
            vol.Optional(CONF_DISCHARGE_LIMIT_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="number")
            ),
            vol.Optional(CONF_AVAILABLE_DISCHARGE_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])
            ),
            vol.Optional(CONF_RATED_DISCHARGE_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])
            ),
            vol.Optional(CONF_AVAILABLE_CHARGE_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])
            ),
            vol.Optional(CONF_RATED_CHARGE_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])
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
            vol.Optional(CONF_BATTERY_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_PV_POWER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_HOUSE_CONSUMPTION_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_EXCLUDED_POWER_ENTITIES): EntitySelector(
                EntitySelectorConfig(domain="sensor", multiple=True)
            ),
            vol.Optional(
                CONF_SENSOR_FAIL_BEHAVIOR,
                default=self._options.get(
                    CONF_SENSOR_FAIL_BEHAVIOR, DEFAULT_SENSOR_FAIL_BEHAVIOR
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SENSOR_FAIL_BEHAVIOR_ASSUME_LOAD,
                        SENSOR_FAIL_BEHAVIOR_BLOCK,
                    ],
                    translation_key="sensor_fail_behavior",
                )
            ),
            vol.Optional(
                CONF_ASSUMED_LOAD_AMPS,
                default=self._options.get(
                    CONF_ASSUMED_LOAD_AMPS, DEFAULT_ASSUMED_LOAD_AMPS
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_ASSUMED_LOAD_AMPS,
                    max=MAX_ASSUMED_LOAD_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(
                CONF_MAX_ESS_CHARGE_AMPS,
                default=self._options.get(
                    CONF_MAX_ESS_CHARGE_AMPS, DEFAULT_MAX_ESS_CHARGE_AMPS
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_MAX_ESS_CHARGE_AMPS,
                    max=MAX_MAX_ESS_CHARGE_AMPS,
                    step=1,
                    unit_of_measurement="A",
                )
            ),
            vol.Optional(
                CONF_ESS_INCREASE_DELAY,
                default=self._options.get(
                    CONF_ESS_INCREASE_DELAY,
                    DEFAULT_ESS_INCREASE_DELAY_SECONDS,
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_ESS_INCREASE_DELAY_SECONDS,
                    max=MAX_ESS_INCREASE_DELAY_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                )
            ),
        }
        schema = vol.Schema(
            {key: val for key, val in schema_dict.items() if key.schema in fields}
        )

        if detected:
            schema = _add_suggested_values(schema, detected)

        return self.async_show_form(
            step_id="ems",
            data_schema=schema,
        )

    async def async_step_ev(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: EV Charging Config — conditional, auto-detected Easee entities."""
        if user_input is not None:
            self._options[CONF_CHARGER_STATUS_ENTITY] = user_input.get(
                CONF_CHARGER_STATUS_ENTITY, ""
            )
            self._options[CONF_CHARGER_POWER_ENTITY] = user_input.get(
                CONF_CHARGER_POWER_ENTITY, ""
            )
            self._options[CONF_CHARGER_DEVICE_ID] = user_input.get(
                CONF_CHARGER_DEVICE_ID, ""
            )
            self._options[CONF_NOTIFY_SERVICE] = user_input.get(
                CONF_NOTIFY_SERVICE, ""
            )
            self._options[CONF_MIN_CHARGE_AMPS] = user_input.get(
                CONF_MIN_CHARGE_AMPS, DEFAULT_MIN_CHARGE_AMPS
            )
            self._options[CONF_MAX_CHARGE_AMPS] = user_input.get(
                CONF_MAX_CHARGE_AMPS, DEFAULT_MAX_CHARGE_AMPS
            )
            self._options[CONF_PHASE_SWITCH_THRESHOLD_KW] = user_input.get(
                CONF_PHASE_SWITCH_THRESHOLD_KW, DEFAULT_PHASE_SWITCH_THRESHOLD_KW
            )
            self._options[CONF_AMP_INCREASE_DELAY] = user_input.get(
                CONF_AMP_INCREASE_DELAY, DEFAULT_AMP_INCREASE_DELAY_SECONDS
            )
            self._options[CONF_AMP_DECREASE_DELAY] = user_input.get(
                CONF_AMP_DECREASE_DELAY, DEFAULT_AMP_DECREASE_DELAY_SECONDS
            )
            self._options[CONF_SOLAR_ACTIVATION_DELAY] = user_input.get(
                CONF_SOLAR_ACTIVATION_DELAY, DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS
            )
            self._options[CONF_SOLAR_DEACTIVATION_DELAY] = user_input.get(
                CONF_SOLAR_DEACTIVATION_DELAY,
                DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS,
            )
            self._options[CONF_EMERGENCY_MARGIN_AMPS] = user_input.get(
                CONF_EMERGENCY_MARGIN_AMPS, DEFAULT_EMERGENCY_MARGIN_AMPS
            )
            return self.async_create_entry(data=self._options)

        # Auto-detect Easee entities + charger device_id; only fills fields
        # that are not already configured
        detected = find_easee_entities(self.hass)
        if detected.get(CONF_CHARGER_STATUS_ENTITY):
            charger_device_id = find_easee_charger_device_id(
                self.hass, detected[CONF_CHARGER_STATUS_ENTITY]
            )
            if charger_device_id:
                detected[CONF_CHARGER_DEVICE_ID] = charger_device_id
        suggested = merge_detected_with_current(detected, self._options)

        schema = vol.Schema(
            {
                vol.Optional(CONF_CHARGER_STATUS_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_CHARGER_POWER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_CHARGER_DEVICE_ID): TextSelector(),
                vol.Optional(CONF_NOTIFY_SERVICE): TextSelector(),
                vol.Optional(
                    CONF_MIN_CHARGE_AMPS,
                    default=self._options.get(
                        CONF_MIN_CHARGE_AMPS, DEFAULT_MIN_CHARGE_AMPS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_MIN_CHARGE_AMPS,
                        max=MAX_MIN_CHARGE_AMPS,
                        step=1,
                        unit_of_measurement="A",
                    )
                ),
                vol.Optional(
                    CONF_MAX_CHARGE_AMPS,
                    default=self._options.get(
                        CONF_MAX_CHARGE_AMPS, DEFAULT_MAX_CHARGE_AMPS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_MAX_CHARGE_AMPS,
                        max=MAX_MAX_CHARGE_AMPS,
                        step=1,
                        unit_of_measurement="A",
                    )
                ),
                # -- Advanced (mirrors the EMS step's grouping) --
                vol.Optional(
                    CONF_AMP_INCREASE_DELAY,
                    default=self._options.get(
                        CONF_AMP_INCREASE_DELAY,
                        DEFAULT_AMP_INCREASE_DELAY_SECONDS,
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_AMP_DELAY_SECONDS,
                        max=MAX_AMP_INCREASE_DELAY_SECONDS,
                        step=1,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(
                    CONF_AMP_DECREASE_DELAY,
                    default=self._options.get(
                        CONF_AMP_DECREASE_DELAY,
                        DEFAULT_AMP_DECREASE_DELAY_SECONDS,
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_AMP_DELAY_SECONDS,
                        max=MAX_AMP_DECREASE_DELAY_SECONDS,
                        step=1,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(
                    CONF_PHASE_SWITCH_THRESHOLD_KW,
                    default=self._options.get(
                        CONF_PHASE_SWITCH_THRESHOLD_KW,
                        DEFAULT_PHASE_SWITCH_THRESHOLD_KW,
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_PHASE_SWITCH_THRESHOLD_KW,
                        max=MAX_PHASE_SWITCH_THRESHOLD_KW,
                        step=0.1,
                        unit_of_measurement="kW",
                    )
                ),
                vol.Optional(
                    CONF_SOLAR_ACTIVATION_DELAY,
                    default=self._options.get(
                        CONF_SOLAR_ACTIVATION_DELAY,
                        DEFAULT_SOLAR_ACTIVATION_DELAY_SECONDS,
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SOLAR_DELAY_SECONDS,
                        max=MAX_SOLAR_DELAY_SECONDS,
                        step=1,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(
                    CONF_SOLAR_DEACTIVATION_DELAY,
                    default=self._options.get(
                        CONF_SOLAR_DEACTIVATION_DELAY,
                        DEFAULT_SOLAR_DEACTIVATION_DELAY_SECONDS,
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SOLAR_DELAY_SECONDS,
                        max=MAX_SOLAR_DELAY_SECONDS,
                        step=1,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(
                    CONF_EMERGENCY_MARGIN_AMPS,
                    default=self._options.get(
                        CONF_EMERGENCY_MARGIN_AMPS, DEFAULT_EMERGENCY_MARGIN_AMPS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_EMERGENCY_MARGIN_AMPS,
                        max=MAX_EMERGENCY_MARGIN_AMPS,
                        step=1,
                        unit_of_measurement="A",
                    )
                ),
            }
        )

        if suggested:
            schema = _add_suggested_values(schema, suggested)

        return self.async_show_form(
            step_id="ev",
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
                vol.Optional(CONF_CHARGER_CONNECTED_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Optional(CONF_LOCATION_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="device_tracker")
                ),
                vol.Optional(
                    CONF_PHASE_CAPABILITY, default=DEFAULT_PHASE_CAPABILITY
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=["1", "2", "3"],
                        translation_key="phase_capability",
                    )
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
            if first_car.get("charger_connected_entity"):
                suggested[CONF_CHARGER_CONNECTED_ENTITY] = first_car[
                    "charger_connected_entity"
                ]
            if first_car.get("location_entity"):
                suggested[CONF_LOCATION_ENTITY] = first_car["location_entity"]
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
            # async_create_entry here would ADD a duplicate subentry --
            # subentry reconfigure must update the existing one.
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
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
                vol.Optional(CONF_CHARGER_CONNECTED_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Optional(CONF_LOCATION_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="device_tracker")
                ),
                vol.Optional(
                    CONF_PHASE_CAPABILITY, default=DEFAULT_PHASE_CAPABILITY
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=["1", "2", "3"],
                        translation_key="phase_capability",
                    )
                ),
            }
        )

        # Pre-fill with existing subentry data
        schema = _add_suggested_values(schema, existing_data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
        )


class ApplianceSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying an appliance."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to add a new appliance."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # An inverted hysteresis band (off >= on) guarantees perpetual
            # on/off cycling -- exactly what APPL-05 exists to prevent.
            if (
                user_input[CONF_APPLIANCE_OFF_THRESHOLD_PCT]
                >= user_input[CONF_APPLIANCE_ON_THRESHOLD_PCT]
            ):
                errors[CONF_APPLIANCE_OFF_THRESHOLD_PCT] = "off_must_be_below_on"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_APPLIANCE_NAME],
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_APPLIANCE_NAME): TextSelector(),
                vol.Required(CONF_APPLIANCE_SWITCH_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain=["switch", "input_boolean"])
                ),
                vol.Required(CONF_APPLIANCE_RATED_POWER_W): NumberSelector(
                    NumberSelectorConfig(
                        min=100, max=25000, step=1, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_APPLIANCE_PHASES, default=DEFAULT_APPLIANCE_PHASES
                ): NumberSelector(NumberSelectorConfig(min=1, max=3, step=2)),
                vol.Optional(CONF_APPLIANCE_POWER_SENSOR_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Optional(
                    CONF_APPLIANCE_PRIORITY, default=DEFAULT_APPLIANCE_PRIORITY
                ): NumberSelector(NumberSelectorConfig(min=1, max=10, step=1)),
                vol.Optional(
                    CONF_APPLIANCE_ON_THRESHOLD_PCT,
                    default=DEFAULT_APPLIANCE_ON_THRESHOLD_PCT,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=50, max=300, step=1, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_APPLIANCE_OFF_THRESHOLD_PCT,
                    default=DEFAULT_APPLIANCE_OFF_THRESHOLD_PCT,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=150, step=1, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_APPLIANCE_ON_SUSTAIN_MINUTES,
                    default=DEFAULT_APPLIANCE_ON_SUSTAIN_MINUTES,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=720, step=1, unit_of_measurement="min"
                    )
                ),
                vol.Optional(
                    CONF_APPLIANCE_OFF_SUSTAIN_MINUTES,
                    default=DEFAULT_APPLIANCE_OFF_SUSTAIN_MINUTES,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=720, step=1, unit_of_measurement="min"
                    )
                ),
                vol.Optional(
                    CONF_APPLIANCE_MIN_ON_MINUTES,
                    default=DEFAULT_APPLIANCE_MIN_ON_MINUTES,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=720, step=1, unit_of_measurement="min"
                    )
                ),
                vol.Optional(
                    CONF_APPLIANCE_MIN_OFF_MINUTES,
                    default=DEFAULT_APPLIANCE_MIN_OFF_MINUTES,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=720, step=1, unit_of_measurement="min"
                    )
                ),
            }
        )

        if user_input is not None:
            # Re-fill the rejected form with what the user entered.
            schema = _add_suggested_values(schema, user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to modify an existing appliance."""
        subentry = self._get_reconfigure_subentry()
        existing_data = dict(subentry.data)

        errors: dict[str, str] = {}
        if user_input is not None:
            # Merge over existing data so the promoted tuning seeds
            # (priority, thresholds, sustain) survive reconfigure, but a
            # cleared power-sensor field arrives as an ABSENT key -- drop
            # it instead of resurrecting the stale entity from the merge.
            new_data = {**existing_data, **user_input}
            if CONF_APPLIANCE_POWER_SENSOR_ENTITY not in user_input:
                new_data.pop(CONF_APPLIANCE_POWER_SENSOR_ENTITY, None)
            # async_create_entry here would ADD a duplicate subentry --
            # subentry reconfigure must update the existing one.
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=user_input[CONF_APPLIANCE_NAME],
                data=new_data,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_APPLIANCE_NAME): TextSelector(),
                vol.Required(CONF_APPLIANCE_SWITCH_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain=["switch", "input_boolean"])
                ),
                vol.Required(CONF_APPLIANCE_RATED_POWER_W): NumberSelector(
                    NumberSelectorConfig(
                        min=100, max=25000, step=1, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_APPLIANCE_PHASES, default=DEFAULT_APPLIANCE_PHASES
                ): NumberSelector(NumberSelectorConfig(min=1, max=3, step=2)),
                vol.Optional(CONF_APPLIANCE_POWER_SENSOR_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Optional(
                    CONF_APPLIANCE_MIN_ON_MINUTES,
                    default=DEFAULT_APPLIANCE_MIN_ON_MINUTES,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=720, step=1, unit_of_measurement="min"
                    )
                ),
                vol.Optional(
                    CONF_APPLIANCE_MIN_OFF_MINUTES,
                    default=DEFAULT_APPLIANCE_MIN_OFF_MINUTES,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=720, step=1, unit_of_measurement="min"
                    )
                ),
            }
        )

        # Pre-fill with existing subentry data (or the rejected user input)
        schema = _add_suggested_values(
            schema, user_input if user_input is not None else existing_data
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

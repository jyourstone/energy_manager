"""Auto-detection of compatible integrations for Energy Manager config flow.

Scans the HA entity registry and config entries to find:
- Nord Pool price sensors (HACS and native variants)
- SigenStor battery inverter entities
- Easee EV charger entities
- Car integrations (Skoda Connect, VW We Connect)
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BATTERY_POWER_ENTITY,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGER_POWER_ENTITY,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMS_SELECT_ENTITY,
    CONF_FORECAST_SOLAR_ENTITY,
    CONF_L_CURRENT_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_SOC_ENTITY,
)
from .nordpool_adapter import auto_detect_nordpool as _nordpool_auto_detect

_LOGGER = logging.getLogger(__name__)


def auto_detect_nordpool(
    hass: HomeAssistant,
) -> tuple[str | None, str | None]:
    """Auto-detect a Nord Pool integration (HACS or native).

    Convenience re-export that delegates to nordpool_adapter.

    Returns:
        Tuple of (entity_id, nordpool_type) or (None, None) if not found.
    """
    return _nordpool_auto_detect(hass)


def find_sigenstor_entities(hass: HomeAssistant) -> dict[str, str]:
    """Scan entity registry for SigenStor battery inverter entities.

    Looks for config entries with domain containing "sigen" and scans
    their entities for battery SOC and power sensors.

    Returns:
        Dict mapping config keys to entity IDs, e.g.:
        {"soc_entity": "sensor.sigen_battery_soc", "battery_power_entity": "sensor.sigen_battery_power"}
        Returns empty dict if not found.
    """
    registry = er.async_get(hass)
    result: dict[str, str] = {}

    # Find SigenStor config entries
    sigen_entries = [
        entry
        for entry in hass.config_entries.async_entries()
        if "sigen" in entry.domain.lower()
    ]

    if not sigen_entries:
        _LOGGER.debug("No SigenStor integration found")
        return result

    for config_entry in sigen_entries:
        entity_entries = er.async_entries_for_config_entry(
            registry, config_entry.entry_id
        )

        for entity_entry in entity_entries:
            if entity_entry.domain != "sensor":
                continue

            entity_id_lower = entity_entry.entity_id.lower()
            unique_id_lower = (entity_entry.unique_id or "").lower()

            # Look for battery state of charge
            if CONF_SOC_ENTITY not in result and (
                "battery_state_of_charge" in entity_id_lower
                or "battery_state_of_charge" in unique_id_lower
                or "battery_soc" in entity_id_lower
            ):
                result[CONF_SOC_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor SOC entity: %s", entity_entry.entity_id
                )

            # Look for battery power
            if CONF_BATTERY_POWER_ENTITY not in result and (
                "battery_power" in entity_id_lower
                or "battery_power" in unique_id_lower
            ):
                # Avoid matching SOC entity again
                if "state_of_charge" not in entity_id_lower and "soc" not in entity_id_lower:
                    result[CONF_BATTERY_POWER_ENTITY] = entity_entry.entity_id
                    _LOGGER.debug(
                        "Found SigenStor battery power entity: %s",
                        entity_entry.entity_id,
                    )

    if not result:
        _LOGGER.debug("SigenStor integration found but no matching entities")

    return result


def find_sigenstor_ems_entities(hass: HomeAssistant) -> dict[str, str]:
    """Scan entity registry for SigenStor EMS control entities.

    Looks for config entries with domain containing "sigen" and scans
    their entities for EMS mode select, charging/discharging limit numbers,
    L-current sensor, and PV power sensor.

    Also scans ALL entities for L-current sensors (may be template sensors).

    Returns:
        Dict mapping config keys to entity IDs, e.g.:
        {"ems_select_entity": "select.sigen_ems_mode", ...}
        Returns empty dict if not found.
    """
    registry = er.async_get(hass)
    result: dict[str, str] = {}

    # Find SigenStor config entries
    sigen_entries = [
        entry
        for entry in hass.config_entries.async_entries()
        if "sigen" in entry.domain.lower()
    ]

    for config_entry in sigen_entries:
        entity_entries = er.async_entries_for_config_entry(
            registry, config_entry.entry_id
        )

        for entity_entry in entity_entries:
            entity_id_lower = entity_entry.entity_id.lower()
            unique_id_lower = (entity_entry.unique_id or "").lower()

            # Look for EMS mode select entity
            if (
                entity_entry.domain == "select"
                and CONF_EMS_SELECT_ENTITY not in result
                and (
                    "remote_ems_control" in entity_id_lower
                    or "remote_ems_control" in unique_id_lower
                    or "ems_control_mode" in entity_id_lower
                    or "ems_control_mode" in unique_id_lower
                )
            ):
                result[CONF_EMS_SELECT_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor EMS select entity: %s",
                    entity_entry.entity_id,
                )

            # Look for max charging limit entity (number or sensor domain)
            if (
                entity_entry.domain in ("number", "sensor")
                and CONF_CHARGE_LIMIT_ENTITY not in result
                and (
                    "max_charging_limit" in entity_id_lower
                    or "max_charging_limit" in unique_id_lower
                    or "ess_max_charging" in entity_id_lower
                    or "ess_max_charging" in unique_id_lower
                    or "ess_rated_charging" in entity_id_lower
                    or "ess_rated_charging" in unique_id_lower
                    or "rated_charging_power" in entity_id_lower
                    or "rated_charging_power" in unique_id_lower
                )
            ):
                result[CONF_CHARGE_LIMIT_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor charge limit entity: %s",
                    entity_entry.entity_id,
                )

            # Look for max discharging limit entity (number or sensor domain)
            if (
                entity_entry.domain in ("number", "sensor")
                and CONF_DISCHARGE_LIMIT_ENTITY not in result
                and (
                    "max_discharging_limit" in entity_id_lower
                    or "max_discharging_limit" in unique_id_lower
                    or "ess_max_discharging" in entity_id_lower
                    or "ess_max_discharging" in unique_id_lower
                    or "ess_rated_discharging" in entity_id_lower
                    or "ess_rated_discharging" in unique_id_lower
                    or "rated_discharging_power" in entity_id_lower
                    or "rated_discharging_power" in unique_id_lower
                )
            ):
                result[CONF_DISCHARGE_LIMIT_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor discharge limit entity: %s",
                    entity_entry.entity_id,
                )

            # Look for L-current sensor in SigenStor
            if (
                entity_entry.domain == "sensor"
                and CONF_L_CURRENT_ENTITY not in result
                and (
                    "highest_l_current" in entity_id_lower
                    or "highest_l_current" in unique_id_lower
                    or "phase_current" in entity_id_lower
                    or "phase_current" in unique_id_lower
                    or "l_current" in entity_id_lower
                    or "l_current" in unique_id_lower
                    or "phase_a_active_power" in entity_id_lower
                    or "phase_active_power" in entity_id_lower
                    or "grid_phase" in entity_id_lower
                )
            ):
                result[CONF_L_CURRENT_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor L-current entity: %s",
                    entity_entry.entity_id,
                )

            # Look for PV power sensor in SigenStor
            if (
                entity_entry.domain == "sensor"
                and CONF_PV_POWER_ENTITY not in result
                and (
                    "pv_power" in entity_id_lower
                    or "pv_power" in unique_id_lower
                    or "solar_power" in entity_id_lower
                    or "solar_power" in unique_id_lower
                    or "pv_generation" in entity_id_lower
                    or "pv_generation" in unique_id_lower
                )
            ):
                result[CONF_PV_POWER_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor PV power entity: %s",
                    entity_entry.entity_id,
                )

    # Fallback: scan ALL entities for L-current sensor (may be a template sensor)
    if CONF_L_CURRENT_ENTITY not in result:
        all_entities = registry.entities
        for entity_entry in all_entities.values():
            if entity_entry.domain != "sensor":
                continue
            entity_id_lower = entity_entry.entity_id.lower()
            if (
                "highest_l_current" in entity_id_lower
                or "l_current" in entity_id_lower
                or "phase_a_active_power" in entity_id_lower
                or "grid_phase" in entity_id_lower
            ):
                result[CONF_L_CURRENT_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found L-current entity (fallback): %s",
                    entity_entry.entity_id,
                )
                break

    # Fallback: scan ALL entities for PV power sensor
    if CONF_PV_POWER_ENTITY not in result:
        all_entities = registry.entities
        pv_candidates: list[str] = []
        for entity_entry in all_entities.values():
            if entity_entry.domain != "sensor":
                continue
            entity_id_lower = entity_entry.entity_id.lower()
            if "pv_power" in entity_id_lower:
                pv_candidates.append(entity_entry.entity_id)

        if pv_candidates:
            # Prefer entity_id containing "sigen"
            sigen_candidates = [
                e for e in pv_candidates if "sigen" in e.lower()
            ]
            if sigen_candidates:
                pv_candidates = sigen_candidates

            # Prefer "plant" over "inverter" (plant-level = total after clipping)
            plant_candidates = [
                e for e in pv_candidates if "plant" in e.lower()
            ]
            if plant_candidates:
                chosen = plant_candidates[0]
            else:
                chosen = pv_candidates[0]

            result[CONF_PV_POWER_ENTITY] = chosen
            _LOGGER.debug(
                "Found PV power entity (fallback): %s", chosen
            )

    if result:
        _LOGGER.debug("Auto-detected EMS entities: %s", result)
    else:
        _LOGGER.debug("No SigenStor EMS control entities found")

    return result


def find_easee_entities(hass: HomeAssistant) -> dict[str, str]:
    """Scan entity registry for Easee EV charger entities.

    Looks for config entries with domain "easee" and scans their entities
    for charger status and power sensors.

    Returns:
        Dict mapping config keys to entity IDs, e.g.:
        {"charger_status_entity": "sensor.easee_status", "charger_power_entity": "sensor.easee_power"}
        Returns empty dict if not found.
    """
    registry = er.async_get(hass)
    result: dict[str, str] = {}

    easee_entries = hass.config_entries.async_entries("easee")

    if not easee_entries:
        _LOGGER.debug("No Easee integration found")
        return result

    for config_entry in easee_entries:
        entity_entries = er.async_entries_for_config_entry(
            registry, config_entry.entry_id
        )

        for entity_entry in entity_entries:
            if entity_entry.domain != "sensor":
                continue

            entity_id_lower = entity_entry.entity_id.lower()
            unique_id_lower = (entity_entry.unique_id or "").lower()

            # Look for charger status
            if CONF_CHARGER_STATUS_ENTITY not in result and (
                "status" in entity_id_lower or "status" in unique_id_lower
            ):
                result[CONF_CHARGER_STATUS_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found Easee status entity: %s", entity_entry.entity_id
                )

            # Look for charger power
            if CONF_CHARGER_POWER_ENTITY not in result and (
                "power" in entity_id_lower or "power" in unique_id_lower
            ):
                result[CONF_CHARGER_POWER_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found Easee power entity: %s", entity_entry.entity_id
                )

    if not result:
        _LOGGER.debug("Easee integration found but no matching entities")

    return result


def find_car_integrations(hass: HomeAssistant) -> list[dict[str, str]]:
    """Scan for Skoda Connect and VW We Connect car integrations.

    For each car found, returns a dict with the car name, battery level
    entity, and platform identifier.

    Returns:
        List of dicts, e.g.:
        [{"name": "Enyaq", "battery_level_entity": "sensor.enyaq_battery_level", "platform": "skoda"}]
        Returns empty list if none found.
    """
    registry = er.async_get(hass)
    cars: list[dict[str, str]] = []

    # Define platform patterns to search for
    platform_patterns = {
        "skoda": ["skoda"],
        "volkswagen": ["volkswagen", "vw"],
    }

    for platform_name, domain_patterns in platform_patterns.items():
        # Find matching config entries
        matching_entries = [
            entry
            for entry in hass.config_entries.async_entries()
            if any(pattern in entry.domain.lower() for pattern in domain_patterns)
        ]

        for config_entry in matching_entries:
            entity_entries = er.async_entries_for_config_entry(
                registry, config_entry.entry_id
            )

            # Group entities by device to find per-car battery level sensors
            device_entities: dict[str | None, list] = {}
            for entity_entry in entity_entries:
                device_id = entity_entry.device_id
                if device_id not in device_entities:
                    device_entities[device_id] = []
                device_entities[device_id].append(entity_entry)

            for device_id, entities in device_entities.items():
                battery_level_entity = None
                car_name = None

                for entity_entry in entities:
                    entity_id_lower = entity_entry.entity_id.lower()
                    unique_id_lower = (entity_entry.unique_id or "").lower()

                    # Look for battery level / SOC sensor
                    if entity_entry.domain == "sensor" and (
                        "battery_level" in entity_id_lower
                        or "battery_level" in unique_id_lower
                        or "state_of_charge" in entity_id_lower
                        or "state_of_charge" in unique_id_lower
                    ):
                        battery_level_entity = entity_entry.entity_id

                    # Try to extract car name from entity's original name or device
                    if car_name is None and entity_entry.original_name:
                        car_name = entity_entry.original_name.split(" ")[0]

                if battery_level_entity is not None:
                    # Use config entry title as fallback name
                    if car_name is None:
                        car_name = config_entry.title or platform_name.capitalize()

                    car_info = {
                        "name": car_name,
                        "battery_level_entity": battery_level_entity,
                        "platform": platform_name,
                    }
                    cars.append(car_info)
                    _LOGGER.debug(
                        "Found %s car: %s (battery: %s)",
                        platform_name,
                        car_name,
                        battery_level_entity,
                    )

    if not cars:
        _LOGGER.debug("No car integrations found (Skoda/VW)")

    return cars


def find_forecast_solar_entities(hass: HomeAssistant) -> dict[str, str]:
    """Scan for Forecast.Solar integration entities.

    Looks for config entries with domain "forecast_solar" and finds
    the energy production today sensor.

    Returns:
        Dict with CONF_FORECAST_SOLAR_ENTITY key if found, empty dict otherwise.
    """
    registry = er.async_get(hass)
    result: dict[str, str] = {}

    # Find Forecast.Solar config entries
    solar_entries = hass.config_entries.async_entries("forecast_solar")

    if not solar_entries:
        _LOGGER.debug("No Forecast.Solar integration found")
        return result

    for config_entry in solar_entries:
        entity_entries = er.async_entries_for_config_entry(
            registry, config_entry.entry_id
        )

        for entity_entry in entity_entries:
            if entity_entry.domain != "sensor":
                continue

            entity_id_lower = entity_entry.entity_id.lower()
            unique_id_lower = (entity_entry.unique_id or "").lower()

            # Look for energy production today sensor
            if (
                "energy_production_today" in entity_id_lower
                or "energy_production_today" in unique_id_lower
            ):
                result[CONF_FORECAST_SOLAR_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found Forecast.Solar entity: %s", entity_entry.entity_id
                )
                return result

    if not result:
        _LOGGER.debug(
            "Forecast.Solar integration found but no matching entities"
        )

    return result

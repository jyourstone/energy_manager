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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BATTERY_POWER_ENTITY,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGER_POWER_ENTITY,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMS_SELECT_ENTITY,
    CONF_FORECAST_SOLAR_ENTITY,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
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

            # Look for battery power (avoid matching the SOC entity again)
            if (
                CONF_BATTERY_POWER_ENTITY not in result
                and (
                    "battery_power" in entity_id_lower
                    or "battery_power" in unique_id_lower
                )
                and "state_of_charge" not in entity_id_lower
                and "soc" not in entity_id_lower
            ):
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

    # Charge/discharge limit candidates by preference tier -- these MUST be
    # writable number-domain setpoints (e.g. number.sigen_plant_ess_max_charging_limit).
    # sensor-domain "rated_*" entities are read-only capabilities, not setpoints,
    # and must never be selected here (see phase41 UAT bug 2).
    charge_limit_candidates: dict[str, str] = {}
    discharge_limit_candidates: dict[str, str] = {}
    disabled_limit_entities: list[str] = []

    for config_entry in sigen_entries:
        entity_entries = er.async_entries_for_config_entry(
            registry, config_entry.entry_id
        )

        for entity_entry in entity_entries:
            entity_id_lower = entity_entry.entity_id.lower()
            unique_id_lower = (entity_entry.unique_id or "").lower()

            # Skip disabled entities -- EntitySelector cannot display them.
            # But remember disabled charge/discharge limit setpoints so we
            # can tell the user to enable them (SigenStor ships the writable
            # number.*_ess_max_*_limit entities disabled by default).
            if getattr(entity_entry, "disabled_by", None) is not None:
                if entity_entry.domain == "number" and (
                    "charging_limit" in entity_id_lower
                    or "charging_limit" in unique_id_lower
                ):
                    disabled_limit_entities.append(entity_entry.entity_id)
                continue

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

            # Look for charging/discharging limit setpoints -- number domain
            # ONLY (never sensor). Preference order: max_charging_limit /
            # max_discharging_limit, then charging_limit / discharging_limit,
            # then ess_rated_charging / ess_rated_discharging (some firmware
            # may expose these as number-domain entities).
            if entity_entry.domain == "number":
                if (
                    "max_charging_limit" in entity_id_lower
                    or "max_charging_limit" in unique_id_lower
                ):
                    charge_limit_candidates.setdefault("max", entity_entry.entity_id)
                elif (
                    "charging_limit" in entity_id_lower
                    or "charging_limit" in unique_id_lower
                ) and (
                    # "charging_limit" is a substring of "discharging_limit"
                    "discharging" not in entity_id_lower
                    and "discharging" not in unique_id_lower
                ):
                    charge_limit_candidates.setdefault("mid", entity_entry.entity_id)
                elif (
                    "ess_rated_charging" in entity_id_lower
                    or "ess_rated_charging" in unique_id_lower
                ):
                    charge_limit_candidates.setdefault(
                        "rated", entity_entry.entity_id
                    )

                if (
                    "max_discharging_limit" in entity_id_lower
                    or "max_discharging_limit" in unique_id_lower
                ):
                    discharge_limit_candidates.setdefault(
                        "max", entity_entry.entity_id
                    )
                elif (
                    "discharging_limit" in entity_id_lower
                    or "discharging_limit" in unique_id_lower
                ):
                    discharge_limit_candidates.setdefault(
                        "mid", entity_entry.entity_id
                    )
                elif (
                    "ess_rated_discharging" in entity_id_lower
                    or "ess_rated_discharging" in unique_id_lower
                ):
                    discharge_limit_candidates.setdefault(
                        "rated", entity_entry.entity_id
                    )

            # Look for grid power sensor (for fuse headroom calculation)
            if (
                entity_entry.domain == "sensor"
                and CONF_GRID_POWER_ENTITY not in result
                and (
                    "grid_active_power" in entity_id_lower
                    or "grid_active_power" in unique_id_lower
                )
                # Exclude per-phase variants (prefer total grid power)
                and "phase_" not in entity_id_lower
            ):
                result[CONF_GRID_POWER_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor grid power entity: %s",
                    entity_entry.entity_id,
                )

            # Look for per-phase grid power sensors (for per-phase fuse protection)
            if (
                entity_entry.domain == "sensor"
                and CONF_GRID_PHASE_A_ENTITY not in result
                and (
                    "phase_a_active_power" in entity_id_lower
                    or "phase_a_active_power" in unique_id_lower
                )
            ):
                result[CONF_GRID_PHASE_A_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor grid phase A entity: %s",
                    entity_entry.entity_id,
                )

            if (
                entity_entry.domain == "sensor"
                and CONF_GRID_PHASE_B_ENTITY not in result
                and (
                    "phase_b_active_power" in entity_id_lower
                    or "phase_b_active_power" in unique_id_lower
                )
            ):
                result[CONF_GRID_PHASE_B_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor grid phase B entity: %s",
                    entity_entry.entity_id,
                )

            if (
                entity_entry.domain == "sensor"
                and CONF_GRID_PHASE_C_ENTITY not in result
                and (
                    "phase_c_active_power" in entity_id_lower
                    or "phase_c_active_power" in unique_id_lower
                )
            ):
                result[CONF_GRID_PHASE_C_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor grid phase C entity: %s",
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
                # First match wins here; the dedicated global fallback scan
                # below handles plant-vs-inverter preference
                result[CONF_PV_POWER_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor PV power entity: %s",
                    entity_entry.entity_id,
                )

    # Resolve charge/discharge limit candidates by preference tier
    for tier in ("max", "mid", "rated"):
        if CONF_CHARGE_LIMIT_ENTITY not in result and tier in charge_limit_candidates:
            result[CONF_CHARGE_LIMIT_ENTITY] = charge_limit_candidates[tier]
            _LOGGER.debug(
                "Found SigenStor charge limit entity (tier=%s): %s",
                tier,
                charge_limit_candidates[tier],
            )
            break
    for tier in ("max", "mid", "rated"):
        if (
            CONF_DISCHARGE_LIMIT_ENTITY not in result
            and tier in discharge_limit_candidates
        ):
            result[CONF_DISCHARGE_LIMIT_ENTITY] = discharge_limit_candidates[tier]
            _LOGGER.debug(
                "Found SigenStor discharge limit entity (tier=%s): %s",
                tier,
                discharge_limit_candidates[tier],
            )
            break

    # Writable setpoints exist but are disabled in the entity registry --
    # they cannot be auto-suggested or selected until the user enables them
    if (
        CONF_CHARGE_LIMIT_ENTITY not in result
        or CONF_DISCHARGE_LIMIT_ENTITY not in result
    ) and disabled_limit_entities:
        _LOGGER.warning(
            "SigenStor charge/discharge limit setpoints found but DISABLED "
            "in the entity registry: %s. Enable them in HA "
            "(Settings > Devices & Services > Entities) to let Energy "
            "Manager control battery charge limits.",
            ", ".join(disabled_limit_entities),
        )

    # Fallback: scan ALL entities for per-phase grid power sensors
    phase_keys = [
        (CONF_GRID_PHASE_A_ENTITY, "phase_a_active_power"),
        (CONF_GRID_PHASE_B_ENTITY, "phase_b_active_power"),
        (CONF_GRID_PHASE_C_ENTITY, "phase_c_active_power"),
    ]
    for phase_key, phase_pattern in phase_keys:
        if phase_key not in result:
            all_entities = registry.entities
            for entity_entry in all_entities.values():
                if entity_entry.domain != "sensor":
                    continue
                if getattr(entity_entry, "disabled_by", None) is not None:
                    continue
                entity_id_lower = entity_entry.entity_id.lower()
                if phase_pattern in entity_id_lower:
                    result[phase_key] = entity_entry.entity_id
                    _LOGGER.debug(
                        "Found %s entity (fallback): %s",
                        phase_key,
                        entity_entry.entity_id,
                    )
                    break

    # Fallback: scan ALL entities for grid power sensor
    if CONF_GRID_POWER_ENTITY not in result:
        all_entities = registry.entities
        for entity_entry in all_entities.values():
            if entity_entry.domain != "sensor":
                continue
            if getattr(entity_entry, "disabled_by", None) is not None:
                continue
            entity_id_lower = entity_entry.entity_id.lower()
            if (
                "grid_active_power" in entity_id_lower
                and "phase_" not in entity_id_lower
            ):
                result[CONF_GRID_POWER_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found grid power entity (fallback): %s",
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
            if getattr(entity_entry, "disabled_by", None) is not None:
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


def derive_charger_device_id(
    charger_status_device_id: str | None,
    fallback_device_ids: list[str],
) -> str | None:
    """Pick the Easee charger's HA device_id for easee.* service addressing.

    Pure decision (no HA access): prefers the device_id of the already
    (auto-)detected charger status entity -- ground truth, since it is the
    exact charger the user pointed at -- falling back to the first
    candidate found among the Easee integration's own device registry
    entries when the status entity isn't registered to a device yet.

    Args:
        charger_status_device_id: device_id of the configured
            charger_status_entity's entity registry entry, or None.
        fallback_device_ids: Candidate device_ids from Easee config entries,
            used only when charger_status_device_id is None.

    Returns:
        The device_id to use, or None if nothing was found.
    """
    if charger_status_device_id:
        return charger_status_device_id
    return fallback_device_ids[0] if fallback_device_ids else None


def find_easee_charger_device_id(
    hass: HomeAssistant, charger_status_entity: str
) -> str | None:
    """Auto-detect the Easee charger's HA device_id for service-call addressing.

    Easee's own services (action_command, set_charger_dynamic_limit,
    set_charger_phase_mode) accept a device_id field addressing the HA
    device registry entry. Looks up the device_id of the configured
    charger_status_entity first (ground truth); falls back to scanning the
    Easee integration's device registry entries if the entity isn't
    registered yet.

    Args:
        hass: Home Assistant instance.
        charger_status_entity: The (auto-detected or user-selected) charger
            status sensor entity ID, or "".

    Returns:
        The device_id string, or None if it could not be determined.
    """
    registry = er.async_get(hass)
    entity_device_id: str | None = None
    if charger_status_entity:
        entity_entry = registry.async_get(charger_status_entity)
        if entity_entry is not None:
            entity_device_id = entity_entry.device_id

    fallback_device_ids: list[str] = []
    if entity_device_id is None:
        device_registry = dr.async_get(hass)
        for config_entry in hass.config_entries.async_entries("easee"):
            fallback_device_ids.extend(
                device.id
                for device in dr.async_entries_for_config_entry(
                    device_registry, config_entry.entry_id
                )
            )

    return derive_charger_device_id(entity_device_id, fallback_device_ids)


def find_house_consumption_entity(hass: HomeAssistant) -> dict[str, str]:
    """Scan for a SigenStor house/plant consumed-power sensor (EMS-13).

    Looks for config entries with domain containing "sigen" and scans their
    entities for a "consumed_power" pattern, used as the house-consumption
    input for the solar-surplus calculation (EV-09, wired in Wave C).

    Returns:
        Dict with CONF_HOUSE_CONSUMPTION_ENTITY key if found, empty dict
        otherwise.
    """
    registry = er.async_get(hass)
    result: dict[str, str] = {}

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
            if entity_entry.domain != "sensor":
                continue

            entity_id_lower = entity_entry.entity_id.lower()
            unique_id_lower = (entity_entry.unique_id or "").lower()

            if "consumed_power" in entity_id_lower or "consumed_power" in unique_id_lower:
                result[CONF_HOUSE_CONSUMPTION_ENTITY] = entity_entry.entity_id
                _LOGGER.debug(
                    "Found SigenStor house consumption entity: %s",
                    entity_entry.entity_id,
                )
                return result

    if not result:
        _LOGGER.debug("No SigenStor house consumption entity found")

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
    device_registry = dr.async_get(hass)
    cars: list[dict[str, str]] = []

    # Define platform patterns to search for
    platform_patterns = {
        "skoda": ["skoda", "myskoda"],
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
                charger_connected_entity = None
                location_entity = None

                for entity_entry in entities:
                    entity_id_lower = entity_entry.entity_id.lower()
                    unique_id_lower = (entity_entry.unique_id or "").lower()

                    # Exclude target/goal SOC entities (e.g. mySkoda's
                    # "target_battery_percentage") -- the ACTUAL SOC entity
                    # must win when both exist on the same device. "mal_" is
                    # the localized (Swedish "mål") entity_id form.
                    is_target_soc = (
                        "target" in entity_id_lower
                        or "target" in unique_id_lower
                        or "mal_" in entity_id_lower
                    )

                    # Look for battery level / SOC sensor
                    if (
                        not is_target_soc
                        and entity_entry.domain == "sensor"
                        and (
                            "battery_level" in entity_id_lower
                            or "battery_level" in unique_id_lower
                            or "state_of_charge" in entity_id_lower
                            or "state_of_charge" in unique_id_lower
                            or "battery_percentage" in entity_id_lower
                            or "battery_percentage" in unique_id_lower
                            or "charging_level" in entity_id_lower
                            or "charging_level" in unique_id_lower
                        )
                    ):
                        battery_level_entity = entity_entry.entity_id

                    # Look for charger connected binary sensor
                    if entity_entry.domain == "binary_sensor" and (
                        "charger_connected" in entity_id_lower
                        or "charger_connected" in unique_id_lower
                        or "plug_connected" in entity_id_lower
                        or "plug_connected" in unique_id_lower
                    ):
                        charger_connected_entity = entity_entry.entity_id

                    # Look for location device tracker
                    if entity_entry.domain == "device_tracker" and (
                        "position" in entity_id_lower
                        or "location" in entity_id_lower
                        or "parking" in entity_id_lower
                    ):
                        location_entity = entity_entry.entity_id

                    # Try to extract car name from entity's original name or device
                    if car_name is None and entity_entry.original_name:
                        car_name = entity_entry.original_name.split(" ")[0]

                if battery_level_entity is not None:
                    # Prefer the car DEVICE's registry name (e.g. "Skoda
                    # Enyaq") over the matched sensor's friendly name --
                    # falls back to the sensor-derived/config-entry name
                    # only when no device name is available.
                    device_name = None
                    if device_id:
                        device = device_registry.async_get(device_id)
                        if device:
                            device_name = device.name_by_user or device.name

                    if device_name:
                        car_name = device_name
                    elif car_name is None:
                        car_name = config_entry.title or platform_name.capitalize()

                    car_info = {
                        "name": car_name,
                        "battery_level_entity": battery_level_entity,
                        "platform": platform_name,
                    }
                    if charger_connected_entity:
                        car_info["charger_connected_entity"] = charger_connected_entity
                    if location_entity:
                        car_info["location_entity"] = location_entity
                    cars.append(car_info)
                    _LOGGER.debug(
                        "Found %s car: %s (battery: %s)",
                        platform_name,
                        car_name,
                        battery_level_entity,
                    )
                else:
                    _LOGGER.debug(
                        "Car integration %s device %s matched domain but no battery "
                        "level entity found. Available sensors: %s",
                        platform_name,
                        device_id,
                        [e.entity_id for e in entities if e.domain == "sensor"],
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

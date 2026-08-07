"""Pure helper logic for the Energy Manager options flow.

Covers the `ems` step's module-dependent field set and the write-back that
preserves fields the step did not show, in addition to auto-detection
merging. Kept free of voluptuous/homeassistant imports so it can be unit
tested without a full Home Assistant environment.
"""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_ASSUMED_LOAD_AMPS,
    CONF_BATTERY_POWER_ENTITY,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_EMS_SELECT_ENTITY,
    CONF_ESS_INCREASE_DELAY,
    CONF_EXCLUDED_POWER_ENTITIES,
    CONF_FUSE_RATING_AMPS,
    CONF_FUSE_SAFETY_BUFFER_AMPS,
    CONF_GRID_PHASE_A_ENTITY,
    CONF_GRID_PHASE_B_ENTITY,
    CONF_GRID_PHASE_C_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_MAX_ESS_CHARGE_AMPS,
    CONF_PV_POWER_ENTITY,
    CONF_SENSOR_FAIL_BEHAVIOR,
    DEFAULT_ASSUMED_LOAD_AMPS,
    DEFAULT_ESS_INCREASE_DELAY_SECONDS,
    DEFAULT_FUSE_RATING_AMPS,
    DEFAULT_MAX_ESS_CHARGE_AMPS,
    DEFAULT_SAFETY_BUFFER_AMPS,
    DEFAULT_SENSOR_FAIL_BEHAVIOR,
)

# Values that count as "not configured" and may be overridden by auto-detection.
_EMPTY_VALUES = (None, "", [])


def merge_detected_with_current(
    detected: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Merge auto-detected values with the currently configured options.

    Existing non-empty values always win; auto-detection only fills in
    fields that are not currently configured. This lets the options flow
    re-run detection on every open without ever clobbering a user's
    existing choice.

    Args:
        detected: Freshly auto-detected values (e.g. from find_sigenstor_entities).
        current: The currently stored option values for the same keys.

    Returns:
        A merged dict suitable for use as suggested_value pre-fills.
    """
    merged = dict(detected)
    for key, value in current.items():
        if value not in _EMPTY_VALUES:
            merged[key] = value
    return merged


def ems_step_fields(battery_enabled: bool, ev_enabled: bool) -> tuple[str, ...]:
    """Fields the shared grid/fuse step shows for the enabled modules.

    The step itself is shown for every non-empty module combination: the EMS,
    Easee and appliance coordinators all build a FuseSensorReader from the same
    top-level options.
    """
    fields = [
        CONF_FUSE_RATING_AMPS,
        CONF_FUSE_SAFETY_BUFFER_AMPS,
        CONF_GRID_POWER_ENTITY,
        CONF_GRID_PHASE_A_ENTITY,
        CONF_GRID_PHASE_B_ENTITY,
        CONF_GRID_PHASE_C_ENTITY,
        CONF_BATTERY_POWER_ENTITY,
        CONF_SENSOR_FAIL_BEHAVIOR,
        CONF_ASSUMED_LOAD_AMPS,
    ]
    if battery_enabled or ev_enabled:
        fields += [
            CONF_PV_POWER_ENTITY,
            CONF_HOUSE_CONSUMPTION_ENTITY,
            CONF_EXCLUDED_POWER_ENTITIES,
        ]
    if battery_enabled:
        fields += [
            CONF_EMS_SELECT_ENTITY,
            CONF_CHARGE_LIMIT_ENTITY,
            CONF_DISCHARGE_LIMIT_ENTITY,
            CONF_MAX_ESS_CHARGE_AMPS,
            CONF_ESS_INCREASE_DELAY,
        ]
    return tuple(fields)


#: Fallback written when a SHOWN ems-step field comes back empty. Entity/text
#: fields fall back to "" (the user cleared them); numeric/select fields fall
#: back to their DEFAULT_*. Fields not shown are never written -- see
#: apply_step_input.
FIELD_DEFAULTS: dict[str, Any] = {
    CONF_FUSE_RATING_AMPS: DEFAULT_FUSE_RATING_AMPS,
    CONF_FUSE_SAFETY_BUFFER_AMPS: DEFAULT_SAFETY_BUFFER_AMPS,
    CONF_GRID_POWER_ENTITY: "",
    CONF_GRID_PHASE_A_ENTITY: "",
    CONF_GRID_PHASE_B_ENTITY: "",
    CONF_GRID_PHASE_C_ENTITY: "",
    CONF_BATTERY_POWER_ENTITY: "",
    CONF_SENSOR_FAIL_BEHAVIOR: DEFAULT_SENSOR_FAIL_BEHAVIOR,
    CONF_ASSUMED_LOAD_AMPS: DEFAULT_ASSUMED_LOAD_AMPS,
    CONF_PV_POWER_ENTITY: "",
    CONF_HOUSE_CONSUMPTION_ENTITY: "",
    CONF_EXCLUDED_POWER_ENTITIES: [],
    CONF_EMS_SELECT_ENTITY: "",
    CONF_CHARGE_LIMIT_ENTITY: "",
    CONF_DISCHARGE_LIMIT_ENTITY: "",
    CONF_MAX_ESS_CHARGE_AMPS: DEFAULT_MAX_ESS_CHARGE_AMPS,
    CONF_ESS_INCREASE_DELAY: DEFAULT_ESS_INCREASE_DELAY_SECONDS,
}


def apply_step_input(
    store: dict[str, Any],
    user_input: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    """Write the submitted values for the fields that were actually shown.

    A field missing from *fields* was not in the schema the user saw, so its
    stored value must survive untouched: writing user_input.get(field, default)
    for a hidden field silently blanks an entity id or resets a tuned number.
    A field that IS in *fields* but missing from *user_input* was cleared by
    the user and gets its FIELD_DEFAULTS fallback.

    Lists are copied on write so the shared FIELD_DEFAULTS list object is never
    aliased into entry.options -- EMSCoordinator keeps the stored list by
    reference, without copying it.
    """
    for field in fields:
        value = user_input.get(field, FIELD_DEFAULTS[field])
        store[field] = list(value) if isinstance(value, list) else value

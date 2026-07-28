"""Pure helper logic for the Energy Manager options flow.

Kept free of voluptuous/homeassistant imports so it can be unit tested
without a full Home Assistant environment.
"""

from __future__ import annotations

from typing import Any

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

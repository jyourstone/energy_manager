"""Home Assistant Repairs issue reporting for the Energy Manager integration.

Thin, failure-proof wrappers around homeassistant.helpers.issue_registry.
Every call is swallowed on error (debug-logged) -- reporting a repairs
issue is diagnostics, and a repairs failure must NEVER fail an update
cycle. Issues are is_persistent=False because every condition is
re-detected on the next update cycle anyway.

Issue ids are fixed strings (no entry_id namespacing) -- the config flow
enforces a single config entry instance via async_set_unique_id(DOMAIN).
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Fixed issue ids (also used as translation keys -- see strings.json "issues")
ISSUE_FUSE_SENSOR_FALLBACK = "fuse_sensor_fallback"
ISSUE_CHARGE_LIMIT_WRONG_DOMAIN = "charge_limit_wrong_domain"
ISSUE_DISCHARGE_LIMIT_WRONG_DOMAIN = "discharge_limit_wrong_domain"
ISSUE_GRID_SENSOR_MISMATCH = "grid_sensor_mismatch"

ALL_ISSUE_IDS = (
    ISSUE_FUSE_SENSOR_FALLBACK,
    ISSUE_CHARGE_LIMIT_WRONG_DOMAIN,
    ISSUE_DISCHARGE_LIMIT_WRONG_DOMAIN,
    ISSUE_GRID_SENSOR_MISMATCH,
)


def async_report_issue(
    hass: HomeAssistant,
    issue_id: str,
    severity: ir.IssueSeverity,
    translation_key: str,
    placeholders: dict[str, str] | None = None,
) -> None:
    """File (or idempotently refresh) a repairs issue. Never raises."""
    try:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=severity,
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )
    except Exception:  # diagnostics must never fail the update cycle
        _LOGGER.debug("Failed to report repairs issue %s", issue_id, exc_info=True)


def async_clear_issue(hass: HomeAssistant, issue_id: str) -> None:
    """Delete a repairs issue (no-op if not filed). Never raises."""
    try:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    except Exception:  # diagnostics must never fail the update cycle
        _LOGGER.debug("Failed to clear repairs issue %s", issue_id, exc_info=True)


def async_issue_exists(hass: HomeAssistant, issue_id: str) -> bool:
    """Return True when the issue is currently filed and active. Never raises.

    Lets callers that share a fixed issue id (the three FuseSensorReader
    instances driving grid_sensor_mismatch) skip duplicate warnings and
    re-files. Returns False on any registry error so the safe direction
    is a duplicate report, never a missed one.

    Restored-from-storage placeholders come back active=False -- HA's
    issue registry persists even is_persistent=False issues to .storage
    and restores them inactive after a restart -- and must not suppress
    re-filing; ir.async_create_issue is exactly what re-activates one.
    `is not False` (rather than truthiness) keeps MagicMock-stubbed
    registries in tests behaving as before.
    """
    try:
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        return issue is not None and issue.active is not False
    except Exception:  # diagnostics must never fail the update cycle
        _LOGGER.debug("Failed to query repairs issue %s", issue_id, exc_info=True)
        return False

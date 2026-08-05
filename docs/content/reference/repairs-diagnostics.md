# Repairs & Diagnostics

Two built-in Home Assistant tools help you spot and report problems with Energy Manager: Repairs for persistent degraded conditions, and Diagnostics for a full state snapshot to attach to a bug report.

## Repairs

Persistent degraded conditions surface in **Settings → Repairs** instead of only the log:

| Issue | Trigger |
|-------|---------|
| Fuse-protection sensor fallback | A configured grid/fuse power sensor has been unavailable and falling back to the assumed load for 5 or more continuous minutes |
| Charge limit entity in the wrong domain | The configured battery charge-limit entity isn't a `number.*` entity, so Energy Manager can't call `number.set_value` on it |
| Discharge limit entity in the wrong domain | Same check, for the configured battery discharge-limit entity |

!!! note "Issues clear themselves"
    Every issue is re-evaluated on the next update cycle, so nothing needs to be manually dismissed — fix the underlying condition (restore the sensor, repoint the entity picker to a `number.*` entity) and the Repairs entry clears automatically once Energy Manager sees a good read.

## Diagnostics

**Settings → Devices & Services → Energy Manager → Download diagnostics** gives a full snapshot of the integration's current state:

- The config entry's data and options (everything set in the setup wizard and **Configure**)
- Every active coordinator's current state (price, battery schedule, EMS, Easee charger, and each car)
- The runtime control flags: **Device control**, **force charging**, and which platforms are forwarded

Attach this file whenever you report a bug — it captures far more context than a screenshot of a single entity.

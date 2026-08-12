# Repairs & Diagnostics

Two built-in Home Assistant tools help you spot and report problems with Energy Manager: Repairs for persistent degraded conditions, and Diagnostics for a full state snapshot to attach to a bug report.

## Repairs

Persistent degraded conditions surface in **Settings → Repairs** instead of only the log:

| Issue | Trigger |
|-------|---------|
| Fuse-protection sensor fallback | A configured grid/fuse power sensor has been unavailable and falling back to the assumed load for 5 or more continuous minutes |
| Charge limit entity in the wrong domain | The configured battery charge-limit entity isn't a `number.*` entity, so Energy Manager can't call `number.set_value` on it |
| Discharge limit entity in the wrong domain | Same check, for the configured battery discharge-limit entity |
| Grid phase sensors disagree with the total grid power sensor | With both the three per-phase grid sensors and the total grid power sensor configured, their readings have disagreed significantly for 5 or more continuous minutes — usually the per-phase entities are inverter-output sensors instead of grid-flow sensors, or use an opposite sign convention |

!!! note "Issues clear themselves"
    Every issue is re-evaluated on the next update cycle, so nothing needs to be manually dismissed — fix the underlying condition (restore the sensor, repoint the entity picker to a `number.*` entity) and the Repairs entry clears automatically once Energy Manager sees a good read. The one exception is the grid sensor mismatch issue: the misconfigured sensors it detects can agree for hours at a time (at night, for example), so once raised it stays raised and is instead cleared when the integration reloads or unloads — fix the sensor configuration in **Configure** (which reloads Energy Manager) and it stays gone.

## Diagnostics

**Settings → Devices & Services → Energy Manager → Download diagnostics** gives a full snapshot of the integration's current state:

- The config entry's data and options (everything set in the setup wizard and **Configure**)
- Every active coordinator's current state (price, battery schedule, EMS, Easee charger, and each car)
- The runtime control flags: **Device control**, **force charging**, and which platforms are forwarded

Attach this file whenever you [report a bug on GitHub](https://github.com/jyourstone/energy_manager/issues) — it captures far more context than a screenshot of a single entity. The snapshot contains no credentials, but do skim it before posting publicly: it includes your entity IDs, configured fees, and schedule data.

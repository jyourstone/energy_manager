# Phase 4.1 Local UAT — Findings (2026-07-28)

**Setup:** local docker dev instance (dev/docker-compose.yaml, HA 2026.7 stable) with REAL integrations configured (native Nord Pool SE4, Sigenergy ESS local, Easee cloud, mySkoda, Forecast.Solar). Fresh config entry via wizard (old Feb entry deleted offline). Both modules enabled, Enyaq car subentry added. Observe-only (Device control switch) left at default OFF.

## Verified working

- Full 4-step wizard in Swedish; auto-detection correct for: Nordpool (Aktuellt pris SE4), SigenStor SOC/battery power/EMS select/phase A-B-C/PV, Easee status+power, Forecast.Solar. New Phase-4.1 options all present with correct defaults (fuse 20 A, buffer 1 A, assume-load 10 A, max ESS 16 A, increase delay 180 s) incl. translated fail-behavior radio labels.
- Live data end-to-end: Elpris 0.06 SEK/kWh; battery schedule computed over REAL 15-minute Nordpool slots (96/day) with correct multi-cycle actions (charge in 0.10-0.16 dip, discharge at 1.10-1.14 peaks) — slot-duration fix verified against live data.
- EMS status: standby, fuse_headroom_amps 14.8 (dynamic, signed math), command_verified true.
- **Observe-only works**: switch default OFF; `dry_run: true`; `last_suppressed_command: "[dry-run] Would call number.set_value on sensor.sigen_plant_ess_rated_charging_power with value=0.0"`; ZERO service calls issued (log verified).
- Car subentry flow: creates subentry + device, entry_type "Bil", correct storage.
- Zero errors/tracebacks in HA log.

## Bugs found (fix before Phase 5)

1. **Car SOC auto-detect picks target instead of actual SOC.** Pre-filled `Mål batteriprocent` (mySkoda `target_battery_percentage`) because it substring-matches `battery_percentage`. Also the suggested car name derived from that sensor ("Batteriprocent") instead of e.g. device/vehicle name. Fix: exclude `target_`/`mal_`-prefixed matches (or require exact suffix), and derive suggested car name from the car DEVICE name.
2. **Charge-limit actuation entity is a read-only sensor.** Autodetect selected `sensor.sigen_plant_ess_rated_charging_power` (rated capability, sensor domain) as `charge_limit_entity`; the dry-run suppressed command was `number.set_value` on that SENSOR — would fail with control enabled. The writable entity on real SigenStor is `number.sigen_plant_ess_max_charging_limit` (what AppDaemon writes). Fix: prefer number-domain `max_charging_limit`/`max_discharging_limit` patterns over `ess_rated_*` sensors; validate at config-flow time that the selected charge/discharge limit entity is in the number domain (or at least warn); same check for discharge_limit_entity. Note 03-04 allowed sensor domain here based on a misdiagnosis — rated_* sensors are capabilities, not setpoints.
3. **Runtime subentry add creates no entities until reload.** Adding the Enyaq subentry created the subentry + device but zero car entities; they only appeared after a full restart. No update/reload listener is registered for subentry changes (options flow is a stub, so no listener path exists at all). Fix: register an update listener on the entry that reloads on subentry create/update/remove (and keep it for the Phase 6 options flow).

## Environment notes

- dev/config had a stale Feb-2026 config entry with pre-audit option keys — removed offline (backup: core.config_entries.bak-20260728). HA image upgraded 2026.2.2 -> 2026.7 in the process; storage migrated cleanly.
- Live HA (192.168.50.19): integration files fully removed; an empty husk dir `/config/custom_components/energy_manager` remains due to an SMB-ghost zero-byte file named "." (tar artifact) — harmless (no manifest); remove server-side via SSH addon: `rm -rf /config/custom_components/energy_manager`.
- Dry-run suppression logs at INFO are not visible with this instance's default logger config; the status-sensor attributes are the observable surface. Consider logging suppressed commands at WARNING the first time, or documenting the logger config for UAT.

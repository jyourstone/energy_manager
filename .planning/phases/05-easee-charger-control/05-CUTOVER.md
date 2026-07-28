# Cutover Checklist — AppDaemon → Energy Manager (live HA)

Run through IN ORDER when ready to migrate the live system. Do NOT enable
Device control (Enhetsstyrning) until the parallel-run phase has proven the
decisions match expectations.

## Phase A — parallel run (observe-only)

1. Enable the two writable SigenStor setpoints if disabled (Settings > Entities):
   `number.sigen_plant_ess_max_charging_limit`, `number.sigen_plant_ess_max_discharging_limit`
   (live HA already has them enabled — AppDaemon writes them).
2. Install Energy Manager (HACS custom repo or copy), run the wizard.
   Verify prefills; set battery capacity 24.18 kWh, car capacities (Enyaq 77 kWh),
   Cissis bil phase capability = 2. Configure notify target.
3. Leave `switch.energy_manager_enhetsstyrning` OFF (default). AppDaemon keeps
   controlling hardware.
4. Compare for at least a few days across price shapes and a sunny day:
   - `sensor.energy_manager_ems_status` decisions vs `sensor.ems_controller_status`
   - `sensor.energy_manager_laddarstatus` (mode/target amps) vs `sensor.easee_controller_status`
   - battery/car schedules vs `sensor.battery_charge_schedule_py` / `*_car_charging_manager_py`
   - `dry_run: true` and `last_suppressed_command` show what WOULD have been sent
5. Investigate every divergence before proceeding (expected divergences:
   15-min slot granularity; static thresholds until Phase 6 economics).

## Phase B — cutover

1. Disable the 5 AppDaemon apps (comment out in apps.yaml or stop the addon):
   home_battery, enyaq_car, id3_car, ems_controller, easee_controller.
2. Remove/disable legacy automations in live HA:
   - `automation.enyaq_laddningsautomatik` + `automation.id_3_laddningsautomatik` (already dead — reference nonexistent entities)
   - `automation.satt_avresetid_johans_bil` / `..._cissis_bil` (departure reset)
   - `automation.satt_malladdning_johans_bil` / `..._cissis_bil` (target reset)
   - `automation.ladda_om_easee_konfiguration_om_den_hangt_sig` (Easee watchdog —
     integration has native stuck-state detection; if Easee status flakiness
     persists in practice, re-add the watchdog but guard against firing mid-command)
3. Seed live values into integration entities (they start at defaults):
   discharge threshold (live formula value = battery_cycle_cost − grid_transfer_fee),
   charge threshold, departure times, target SOCs, max charge powers.
4. Turn ON `switch.energy_manager_enhetsstyrning` during a calm period (midday,
   no charging scheduled). Watch the first mode transition and first ESS-limit
   write. Verify `command_verified: true` and no fuse warnings.
5. First car charge session: watch target amps ramp (120 s increase steps),
   confirm charger follows, verify fuse headroom stays positive.

## Phase C — cleanup (after stable operation)

- Delete the 24 manual helpers + template sensors as Phase 6 internalizes them
  (keep `input_number.battery_cycle_cost` + fee helpers until Phase 6 economics lands).
- Repoint dashboards from `*_py` sensors to integration sensors.
- Uninstall/disable the AppDaemon addon apps permanently; archive the git repo.

## Rollback

Turn OFF `switch.energy_manager_enhetsstyrning` (instant, observe-only again),
re-enable the 5 AppDaemon apps, restart AppDaemon addon. No state is lost —
AppDaemon reads everything from HA entities.

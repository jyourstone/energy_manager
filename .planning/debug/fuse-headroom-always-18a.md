---
status: diagnosed
trigger: "fuse_headroom_amps stays at 18A all the time regardless of actual phase power"
created: 2026-02-22T13:32:49Z
updated: 2026-02-22T13:32:49Z
symptoms_prefilled: true
goal: find_root_cause_only
---

## Current Focus

hypothesis: L-current entity not configured (empty string) → _read_float_state returns 0.0 default → headroom = fuse_rating - 0 - safety_buffer = 20 - 0 - 2 = 18A always
test: traced entire chain from config_flow through coordinator to ems_controller
expecting: confirmed — the 18A value is the result of 0A current draw being assumed
next_action: COMPLETE — root cause identified, ready to report

## Symptoms

expected: fuse_headroom_amps should decrease as phase load increases
actual: fuse_headroom_amps always reads 18A regardless of real load
errors: none visible — sensor works, value is just static
reproduction: any time L-current entity is not configured or returns unavailable
started: from initial integration setup if L-current auto-detection failed

## Eliminated

- hypothesis: sensor.py EMSStatusSensor reports wrong field from EMSData
  evidence: sensor.py line 338 directly reads data.fuse_headroom_amps from EMSData with no transformation
  timestamp: 2026-02-22T13:32:49Z

- hypothesis: compute_ems_state() has a bug in the headroom formula
  evidence: ems_controller.py line 187 formula is correct: max(0.0, fuse_rating - current_l_amps - safety_buffer)
  timestamp: 2026-02-22T13:32:49Z

- hypothesis: config_flow stores L-current entity in entry.data instead of entry.options
  evidence: config_flow.py line 424 puts CONF_L_CURRENT_ENTITY into options dict, coordinator.py line 552 reads from entry.options — they match
  timestamp: 2026-02-22T13:32:49Z

## Evidence

- timestamp: 2026-02-22T13:32:49Z
  checked: ems_controller.py line 187
  found: headroom = max(0.0, fuse_rating_amps - current_l_amps - safety_buffer_amps)
  implication: with fuse_rating=20, current_l_amps=0, safety_buffer=2 → headroom = 18.0 exactly

- timestamp: 2026-02-22T13:32:49Z
  checked: coordinator.py line 637
  found: l_current = self._read_float_state(self._l_current_entity, 0.0)
  implication: default is 0.0 — if entity_id is empty string or entity is unavailable, 0.0 is used

- timestamp: 2026-02-22T13:32:49Z
  checked: coordinator.py _read_float_state() lines 713-733
  found: if not entity_id: return default — empty string causes immediate 0.0 return
  implication: if user skipped L-current config or auto-detect returned nothing, _l_current_entity = "" and l_current is always 0.0

- timestamp: 2026-02-22T13:32:49Z
  checked: coordinator.py lines 552-554
  found: self._l_current_entity: str = entry.options.get(CONF_L_CURRENT_ENTITY, "")
  implication: if CONF_L_CURRENT_ENTITY was never written to options (e.g., user clicked through EMS step without picking an entity), it defaults to ""

- timestamp: 2026-02-22T13:32:49Z
  checked: auto_detect.py find_sigenstor_ems_entities() lines 198-252
  found: auto-detection looks for "highest_l_current" or "l_current" or "phase_current" in entity_id or unique_id
  implication: if real entity is e.g. sensor.sigen_phase_a_current (not matching any of these patterns), auto-detect returns nothing for CONF_L_CURRENT_ENTITY — field shows blank in UI

- timestamp: 2026-02-22T13:32:49Z
  checked: config_flow.py EnergyManagerOptionsFlow lines 440-456
  found: OptionsFlow is a stub — async_step_init returns self.config_entry.options unchanged with empty schema vol.Schema({})
  implication: user cannot reconfigure L-current entity via the Options flow without a full re-setup

- timestamp: 2026-02-22T13:32:49Z
  checked: coordinator.py _async_setup() lines 586-593
  found: if self._l_current_entity: — state-change listener is only registered when entity_id is non-empty
  implication: with empty entity_id, no reactive updates trigger for fuse protection either — the 30-second polling interval is all that runs

## Resolution

root_cause: |
  CONF_L_CURRENT_ENTITY is empty string in entry.options, causing _read_float_state() to return the
  default of 0.0 for current_l_amps on every update cycle. The formula in compute_ems_state() is correct:
  headroom = 20 (fuse) - 0.0 (assumed current) - 2.0 (safety buffer) = 18.0A, a static value.

  The 18A is not a calculation error — it is the mathematically correct result when current load is
  assumed to be zero. The bug is that the L-current entity was never wired up, either because:
  (a) Auto-detection in find_sigenstor_ems_entities() failed to find the entity (entity name doesn't
      match any of the checked patterns: "highest_l_current", "l_current", "phase_current"), or
  (b) The user left the field blank during the EMS config flow step.

  There is also a secondary compounding issue: the OptionsFlow is a stub (lines 440-456 of config_flow.py)
  that shows an empty form and cannot update any settings. This means the user cannot fix the missing
  L-current entity without deleting and re-adding the integration.

fix: NOT APPLIED (goal: find_root_cause_only)
verification: NOT PERFORMED
files_changed: []

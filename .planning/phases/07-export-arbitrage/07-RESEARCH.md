# BATT-17: Battery → Grid Export Arbitrage — Investigation

**Status: POST-CUTOVER BACKLOG — investigation only, no implementation plan yet.**
Owner requirements (2026-08-02): opt-in, **disabled by default**, absolute spike
threshold (configurable), **reserve SOC floor** (e.g. never sell below 20%).

## Goal

Discharge the home battery **into the grid** (beyond house load) when the spot
price spikes high enough that selling now beats every future use of that energy.
EM today only does self-consumption arbitrage: discharge covers house load
(~1-2 kW), capped implicitly by consumption. On spike days the battery holds
20+ kWh that could earn real money at 3-8 SEK/kWh.

## Economics (Sweden, 2026 — owner-confirmed facts)

- **The 60 öre/kWh skattereduktion for sold electricity is scrapped entirely**
  (owner-confirmed 2026-08-02). Export revenue ≈ spot + nätnytta/energiersättning
  from the DSO (typically ~4-8 öre/kWh — exact value: open question, check
  Johan's DSO contract).
- **Buy-side fees are large:** live prod reading 2026-08-02: spot 0.077 vs
  actual price 0.927 SEK/kWh → transfer fee + energiskatt + company fee
  ≈ **0.85 SEK/kWh**. This asymmetry (sell at bare spot, buy back with +0.85)
  makes Swedish export arbitrage much harder than SEM's Dutch example.
- **Grid-sourced energy:** profitable iff
  `spike_spot > cheap_spot + 0.85/η + cycle_cost` ≈ cheap + ~1.2 SEK
  (η≈0.9, cycle cost from existing Battery Cycle Cost formula).
  Rare — winter spike events only. This is why the spike threshold approach
  (owner's instinct) is correct: don't model marginal cases, only fire on
  unambiguous spikes.
- **Solar-sourced energy:** opportunity cost of stored solar = avoided evening
  import = `evening_spot + 0.85`. Export at spike beats holding iff
  `spike_spot + nätnytta > evening_spot + 0.85` — and only for energy **beyond**
  what the scheduled discharge slots + reserve need.
- **Effekttariff:** NOT relevant — the mandatory rollout was scrapped; provider
  decides, and owner's provider will not add it. No peak-billing term in the
  economics.

## What SEM did (and why it's dead there) — full autopsy

SEM implemented this (`evaluate_arbitrage`, sem battery_charge_scheduler.py:643)
with a sound economics gate, worth mirroring:

1. Export rate ≥ configurable min-export floor
2. SOC > configurable reserve floor (never sell backup)
3. `export > cheapest_upcoming_import / roundtrip_eff + 2 × cycle_cost`
   (sell now must beat buying the same kWh back later)
4. **No import-price forecast → hold** (can't prove profitable → don't fire)
5. Max-export-W cap (they added it for capacity-tariff markets; we need it
   for the fuse — see below)

**The incident that killed it (their #532/#533):** a SEM restart stranded an
in-flight Huawei forcible discharge. Nobody sent stop. The inverter drained a
real LUNA2000 **80% → 20% exporting to grid unsupervised**. They pulled the
feature from stable 1.7.3, retargeted re-enable to 1.7.4, then closed the
re-enable issue **"not planned"** — v1.7.4/1.7.5-beta ship with the mode
removed from the selector and the toggle force-off. The feature is abandoned
dormant code as of 2026-08.

**Root cause is architectural:** imperative one-shot commands ("start force
discharge") with no reconciliation loop. A restart loses the intent; hardware
keeps executing the last order.

## Why EM is structurally better positioned

EM's EMS controller is declarative: every 30s cycle it recomputes the schedule
and re-asserts the target mode/limits from current state. A restart recomputes
and immediately commands the *correct* mode for the current slot — no stranded
intent. The residual risk is **EM crash / HA down mid-export**: SigenStor keeps
the last commanded mode. Mitigations (all must be in the plan):

1. **Hardware floor precondition:** SigenStor plant backup/min-SOC must be
   configured at the inverter level ≥ EM's own min SOC. Worst case is then
   bounded by hardware, exactly like SEM's incident stopped at 20% (their
   hardware floor). Document as a setup precondition; verify at runtime and
   file a Repairs issue if unset/lower.
2. **Reserve SOC floor config** (owner requirement, default ~20%): EM stops
   export slots when SOC ≤ floor, independent of hardware floor.
3. **Fuse cap on export power:** plant `ess_max_discharging_limit` is
   currently 14.4 kW; the 20A×3×230V main fuse ceiling is **13.8 kW**. Export
   is fuse current in the export direction:
   `export_limit_kw ≤ (fuse_amps − safety_buffer) × 3 × 0.230 + house_load_kw`
   (house load consumes battery output before the meter). Reuse the existing
   signed fuse-chain conventions; the existing per-phase guard must treat
   sustained export as a first-class flow, not just headroom.
4. **Observe-only first:** ship the scheduler marking `export` slots +
   attributes with the feature commanding nothing (same pattern as the whole
   parallel run). Enable hardware commands only after the dry-run schedule
   looks right across a few spike events.

## Scheduler integration sketch

- **Unified merit order** (the elegant fit to EM's existing virtual-energy
  optimizer): value per kWh of a candidate slot —
  - discharge (self-consumption): `spot + fees` (avoided import ≈ +0.85)
  - export: `spot + nätnytta` (bare-ish spot)
  Export slots therefore only outrank discharge slots on genuine spikes —
  the economics fall out of the existing demotion logic naturally.
- Export slot qualifies iff `spot ≥ CONF_EXPORT_SPIKE_THRESHOLD` AND the
  SEM-style replacement-cost check passes (reuse existing cycle-cost and
  transfer-fee entities — EM has the real fee structure as numbers already).
- Energy budget: `usable − Σ(scheduled discharge energy) − reserve_floor_energy`.
  Export never demotes a self-consumption slot unless its value/kWh is higher
  (merit order handles this).
- Execution: EMS mode `Command Discharging (ESS First)` (confirmed present in
  prod select options) + discharge-limit number = fuse-capped export power.
  New slot action `"export"`, new state `exporting`.

## Config surface (minimal)

| Key | Default | Meaning |
|-----|---------|---------|
| `CONF_EXPORT_SPIKE_THRESHOLD` | unset = **feature off** | Spot price (SEK/kWh) above which export slots may be scheduled |
| `CONF_EXPORT_RESERVE_SOC_PCT` | 20 | Never export below this SOC |

Export power cap derived from fuse config — not a user knob.

## Open questions (resolve before planning)

1. Exact nätnytta/energiersättning value from Johan's DSO (öre/kWh, feeds the
   merit order; safe to assume 0 initially — conservative).
2. `Command Discharging (ESS First)` vs `(PV First)` exact SigenStor semantics
   (ESS First presumed: battery discharges at limit regardless of PV; verify
   against Sigenergy Modbus docs / live test at cutover).
3. Does the Sigenergy integration expose a grid-export-limitation entity?
   (Not found under guessed names on prod — check integration docs; plant-level
   export limit may also cap what export arbitrage can push.)
4. Does SigenStor's Remote EMS (`PCS Remote Control` path) have a heartbeat/
   timeout that reverts to self-consumption if the controller goes silent?
   If yes, the crash-mid-export residual risk shrinks to the timeout window.
5. Tax/moms on sold energy for a private producer post-reduction-scrapping —
   owner tracks; assume net spot for design.

## Sequencing

Strictly post-cutover: (1) cutover completes and discharge-gate validation
ends; (2) forecast-accuracy Stage 2 lands (learned production factor improves
the solar-energy budget the export planner draws from); (3) BATT-17 observe-only
ships behind unset-threshold default; (4) enable after a few observed spike
events look right. Winter 2026/27 is the payoff window — design in autumn.

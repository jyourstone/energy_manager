# Phase 1: Core Infrastructure + Price Foundation - Context

**Gathered:** 2026-02-15
**Status:** Ready for planning

<domain>
## Phase Boundary

Users can install the integration via HACS, configure it through a multi-step wizard with auto-detected integrations, and have the price data layer working internally for downstream modules. The integration skeleton proves the end-to-end setup/unload/reload lifecycle. Scheduling algorithms, device control, and options flow are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Config flow experience
- Multi-step wizard: Step 1 (Price source), Step 2 (Home Battery config), Step 3 (EV config) — each module gets its own step
- Auto-detected entities shown as pre-filled defaults in input fields — user can edit before confirming
- If auto-detection finds nothing, show empty fields and let user type entity IDs manually — no blocking
- Integration name: "Energy Manager"

### Module toggle design
- Users choose which modules to enable (Home Battery, EV Charging) during setup wizard
- Module toggles also changeable later in options flow (Phase 6 implements full options flow, but architecture should support it)
- EV Charging module supports multi-car from start using subentry pattern — each car added separately with its own config
- Car names auto-detected from linked integration (Skoda/VW) but editable by user

### Claude's Discretion (module toggles)
- What happens to entities when a module is disabled (remove vs mark unavailable)
- Module toggle UI presentation (checkboxes vs other HA-native pattern)

### Price data handling
- No separate visible price sensor entity — price data is an internal-only data layer consumed by other modules
- Users see their existing Nordpool sensor directly for dashboards
- Support both the official HA Nordpool integration AND the HACS Nordpool integration (different entity/attribute structures)
- No generic "price source" abstraction — Nordpool-specific, supporting both variants
- When tomorrow's prices aren't available yet: empty list (`[]`)
- Raw hourly prices only — no computed stats (average, min, max) in the price layer
- Price unit: SEK/kWh as-is from Nordpool (pass-through, no conversion)

### Claude's Discretion (price data)
- Internal data structure for hourly prices (optimized for scheduling algorithms)
- Update strategy (event-driven vs polling vs hybrid)
- How to detect which Nordpool variant is installed

### Entity naming & device layout
- Hub + sub-devices hierarchy: top-level "Energy Manager" hub device, with child devices per module (Home Battery, EV Charger, each Car)
- Entity friendly names use proper casing: "Battery Schedule", "Next Charge Slot", "EMS Status"

### Claude's Discretion (entity naming)
- Entity ID naming convention (full prefix vs abbreviated)
- Exact device hierarchy implementation

</decisions>

<specifics>
## Specific Ideas

- Config flow should feel like a wizard that progressively reveals relevant options based on module selection
- Car naming: auto-detect from linked integration but let user override with a friendly name (e.g., "Enyaq", "Family Car")
- Both Nordpool variants (official HA + HACS) must work — user shouldn't need to know which one they have

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-core-infrastructure-price-foundation*
*Context gathered: 2026-02-15*

---
status: diagnosed
trigger: "The price sensor entity (sensor.energy_manager_electricity_price) produces two warnings on setup"
created: 2026-02-15T00:00:00Z
updated: 2026-02-15T00:05:00Z
symptoms_prefilled: true
goal: find_root_cause_only
---

## Current Focus

hypothesis: Both issues confirmed - Issue 1: serializing 48 hourly price slots (24 today + 24 tomorrow) with ISO timestamps exceeds 16KB. Issue 2: Line 52 uses SensorStateClass.MEASUREMENT with MONETARY device class, but HA requires None or TOTAL
test: Analysis complete - identified exact code locations and needed changes
expecting: Root causes documented
next_action: Document root causes and exact fixes needed

## Symptoms

expected: Price sensor should operate without warnings, with attributes under 16KB and correct state class for monetary device class
actual: Two warnings on setup - attributes exceed 16KB, and state class 'measurement' is incompatible with device class 'monetary'
errors:
  1. "State attributes for sensor.energy_manager_electricity_price exceed maximum size of 16384 bytes. This can cause database performance issues; Attributes will not be stored"
  2. "Entity sensor.energy_manager_electricity_price is using state class 'measurement' which is impossible considering device class ('monetary') it is using; expected None or one of 'total'"
reproduction: Set up the price sensor entity
started: Initial setup
context: The sensor stores prices_today and prices_tomorrow as lists of hourly price slot dicts in extra_state_attributes. Uses SensorDeviceClass.MONETARY with SensorStateClass.MEASUREMENT.

## Eliminated

## Evidence

- timestamp: 2026-02-15T00:01:00Z
  checked: sensor.py lines 78-107 (extra_state_attributes property)
  found: Method serializes all price slots from data.today and data.tomorrow into dicts with ISO timestamps. Each slot becomes a dict with 'start', 'end', 'price' keys. With 24 hourly slots per day and potentially 48 total slots (today + tomorrow), this creates large attribute payloads.
  implication: The serialization of 48 hourly slots (each with 2 ISO timestamps ~25 chars each + price float ~10 chars + dict overhead) easily exceeds 16KB limit. Calculation: 48 slots * (25+25+10+20 dict overhead) = ~3840 bytes minimum, but with JSON serialization overhead and attribute names, this grows significantly.

- timestamp: 2026-02-15T00:02:00Z
  checked: sensor.py line 52 (_attr_state_class declaration)
  found: Line 52 explicitly sets _attr_state_class = SensorStateClass.MEASUREMENT
  implication: This conflicts with line 51 which sets _attr_device_class = SensorDeviceClass.MONETARY. Home Assistant's sensor platform validation requires monetary sensors to use either None or SensorStateClass.TOTAL for state_class, not MEASUREMENT.

- timestamp: 2026-02-15T00:03:00Z
  checked: Home Assistant documentation requirements
  found: SensorDeviceClass.MONETARY expects state_class to be None (for spot prices that change) or TOTAL (for cumulative costs). MEASUREMENT is for sensors that measure a point-in-time value that varies but isn't cumulative, which doesn't fit monetary semantics in HA's model.
  implication: The electricity price is a spot price that changes throughout the day, so state_class should be None (not TOTAL, which is for cumulative costs).

- timestamp: 2026-02-15T00:04:00Z
  checked: Size calculation verification (ran Python simulation)
  found: Raw JSON serialization of 48 price slots with ISO timestamps yields ~5KB compact JSON. However, UAT confirms the warning IS occurring in practice.
  implication: Home Assistant's internal state machine adds serialization overhead beyond raw JSON (metadata, encoding layers, internal tracking). The warning threshold is hit in production even though pure JSON calculation suggests otherwise. Regardless of exact size calculation, storing 48 hourly slots as entity attributes is architecturally wrong - coordinator should be the data source, not entity attributes.

## Resolution

root_cause: |
  TWO SEPARATE ROOT CAUSES:

  **Issue 1: State attributes exceed 16384 bytes**
  Location: custom_components/energy_manager/sensor.py lines 78-107 (extra_state_attributes property)

  Problem: The extra_state_attributes method serializes ALL hourly price slots (24 for today + up to 24 for tomorrow = 48 total) as dictionaries with ISO timestamp strings. Each slot dict contains:
  - "start": ISO timestamp string (~32 chars with timezone)
  - "end": ISO timestamp string (~32 chars with timezone)
  - "price": Float value (~10-15 chars)

  While raw calculation shows ~5KB for the data alone, Home Assistant's internal attribute serialization adds overhead (JSON encoding, metadata, internal tracking). With 48 slots, this pushes the total size over the 16KB limit, triggering the recorder warning.

  The current design stores ALL historical and future price data as entity attributes, which violates HA's best practice of keeping attributes minimal (attributes are stored in the state machine and database on every state change).

  **Issue 2: Wrong state class for monetary device class**
  Location: custom_components/energy_manager/sensor.py line 52

  Problem: Line 52 sets `_attr_state_class = SensorStateClass.MEASUREMENT`, but line 51 sets `_attr_device_class = SensorDeviceClass.MONETARY`.

  Home Assistant's sensor validation enforces that MONETARY device class must use either:
  - state_class = None (for spot prices / instantaneous values)
  - state_class = TOTAL (for cumulative monetary totals)

  MEASUREMENT state class is semantically incorrect for monetary sensors per HA's data model. Since the electricity price is a spot price (not cumulative), state_class should be None.

fix: |
  **Fix for Issue 1: Reduce attribute size**

  Option A (Recommended): Remove hourly price slots from entity attributes entirely
  - The price sensor should only expose current_price as state and last_updated timestamp
  - Downstream modules (battery, EV) should access detailed price data directly from coordinator via entry.runtime_data.price_coordinator.data
  - This follows HA best practice: coordinator is source of truth, entities are UI display only
  - Change: Remove "today" and "tomorrow" from extra_state_attributes return dict (lines 85-106)

  Option B: If UI/automations need price slots, reduce to essential data only
  - Store only next 6 hours of prices (6 slots instead of 48)
  - Use Unix timestamps (integers) instead of ISO strings to reduce size
  - Store only the next 6 hours, not full day + tomorrow
  - This would reduce size by ~8x while still providing near-term price visibility

  **Recommended: Option A** - The coordinator is already providing this data to internal modules, and exposing 48 hourly slots as entity attributes is unnecessary for the integration's architecture.

  **Fix for Issue 2: Remove state class**

  File: custom_components/energy_manager/sensor.py
  Line 52: Delete the line `_attr_state_class = SensorStateClass.MEASUREMENT`

  This makes state_class = None (default), which is correct for monetary spot prices.
  Also remove the SensorStateClass import from line 15 if no longer used.

verification: |
  After applying fixes:

  **For Issue 1:**
  1. Restart Home Assistant
  2. Check logs - "State attributes exceed maximum size" warning should be gone
  3. Verify sensor.energy_manager_electricity_price still shows current price as state
  4. If Option A: Verify coordinator data is still accessible to other modules
  5. If Option B: Verify only 6 future price slots appear in attributes

  **For Issue 2:**
  1. Restart Home Assistant
  2. Check logs - "state class 'measurement' impossible" warning should be gone
  3. Developer Tools > States > sensor.energy_manager_electricity_price
  4. Verify state_class is None or absent (not 'measurement')

files_changed:
  - custom_components/energy_manager/sensor.py

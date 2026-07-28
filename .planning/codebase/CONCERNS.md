# Codebase Concerns

**Analysis Date:** 2025-02-15

## Tech Debt

**Bare exception handlers throughout codebase:**
- Issue: Multiple locations use bare `except:` blocks without specifying exception types, masking real errors and making debugging difficult
- Files:
  - `apps/easee_controller.py` lines 1214, 1223
  - `apps/ems_controller.py` lines 597, 606
  - `apps/log_rotation_helper.py` line 194
- Impact: Silent failures; timer checks or scheduler operations fail without being logged or handled appropriately
- Fix approach: Replace bare `except:` with specific exception types (`except (AttributeError, KeyError, TypeError):`) and add logging to understand what's failing

**EaseeController is oversized and complex:**
- Issue: `apps/easee_controller.py` is 1583 lines - largest file in codebase - with multiple responsibilities (status management, charging sequence, solar charging, limit calculation, dynamic limits)
- Files: `apps/easee_controller.py`
- Impact: Difficult to test, maintain, and understand. Single points of failure affect multiple features
- Fix approach: Split into separate modules:
  - `charging_sequencer.py` - handles start/stop charging sequences
  - `limit_calculator.py` - calculates safe charging limits based on fuse/capacity
  - `status_handler.py` - manages charger status and state transitions
  - Keep main controller as orchestrator only

**State management via polling instead of listeners:**
- Issue: EMSController uses manual polling with `check_state_changes()` instead of AppDaemon's event listeners
- Files: `apps/ems_controller.py` lines 242-264
- Impact: Simulates listeners poorly; relies on `previous_states` dict that can become inconsistent if state changes happen faster than polling interval
- Fix approach: Replace polling with native AppDaemon `listen_state()` callbacks for all monitored entities

**Broad exception handling in error paths:**
- Issue: Multiple try-except blocks catch generic Exception without distinguishing between recoverable and non-recoverable errors
- Files:
  - `apps/car_charging_manager.py` lines 184, 332
  - `apps/home_battery_manager.py` lines 298, 392, 535
  - `apps/ems_controller.py` lines 348, 586, 630, 647, 735
- Impact: Errors are logged but execution continues, potentially leaving system in inconsistent state
- Fix approach: Categorize errors (network, configuration, logic) and handle appropriately; fail fast for configuration errors

## Known Bugs

**Infinite loop bug previously fixed in EMSController:**
- Symptoms: State changes not detected properly, infinite retry loops when `set_state()` fails
- Files: `apps/ems_controller.py` line 15 (bug fix documented)
- Trigger: When setting state fails but `previous_states` was updated, causing false change detection
- Workaround: Current code only updates `previous_states` when real changes detected (line 261)
- Status: Fixed but similar pattern risk in other apps

**Timer validity checking is fragile:**
- Symptoms: Checking if timer handles are still valid requires accessing internal AppDaemon structures (`self.AD.sched.schedule`)
- Files: `apps/easee_controller.py` lines 1209-1225
- Trigger: AppDaemon version changes or internal API changes
- Workaround: Current code has try/except fallback that assumes timer is invalid on error
- Fix approach: Maintain timer state locally; don't rely on AppDaemon internals

**State consistency between set_state calls and actual HA state:**
- Symptoms: Setting state via `set_state()` may fail silently; no guaranteed sync back to Home Assistant
- Files: `apps/ems_controller.py` lines 107, 346; `apps/car_charging_manager.py` line 148+
- Trigger: HA service failures, permissions issues, entity not existing yet
- Workaround: None; app continues assuming state was set
- Fix approach: Add retry logic with exponential backoff; log all state change attempts and their results

## Security Considerations

**No input validation on entity IDs:**
- Risk: Malformed or missing entity IDs in config will cause runtime failures
- Files: All app files in initialize() - rely on `self.args.get()` without validation
- Current mitigation: Logs warnings but proceeds; will crash when trying to get_state on invalid entity
- Recommendations:
  - Validate all required entity IDs exist and are accessible at startup
  - Check entity state before operations (verify "unavailable" vs missing)
  - Fail fast during initialization if critical entities missing

**Exception details logged to files:**
- Risk: Traceback details could expose system paths or internal structure information
- Files: `apps/easee_controller.py` line 502 logs full traceback
- Current mitigation: Traceback goes to log files (not network)
- Recommendations: Log only exception type/message; save traceback only for ERROR+ level in debug mode

**No authentication for state manipulation:**
- Risk: Apps can set arbitrary entity states; no validation that changes are authorized
- Files: All apps use `set_state()` and `call_service()` without checks
- Current mitigation: AppDaemon restricts to authenticated sessions
- Recommendations: Verify this is enforced at AppDaemon level; document assumptions

## Performance Bottlenecks

**Polling-based state change detection:**
- Problem: EMS controller runs `check_state_changes()` every 5 seconds, iterating all previous_states even when nothing changed
- Files: `apps/ems_controller.py` lines 229-264, main loop every 5s (line 62)
- Cause: Manual polling instead of event-driven listeners
- Improvement path: Replace with `listen_state()` callbacks; only run logic when actual state changes occur

**Inefficient list operations in schedule processing:**
- Problem: `car_charging_manager.py` creates old_map dict from schedule, then iterates again - O(n²) effectively
- Files: `apps/car_charging_manager.py` lines 151-152, then line 231+
- Cause: No indexing on schedule slots
- Improvement path: Use dict comprehension; index by time slot immediately

**Repeated attribute lookups in loops:**
- Problem: State lookups happen repeatedly in tight loops without caching
- Files: `apps/easee_controller.py` - multiple `get_state()` calls per loop iteration
- Cause: Defensive programming - getting latest state each time
- Improvement path: Cache at loop start; only re-fetch when state change detected

**String parsing in hot paths:**
- Problem: Departure time parsing (`datetime.fromisoformat()`) and split operations in scheduling loops
- Files: `apps/car_charging_manager.py` lines 177-187
- Cause: Trying multiple parse strategies without early exit
- Improvement path: Use single parse format; validate config format at startup

## Fragile Areas

**Schedule attribute parsing is fragile:**
- Files:
  - `apps/car_charging_manager.py` lines 151-157 (get schedule attribute)
  - `apps/home_battery_manager.py` lines 389-395 (parse datetime from schedule)
- Why fragile: Relies on specific attribute structure; no schema validation; silent failures return empty list
- Safe modification: Add schema validation function; test with sample data before deployment
- Test coverage: No unit tests for schedule parsing; only integration tested

**Easee charger status state machine:**
- Files: `apps/easee_controller.py` lines 422-451 (start sequence), 468-479 (stop sequence)
- Why fragile: Complex state transitions (awaiting_start → ready_to_charge → charging) with timed delays
  - Multiple parallel delays (4s initial limit, 5s extra resume) can interfere
  - Status changes while sequence running can leave system in bad state
- Safe modification: Add state machine diagram; test all status transitions; add guards against concurrent commands
- Test coverage: No automated tests; manual testing only

**Car charging vs battery charging priority logic:**
- Files: `apps/ems_controller.py` lines 280-290 (priority decision)
- Why fragile: Multiple overlapping conditions (car_charging_active, car_charging_scheduled, battery wants grid charging)
  - If any sensor unavailable or returns unexpected value, wrong mode selected
  - No fallback if schedule sensor missing
- Safe modification: Add detailed decision matrix; validate sensor values at start of logic
- Test coverage: Partially tested; missing edge cases (one car plugged in, other not, etc)

**Fuse-based capacity calculation:**
- Files: `apps/easee_controller.py` lines 843-870+ (capacity calculation), lines 800-812 (usage)
- Why fragile: Depends on highest_l_current_sensor returning numeric value; no error handling if "unavailable"
  - Math: `available_capacity = max_ampere - highest_current + easee_current_dynamic_amps` assumes all values valid
  - Uses math.ceil() assuming positive; could produce negative values
- Safe modification: Add input validation; clamp to [0, max_ampere]; add logging of all inputs
- Test coverage: No tests; only manual verification with real charger

## Scaling Limits

**Single-threaded AppDaemon limits concurrent operations:**
- Current capacity: 5+ apps with 5-60 second check intervals
- Limit: If any operation blocks (network call, file I/O), all other apps stall
- Scaling path:
  - Use `async` APIs where available (AppDaemon supports async callbacks)
  - Offload slow operations (API calls) to background tasks
  - Monitor loop timing; alert if any check takes >1s

**Log file growth without bounds:**
- Current capacity: Log rotation not enabled by default; DEBUG level logs everything
- Limit: Log files can grow to gigabytes; no automatic cleanup
- Scaling path: Enable log rotation in apps.yaml; set max_files=10, max_file_size_mb=20
- Fix: `apps/logging_utils.py` line 100 - rotation defaults to False

**State attribute storage in sensor attributes:**
- Current capacity: `sensor.ems_controller_status_py` stores history/state in attributes
- Limit: HA limits attribute size; very large schedules could cause issues
- Scaling path: Store long-term history in external database; keep attributes for current state only
- Files: `apps/ems_controller.py` line 96+

## Dependencies at Risk

**AppDaemon API compatibility:**
- Risk: Code accesses internal AppDaemon structures (`self.AD.sched.schedule`)
- Impact: Breaks on AppDaemon version upgrades
- Current version requirement: Not specified in codebase
- Migration plan:
  1. Replace internal access with public API calls
  2. If no public API exists, maintain version-specific compatibility layer
  3. Add version check at startup

**Python version compatibility:**
- Risk: Using features like `datetime.fromisoformat()` (Python 3.7+) without version check
- Impact: Breaks on older Python versions
- Current requirement: Not specified
- Migration plan: Add Python version check; document minimum version; provide fallback for older versions

## Missing Critical Features

**No retry logic for Home Assistant service calls:**
- Problem: `call_service()` and `set_state()` calls have no retry or failure handling
- Blocks: If HA is restarting, app can't recover; charger commands may be silently lost
- Files: All apps - `apps/easee_controller.py` lines 439-446, 820-822; `apps/ems_controller.py` lines 346, 628, 645

**No configuration validation:**
- Problem: Required config keys (entity_ids, sensors) not validated at startup
- Blocks: Bad config only discovered at runtime when state lookup fails
- Files: All apps - initialize() methods don't validate `self.args`

**No watchdog/heartbeat for app health:**
- Problem: No way to detect if app is stuck, unresponsive, or looping
- Blocks: Can't alert user to stuck processes
- Files: All apps - no health/status endpoints

**No integration test framework:**
- Problem: All testing is manual; no way to verify apps work together
- Blocks: Can't catch regressions; hard to validate complex scenarios
- Files: No test directory; no test infrastructure

## Test Coverage Gaps

**Schedule parsing not tested:**
- What's not tested: `car_charging_manager.py` schedule attribute parsing and datetime conversion
- Files: `apps/car_charging_manager.py` lines 155-157, 390-394
- Risk: Malformed schedule data silently returns empty list; no error indication
- Priority: High - core feature

**State change detection not tested:**
- What's not tested: Polling loop correctly detects state changes and triggers handlers
- Files: `apps/ems_controller.py` lines 242-264
- Risk: Edge cases (rapid changes, timer intervals) could cause missed detections
- Priority: High - core feature

**Fuse capacity math not tested:**
- What's not tested: Capacity calculation with various input values (negative currents, unavailable states, edge cases)
- Files: `apps/easee_controller.py` lines 800-812, 843-870+
- Risk: Wrong limit set; charger starts without capacity; blown fuses
- Priority: Critical - safety issue

**Error handling paths not tested:**
- What's not tested: Exception handlers for API failures, missing entities, invalid states
- Files: All exception handlers throughout codebase
- Risk: Unknown behavior on failures; silent degradation
- Priority: Medium - reliability

**Car charging priority logic not tested:**
- What's not tested: All combinations of car plugged in, scheduled, battery wants charging
- Files: `apps/ems_controller.py` lines 280-290
- Risk: Wrong priority selected in edge cases
- Priority: High - feature correctness

**Charger status transitions not tested:**
- What's not tested: All charger states (awaiting_start, ready_to_charge, charging, completed, error, etc) and transitions
- Files: `apps/easee_controller.py` lines 422-479
- Risk: Start sequence fails with certain status combos; left in bad state
- Priority: High - critical feature

---

*Concerns audit: 2025-02-15*

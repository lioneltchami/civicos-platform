# Item 03 — SCH-01.1 Independent Verification

**Date:** 2026-08-20  
**Stage:** Two totally fresh independent closure reviews  
**Verdict:** **SCH-01.1 Still open — implementation discarded under the all-or-discard rule.**

## Joint independent conclusion

Both fresh reviews agreed that the candidate implementation added the minimum schedule marker, a forward migration, a locked generation check, explicit outcomes, atomic recipient/outbox creation, and exactly the six required `TransactionTestCase` method definitions without expanding into task integration, lifecycle fencing, recovery, adapters, projections, fakes, harnesses, staging, official-suite, submission, Item 02, or another Building Block.

However, one independent review found a closure-blocking contradiction: the pre-existing public `scheduler_runtime.materialize(...)` function remained directly callable with a caller-supplied generation, without locking `GovStackAlertSchedule` or validating its current `delivery_generation`. That exposed path could create recipient and outbox rows while bypassing the new admission authority. The SCH-01.1 change list requires materialization to become an internal subordinate primitive or otherwise be unable to bypass the locked authority. The candidate did not satisfy that requirement.

> A guarded new path is insufficient when the old production persistence primitive remains callable as a generation-bypass path.

## Evidence record

| Verification area | Result |
|---|---|
| Minimal `admitted_generation` / `admission_outcome` marker | Present in candidate source. |
| Forward-only migration after `0020_scheduler_outbox_publisher_claim` | Present in candidate source. |
| Locked expected-generation check in `admit_schedule_generation` | Present in candidate source. |
| Atomic recipient/outbox materialization and deterministic outcomes | Present for the new guarded path. |
| Six exact `TransactionTestCase` methods | Present in candidate source. |
| Local isolated validation | Candidate migration-drift check passed; six focused tests passed; existing Scheduler runtime regression reported five passing tests. |
| Single authoritative materialization path | **Failed.** Direct public `materialize(...)` remained a current-generation bypass. |
| Scope boundary | No prohibited new implementation surface identified. |

## All-or-discard disposition

The current user instruction permits retention only if the complete core admission service and all six tests pass **and** the increment is complete. Because the exposed bypass violates the single-authority requirement, passing candidate tests do not establish closure. The SCH-01.1 implementation commit is therefore reverted; no model marker, migration, runtime service, or test module remains in the working codebase.

No Stage 4 closure claim is made. The record itself is retained to preserve the independent finding and the exact prerequisite for a future attempt: make every materialization path enforce the locked current-generation admission authority before re-running the six real-database tests.

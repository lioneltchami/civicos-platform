# Item 03 — SCH-01.1 Retry Independent Verification

**Date:** 2026-08-21  
**Stage:** Two totally fresh independent closure reviews  
**Verdict:** **SCH-01.1 Closed.**

> The previous callable generation-bypass path is closed. `materialize(...)` remains only as a compatibility wrapper for the unchanged live task and delegates exclusively to locked `admit_schedule_generation(...)`; raw recipient/outbox creation is isolated beneath a private helper.

## Independent findings

| Closure criterion | Independent result | Evidence |
|---|---|---|
| Minimal marker and forward migration | Closed | `0021_sch01_1_core_admission` follows `0020_scheduler_outbox_publisher_claim` and adds only nullable `admitted_generation` and bounded `admission_outcome`. |
| Locked current-generation authority | Closed | `admit_schedule_generation(...)` opens one atomic transaction, locks `GovStackAlertSchedule`, checks expected generation against locked `delivery_generation` before row creation, and persists deterministic outcomes. |
| Callable bypass eliminated | Closed | Raw `SchedulerRecipientDelivery` / `SchedulerOutbox` creation occurs only in `_materialize_locked(...)`. Public `materialize(...)` has no direct persistence logic and delegates to the authority. |
| Task compatibility without task change | Closed | `apps/appointments/tasks.py` remains unchanged and continues to call the legacy-shape wrapper; the wrapper passes its generation only as `expected_generation`. |
| Exact six acceptance methods | Closed | `SchedulerAdmissionTransactionTests(TransactionTestCase)` contains exactly the six required method names and uses the authority for all admission cases. |
| Scope boundary | Closed | No task integration, lifecycle fencing, recovery, adapters, projections, fakes, harness, staging, official-suite, submission, Item 02, or other Building Block change was added. |

## Executed validation evidence

The independent archive reviews were source-level because their restricted package was not a runnable complete repository. Separately, the implementation was validated in the already-established isolated complete Django environment using a test-only secret value.

| Command / suite | Result |
|---|---|
| `makemigrations appointments --check --dry-run --settings=config.settings.test` | Passed; no model/migration drift. |
| `SchedulerAdmissionTransactionTests` | **6 passed** in 1.028 seconds. |
| Existing `test_scheduler_runtime` regression module | **5 passed** in 0.076 seconds. |
| Source call-graph inventory | Confirmed only the private helper contains raw recipient/outbox creation; the two remaining public `materialize` call sites are the unchanged task and existing regression fixture, both routed through the compatibility wrapper. |

## Required acceptance methods

1. `test_current_generation_materializes_one_recipient_and_one_outbox_per_recipient`
2. `test_duplicate_admission_converges_without_duplicate_rows`
3. `test_concurrent_current_generation_admission_is_single_winner`
4. `test_stale_generation_cannot_materialize_current_work`
5. `test_materialization_rollback_leaves_no_recipient_or_outbox_or_dispatched_claim`
6. `test_zero_recipient_admission_is_deterministic_and_rowless`

All six passed. The new implementation is therefore retained.

## Boundary statement

This closes **SCH-01.1 only**. It does not close the broader Scheduler Building Block, SCH-01, recovery, adapters, projections, topology-wired fakes, the 37-operation runtime harness, staging, official-suite validation, or submission. No conformance or certification claim is made.

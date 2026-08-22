# Item 03 — SCH-01.2c Delete-and-Re-arm Scope Confirmation

**Date:** 2026-08-21  
**Stage:** Two independent scope confirmations  
**Verdict:** **Confirmed as a future implementation scope; no SCH-01.2c code exists yet.**

> The supplied source is the closed SCH-01.2b Modify snapshot. It intentionally does not contain Delete-and-Re-arm implementation or its two acceptance tests. This record defines the narrowly permitted next increment.

## Delete contract

| Requirement | Scope-correct implementation result |
|---|---|
| Durable safety | Lock the parent schedule; fence non-terminal recipient work and unpublished outbox work before deletion; then perform the existing hard delete and database cascade. |
| Stale invocation | A later admission invocation cannot recreate work because the locked parent schedule no longer exists. The missing schedule is the durable fence, not broker behavior. |
| Broker revoke | Existing broker revoke remains best-effort cleanup only. Correctness must not depend on revocation succeeding. |

## Re-arm contract

| Requirement | Scope-correct implementation result |
|---|---|
| Distinct transition | Do not reuse incidental Modify behavior. Use a locked Re-arm primitive. |
| State change | Fence old non-terminal recipient/outbox work; advance `delivery_generation` once; set `delivery_admittable=True`; reset `admitted_generation` and `admission_outcome`. |
| Idempotency | Repeated Re-arm on the same state is a no-op/duplicate. |
| New admission | One new-generation admission succeeds through the existing SCH-01.1 authority; repeated admission remains duplicate/no-op. |

## Permitted files and tests

| File | Narrow SCH-01.2c purpose |
|---|---|
| `apps/appointments/services/scheduler_runtime.py` | Locked Delete and distinct Re-arm primitives only. |
| `apps/appointments/services/govstack_alert_schedule.py` | Route Delete through the locked primitive and expose Re-arm only. |
| `apps/appointments/tests/test_scheduler_lifecycle.py` | Exactly `test_delete_schedule_cannot_leave_recreatable_admission_work` and `test_rearm_advances_generation_and_admits_exactly_once`. |

No model or migration change is needed. Task integration, recovery, adapters, projections, fakes, harnesses, staging, official-suite execution, submission, Item 02, and later work are excluded.

# Item 03 — SCH-01.2a Independent Cancellation Verification

**Date:** 2026-08-21  
**Stage:** Two fresh independent closure reviews, followed by a complete evidence-package re-review  
**Verdict:** **SCH-01.2a Closed.**

> The original partial verification package was incomplete. A complete inventory-checked package was rebuilt and reviewed by two fresh independent reviewers; both confirmed closure at source level.

## Verified cancellation controls

| Requirement | Verified result |
|---|---|
| Durable schedule cancellation state | `delivery_admittable=True` is durable by default; locked cancellation sets it to `False`. |
| Durable outbox cancellation state | Nullable `SchedulerOutbox.cancelled_at` is added by the forward-only `0022_sch01_2a_cancel_only` migration. |
| Locked recipient fence | Cancellation locks the schedule first, cancels all non-terminal recipients, stamps cancellation time, and clears recipient lease state. |
| Locked unpublished-outbox fence | Cancellation stamps associated unpublished outbox rows and clears publisher token, owner, and lease state. Published outbox rows remain unchanged. |
| Admission marker reset | Cancellation resets `admitted_generation` and `admission_outcome` without advancing generation. |
| Rowless admission block | The locked SCH-01.1 authority returns `not_admittable`, current generation, zero created rows, and no deliveries before any child materialization. |
| Publisher-side exclusion | Cancelled rows are excluded from outbox claim, due, publish-success, and publish-failure paths. |
| Exact test | `test_cancel_schedule_fences_non_terminal_work_and_blocks_admission` is the only lifecycle test added in `SchedulerLifecycleTransactionTests(TransactionTestCase)`. |

## Runtime validation

| Validation | Result |
|---|---|
| Appointments migration-drift check | Passed; no pending model changes. |
| Required cancellation TransactionTestCase | **1 passed** in 0.186 seconds. |
| SCH-01.1 authoritative admission regression suite | **6 passed** in 1.192 seconds. |
| Fresh independent source reviews on complete package | Both **Closed**; package included all implementation, migration, test, task, settings, and scope/change-list files. |

## Scope audit

The implementation commit changes only the permitted model, migration, runtime, and single test files. It does not change task code and adds no modify, delete, re-arm, recovery, adapters, projections, fakes, harness, staging, official-suite, submission, Item 02, or later Scheduler behavior.

## Boundary statement

This closes **SCH-01.2a Cancel only**. It does not close SCH-01.2 overall or authorize work on SCH-01.2b (Modify) until that next increment is separately scoped and verified.

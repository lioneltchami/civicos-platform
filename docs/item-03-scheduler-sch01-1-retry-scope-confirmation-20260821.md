# Item 03 — SCH-01.1 Retry Scope Confirmation

**Date:** 2026-08-21  
**Stage:** Two independent bypass-closure scope reviews  
**Verdict:** **Confirmed.** The retry is limited to core admission plus elimination of the callable generation-bypass path.

> `admit_schedule_generation(...)` will be the only callable admission authority. The prior public `materialize(...)` entry point will not remain a row-creating public API; it will be replaced by a narrow private helper invoked only below the locked current-generation admission transaction.

## Required authority design

| Layer | Required responsibility | Prohibited behavior |
|---|---|---|
| Public `admit_schedule_generation(...)` | Open `transaction.atomic()`, lock `GovStackAlertSchedule` with `select_for_update()`, compare expected generation to locked `delivery_generation`, canonicalize recipients, invoke the internal helper, and persist the marker/outcome. | It must not perform transport, broker, HTTP, adapter, publisher, recovery, or lifecycle work. |
| Private row-creation helper | Accept the already-locked schedule and already-validated generation from the admission service, then converge deterministic recipient and outbox rows. | It must not accept caller-authoritative generation data, derive schedule authority, or be an importable admission API. |
| Schedule marker | Record only `admitted_generation` and bounded `admission_outcome`; `delivery_generation` remains authoritative. | No authority may be inferred from `dispatched`, `celery_task_id`, publisher state, recipient state, or outbox state. |

## Required outcomes and test boundary

The retry retains exactly the six mandatory real-database `TransactionTestCase` methods from the prior SCH-01.1 scope. Current generation, duplicate, concurrent, stale-generation, rollback, and zero-recipient cases must all prove results through the one locked authority.

## Explicit exclusions

Modify, cancel, delete, re-arm, task integration, recovery, adapters, projections, fakes, the 37-operation harness, staging, official suite, submission, Item 02, all other Building Blocks, broader lifecycle fencing, transport-I/O tests, and legacy-field behavior tests remain excluded.

No code was changed in this stage.

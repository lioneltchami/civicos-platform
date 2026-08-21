# Item 03 — Scheduler Increment 1 Plan

**Date:** 2026-08-20  
**Increment:** **SCH-01 — Authoritative Durable Schedule Admission**  
**Status:** Planned only; no code is implemented by this document.

> **Purpose.** SCH-01 closes the first Scheduler P0 gap: it makes one locked, generation-scoped database transition the sole authority for admitting alert-schedule work into durable recipient and outbox rows. It does **not** claim delivery completion, recovery completeness, official validation, or external readiness.

## Scope

SCH-01 covers the durable admission boundary from a due `GovStackAlertSchedule` through current-generation recipient materialization and durable outbox creation. It establishes the contract for duplicate calls, rollback, zero recipients, modification, cancellation, deletion, re-arm, and no-pre-commit I/O.

| In scope | Required result |
|---|---|
| Current-generation admission | Lock the schedule, validate that the requested generation is current and eligible, resolve deterministic recipients, and atomically create/converge recipient and outbox rows. |
| Single admission authority | The committed schedule-generation admission plus its durable child rows is the source of truth. `dispatched` and `celery_task_id` become compatibility metadata only. |
| Generation transition rules | Modification, cancellation, deletion, and re-arm fence old work before any new admission. A stale invocation cannot recreate current or old work. |
| Database atomicity | Fan-out and outbox creation either commit completely or leave no recipient row, outbox row, or successful-admission marker. |
| Post-commit handoff boundary | Broker, Celery publish, HTTP, adapter, and recipient transport work cannot occur before successful database commit. |
| Focused real-database evidence | Add the defined `TransactionTestCase` coverage for admission/lifecycle semantics only. |

## Exact implementation surface

| File | In-scope symbols and planned responsibility |
|---|---|
| `apps/appointments/models.py` | `GovStackAlertSchedule`, `SchedulerRecipientDelivery`, and `SchedulerOutbox`. Preserve existing schedule and recipient/outbox identities and uniqueness constraints. Add the smallest explicit schedule-level authoritative admission state necessary to distinguish **not admitted**, **admitted with recipients**, and **admitted with zero recipients** for a specific `delivery_generation`. The recommended additive fields are `admitted_generation` (nullable positive integer) and `admission_outcome` (bounded enum); final names may vary only if they preserve these semantics. |
| `apps/appointments/migrations/0021_scheduler_authoritative_admission.py` | One forward-only, migration-safe migration for the minimal admission marker/index/constraint. No adapter, recovery, status, telemetry, authorization, or unrelated schema is permitted. Existing schedules must receive a conservative non-authoritative legacy state; the migration must never infer a durable admission only from `dispatched=True`. |
| `apps/appointments/services/scheduler_runtime.py` | Add one narrowly named authoritative service, recommended as `admit_schedule_generation(...)`. It must run under the caller’s atomic transaction and locked schedule row; verify `expected_generation == delivery_generation`; converge duplicate materialization; create delivery/outbox rows atomically; commit the explicit zero-recipient outcome; and return a typed/domain result such as `created`, `duplicate`, `stale_generation`, `zero_recipients`, or `ineligible`. It must not publish or transport. Update `materialize` only as a subordinate primitive, not as a public stale-generation bypass. |
| `apps/appointments/tasks.py` | Refactor `dispatch_alert_schedule` into a caller of the authoritative admission service. The task must lock/reload the schedule, use its current generation, and not let `dispatched` or `celery_task_id` independently suppress or authorize admission. Any retained wake-up/publication scheduling occurs through `transaction.on_commit()` only and is not proof of admission. |
| `apps/appointments/services/govstack_alert_schedule.py` | Route create/modify/cancel/delete/re-arm transitions through one generation-transition contract. Delivery-content or target/event changes invalidate the old generation before a later admission. Cancellation and deletion prevent later admission. Re-arm advances the generation only for an eligible reschedule. Legacy Celery revoke stays best-effort cleanup. |
| `apps/appointments/tests/test_scheduler_admission.py` | New focused `TransactionTestCase` module for SCH-01. Existing view/task tests remain regression tests but do not substitute for real transaction evidence. |

## Authoritative transition contract

The service must have one logical transition:

```text
lock current schedule
  → reject missing, deleted, cancelled, or stale-generation invocation
  → resolve deterministic current recipients
  → converge existing current-generation durable work
  → create recipient rows and one outbox per recipient atomically
  → persist current-generation admission outcome, including zero recipients
commit transaction
  → only then register any existing compatibility wake-up through on_commit
```

The service must receive `expected_generation`, but cannot trust it blindly. After `select_for_update()`, the service compares it with `schedule.delivery_generation`; a mismatch produces a deterministic `stale_generation` outcome and **no durable child mutation**.

### Compatibility rule

`GovStackAlertSchedule.dispatched` is not an admission gate. During rollout, it may be updated only as a derived compatibility projection after authoritative durable admission commits, or remain readable as legacy metadata. It must never decide whether an otherwise valid current generation may materialize. A schedule with `dispatched=True` but no current-generation durable admission must still be admitted once; a schedule with `dispatched=False` but current-generation durable rows/marker must converge as duplicate.

`celery_task_id` is likewise a compatibility revocation/wake-up handle. It is not a generation fence, idempotency key, or durable-delivery result. Failure to revoke an old task cannot undermine the durable transition: any later invocation must fail the generation/eligibility gate before it can create current work.

### Lifecycle rules inside SCH-01

| Condition | Required result |
|---|---|
| First admission of current generation | Create exactly one `SchedulerRecipientDelivery` per resolved logical recipient and one `SchedulerOutbox` per delivery in the same transaction. Set the explicit admission marker/outcome for this generation. |
| Same-generation duplicate | Return `duplicate`/`already_admitted`; do not advance generation; create no additional deliveries or outboxes. Explicitly converge uniqueness conflicts rather than leaking an integrity error or treating it as a new admission. |
| Zero recipients | Persist `zero_recipients` for the current generation, with zero delivery and outbox rows. Repeat calls converge to the same outcome. The outcome cannot be inferred from `dispatched`. |
| Materialization exception | Roll back all changes: no delivery, no outbox, and no admission marker/outcome. `dispatched` must not assert a successful admission after rollback. |
| Delivery-content modification | Under schedule lock, invalidate/fence current non-terminal work, increment `delivery_generation` exactly once, and permit subsequent admission only for the new generation. Old work cannot become current work. |
| Cancellation | Under lock, make the current generation non-admittable and fence/cancel current non-terminal delivery/outbox work under the existing retention model. A later stale admission makes no rows. |
| Deletion | Fence/invalidate dependent work before model removal or apply the established cascade/retention policy consistently. Legacy task revocation is best-effort only. A stale task cannot recreate work after the schedule is gone. |
| Re-arm | A valid reschedule creates a new current generation. It admits exactly once; prior-generation work remains fenced and cannot be revived. |

## Acceptance criteria

SCH-01 can be committed only when every criterion below is met in the real database test configuration.

| ID | Acceptance criterion |
|---|---|
| SCH01-A1 | One explicit service is the only production authority for durable current-generation admission. Its caller locks the schedule before generation/eligibility checks. |
| SCH01-A2 | The service rejects a stale generation with no recipient, outbox, marker, compatibility-Boolean, or task-state mutation. |
| SCH01-A3 | First admission atomically creates exactly one current-generation recipient row per logical recipient and one linked outbox row per delivery. |
| SCH01-A4 | Duplicate and concurrent same-generation admissions converge to one durable row set and one admission outcome. |
| SCH01-A5 | A deterministic zero-recipient current-generation outcome is durable and idempotent without fabricating recipient/outbox rows. |
| SCH01-A6 | A forced fan-out failure rolls back every admission artifact and triggers no transport/wake-up call. |
| SCH01-A7 | `dispatched` and `celery_task_id` cannot independently authorize or suppress admission; legacy disagreement with durable state is covered by tests. |
| SCH01-A8 | Modification, cancellation, deletion, and re-arm apply a documented, locked generation-fence transition; stale work cannot rematerialize after any transition. |
| SCH01-A9 | No broker, Celery publish, HTTP, adapter, recipient transport, or external side effect occurs before commit. A post-commit wake-up failure does not erase the committed durable admission. |
| SCH01-A10 | Existing safe URL, timeout, redaction, recipient/outbox identity, and lease fields are not weakened or bypassed. |

## Required test methods

Create `apps/appointments/tests/test_scheduler_admission.py` with a `TransactionTestCase` class named `SchedulerAdmissionTransactionTests` and the following methods. They must exercise real database transactions and real admission/task code; transport boundaries may be spied on only to prove the no-pre-commit rule.

| Required test method | Required proof |
|---|---|
| `test_current_generation_materializes_one_recipient_and_one_outbox_per_recipient` | Current-generation fan-out has exactly one recipient and one outbox per logical recipient, all carrying the current generation and deterministic identity. |
| `test_duplicate_admission_converges_without_duplicate_rows` | Repeated same-generation admission is idempotent and does not advance generation. |
| `test_concurrent_current_generation_admission_is_single_winner` | Two competing real database admissions serialize/converge to a single durable result. |
| `test_stale_generation_cannot_materialize_current_work` | Stale input creates no current or stale rows and returns the documented domain outcome. |
| `test_materialization_rollback_leaves_no_recipient_or_outbox_or_dispatched_claim` | Injected fan-out failure leaves no orphan row or false legacy success indicator. |
| `test_zero_recipient_admission_is_deterministic_and_rowless` | Zero-recipient outcome is explicit, durable, rowless, and repeatable. |
| `test_modify_delivery_content_invalidates_old_generation_before_new_admission` | Admission-affecting change increments once, fences old work, and only new generation is admitted. |
| `test_cancel_schedule_fences_non_terminal_work_and_blocks_admission` | Cancellation blocks stale/later admission and handles non-terminal work under the retained history contract. |
| `test_delete_schedule_cannot_leave_recreatable_admission_work` | Deletion/cascade-or-retention policy prevents a later task from recreating schedule work; revoke is not relied on for correctness. |
| `test_rearm_advances_generation_and_admits_exactly_once` | Re-arm creates one new generation and one admission set, while prior work stays fenced. |
| `test_legacy_dispatched_and_celery_state_never_authorize_admission` | Boolean/task-id disagreement cannot create or suppress authoritative work. |
| `test_no_transport_io_before_admission_commit` | All transport/broker/Celery-publish calls are absent while the transaction is open and may only be scheduled post-commit. |
| `test_rolled_back_admission_does_not_publish_or_schedule_transport` | Rollback leaves no work and no scheduling/transport action. |

## Explicit exclusions

The following are intentionally excluded from SCH-01 and must not be changed merely to make these tests pass:

| Excluded area | Deferred increment or status |
|---|---|
| Publisher/recipient lease expiry, reclaim, crash windows, backoff, retry exhaustion, dead-letter, replay, stale completion | SCH-02 |
| Normalized channel/cross-BB adapter result contract | SCH-02/SCH-04 |
| Durable status projection, immutable lifecycle history, metrics, and persisted owner/tenant authorization redesign | SCH-03 |
| Payments/Consent fake topology and any external endpoint | SCH-04; local-only and disabled-by-default when planned |
| Complete internal request-level 37-operation harness | SCH-05 |
| Staging, official suite, credentials, deployment, testing-site action, certification/conformance claim, or submission | Out of scope |
| RB-03/RB-04 from Payments or any Payments implementation | Out of scope |

## References

This plan is grounded in the [Item 03 gap analysis](item-03-scheduler-gap-analysis-20260820.md), its [independent review](item-03-scheduler-gap-analysis-review-20260820.md), and the official [Scheduler description][1], [functional requirements][2], [service APIs][3], and [official `bb-scheduler` repository][4].

[1]: https://specs.govstack.global/scheduler/2-description
[2]: https://specs.govstack.global/scheduler/6-functional-requirements
[3]: https://specs.govstack.global/scheduler/8-service-apis
[4]: https://github.com/GovStackWorkingGroup/bb-scheduler

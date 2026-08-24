# Item 03 — SCH-01.2 Scope Confirmation

**Date:** 2026-08-21  
**Stage:** Two independent scope confirmations  
**Verdict:** **Confirmed with required precision corrections.**

> SCH-01.2 is strictly limited to locked, generation-fenced delivery lifecycle transitions: modify, cancel, delete, and re-arm. It must extend the existing admission authority only by the minimum lifecycle-eligibility guard needed to make cancellation non-admittable; it must not redesign SCH-01.1.

## Required transition boundary

| Transition | Required durable result |
|---|---|
| Delivery-affecting modify | Under the schedule lock, changes to `event_id`, `target_category`, `message_id`, or `alert_datetime` fence old non-terminal recipient/outbox work, advance `delivery_generation` exactly once, reset current admission markers, and permit only the new generation to be admitted. Metadata-only changes must not advance it. |
| Cancel | Under the schedule lock, the schedule becomes explicitly non-admittable, current non-terminal recipient work is cancelled, and associated unpublished outbox work is made non-publishable. A stale or later admission produces no recipient/outbox rows. |
| Delete | The policy is **hard delete with database cascade**: `SchedulerRecipientDelivery.schedule` and `SchedulerOutbox.delivery` already cascade. The service must lock and fence before deletion; broker revoke is best-effort cleanup, not the correctness fence. A later stale invocation must be unable to recreate work because the schedule row no longer exists. |
| Re-arm | A distinct re-arm transition advances generation exactly once, clears the cancellation/ineligibility state, resets admission markers, fences all old non-terminal work, and permits exactly one new-generation admission. |

## Required correction to existing state

The current model has `delivery_generation` but no durable non-admittable/cancelled schedule state. A generation bump alone cannot block a later invocation that presents the new current generation. SCH-01.2 must therefore add the smallest durable lifecycle eligibility marker needed for cancellation and make the existing admission authority reject an ineligible schedule before child-row creation. This is a narrow lifecycle guard, not a redesign of SCH-01.1 authority.

## Required tests

The focused real-database `TransactionTestCase` acceptance surface contains exactly:

1. `test_modify_delivery_content_invalidates_old_generation_before_new_admission`
2. `test_cancel_schedule_fences_non_terminal_work_and_blocks_admission`
3. `test_delete_schedule_cannot_leave_recreatable_admission_work`
4. `test_rearm_advances_generation_and_admits_exactly_once`

Each must use the real lifecycle and admission services, verify stale generation rowlessness, and prove recipient/outbox behavior rather than relying on Celery revoke or mocked sequential behavior.

## Explicit exclusions

Do not reopen the SCH-01.1 materialization authority design, modify task integration, add recovery or lease-reclaim logic, implement adapters, projections, fakes, the 37-operation harness, staging, official-suite execution, submission, Item 02, or later Scheduler increments.

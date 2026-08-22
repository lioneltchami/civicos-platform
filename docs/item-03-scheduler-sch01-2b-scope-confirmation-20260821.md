# Item 03 — SCH-01.2b Modify-Only Scope Confirmation

**Date:** 2026-08-21  
**Stage:** Two independent scope confirmations  
**Verdict:** **Confirmed — Modify only.**

> SCH-01.2b corrects the delivery-affecting Modify transition only. It must not use permanent cancellation semantics: after Modify, the schedule remains admissible for the new generation.

## Required behavior

| Scenario | Required durable result |
|---|---|
| Genuine delivery-affecting change | A changed `event_id`, `target_category`, `message_id`, or `alert_datetime` locks the schedule, fences all old non-terminal recipient work and unpublished outbox work, advances `delivery_generation` exactly once, clears `admitted_generation` and `admission_outcome`, and retains `delivery_admittable=True`. |
| Multiple changed delivery fields in one Modify call | One transition and exactly one generation advance. |
| Metadata-only or no-op Modify | No generation advance, no fencing, and no marker reset. |
| Stale old-generation admission | The existing SCH-01.1 authority returns stale-generation rowlessly and cannot create new recipient/outbox work. |
| New generation admission | One admission can materialize the new generation; repeated admission remains duplicate/no-op under SCH-01.1. |

## Required correction

The present Modify path uses cancellation behavior, which leaves the schedule non-admittable. SCH-01.2b must instead use a Modify-specific locked fencing primitive that cancels old non-terminal child work and fences old unpublished outbox rows **without** setting `delivery_admittable=False`.

## Permitted file set

| File | Permitted narrow change |
|---|---|
| `apps/appointments/services/govstack_alert_schedule.py` | Correct the locked delivery-affecting Modify path only. |
| `apps/appointments/services/scheduler_runtime.py` | Add only the locked reusable Modify-specific child fencing primitive required by that service. |
| `apps/appointments/tests/test_scheduler_lifecycle.py` | Add exactly `test_modify_delivery_content_invalidates_old_generation_before_new_admission`. |

No model or migration change is required. Delete, Re-arm, task changes, recovery, adapters, projections, fakes, harnesses, staging, official-suite work, submission, Item 02, and later work are excluded.

# Item 03 — Scheduler Dispatch PostgreSQL Lock Diagnosis

**Status: Diagnosed; no code changed in this stage.** Two independent reviewers examined the committed Scheduler dispatch path and reached the same conclusion: the PostgreSQL failure is caused by an unrestricted row lock on a `select_related()` query that includes the nullable `slot__resource` relation. The intended concurrency boundary is the single `GovStackAlertSchedule` row; no related row needs to be locked for authoritative admission.

## Exact failing query

`dispatch_alert_schedule()` enters a short `transaction.atomic()` block and retrieves the target schedule with the following query at `apps/appointments/tasks.py:513–518`:

```python
GovStackAlertSchedule.objects.select_for_update()
.select_related("slot", "slot__staff", "slot__resource", "message")
.get(pk=alert_schedule_pk)
```

The `slot__resource` relation is nullable. Django consequently represents that related lookup with an outer join. Unscoped `select_for_update()` asks PostgreSQL to lock the joined rows as well as the base schedule row, and PostgreSQL rejects locking the nullable side of an outer join with `FOR UPDATE cannot be applied to the nullable side of an outer join`. The failure occurs before durable admission begins.

| Aspect | Confirmed behavior | Boundary that must remain unchanged |
|---|---|---|
| Intended lock target | The one `GovStackAlertSchedule` selected by primary key | Lock it for the complete authoritative admission transaction |
| Nullable join | `slot__resource` in the eager snapshot query | Do not ask PostgreSQL to lock the optional resource row |
| Authoritative operation | `scheduler_runtime.admit_schedule_generation()` is called while the schedule is locked | Keep its generation, admissibility, and idempotency decision in the same transaction |
| Wake-up path | A publisher wake-up is registered only with `transaction.on_commit()` when admission creates work | Do not enqueue before commit or move broker I/O into the transaction |
| Recipient behavior | Subscriber, staff, and optional-resource recipient discovery occurs before admission | Preserve it unchanged; this increment is only a lock-query correction |

## Why the schedule row must stay locked

The current task documentation identifies `delivery_generation`, `delivery_admittable`, and durable admission markers as the sole admission authority. Inside the transaction, the task derives recipients, invokes `admit_schedule_generation()` with the current generation, and registers the post-commit publisher wake-up only when the durable admission outcome reports newly created work (`apps/appointments/tasks.py:519–547`). The schedule row lock must therefore continue to cover this entire sequence. The task returns only after the transaction has committed, and it still performs no direct recipient transport.

The prior failure record is confirmed: it is reproducible on the committed baseline and is not a consequence of the discarded SCH-02.1 publisher-recovery attempt. The current source shows that this affects the live SCH-01 admission task itself, so a correction must be both PostgreSQL-safe and minimal.

## Diagnosis conclusion

The smallest safe correction shape is to retain the current eager snapshot joins but scope the PostgreSQL lock to the base schedule relation with `select_for_update(of=("self",))`. That preserves a locked schedule row throughout authoritative admission without attempting to lock the nullable `slot__resource` join. A two-query implementation—locking the base schedule first and then loading related rows—is a possible fallback, but it is broader than necessary and risks subtly changing snapshot timing.

No Scheduler behavior has been altered in this stage. The next stage must independently review whether the scoped-lock expression is supported by the project’s Django/PostgreSQL combination and freeze the exact regression tests before implementation.

# Item 03 — Scheduler Dispatch PostgreSQL Lock Verification

**Independent conclusion: Resolved.** Two totally new blind reviewers independently examined the committed source, the Stage 1 diagnosis, the Stage 2 frozen fix plan, and the raw PostgreSQL evidence. Both concluded that the pre-existing live-dispatch PostgreSQL incompatibility is resolved by the minimal scoped-lock correction. SCH-01 remains preserved, and **SCH-02.1 may be retried in a new clean implementation cycle**.

> This conclusion is limited to the pre-existing dispatch lock defect. It is not a closure claim for SCH-02.1 publisher recovery, recipient recovery, crash-window proof, acknowledgement/history, projections, adapters, the operation harness, staging, an official suite, or submission.

## Verified implementation boundary

The committed correction in `0d65278` makes one production expression change in `apps/appointments/tasks.py`: `GovStackAlertSchedule.objects.select_for_update()` is now `GovStackAlertSchedule.objects.select_for_update(of=("self",))`. The existing eager snapshot relations, including nullable `slot__resource`, remain in place. The scoped `OF` target means PostgreSQL locks the selected schedule relation only, rather than attempting to lock the nullable side of the outer join.

The accompanying test change is limited to the existing live dispatch test. It sets `self.slot.resource_id = None` and saves that relationship immediately before invoking `dispatch_alert_schedule.run()`. This directly recreates the nullable relationship that previously caused PostgreSQL to reject the query while retaining the existing assertions for durable materialization, pending delivery state, durable outbox creation, and exactly one post-commit publisher wake-up.

| Verification condition | Independent result | Evidence |
|---|---|---|
| Only the target schedule row is locked | Pass | `select_for_update(of=("self",))` in the committed dispatch query. |
| Nullable joined resource is read but not lock-targeted | Pass | The focused fixture persists `resource_id=None`; PostgreSQL completes the task successfully. [1] |
| One transaction still covers recipient derivation and authoritative admission | Pass | `transaction.atomic()`, recipient derivation, and `admit_schedule_generation()` remain unchanged in `dispatch_alert_schedule()`. |
| Conditional post-commit wake-up remains intact | Pass | `transaction.on_commit(wake_outbox_after_commit)` is still registered only when admission creates work. |
| Focused PostgreSQL dispatch case passes | Pass | Five tests completed in 6.183 seconds with `OK`, including the formerly failing live-dispatch test. [1] |
| Bounded Scheduler runtime and SCH-01 lifecycle regressions pass | Pass | Nine tests completed in 9.211 seconds with `OK`, covering runtime behavior and Cancel, Modify, Delete, and Re-arm lifecycle cases. [2] |
| Publisher recovery and other excluded work were not introduced | Pass | The production and test diff is limited to the scoped-lock expression and the nullable-resource fixture. |

## Preservation assessment

The schedule lock still begins when the target schedule is fetched and remains held through recipient derivation and `scheduler_runtime.admit_schedule_generation()`. The publisher wake-up remains deferred through `transaction.on_commit()` and remains conditional on newly created durable work. No publisher I/O is moved into the transaction, and no changes were made to generation rules, admission markers, lifecycle fencing, recipient recovery, task retry/acknowledgement settings, or transport behavior.

Both independent reviewers noted that the evidence is appropriate for this narrowly bounded query correction. The evidence does not prove excluded work such as publisher crash windows, competing publishers, recipient recovery, or the full Scheduler operation surface; those must remain separate SCH-02 increments.

## Decision and next boundary

The **pre-existing PostgreSQL dispatch lock issue is resolved**. The next permissible work is a fresh, all-or-discard SCH-02.1 bounded publisher-recovery retry using the previously established requirements and a new implementation/evidence cycle. That retry must retain its ten PostgreSQL `TransactionTestCase` gate and its bounded Scheduler regression gate; it may not treat this lock-query correction as publisher-recovery evidence.

## References

[1]: evidence/item-03-scheduler-dispatch-lock-focused-postgres-20260822.log "Focused nullable-resource dispatch and SCH-01 lifecycle PostgreSQL output"
[2]: evidence/item-03-scheduler-dispatch-lock-regression-postgres-20260822.log "Bounded Scheduler runtime and lifecycle PostgreSQL regression output"

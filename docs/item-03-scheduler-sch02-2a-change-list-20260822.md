# Item 03 — SCH-02.2-A Exact Change List

| Order | File | Frozen change |
|---:|---|---|
| 1 | `apps/appointments/models.py` | Add immutable append-only `SchedulerPublisherAttemptEvent` linked to `SchedulerOutbox`, storing attempt, generation, phase/event, timestamp, bounded non-PII detail, owner, and token correlation. Index outbox/attempt/generation/time; prevent updates/deletes. |
| 2 | `apps/appointments/migrations/0024_sch02_2a_publisher_attempt_events.py` | Add only the new event table, constraints, and indexes. No legacy state rewrite or backfill. |
| 3 | `apps/appointments/services/scheduler_runtime.py` | Append durable events in existing claim/failure/finalization/reclaim seams. Record claim, pre-handoff, handoff returned, local/unknown failure, reclaim, successful finalization, and stale-finalization rejection. Events are observational, not transition authority. |
| 4 | `apps/appointments/scheduler_tasks.py` | Add default-disabled deterministic publisher-boundary hooks at `pre_handoff` and `post_handoff_pre_finalization`; record events before each hook. Hook crashes propagate and never infer publication. |
| 5 | `apps/appointments/tests/test_scheduler_runtime.py` | Add PostgreSQL TransactionTestCase proof for pre-handoff crash/reclaim, post-handoff/pre-finalization crash/reclaim/stale fence, unknown handoff, event append-only behavior, and concurrent stale finalization. |

## Required exact test methods

1. `test_pre_handoff_crash_leaves_claimed_and_records_pre_handoff_phase`
2. `test_post_handoff_pre_finalization_crash_leaves_unknown_outcome_and_records_post_handoff_phase`
3. `test_unknown_handoff_records_distinct_attempt_event`
4. `test_expired_claim_is_reclaimed_with_new_attempt_and_event`
5. `test_stale_finalization_records_event_and_cannot_publish`

The retained implementation must pass a fresh PostgreSQL migration graph, migration drift check, the five methods above, SCH-02.1’s ten publisher tests, and bounded SCH-01/runtime regressions. Preserve `PUBLISHED` as current fenced local enqueue finalization only; do not alter recipient work, adapters, exactly-once semantics, or SCH-01.

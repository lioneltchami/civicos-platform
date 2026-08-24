# Item 03 — SCH-02.2-A1 Exact Change List

| File | Frozen change |
|---|---|
| `apps/appointments/models.py` | Add immutable `SchedulerPublisherAttemptEvent`, linked to `SchedulerOutbox`, with attempt, generation, event type, occurred time, bounded detail, owner/token correlation, a chronological index, and a database uniqueness identity. The outbox remains transition authority. |
| `apps/appointments/migrations/0024_sch02_2a1_publisher_attempt_events.py` | Add only the event table, constraint, and index; no backfill or prior migration edits. |
| `apps/appointments/services/scheduler_runtime.py` | Add transactional append helper and write events only after successful current-owner claim, failure, publication, and expiry reclaim transitions. Rejected stale transitions append nothing. |
| `apps/appointments/tests/test_scheduler_runtime.py` | Add PostgreSQL-focused tests for immutable/unique events and events on claim, local failure, unknown handoff, publication, reclaim, and stale rejection. |

Use a unique identity of `(outbox, publisher_generation, attempt_number, event_type)` so one publisher attempt can record multiple distinct outcomes while duplicate appends are prevented. No crash hooks/window tests, recipient work, adapters, or SCH-01 changes are permitted.

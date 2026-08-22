# Item 03 — SCH-02.1 Exact Change List: Bounded Publisher Recovery

**Date:** 2026-08-21  
**Status:** Stage 2 change list only; no implementation is included.  
**Scope:** Publisher recovery/state contract and a focused reproducible PostgreSQL transaction-test gate.

## Implementation boundary

SCH-02.1 must add a durable publisher state machine without changing recipient behavior, SCH-01 admission/lifecycle behavior, publisher crash-window proof, acknowledgement/history, projections, adapters, fakes, harnesses, staging, official-suite work, or submission. The state contract must distinguish `PENDING`, `CLAIMED`, `LOCAL_FAILURE`, `UNKNOWN_HANDOFF`, `PUBLISHED`, `EXHAUSTED`, and the existing cancellation fence.

The key correction from the scope reviews is that the existing default test settings use in-memory SQLite despite a PostgreSQL comment. SCH-02.1 needs a **separate, reproducible PostgreSQL profile** for its transaction/locking/migration gate. That preserves the project’s fast SQLite unit profile and avoids an unrelated test-harness rewrite.

## Exact file-level changes

| File | Required change | Scope safeguard |
|---|---|---|
| `apps/appointments/models.py` | Extend `SchedulerOutbox` with the minimum explicit publisher state/classification and timestamp fields for bounded publisher recovery. Preserve `publish_attempts`, `available_at`, `published_at`, publisher generation/token/owner/lease, `cancelled_at`, error context, and stable delivery correlation/idempotency identity. Add only claim-state indexes/constraints needed to exclude terminal/cancelled/published work efficiently. | Do not alter `SchedulerRecipientDelivery`, acknowledgement, schedule, or SCH-01 fields. |
| `apps/appointments/migrations/0023_sch02_1_bounded_publisher_recovery.py` | Add one additive migration with safe defaults/backfill. Existing unpublished, non-cancelled rows become safe recoverable publisher intent; published rows remain published; cancelled rows stay final; attempts, availability, leases, generation/token/owner, errors, and stable correlation are preserved. | Do not edit prior migrations or mutate recipient/SCH-01 state. |
| `apps/appointments/services/scheduler_runtime.py` | Make publisher due/claim paths state-aware and implement finite, classified retry/backoff/attempt-budget behavior. Add atomic exhaustion with active lease clearing, guarded replay for eligible exhausted rows, and current token/generation fencing for both success and failure. Preserve distinction between known local failure and unknown handoff. | Do not modify recipient claim/fail/replay/reap/acknowledgement semantics. Do not implement SCH-02.2 crash-window/reclaim proof. |
| `apps/appointments/scheduler_tasks.py` | Map known local enqueue failures and indeterminate handoff paths to the constrained runtime publisher contract; retain correlation and do not turn unknown handoff into either local failure or publication. | No adapter redesign or external exactly-once claim. |
| `apps/appointments/tests/test_scheduler_runtime.py` | Add the focused PostgreSQL-backed `TransactionTestCase` publisher recovery methods below, with committed row reloads and deterministic `now`/policy inputs. | Do not add recipient, harness, or official-suite coverage. |
| `config/settings/sch02_postgres.py` | Add a narrowly scoped test settings module that inherits the existing test-safe settings but overrides the SQLite `DATABASES` configuration with `env.db("DATABASE_URL", default="postgres://civicos:civicos@localhost:5432/civicos")`. Retain eager Celery, test keys, test media, and other test-only safeguards. Migrations must remain enabled for this profile. | Do not replace or broaden the existing generic SQLite test profile. |
| `config/settings/base.py` | Add only finite publisher policy defaults required by the runtime: maximum publish attempts, base retry delay, maximum retry delay, and any tightly scoped publisher lease/configuration names needed to evaluate the bounded policy. | No recipient/global SCH-01/transport policy changes. |

## Focused required test methods

| Test method | Required durable proof |
|---|---|
| `test_claim_outbox_consumes_attempt_and_sets_publisher_lease` | A single active publisher claim persists attempt, token, generation, owner, and expiry; a second current claim cannot win. |
| `test_local_enqueue_failure_is_classified_backed_off_and_reclaimable` | Known local failure is distinct, clears active lease, preserves correlation, gets a future `available_at`, and is reclaimable only after policy availability. |
| `test_unknown_handoff_is_distinct_and_preserves_correlation` | Unknown handoff is distinct from local failure and publication; stable identity survives later policy-allowed recovery and no external outcome is falsely claimed. |
| `test_retry_policy_is_bounded_and_not_an_unbounded_hot_loop` | Every retryable failure has bounded deterministic future timing and monotonically consumed attempts. |
| `test_attempt_budget_transitions_to_terminal_publisher_failure` | The final permitted failure atomically produces durable `EXHAUSTED`, clears lease data, preserves reason/context, and excludes ordinary due/claim paths. |
| `test_guarded_replay_reopens_terminal_row_without_losing_correlation` | Only a valid guarded replay reopens an eligible exhausted row, clears stale publisher ownership, preserves stable correlation, and follows documented accounting. |
| `test_replay_rejects_published_and_cancelled_rows` | Published and cancelled rows cannot regress, gain a new lease, or alter recipients. |
| `test_published_and_failure_require_current_publisher_token_and_generation` | Wrong/stale token or generation cannot publish/fail a reclaimed or superseded row. |
| `test_existing_outbox_rows_migrate_to_safe_publisher_states` | Migration preserves old unpublished/published/cancelled intent, attempts, correlation, and relevant lease/error facts. |
| `test_real_competing_publishers_have_one_current_owner` | Separate PostgreSQL transactions demonstrate one current owner and stale finalization rejection. This is not the later full crash-window proof. |

## Reproducible PostgreSQL gate

The profile must run against the existing `db` service declared in `docker-compose.yml` and must not silently fall back to SQLite. The acceptance procedure must run migrations and invoke the focused module through Django’s test runner:

```bash
docker compose up -d db
DATABASE_URL=postgres://civicos:civicos@localhost:5432/civicos \
DJANGO_SETTINGS_MODULE=config.settings.sch02_postgres \
python manage.py test apps.appointments.tests.test_scheduler_runtime
```

The implementation must record the actual executed command, dependency/environment identity, migration status, and raw test result before it can be retained. The generic `config.settings.test` SQLite profile remains available for unrelated fast tests but is not SCH-02.1 closure evidence.

## Non-negotiable exclusions

SCH-02.1 excludes recipient changes; publisher before/after-handoff crash-window and reclaim proof; acknowledgement/history; SCH-01 modifications; status projections/read models; adapters/transports; fakes; the 37-operation harness; staging; official suite; deployment; submission; and external exactly-once claims. `UNKNOWN_HANDOFF` is a persisted at-least-once ambiguity, not proof of local or external failure/success.

## Ready-to-implement condition

Implementation may begin only if it can complete every listed file boundary, provision the PostgreSQL test gate, execute migrations, and pass all ten named publisher tests. If any transition, migration requirement, test, or PostgreSQL execution gate is incomplete, all code changes must be discarded.

# Item 03 — SCH-02.1 Retry 2 Exact Change List

**Status: Frozen for one all-or-discard implementation attempt.** This list supersedes the discarded retry candidate only as an implementation plan. It retains the verified PostgreSQL dispatch correction and changes no SCH-01 behavior.

> **Implementation decision.** Use the existing `available_at`, `publish_attempts`, `publisher_generation`, `publisher_token`, `publisher_owner`, `publisher_lease_expires_at`, `published_at`, `cancelled_at`, and `last_error` fields. Add only explicit publisher state/classification/timing fields needed to make recovery bounded and auditable; do not invent a second retry or correlation model.

## Ordered files and exact responsibilities

| Order | File | Exact change |
|---:|---|---|
| 1 | `apps/appointments/models.py` | Extend `SchedulerOutbox` with seven durable state constants and choices: `PENDING`, `CLAIMED`, `LOCAL_FAILURE`, `UNKNOWN_HANDOFF`, `PUBLISHED`, `EXHAUSTED`, and `CANCELLED`. Add `publisher_state` (indexed, default `PENDING`), `publisher_failure_class`, `publisher_state_changed_at`, `exhausted_at`, and a `(publisher_state, available_at)` index. Reuse all existing attempt, owner, token, generation, lease, error, availability, publication, cancellation, delivery idempotency, and correlation data. |
| 2 | `apps/appointments/migrations/0023_sch02_1_bounded_publisher_recovery.py` | Add those fields and index in one additive migration depending on `0022_sch01_2a_cancel_only`. Backfill deterministically with finality precedence: `cancelled_at IS NOT NULL → CANCELLED`; otherwise `published_at IS NOT NULL → PUBLISHED`; otherwise non-null `publisher_token → CLAIMED`; otherwise `PENDING`. Preserve every existing field. Do not edit prior Appointments or Payments migrations. |
| 3 | `config/settings/base.py` | Add only three finite, overrideable policy defaults: `GOVSTACK_SCHEDULER_PUBLISH_MAX_ATTEMPTS=3`, `GOVSTACK_SCHEDULER_PUBLISH_RETRY_BASE_SECONDS=30`, and `GOVSTACK_SCHEDULER_PUBLISH_RETRY_MAX_SECONDS=900`. |
| 4 | `config/settings/sch02_postgres.py` | Add the narrow PostgreSQL-only SCH-02.1 test profile derived from `config.settings.test`, replacing its database with PostgreSQL from the Compose environment, retaining migrations, and containing no SQLite fallback. Keep tests eager and isolated. |
| 5 | `apps/appointments/services/scheduler_runtime.py` | Implement the publisher state machine using the existing fields. `claim_outbox()` may claim a due `PENDING`, `LOCAL_FAILURE`, or `UNKNOWN_HANDOFF` row, and may take over an expired `CLAIMED` row; it must atomically increment attempts/generation and set current token/owner/lease/state. `mark_outbox_published()` must accept only the current `CLAIMED` token and generation and persist `PUBLISHED` while clearing the lease. `mark_outbox_failed()` must distinguish known local failure from unknown handoff, persist deterministic exponential capped backoff in `available_at`, and exhaust the row at the finite budget while clearing the lease. Add `mark_outbox_unknown_handoff()`. `due_outbox()` must exclude `EXHAUSTED`, `PUBLISHED`, and `CANCELLED`. Add `replay_outbox()` that reopens only `EXHAUSTED` unpublished, uncancelled rows, preserves delivery correlation/idempotency, clears ownership, and resets attempt scheduling; it must reject published/cancelled rows. Preserve every current token-and-generation fence. Update the existing SCH-01 child-work fence so unpublished outbox work is durably marked `CANCELLED` with cleared publisher ownership. |
| 6 | `apps/appointments/scheduler_tasks.py` | Retain current claim/I/O/finalization ordering. Route `TimeoutError` from broker enqueue to `mark_outbox_unknown_handoff()` because the handoff is indeterminate; route other enqueue exceptions to `mark_outbox_failed()` as known local failure. Do not assert external exactly-once delivery. |
| 7 | `apps/appointments/tests/test_scheduler_runtime.py` | Add `SchedulerPublisherRecoveryPostgresTests(TransactionTestCase)` with the ten exact test methods below. It must skip rather than claim evidence if the backend is not PostgreSQL. Use real committed database writes and separate PostgreSQL connections for the competing-publisher test. |

## Required durable transition contract

| Starting state | Event | Result | Mandatory durable guard |
|---|---|---|---|
| `PENDING`, due `LOCAL_FAILURE`, due `UNKNOWN_HANDOFF`, or expired `CLAIMED` | Claim | `CLAIMED` | Lock row, ensure nonterminal/due eligibility, increment attempt and generation, issue current token/owner/lease. |
| Current `CLAIMED` | Known local enqueue failure before confirmed handoff | `LOCAL_FAILURE` or `EXHAUSTED` | Token + generation fence; persist failure class and future `available_at`, or exhaustion at budget. |
| Current `CLAIMED` | Timeout/indeterminate broker handoff | `UNKNOWN_HANDOFF` or `EXHAUSTED` | Token + generation fence; retain at-least-once ambiguity and correlation; never infer publication. |
| Current `CLAIMED` | Confirmed local publish completion | `PUBLISHED` | Token + generation fence; clear ownership and persist `published_at`. |
| `EXHAUSTED` | Explicit guarded replay | `PENDING` | Only unpublished, uncancelled exhausted rows; preserve correlation/idempotency; clear ownership and reset scheduling. |
| Any unpublished row fenced by SCH-01 lifecycle cancellation | `CANCELLED` | Terminal | Set publisher state and clear ownership; never due, claimable, or replayable. |

## Exact required PostgreSQL test names

1. `test_claim_outbox_consumes_attempt_and_sets_publisher_lease`
2. `test_local_enqueue_failure_is_classified_backed_off_and_reclaimable`
3. `test_unknown_handoff_is_distinct_and_preserves_correlation`
4. `test_retry_policy_is_bounded_and_not_an_unbounded_hot_loop`
5. `test_attempt_budget_transitions_to_terminal_publisher_failure`
6. `test_guarded_replay_reopens_terminal_row_without_losing_correlation`
7. `test_replay_rejects_published_and_cancelled_rows`
8. `test_published_and_failure_require_current_publisher_token_and_generation`
9. `test_existing_outbox_rows_migrate_to_safe_publisher_states`
10. `test_real_competing_publishers_have_one_current_owner`

The tenth method must use real concurrent PostgreSQL connections/transactions and prove exactly one current owner plus stale finalization rejection. The migration test must execute the `0023` backfill logic against representative legacy published, cancelled, active-token, and ordinary rows.

## Evidence gate

Use `DJANGO_SETTINGS_MODULE=config.settings.sch02_postgres` with Compose `db` (`postgres:16-alpine`) and disposable `civicos_sch02` / `civicos_sch02_test` databases. The retained implementation must pass a fresh full migration graph, `makemigrations --check --dry-run`, the exact ten-method publisher class, and the bounded PostgreSQL regression comprising `SchedulerRuntimeTests` and `SchedulerLifecycleTransactionTests`. Preserve commands and raw output.

## Explicit exclusions

Do not change the verified `select_for_update(of=("self",))` dispatch query or its nullable-resource test fixture. Do not modify recipient delivery/recovery, SCH-02.2 crash-window proof, acknowledgement/history, projections, adapters/transports redesign, fakes, the 37-operation harness, staging/deployment/official tests/submission, Payments, or any other SCH-01 behavior. A failed requirement or regression requires restoring every SCH-02.1 candidate source/test/migration/settings file and recording a rejection; no partial implementation is retainable.

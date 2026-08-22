# Item 03 — SCH-02.1 Retry Change List: Bounded Publisher Recovery

**Date:** 2026-08-21  
**Status:** Fresh blind-review reconciliation; ready for clean all-or-discard implementation attempt.

## Ordered implementation boundary

| Order | File | Required change |
|---:|---|---|
| 1 | `apps/appointments/models.py` | Add only the durable publisher state/classification and timing fields necessary to distinguish `PENDING`, `CLAIMED`, `LOCAL_FAILURE`, `UNKNOWN_HANDOFF`, `PUBLISHED`, `EXHAUSTED`, and `CANCELLED`, while preserving existing correlation, generation, token, ownership, availability, attempt, error, and cancellation data. |
| 2 | `apps/appointments/migrations/0023_sch02_1_bounded_publisher_recovery.py` | Add one backward-safe additive migration. Backfill unpublished non-cancelled intent as recoverable, retain published/cancelled finality, and preserve correlation, attempts, availability, leases, generation/token/owner, and errors. Do not edit prior migrations. |
| 3 | `apps/appointments/services/scheduler_runtime.py` | Make due/claim/finalization state-aware; apply finite attempt budget and deterministic persisted backoff; classify local versus unknown handoff; atomically exhaust and clear leases; add guarded replay; and preserve token/generation fences for publication and failure. |
| 4 | `apps/appointments/scheduler_tasks.py` | Map known local enqueue failures and indeterminate broker handoff into the runtime contract without treating unknown handoff as local failure or confirmed publication. |
| 5 | `config/settings/base.py` | Add only finite publisher-policy defaults: attempt budget, base delay, maximum delay, and any narrow publisher lease setting required by the state machine. |
| 6 | `config/settings/sch02_postgres.py` | Add the narrow PostgreSQL test settings profile with migrations enabled and no SQLite fallback. Preserve all existing test-safe settings. |
| 7 | `apps/appointments/tests/test_scheduler_runtime.py` | Add the ten required PostgreSQL `TransactionTestCase` methods below using committed reloads, deterministic time/policy inputs, and real competing transactions. |

## Required PostgreSQL test methods

```text
test_claim_outbox_consumes_attempt_and_sets_publisher_lease
test_local_enqueue_failure_is_classified_backed_off_and_reclaimable
test_unknown_handoff_is_distinct_and_preserves_correlation
test_retry_policy_is_bounded_and_not_an_unbounded_hot_loop
test_attempt_budget_transitions_to_terminal_publisher_failure
test_guarded_replay_reopens_terminal_row_without_losing_correlation
test_replay_rejects_published_and_cancelled_rows
test_published_and_failure_require_current_publisher_token_and_generation
test_existing_outbox_rows_migrate_to_safe_publisher_states
test_real_competing_publishers_have_one_current_owner
```

The evidence gate must use `config.settings.sch02_postgres`, run the full fresh PostgreSQL migration graph with migrations enabled, and preserve the exact command, environment/dependency identity, migration output, and raw ten-method output. SQLite is not valid closure evidence.

## Non-negotiable exclusions

This retry does not modify recipient delivery/recovery, acknowledgement or transaction history, SCH-01 admission/lifecycle, publisher crash-window proof reserved for SCH-02.2, projections, adapters/transports, fakes, the 37-operation harness, staging, deployment, official-suite validation, submission, external exactly-once claims, or Payments behavior/migrations.

> `UNKNOWN_HANDOFF` is an at-least-once ambiguity. Local database fencing prevents stale local finalization; it cannot retract an externally accepted handoff.

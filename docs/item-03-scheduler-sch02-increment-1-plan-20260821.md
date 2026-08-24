# Item 03 — SCH-02.1 Plan: Bounded Publisher Recovery and State Contract

**Date:** 2026-08-21  
**Increment:** SCH-02.1 — Bounded Publisher Recovery and State Contract  
**Status:** Planning only; no implementation is included in this record.

## Objective and strict boundary

SCH-02.1 is the first narrow recovery increment. It replaces the current publisher path’s immediate unbounded re-availability after enqueue failure with a **durable, bounded, token/generation-fenced publisher recovery policy**. It preserves the transactional outbox, stable recipient correlation, post-claim I/O separation, and SCH-01 authority/lifecycle work already closed.

The increment must define the minimum publisher vocabulary needed to distinguish a failure known to be local from an external handoff whose result is unresolved. It must not claim that local fencing can guarantee external exactly-once delivery. The appropriate contract is **durable at-least-once recovery with stable correlation**: an unknown broker handoff can be retried later and may create an external duplicate.

The official Scheduler requirements call for configured retries before communication failure is recorded and for durable logging/correlation; they leave the retry algorithm implementation-specific.[1] [2]

## Exact scope

| In scope | Required SCH-02.1 behavior |
|---|---|
| Publisher vocabulary | Persist explicit publisher states/classifications sufficient to distinguish `PENDING`, `CLAIMED`, `LOCAL_FAILURE`, `UNKNOWN_HANDOFF`, `PUBLISHED`, `EXHAUSTED`, and the existing cancellation fence. The exact field names may follow project conventions. |
| Bounded failure policy | Define finite publisher attempt budget, deterministic configured backoff, future `available_at` for retryable failure, and a terminal exhausted publisher state. No failure path may re-expose a row immediately by default. |
| Local versus unknown handoff | Persist the distinction between a known local enqueue failure and an indeterminate handoff. Neither is `PUBLISHED`; unknown handoff must not be represented as proof of non-delivery. |
| Terminal exhaustion | Once budget is consumed, exclude the row from ordinary due polling and claims, clear active publisher lease ownership, and preserve terminal classification/error/attempt context. |
| Guarded replay | Permit a deliberate locked replay only for eligible exhausted publisher rows. It must clear stale lease data, preserve stable delivery/idempotency correlation, set policy-consistent availability, and reject published/cancelled rows. |
| Existing fencing | Preserve publisher token/generation requirements on both publication and failure completion. A stale publisher must change nothing after reclaim/supersession. |
| Migration and configuration | Add an additive, backward-safe migration and only publisher-specific finite configuration/settings needed for deterministic policy testing. |
| Focused evidence | Add real-database `TransactionTestCase` proof for the publisher state machine and its bounded recovery behavior. |

## Durable contract vocabulary

| State or contract term | Meaning | Ordinary claim eligibility |
|---|---|---:|
| `PENDING` | Durable publisher intent that is due and unclaimed. | Yes, when due and not cancelled. |
| `CLAIMED` | Current publisher owns the attempt through active token, generation, owner, and lease. | No. |
| `LOCAL_FAILURE` | Definite local/broker enqueue failure with no evidence of external acceptance. | Only after policy backoff, within budget. |
| `UNKNOWN_HANDOFF` | External acceptance cannot be determined; may represent an at-least-once duplicate risk on later recovery. | Only as defined by policy; never conflated with local failure or publication. |
| `PUBLISHED` | Matching active token/generation durably confirmed local handoff. | No. |
| `EXHAUSTED` | Finite publisher budget has been consumed without confirmed publication. | No; guarded replay required. |
| `CANCELLED` | Existing lifecycle fence remains authoritative. | No. |

`PUBLISHED` requires its durable publication marker. `EXHAUSTED` is never published. `CLAIMED` requires active fenced lease information. Cancellation remains terminal and cannot be bypassed by failure or replay.

## Acceptance criteria

1. Publisher failures have a finite, configured attempt budget and deterministic persisted retry timing; no failure result makes a row immediately due unless an explicit policy setting safely requires that and the test proves it.
2. A publisher completion can record `LOCAL_FAILURE` or `UNKNOWN_HANDOFF` distinctly, and neither outcome is falsely marked published.
3. Maximum-attempt exhaustion is atomic, durable, lease-clearing, and excluded from ordinary due/claim paths.
4. A guarded replay reopens only an eligible exhausted row, preserves stable recipient correlation, and does not silently erase attempt history.
5. Published and cancelled rows cannot be replayed or otherwise returned to ordinary publisher claim paths.
6. Publisher success and failure finalization require the current token and generation; stale finalization returns false and changes no durable state.
7. Existing publisher rows migrate safely: unpublished intent remains recoverable, published rows remain published, cancelled rows remain fenced, and attempts/correlation/lease context are preserved.
8. The implementation does not alter recipient claim/fail/replay/reap/acknowledgement semantics, SCH-01 behavior, lifecycle transitions, projections, adapters, fakes, harnesses, staging, official-suite, or submission code.

## Required focused transaction tests

All tests must use the configured real Django test database and committed row reloads. They belong in `apps/appointments/tests/test_scheduler_runtime.py`, or a narrowly named companion runtime test module if repository conventions require one.

| Test method | Required proof |
|---|---|
| `test_claim_outbox_consumes_attempt_and_sets_publisher_lease` | One active durable claim; persisted attempt, token, generation, owner, and expiry; no second active claim. |
| `test_local_enqueue_failure_is_classified_backed_off_and_reclaimable` | Local failure has a future retry time, correct classification, cleared lease, preserved correlation, and becomes due only under the policy. |
| `test_unknown_handoff_is_distinct_and_preserves_correlation` | Unknown handoff is durably distinct from local failure and publication; stable correlation/idempotency context survives allowed future recovery. |
| `test_retry_policy_is_bounded_and_not_an_unbounded_hot_loop` | Repeated failures use deterministic bounded policy and never become immediately due by accident. |
| `test_attempt_budget_transitions_to_terminal_publisher_failure` | Final allowed attempt produces durable exhaustion, clears lease, retains reason, and is absent from ordinary due/claim selection. |
| `test_guarded_replay_reopens_terminal_row_without_losing_correlation` | Only guarded replay reopens exhaustion, preserves correlation, clears stale lease data, and follows documented attempt accounting. |
| `test_replay_rejects_published_and_cancelled_rows` | Published/cancelled rows do not regress, acquire a lease, or mutate recipients. |
| `test_published_and_failure_require_current_publisher_token_and_generation` | Wrong/stale token/generation cannot publish or fail a row after reclaim/supersession. |
| `test_existing_outbox_rows_migrate_to_safe_publisher_states` | Migration preserves prior unpublished, published, and cancelled state/correlation/attempt semantics. |
| `test_real_competing_publishers_have_one_current_owner` | Separate real transactions prove one current owner and stale finalization rejection. |

## Exact implementation boundary

| File | Intended change | Explicit boundary |
|---|---|---|
| `apps/appointments/models.py` | Add minimum `SchedulerOutbox` publisher state/classification/timestamp fields and efficient claim-state indexes/constraints. | Do not change recipient delivery, acknowledgement, schedule, or SCH-01 fields. |
| `apps/appointments/migrations/0023_sch02_1_bounded_publisher_recovery.py` | Additive schema/default/backfill mapping for safe publisher states. | Do not edit historical migrations or change recipient/SCH-01 state. |
| `apps/appointments/services/scheduler_runtime.py` | State-aware publisher claim, bounded failure transition, exhaustion, guarded replay, and stale token/generation fencing. | Do not modify recipient runtime semantics or implement SCH-02.2 crash-window proof. |
| `apps/appointments/scheduler_tasks.py` | Map known local failure and indeterminate handoff paths to the constrained runtime contract. | Do not redesign adapters/transport or claim external exactly-once delivery. |
| `apps/appointments/tests/test_scheduler_runtime.py` | Add focused real-database publisher state-machine tests listed above. | Do not touch the 37-operation harness or official suite. |
| `config/settings/base.py`, `config/settings/test.py` | Only finite publisher policy defaults and deterministic test overrides, if required. | No recipient or global SCH-01 configuration changes. |

## Migration requirements

Existing unpublished and non-cancelled outbox rows must become safe recoverable publisher intent, not be inferred as published or terminal. Published rows remain published and cancelled rows remain excluded. Existing `publish_attempts`, `available_at`, publisher lease/generation/token/owner, error data, and the stable delivery idempotency/correlation identity must survive the migration. Any state constraint must be safe for legacy null and in-flight values during rollout.

## Explicit exclusions

SCH-02.1 excludes publisher crash-window/reclaim proof beyond preserving existing fencing; full competing-publisher crash proof is SCH-02.2. It excludes all recipient outcome/lease/acknowledgement changes, recovery history, read models/projections, SCH-01 admission/lifecycle work, adapters, fakes, the 37-operation harness, staging, official-suite execution, submission, and external exactly-once claims.

## Definition of done

SCH-02.1 is ready to close only if its migration, model, runtime, task mapping, configuration, and focused real-database tests agree on one finite publisher recovery contract; retryable work is delayed; exhaustion is durable; replay is guarded; local failure and unknown handoff are distinct; stale completion is fenced; cancellation remains final; and every required test passes.

## References

[1]: [GovStack Scheduler Specification — Description](https://specs.govstack.global/scheduler/2-description), §2.1.5.

[2]: [GovStack Scheduler Specification — Key Digital Functionalities](https://specs.govstack.global/scheduler/4-key-digital-functionalities), §§4.3, 4.9, and 4.12.

[3]: [GovStackWorkingGroup/bb-scheduler](https://github.com/GovStackWorkingGroup/bb-scheduler).

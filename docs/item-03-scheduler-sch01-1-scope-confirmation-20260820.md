# Item 03 — SCH-01.1 Scope Confirmation

**Date:** 2026-08-20  
**Stage:** Two independent scope confirmations  
**Verdict:** **Confirmed with a required narrowing correction.**

> SCH-01.1 is limited to the minimum durable current-generation admission marker and forward migration, one locked authoritative admission service, atomic recipient/outbox materialization, and exactly six named real-database `TransactionTestCase` methods. No lifecycle fencing, task integration, or transport concern is implicit in this increment.

## Included boundary

| Concern | SCH-01.1 requirement |
|---|---|
| Marker and migration | Add only a schedule-level `admitted_generation` plus bounded `admission_outcome` (or equivalent) and one forward-only migration. |
| Authority | Implement one service that locks the schedule, verifies the expected generation against the current `delivery_generation`, and owns the core durable admission result. |
| Materialization | Atomically create or converge one recipient work row and one linked outbox row per logical recipient. A failure rolls back every admission artifact. |
| Outcomes | Support deterministic `created`, `duplicate`, `stale_generation`, and `zero_recipients` outcomes. |
| Tests | Satisfy exactly the six named `TransactionTestCase` methods below, including real transaction concurrency and rollback behavior. |

## Required acceptance methods

1. `test_current_generation_materializes_one_recipient_and_one_outbox_per_recipient`
2. `test_duplicate_admission_converges_without_duplicate_rows`
3. `test_concurrent_current_generation_admission_is_single_winner`
4. `test_stale_generation_cannot_materialize_current_work`
5. `test_materialization_rollback_leaves_no_recipient_or_outbox_or_dispatched_claim`
6. `test_zero_recipient_admission_is_deterministic_and_rowless`

The concurrent case must use real database transaction behavior rather than a mocked sequential call. The rollback case must inject a failure during materialization and show that marker, recipient, outbox, and any false success claim are absent after rollback.

## Required correction to prior broader records

The original SCH-01 plan and broad change-list describe a thirteen-method full increment. For **SCH-01.1**, the following seven concerns are explicitly deferred and must not be added to this implementation or used as acceptance criteria: modify fencing, cancel fencing, delete safety, re-arm, legacy-field compatibility-only semantics, no-pre-commit transport-I/O proof, and post-rollback transport scheduling proof.

## Explicit exclusions

Task integration; modify/cancel/delete/re-arm lifecycle fencing; recovery; adapters; status projection; fakes; the 37-operation harness; staging; official suite; submission; Item 02; and every other Building Block are excluded. No code was changed by this scope-confirmation stage.

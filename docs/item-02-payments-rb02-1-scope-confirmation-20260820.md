# Item 02 — RB-02.1 Scope Confirmation

**Date:** 2026-08-20  
**Stage:** Scope confirmation before the RB-02.1 change-list review

> **Scope: confirmed.** The increment is limited to the reusable durable lease service, replacement of the current task-local lease handling in `process_bulk_payment_batch()`, basic owner-token/generation fencing of the task’s current critical writes, and exactly three live-path `TransactionTestCase` methods.

## Included work

| Area | RB-02.1 requirement |
|---|---|
| Lease service | Add `apps/payments/govstack_batch_lease.py` with atomic acquire, heartbeat, expiry takeover, `assert_current_owner`, and release using the existing `BatchLease` row and monotonic owner-token/generation semantics. |
| Live task integration | Replace the current task-local acquire/takeover/recheck logic in `process_bulk_payment_batch()` with the service. |
| Basic fencing | Assert current owner immediately before each critical durable write the current task performs: instruction mutation, instruction audit, batch aggregate/projection, batch audit, callback attempt/outbox creation, and release. Existing provider-runtime claim fencing remains unchanged. |
| Required tests | Define and pass exactly: `test_fresh_acquire_and_heartbeat_is_fenced`, `test_expiry_takeover_advances_generation_once`, and `test_stale_owner_cannot_write_any_side_effect`. Each must exercise the real `process_bulk_payment_batch()` path where applicable. |

## Explicit exclusions

RB-02.1 does not add instruction-attempt binding, provider-observation/reconciliation finality materialization, policy evaluation, durable batch decisions, threshold/retry/review/return-funds behavior, callback or audit replacement records, execution-admission redesign, full competing-worker/race coverage, RB-03, RB-04, staging, official-suite work, or Stage 4 verification.

The three user-specified test names resolve the only ambiguity identified by the scope reviewers. Their reference to the absence of names in the archived broader materials is not a remaining blocker because the increment request itself supplies the exact names.

# Item 02 — RB-02.1 Exact Change List

**Date:** 2026-08-20  
**Stage:** Fresh blind Stage 2 review  
**Scope:** Lease service, basic fencing of existing live-task writes, and exactly three required live-path tests.

> **Conclusion:** The current `BatchLease` schema is already durable and sufficient for this increment. RB-02.1 requires no model or migration change. The implementation must use the existing one-to-one batch lease row rather than add a new persistence record.

## Ordered file-level changes

| Order | File | Required RB-02.1 change |
|---:|---|---|
| 1 | `apps/payments/govstack_batch_lease.py` (new) | Add the reusable database-backed lease service with atomic acquire, heartbeat, expiry takeover, `assert_current_owner`, and release. All owner-sensitive operations must compare `batch`, `owner_token`, `generation`, and a non-expired `expires_at` under transaction/row-lock protection. Expiry takeover must replace the owner token and advance generation exactly once; release must never delete/reset generation. |
| 2 | `apps/payments/govstack_tasks.py` | Replace the inline task-local `BatchLease` acquisition/takeover/recheck logic in `process_bulk_payment_batch()` with the new service. Retain a lease context through the whole current task path and heartbeat/check it as needed. |
| 3 | `apps/payments/govstack_tasks.py` | Assert current owner immediately before each existing critical durable action: instruction lifecycle/attempt invocation, instruction audit creation, instruction status/failure save, batch aggregate/result save, batch audit creation, callback attempt/outbox queue, durable callback result recording, and lease release. A stale/expired owner must return without performing that next side effect. Provider-runtime semantics remain unchanged. |
| 4 | `apps/payments/tests/test_item02_rb02_batch_lease_live.py` (new) | Add a real-database `TransactionTestCase` module that invokes the actual `process_bulk_payment_batch()` path and contains **exactly** the three required method names below. Use controlled time and worker/lease seams as necessary; do not add broader RB-02.2/finality/policy/race tests. |
| 5 | Directly affected existing task tests only, if needed | Adjust setup/assertions only where replacing inline lease handling with the service changes existing task-path assumptions. Do not add new scenarios to unrelated tests. |

## Exact required test methods

| Method | Required proof |
|---|---|
| `test_fresh_acquire_and_heartbeat_is_fenced` | A fresh owner acquires the real durable lease; its heartbeat preserves owner/generation and extends expiry; wrong token or generation cannot heartbeat or pass current-owner validation on the live task path. |
| `test_expiry_takeover_advances_generation_once` | A second owner can take over only after expiry; generation advances exactly once; repeated acquire/replay does not advance it again. |
| `test_stale_owner_cannot_write_any_side_effect` | After takeover, the former owner cannot persist any existing critical task side effect, including instruction lifecycle/status, audit, batch projection, callback/outbox, callback result, or release; the proof must execute the real `process_bulk_payment_batch()` path. |

## Explicitly excluded

RB-02.1 must not change `govstack_models.py`, add migrations, bind instructions to attempts, materialize provider finality, alter reconciliation, invoke new policy/decision behavior, add durable decision records, redesign callback/audit records, change provider-runtime admission/finalization, add broad competing-worker coverage, or touch RB-03, RB-04, staging, official suites, submission, or Stage 4.

Any incomplete lease API, unfenced existing critical write, missing/renamed test, or failing required test requires discarding the whole implementation increment.

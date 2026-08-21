# Item 02 — RB-02.4 Exact Change List

**Date:** 2026-08-20  
**Stage:** Fresh blind Stage 2 review  
**Scope:** One deterministic real-task competing-worker proof and the 12-test focused RB-02 regression.

> **Conclusion:** RB-02.4 needs no new persistence, policy, provider, callback, or migration feature. It adds only one production-inert timing seam and one live-database test that proves the already implemented owner-token/generation fence across expiry.

## Ordered file-level changes

| Order | File | Required RB-02.4 change |
|---:|---|---|
| 1 | `apps/payments/govstack_tasks.py` | Add one disabled-by-default in-process test hook immediately after `BatchLeaseService.acquire()` and heartbeat have succeeded, but before the first child loop and every child, decision, projection, audit, callback-attempt, and callback-delivery write. The hook must be armed only by the test, block/release deterministically, and otherwise have zero production behavior. It must not replace the real task, lease service, or persistence path. |
| 2 | `apps/payments/tests/test_item02_rb02_batch_lease_live.py` | Add exactly `test_two_live_workers_cross_expiry_and_stale_finalization` plus only the minimal thread/event/barrier helper state needed to arm the hook, start/join two real task invocations, and coordinate expiry/takeover. The existing eleven tests remain unchanged. |
| 3 | `apps/payments/tests/test_item02_rb02_batch_lease_live.py` | The new method must prove: A owns and pauses at generation 1; its lease expires; B invokes the same live task, takes generation 2, and is the sole owner of the one durable decision and final projection; A resumes; and all durable counts/values are unchanged after A resumes. Assertions cover decision, decision audit, callback attempt/delivery, instruction/attempt state, batch projection, and lease generation/owner. |
| 4 | Focused test command | Run the whole existing real-database module, yielding exactly 12 passing tests: 3 RB-02.1 lease/fencing, 2 RB-02.2 finality, 6 RB-02.3 policy/decision, and this one RB-02.4 proof. |

## Required assertion boundary

The RB-02.4 test must invoke `process_bulk_payment_batch()` for both workers; it cannot mock, substitute, or call only helper services. Worker A’s resumed path must be rejected by the normal stale-ownership handling before any side effect. The test must prove zero stale writes by comparing durable rows and fields before versus after A resumes.

## Explicit exclusions

No model or migration change, prior-increment rewrite, additional race feature, provider-runtime redesign, callback/refund feature, RB-03, RB-04, staging, official-suite work, submission, or Stage 4 work is permitted. The task seam is timing instrumentation only and cannot introduce a production control path.

# Item 02 — Payments RB-02 Plan: Live Batch Lease and Provider Finality

**Date:** 2026-08-20  
**Scope:** RB-02 only. This plan concerns `process_bulk_payment_batch` and its durable batch decision path. It excludes prepayment, staging, the official test suite, submission, and full P02 closure.

## Current conclusion

> **RB-02 is open.** The current live batch task treats beneficiary/ID-Mapper eligibility as a terminal payment result. It does not use the existing provider-finality-aware policy, authoritative child observations, or a reusable owner-token/generation lease lifecycle before writing a terminal batch state.

The relevant official Payments expectations are authenticated/authorised initiation, durable request/status handling, finality-aware outcomes, batch/error processing, and secure API operations. The official harness validates selected API contracts; it cannot substitute for the missing internal lease, recovery, and provider-finality evidence. [1] [2] [3]

## Exact implementation boundary

| Required RB-02 control | Current gap | Exact primary files |
|---|---|---|
| Live fenced lease lifecycle | The task has local create/expiry logic only. It lacks reusable atomic acquire, heartbeat, expiry takeover, and compare-and-set finalization. | `apps/payments/govstack_tasks.py`, `apps/payments/govstack_models.py` (`BatchLease`) |
| Authoritative child finality | The task marks instructions complete/failed from mapper lookup without requiring a bound `PaymentAttempt` and verified provider observation, or an explicit review result. | `apps/payments/govstack_tasks.py`, `apps/payments/govstack_models.py`, `apps/payments/govstack_failure_services.py` |
| Existing finality policy in production | `govstack_batch_policy.evaluate()` correctly keeps uncertain/review/unresolved states non-final, but the live task never invokes it. | `apps/payments/govstack_batch_policy.py`, `apps/payments/govstack_tasks.py` |
| Threshold/retry/return-funds decisions | Pause, retry, review, and any return-funds decision are not durable, idempotent batch decisions. | `apps/payments/govstack_batch_policy.py`, `apps/payments/govstack_tasks.py`, batch/audit models |
| Live competing-worker proof | Existing tests do not run two real task invocations through lease expiry and stale finalization. | New `apps/payments/tests/test_item02_rb02_batch_lease_live.py` |

## Locked implementation sequence

The implementation must stay inside RB-02 and proceed in the following order.

1. Introduce a batch lease service with atomic acquisition, owner-token/generation compare-and-set heartbeat, expiry takeover, and release. Every child mutation, audit write, callback enqueue, policy decision, and terminal batch write must verify the same current owner token and generation.
2. Bind each processable instruction to a durable payment-attempt reference or produce an explicit durable review result. Mapper lookup cannot itself establish financial completion.
3. Materialize child finality from verified, exactly bound provider observations and reconciliation state. Missing, conflicting, malformed, unverified, retryable, uncertain, and review outcomes remain non-final.
4. Invoke the existing batch policy in the live task and persist exactly one deterministic batch decision: terminal, paused, retry, review, or configured return-funds. A terminal status is forbidden while any child is non-final.
5. Make empty-batch, already-terminal-child, replay, duplicate-finalization, and callback delivery paths idempotent under the fenced lease.

## Mandatory acceptance evidence

The new test module must exercise `process_bulk_payment_batch` itself with `TransactionTestCase`, a real database, controllable clock, and a barrier-controlled worker/provider seam. It must prove: one fresh acquire; heartbeat ownership; expiry takeover to a new generation; stale-owner rejection on child writes, finalization, audit, callback, and release; mixed verified settled/rejected/retryable/uncertain/review/unresolved child aggregation; threshold pause; retry and return-funds/review idempotency; empty batch; duplicate finalization; already-terminal children; and two competing live task invocations.

No prepayment authority, request/response contract, staging topology, official-suite harness, credential, or submission change may be included. These restrictions avoid the fabricated-tenant, HTTP-contract, missing-test, and missing-financial-binding errors that invalidated the earlier unrelated prototype.

## References

[1]: https://govstack.global/ "GovStack"
[2]: https://specs.govstack.global/ "GovStack Specifications"
[3]: https://github.com/GovStackWorkingGroup/bb-payments "GovStack Working Group Payments Building Block"

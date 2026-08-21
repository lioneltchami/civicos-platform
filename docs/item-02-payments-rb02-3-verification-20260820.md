# Item 02 — RB-02.3 Independent Verification

**Date:** 2026-08-20  
**Stage:** Independent verification of the constrained live-policy, durable-decision, and audit/outbox increment

> **Final verdict: RB-02.3 Closed.** Two totally fresh independent reviewers found every RB-02.3 acceptance element satisfied within the stated narrow scope. The focused isolated-Django transaction suite also passed with all eleven tests, including the six required RB-02.3 methods.

## Acceptance verification

| Acceptance element | Independent evidence | Result |
|---|---|---|
| Live policy use | The real `process_bulk_payment_batch()` builds child items from the closed RB-02.2 materializer and invokes `BatchDecisionService.decide()`, which invokes the existing `govstack_batch_policy.evaluate()` exactly as the policy primitive. Mapper eligibility is not used as financial finality. | **Met** |
| Durable fenced decision | `GovStackBatchDecision` is an additive append-only record containing batch, deterministic fingerprint, original owner/generation, policy state, explicit action, counts, reason, and non-PII details. The durable `(batch, fingerprint)` uniqueness constraint replays the same logical delivery; changed same-generation inputs conflict; stale ownership is asserted before persistence. | **Met** |
| Decision audit/outbox coupling | First decision insertion creates one append-only `batch_decision` audit record and, when an existing callback URL is present, queues one canonical `CallbackDelivery` through the current callback-attempt and payload-hash seams. The decision, projection, audit, and queued outbox row share the task transaction. Replay does not create a second logical record. | **Met** |
| Projection guard and empty behavior | Non-final materialized children map only to `pause`, `retry`, or `review`, and the task rejects a terminal projection with a nonzero non-final count. Empty input produces the explicit durable `empty` action, zero counts, and no fabricated settlement projection. | **Met** |
| Exact six tests | The existing real-database `TransactionTestCase` class contains all six required exact method names and invokes the live Celery task/database path twice to prove idempotency. | **Met** |

## Test evidence

The following focused isolated-Django command passed after migration-state validation reported **“No changes detected in app 'payments'”**:

```text
DJANGO_SECRET_KEY=rb023-isolated-test-secret \
DJANGO_SETTINGS_MODULE=config.settings.test \
python3 manage.py test apps.payments.tests.test_item02_rb02_batch_lease_live --verbosity 1

Ran 11 tests in 2.262s
OK
```

| Required RB-02.3 test | Result |
|---|---|
| `test_threshold_pause_persists_one_idempotent_decision` | Passed |
| `test_retry_decision_is_durable_and_idempotent` | Passed |
| `test_review_decision_is_durable_and_idempotent` | Passed |
| `test_configured_return_funds_is_explicit_and_idempotent` | Passed |
| `test_empty_batch_has_one_durable_outcome` | Passed |
| `test_duplicate_finalization_creates_one_decision_audit_and_callback` | Passed |

The test module’s eleven passing methods also include the already-closed RB-02.1 and RB-02.2 regression tests. An attempted legacy task-suite run continues to assert the superseded mapper-derived terminal behavior and therefore is not an RB-02.3 acceptance test; it was not changed or used to broaden this increment.

## Scope verification

RB-02.3 adds no provider refund execution, callback transport, full competing-worker test, provider-runtime redesign, RB-03, RB-04, staging, official-suite work, submission, or broader Stage 4 work. The closed RB-02.1 lease fence and RB-02.2 materializer remain prerequisites rather than being reimplemented.

## Next gate

RB-02.3 is closed. The next permitted increment is **RB-02.4**, planned separately. RB-02 itself remains open until the remaining permitted increment(s) and the eventual RB-02-specific independent closure verification are complete.

# Item 02 — RB-02.4 Independent Verification

**Date:** 2026-08-20  
**Stage:** Independent verification of the constrained final competing-worker proof

> **Final verdict: RB-02.4 Closed.** Two totally fresh independent reviewers found the committed-admission boundary, real two-worker takeover proof, zero-stale-write assertions, and focused regression boundary complete. The isolated Django runtime also passed the entire 12-test focused module.

## Independent acceptance verification

| Requirement | Independent evidence | Result |
|---|---|---|
| Committed lease admission before pause | `process_bulk_payment_batch()` now acquires and heartbeats the durable lease inside a short admission transaction, exits it, and only then calls the disabled-by-default timing hook. Therefore a paused A no longer holds the batch `select_for_update()` lock. | **Met** |
| Fresh downstream fence | The task opens a second transaction after the hook, reloads the batch, and asserts the current owner token/generation before child enumeration or any side effect. It repeats existing assertions before child mutations, decision/projection, audit, callback outbox, and release. | **Met** |
| Real Worker A/B scenario | The exact new `TransactionTestCase` method starts Worker A through the live Celery task, pauses A only after committed generation-1 admission, expires the durable lease, invokes Worker B through the same task to take generation 2 and finish, then resumes and joins A. | **Met** |
| Sole B finalization and zero A stale writes | The test requires B’s owner token and generation on the sole decision, then compares lease, batch projection, instruction state, decision count/identity/generation, decision-audit count, callback-attempt count, callback-delivery count, and delivery identity before and after A resumes. | **Met** |
| Full focused regression | The module contains 3 RB-02.1, 2 RB-02.2, 6 RB-02.3, and 1 RB-02.4 test: **12 total**. | **Met** |
| Scope boundary | RB-02.4 changed only `govstack_tasks.py` and the focused live test module. There is no RB-02.4 model, migration, provider-runtime redesign, RB-03/RB-04, staging, official-suite, submission, or broader Stage 4 work. | **Met** |

## Runtime test evidence

The isolated Django command below completed successfully after creating the test database:

```text
DJANGO_SECRET_KEY=rb024-isolated-test-secret \
DJANGO_SETTINGS_MODULE=config.settings.test \
python3 manage.py test apps.payments.tests.test_item02_rb02_batch_lease_live --verbosity 2

Ran 12 tests in 2.506s
OK
```

| Test group | Passing methods |
|---|---:|
| RB-02.1 lease/fencing regression | 3 |
| RB-02.2 durable binding/finality regression | 2 |
| RB-02.3 policy/decision regression | 6 |
| RB-02.4 two-live-worker expiry/takeover proof | 1 |
| **Total** | **12** |

The new method is `test_two_live_workers_cross_expiry_and_stale_finalization`. It verifies that Worker B owns the durable generation-2 final decision and that Worker A makes no stale child, decision, audit, callback, or projection write after resuming.

## RB-02 conclusion

RB-02.1, RB-02.2, RB-02.3, and RB-02.4 are closed. Therefore **RB-02 is internally closed** under the required incremental control-path evidence. This is an internal remediation conclusion only; it is not a conformance, certification, staging, official-suite, or testing-site submission claim.

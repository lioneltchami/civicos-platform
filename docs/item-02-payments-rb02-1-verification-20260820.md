# Item 02 — RB-02.1 Independent Verification

**Date:** 2026-08-20  
**Stage:** Independent verification of the constrained lease-service increment

> **Final verdict: RB-02.1 Closed.** Two totally fresh independent reviewers found all three acceptance elements satisfied within the requested narrow scope. The three required real-database transaction tests also passed in the isolated Django test configuration.

## Acceptance verification

| Acceptance element | Independent evidence | Result |
|---|---|---|
| Reusable durable lease service | `apps/payments/govstack_batch_lease.py` uses the existing one-to-one `BatchLease` row and implements atomic acquisition, same-owner replay, heartbeat, expiry takeover, `assert_current_owner`, and release. Owner token, generation, and expiry are checked under transactions/row locks. Takeover increments generation once; release expires instead of deleting/resetting the lease. | **Met** |
| Live task integration and basic fencing | `process_bulk_payment_batch()` uses the service rather than inline `BatchLease` logic. It checks current owner before instruction lifecycle/outcomes, instruction audits, instruction saves, batch projection, batch audit, callback attempt/outbox, callback result, and release. Ownership loss is caught and prevents later task effects. | **Met** |
| Exact three real-path tests | `apps/payments/tests/test_item02_rb02_batch_lease_live.py` is a `TransactionTestCase` module containing exactly `test_fresh_acquire_and_heartbeat_is_fenced`, `test_expiry_takeover_advances_generation_once`, and `test_stale_owner_cannot_write_any_side_effect`. The module uses real ORM rows and invokes `process_bulk_payment_batch.apply(...)`. | **Met** |

## Test evidence

The focused command below passed in the isolated Django test environment using `config.settings.test` with the test-only secret provided as an environment variable:

```text
DJANGO_SECRET_KEY=rb021-isolated-test-secret \
DJANGO_SETTINGS_MODULE=config.settings.test \
python3 manage.py test apps.payments.tests.test_item02_rb02_batch_lease_live --verbosity 1

Ran 3 tests in 0.568s
OK
```

The passing tests prove fresh acquisition/heartbeat token-and-generation fencing, exactly-once generation advancement on expiry takeover, and no existing durable live-task side effect when an old owner re-enters after a committed takeover.

## Scope verification

No `CreditInstruction` binding, provider-finality materialization, policy/decision model, callback/audit replacement record, provider-runtime redesign, RB-03, RB-04, staging, official-suite, submission, or full competing-worker coverage was added. Existing ID-Mapper semantics remain unchanged; this closure concerns only the durable lease service and basic fencing.

## Next gate

RB-02.1 is closed. The next permitted increment is **RB-02.2**, which must be separately planned and must not claim full RB-02 closure until its own acceptance surface and independent verification are complete.

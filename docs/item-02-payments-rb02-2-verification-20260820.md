# Item 02 — RB-02.2 Independent Verification

**Date:** 2026-08-20  
**Stage:** Independent verification of the constrained binding and authoritative-finality increment

> **Final verdict: RB-02.2 Closed.** Two totally fresh independent reviewers found each RB-02.2 acceptance element satisfied within the explicit narrow scope. The focused five-test live transaction suite, including the two required RB-02.2 methods, also passed in the isolated Django test configuration.

## Acceptance verification

| Acceptance element | Independent evidence | Result |
|---|---|---|
| Durable additive binding | `CreditInstruction.payment_attempt` is nullable, additive, `PROTECT`-on-delete, one-to-one to the existing canonical `PaymentAttempt`, and introduced by forward migration `0044`. `bind_credit_instruction_attempt()` locks both records, validates canonical bulk operation/request/amount/currency identity, permits replay, and rejects rebinding. | **Met** |
| Authoritative materializer | `materialize_credit_instruction_finality()` reads only the bound attempt, its `ProviderObservation` rows, and its `PaymentReconciliation` rows. It requires exactly one verified accepted `settled`/`rejected` observation with exact tenant/amount/currency/hash binding, matching attempt status, and matched reconciliation evidence. Every missing, malformed, unverified, uncertain, retryable, review, unresolved, or conflicting state remains non-final. | **Met** |
| Live task use | `process_bulk_payment_batch()` binds a canonical lifecycle attempt when unbound, invokes the materializer for every child, keeps non-final batches `PROCESSING` without a result timestamp, and terminalizes only when all bound children are authoritatively settled/rejected. Mapper eligibility is not financial finality. Existing terminal bound children aggregate without lifecycle reprocessing; historic terminal unbound rows are not replayed. | **Met** |
| Exact required tests | The real-database `TransactionTestCase` module contains `test_authoritative_mixed_child_finality_is_nonterminal` and `test_already_terminal_children_are_aggregated_not_reprocessed`. Each invokes the live `process_bulk_payment_batch()` path using durable rows. | **Met** |

## Test evidence

The following focused test command passed in the isolated Django configuration with a test-only secret supplied through the environment:

```text
DJANGO_SECRET_KEY=rb022-isolated-test-secret \
DJANGO_SETTINGS_MODULE=config.settings.test \
python3 manage.py test apps.payments.tests.test_item02_rb02_batch_lease_live --verbosity 1

Ran 5 tests in 1.025s
OK
```

The five passing tests include all three previously closed RB-02.1 lease/fencing methods and the two RB-02.2 methods. The mixed-finality test proves an authoritatively settled child contributes only its final amount while an otherwise bound non-final child leaves the batch `PROCESSING`; the terminal-child test proves a pre-bound settled child is aggregated without changing its attempt count.

## Scope verification

No live policy invocation, durable batch decision, threshold/retry/review/return-funds behavior, audit/callback decision coupling, replacement audit/outbox record, full competing-worker proof, provider-runtime redesign, RB-03, RB-04, staging, official-suite, submission, or broader Stage 4 work was added. Existing audit/callback behavior remains surrounding task behavior and is not part of RB-02.2’s materializer or acceptance claim.

## Next gate

RB-02.2 is closed. The next permitted increment is **RB-02.3**, separately planned and independently verified. RB-02 remains open until all later permitted increments have been completed and the eventual RB-02-specific independent closure review is performed.

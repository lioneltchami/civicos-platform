# Item 02 — RB-02.2 Scope Confirmation

**Date:** 2026-08-20  
**Stage:** Scope confirmation before the RB-02.2 change-list review

> **Scope: confirmed with the explicit increment carve-out below.** RB-02.2 is a binding and authoritative-finality-materialization increment only. It is not a policy, durable-decision, audit/callback-coupling, or full-concurrency increment.

## Included work

| Area | RB-02.2 requirement |
|---|---|
| Durable binding | Add an additive `CreditInstruction` relation to the existing canonical `PaymentAttempt`. No parallel attempt, execution-intent, reconciliation, audit, callback, or decision record is permitted. |
| Authoritative materialization | Materialize child state only from the bound attempt and durable verified `ProviderObservation` / `PaymentReconciliation` evidence. Exact verified `settled` or `rejected` evidence is final; missing, unbound, malformed, uncertain, retryable, review, unresolved, conflicting, or unverified evidence is non-final/review. |
| Live task use | Make `process_bulk_payment_batch()` use that materializer for child aggregation. Local beneficiary/ID-Mapper eligibility remains distinct from financial finality and cannot terminalize a financial child or batch. |
| Required tests | Add exactly `test_authoritative_mixed_child_finality_is_nonterminal` and `test_already_terminal_children_are_aggregated_not_reprocessed` in a dedicated or extended real-database `TransactionTestCase` module that invokes the live bulk task path. |

## Explicit exclusions

RB-02.2 does not introduce or invoke live policy evaluation, durable batch decisions, threshold/retry/review/return-funds behavior, audit/callback decision coupling, replacement audit/outbox records, complete competing-worker proof, provider-runtime redesign, RB-03, RB-04, staging, official-suite work, submission, or Stage 4.

The two scope reviews correctly observed that broader RB-02 source documents describe those later controls. They are intentionally deferred and must not appear in the RB-02.2 change list, implementation, test claims, or closure determination. The user-supplied increment request fixes the exact two test names and resolves their absence from the archived broader materials.

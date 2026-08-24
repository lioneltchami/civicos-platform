# Item 02 — RB-02.2 Exact Change List

**Date:** 2026-08-20  
**Stage:** Fresh blind Stage 2 review  
**Scope:** Durable instruction-to-attempt binding, authoritative child-finality materialization, live-task use, and exactly two additional live-path tests.

> **Conclusion:** RB-02.2 must reuse the frozen live attempt, observation, reconciliation, lease, audit, and callback seams. It adds no policy, decision, audit/callback coupling, provider-runtime, or competing-worker behavior.

## Ordered file-level changes

| Order | File | Required RB-02.2 change |
|---:|---|---|
| 1 | `apps/payments/govstack_models.py` | Add an additive nullable durable `CreditInstruction` foreign-key binding to the existing `PaymentAttempt`, with an index suitable for bound-child aggregation. Preserve historic rows and public contracts. Do not add decision, policy, audit, callback, execution-intent, or replacement evidence models. |
| 2 | `apps/payments/migrations/0044_item02_rb022_credit_instruction_payment_attempt_binding.py` | Add only the nullable/backfill-safe instruction-attempt binding and supporting index/constraint, depending on `0043_executionintent_submit_admission`. Do not rewrite migration history or introduce other persistence structures. |
| 3 | `apps/payments/govstack_failure_services.py` | Add a reusable authoritative child-finality materializer that reads only the instruction-bound attempt plus that attempt’s verified `ProviderObservation` and `PaymentReconciliation` rows. Exact verified accepted `settled`/`rejected` evidence is final. Missing, unbound, malformed, conflicting, unverified, uncertain, retryable, review, or unresolved evidence is non-final/review. |
| 4 | `apps/payments/govstack_tasks.py` | Bind the existing lifecycle attempt to its `CreditInstruction`, invoke the materializer, and aggregate its child state through the live `process_bulk_payment_batch()` path. Mapper eligibility may control local processing only; it cannot serve as financial finality. A mixed-finality batch must remain non-terminal, and already terminal children must aggregate without rebinding or reprocessing. |
| 5 | `apps/payments/tests/test_item02_rb02_batch_lease_live.py` | Extend the existing real-database `TransactionTestCase` module with exactly two additional RB-02.2 methods: `test_authoritative_mixed_child_finality_is_nonterminal` and `test_already_terminal_children_are_aggregated_not_reprocessed`. Each must invoke the live task/materializer path against durable rows. The existing three RB-02.1 tests remain unchanged. |

## Explicit exclusion boundary

RB-02.2 does not add or invoke live policy evaluation, durable decisions, threshold/retry/review/return-funds decisions, audit/callback decision coupling, replacement audit/outbox records, full race/competing-worker coverage, provider-runtime redesign, RB-03, RB-04, staging, official-suite work, submission, or Stage 4.

The implementation must be discarded if the relation is not durable/additive, finality is inferred from mapper eligibility or unverified evidence, mixed non-final children terminalize a batch, terminal children are reprocessed, either named test is missing/failing, or excluded scope is changed.

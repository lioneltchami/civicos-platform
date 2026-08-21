# Item 02 — RB-02.3 Scope Confirmation

**Date:** 2026-08-20  
**Stage:** Scope confirmation before the RB-02.3 change-list review

> **Scope: confirmed with the explicit carve-out below.** RB-02.3 introduces live policy use, one durable fenced batch decision per lease generation, and the decision's idempotent audit/outbox coupling. It does not reopen the already closed RB-02.1 lease implementation or RB-02.2 child-finality materializer.

## Included work

| Area | RB-02.3 boundary |
|---|---|
| Policy invocation | The real `process_bulk_payment_batch()` must call the existing `govstack_batch_policy.evaluate(items, failure_threshold=...)` after it has materialized each child through the closed RB-02.2 path. The evaluator is not redesigned. |
| Input to policy | Each item has the durable instruction identity and the authoritative RB-02.2 state. Any non-final materialization maps to a non-final policy status. Mapper eligibility is never an input substitute for provider finality. |
| Durable decision identity | Add one additive decision model, uniquely keyed by `(batch, policy_input_fingerprint)`. It stores the lease owner token and generation that first created it, mapped decision outcome, policy state, child counts, reason, and relevant non-PII decision metadata. A later legitimate delivery with the same logical inputs replays the same decision even if the prior lease was released and a new generation was acquired. A changed fingerprint in the same generation is rejected; a changed fingerprint in a later valid generation may produce a later decision. A stale owner/generation cannot create, project, audit, or enqueue a decision side effect. |
| Outcome mapping | Persist the explicit RB-02.3 decision outcome vocabulary: `empty`, `terminal`, `pause`, `retry`, `review`, and `return_funds`. Empty is the explicit zero-child outcome. `pause` maps policy `paused`; `retry` maps `retryable`, `uncertain`, or `unresolved`; `review` maps policy `review`; `terminal` maps all-final `completed` or `partial`; and `return_funds` maps an all-final rejected/partial result only when the new explicit configuration is enabled. It is an explicit terminal-action category, not an implicit refund transaction. |
| Terminal projection guard | A batch may be projected to `completed`, `partial`, or `failed` only when every authoritative child is final and the decision is `terminal` or `return_funds`. Any non-final child prohibits terminal projection regardless of policy status. |
| Audit and callback coupling | In the task transaction, after a fresh owner/generation assertion, persist/reuse the decision, one logical append-only audit row keyed by that decision identity, and, when the existing batch callback contract includes a callback URL, one existing `CallbackDelivery` row through the canonical callback-attempt and `(attempt, payload_hash)` uniqueness seam. These operations must commit or roll back with the decision; replay must not create a second logical decision/audit/outbox record. No callback route is fabricated for a batch that contractually has no callback URL. |
| Required tests | Extend the existing real-database `TransactionTestCase` class in `apps/payments/tests/test_item02_rb02_batch_lease_live.py` with exactly these six RB-02.3 methods: `test_threshold_pause_persists_one_idempotent_decision`, `test_retry_decision_is_durable_and_idempotent`, `test_review_decision_is_durable_and_idempotent`, `test_configured_return_funds_is_explicit_and_idempotent`, `test_empty_batch_has_one_durable_outcome`, and `test_duplicate_finalization_creates_one_decision_audit_and_callback`. |

## Explicit exclusion boundary

RB-02.3 does not alter the RB-02.1 lease service or tests, RB-02.2 binding/materializer or tests, provider-runtime design, full two-live-worker/competing-worker proof, source-route admission, RB-03, RB-04, staging, the official suite, submission, or Stage 4. It does not execute a return-funds provider transfer; it persists only the explicit decision required for a later separately authorized action.

The two scope reviews correctly identified that this detail is absent from the broader archived RB-02 documents. This record supplies the increment-specific outcome vocabulary, uniqueness/fencing behavior, callback boundary, and exact six-method allowlist. The Stage 2 change list must identify the concrete model, migration, settings key, policy-adaptation helper, task call sites, audit action, callback payload identity, and test fixture setup without expanding the boundary above.

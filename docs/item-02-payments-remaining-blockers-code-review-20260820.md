# Item 02 — Payments Remaining Blockers Code Review

**Date:** 2026-08-20  
**Stage:** 2 — fresh blocker-only review  
**Scope:** RB-01 through RB-04 from `item-02-payments-remaining-blockers-plan-20260820.md`. This record authorises no external action and makes no P02 closure claim.

## Independent review conclusion

Two fresh reviews agree that the four blockers are still open. The existing command/attempt/intent/outbox and recovery primitives are useful, but strict closure requires the live path and its race evidence, not the presence of those primitives alone.

| Blocker | Required code changes | Transaction and migration guardrails | Mandatory closure tests |
|---|---|---|---|
| **RB-01 — Recovery fence** | Make `provider_runtime.py` the single claim/submit/poll/finalize protocol; route `payment_recovery.py`, `provider_runtime_tasks.py`, and recovery/reconciliation callers through it. Poll first from durable correlation; heartbeat under token/generation; condition every final write on current token, generation, and lease. | Claim, renewal, takeover, and finalization use short row-locked transactions; provider calls occur outside. Any new state is additive and nullable/defaulted; existing in-flight attempts are recoverable/uncertain, never auto-settled. | Barrier `TransactionTestCase`: accepted submit/crash, delayed result, expiry takeover, stale completion, correlation collision, timeout/network ambiguity, competing recovery, one external submit, status-before-replay, one accepted observation, no false settlement. |
| **RB-02 — Live bulk lease/finality** | Refactor `govstack_tasks.py::process_bulk_payment_batch` to acquire/renew/take over a real owner-token/generation lease, route each provider-executable child through RB-01, and derive final batch state from authoritative observations using `govstack_batch_policy.py`. | Lease claim/renew/finalization and child writes are short transactions; no provider call under a database lock. Expired takeover changes generation; old worker finalization must be conditional and fail closed. | Competing live-task workers: acquire, heartbeat, expiry takeover, stale finalization, mixed terminal/review children, threshold pause, retry/return-funds, empty input, duplicate child delivery, duplicate finalization. No completion with unresolved child finality. |
| **RB-03 — Prepayment execution chain** | Extend `prepayment_execution.py`, mounted approval/response boundary, command/outbox binding, and worker tasks so authorised execution atomically creates one tenant-scoped attempt and immutable intent, then emits one post-commit worker handoff. Validation remains provider-free. | Lock validation/execution rows and atomically create attempt, intent, command, and outbox. Provider I/O is worker-only. Add compatible one-to-one/uniqueness constraints; ambiguous existing rows become recoverable/review, not auto-executed. | Mounted tests: validation zero provider calls; one attempt/intent/outbox after authority; duplicate approval/response; rollback; callback failure; retry; missing registration; timeout; cross-tenant denial; one worker handoff and bound callback/reconciliation evidence. |
| **RB-04 — Universal admission** | Extend `resolver_inventory.py`, both URL mounts, admission boundary, and direct service/task/runtime/lifecycle paths. Every provider-executable mutation must have a resolver-derived classification and enter the same trusted scope → command/idempotency → attempt/intent → outbox → worker chain. | Admission/binding is one transaction; provider calls only after commit in a worker. Unclassified provider mutation fails closed. Any persisted route policy is additive and versioned; ambiguous records remain reviewable. | Table-driven resolver matrix across `/payments/` and `/govstack/payments/`: trusted scope, replay/conflict, rollback, one post-commit handoff, worker-only provider calls, recovery/finality/observation, authorised reads. Assert zero unclassified executable routes and reject a newly introduced bypass. |

## Cross-blocker decisions

Implementation must proceed in the locked order **RB-01 → RB-02 → RB-03 → RB-04**. The bulk and prepayment implementations may consume the same canonical command path only after its worker recovery semantics meet RB-01. A policy helper, model field, source scan, or test mock alone is not closure evidence.

Before each increment is committed, the implementation must demonstrate migration drift freedom, focused race or integration tests, and a truthful plan update. Stage 4 is prohibited until the four blockers appear closed through the required live-path evidence.

## Internal traceability

- `docs/item-02-payments-remaining-blockers-plan-20260820.md`
- `docs/item-02-payments-blueprint-remediation-plan-20260820.md`
- `docs/item-02-payments-official-source-deep-dive-20260820.md`

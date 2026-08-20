# Item 02 — Payments Remaining Blockers Plan

**Date:** 2026-08-20  
**Scope:** Internal strict remediation only. This plan covers the four confirmed remaining implementation blockers. It does not authorise staging, official harness execution, deployment, portal activity, credential access, certification, or submission.

## Source baseline

The required end state remains the blueprint control path:

> **Mounted provider-executable route → trusted scope → canonical command/idempotency → bound outbox → worker acknowledgement → claim/status-first recovery → provider observation/reconciliation → authorised status surface.**

The authoritative external baseline is the official GovStack Payments specification, the Payments Building Block repository, and the GovStack testing requirements. The current deep-dive and blueprint records remain the repository-level mapping of those sources to CivicOS implementation work.[1] [2] [3]

## Stage 1 conclusion

Two shared-context analyses independently confirm that **all four blockers remain open**. The repository contains useful partial foundations, but none meets the blueprint definition of strict closure. The next stage must review only the code needed to close the following four blockers.

| ID | Confirmed remaining blocker | Exact current code boundary | Retained partial foundation | Strict closure evidence required |
|---|---|---|---|---|
| **RB-01** | Lease-expiry takeover, stale completion, and timeout/network recovery are not proven end-to-end. | `apps/payments/provider_runtime.py`, `payment_recovery.py`, `provider_runtime_tasks.py`, `govstack_failure_services.py`, `PaymentAttempt`, `PaymentExecutionIntent`. | Claim token, generation, expiry, heartbeat, durable correlation, status-first selection, and a canonical recovery entry point exist. | Barrier-controlled two-worker `TransactionTestCase`: accepted-submit/crash, delayed outcome, expiry takeover, stale completion, correlation collision, timeout/network ambiguity, **at most one external submit**, status poll before replay, one accepted observation, and no settlement without authoritative finality. All retry/replay/reconciliation entry points must use the canonical recovery command. |
| **RB-02** | The live bulk worker does not derive provider-finality-aware child decisions under a real fenced lease before completing a batch. | `apps/payments/govstack_tasks.py::process_bulk_payment_batch`, bulk services, `govstack_batch_policy.py`, `BatchLease`/batch models, `PaymentAttempt`/provider observations. | The policy distinguishes terminal, retryable, uncertain, review, unresolved, and threshold-pause outcomes. | Real owner-token/generation lease acquire, heartbeat, expiry takeover, stale finalization fencing, authoritative child observations or explicit review, threshold pause/retry/return-funds handling, empty and duplicate finalization cases, and competing-worker proof against the live task path. |
| **RB-03** | Prepayment eligibility does not bind into a real attempt, execution intent, outbox, and worker lifecycle. | `apps/payments/prepayment_execution.py`, prepayment validation models/views/services, `PaymentAttempt`, `PaymentExecutionIntent`, command/outbox and worker tasks. | `PrepaymentExecution` provides one-to-one eligibility, validation-state gating, replay/conflict, and rollback safety. | Mounted approval/execute proof: validation alone makes zero provider calls; authorised execution atomically creates one tenant-scoped attempt and immutable intent, emits one post-commit handoff, and is consumed by the worker. Cover duplicate approval/response, rollback, callback failure, retry, missing registration, timeout, and cross-tenant denial. |
| **RB-04** | Only the bulk route enters canonical admission; every provider-executable mounted route must be enforced. | `apps/payments/urls.py`, mounted `/payments/` and `/govstack/payments/` views, `resolver_inventory.py`, `payment_command_boundary.py`, direct task/runtime/lifecycle call sites. | Resolver inventory, canonical bulk admission, command/attempt/intent/outbox binding, worker acknowledgement, and authorised reconciliation read exist. | Resolver-derived classification of both mounts; failure for unclassified provider mutations; table-driven mounted-route matrix proving trusted scope, command/idempotency, rollback-before-commit, one post-commit handoff, worker-only provider execution, replay/conflict, recovery/finality, observation/reconciliation, and read isolation for every provider-executable route. |

## Locked implementation order

The implementation stage must not skip dependencies. First complete RB-01 because it supplies the safe worker claim/recovery protocol. Then implement RB-02 using that protocol for live bulk children. Implement RB-03 by binding eligible prepayment execution to the same canonical attempt/intent/outbox/worker path. Finally complete RB-04 with a resolver-derived admission matrix that routes all provider-executable mutations through the now-proven path.

Each increment must be accompanied by focused regression tests and a small blocker-named commit. The controlling blueprint plan must be updated after each increment. No P02-01 through P02-09 result may be marked closed until Stage 4 independently verifies the complete route-to-authorised-status path.

## References

[1]: https://specs.govstack.global/ "GovStack Specifications"
[2]: https://github.com/GovStackWorkingGroup/bb-payments "GovStack Working Group — Payments Building Block"
[3]: https://testing.govstack.global/en/requirements "GovStack Testing Requirements"

## Internal traceability

- `docs/item-02-payments-official-source-deep-dive-20260820.md`
- `docs/item-02-payments-blueprint-remediation-plan-20260820.md`
- `docs/item-02-payments-blueprint-code-review-20260820.md`


### Stage 3 interim increment — RB-01 durable ambiguity evidence

`PaymentAttempt` now stores additive `recovery_evidence`. The worker polls only from explicit durable provider correlation, provider IDs, external transaction IDs, or a persisted ambiguous provider outcome; a reserved command or reserved intent alone submits first. Network, timeout, and uncertain outcomes persist bounded ambiguity evidence and remain non-final.

The isolated validation reported **no Payments migration drift** and **12 focused tests passing**. The focused evidence covers reserved first submission, correlation-driven polling, live claim refusal, expired claim generation increase, and timeout remaining uncertain. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-rb01-recovery-evidence-validation-20260820.log`.

RB-01 is still open. The required barrier-controlled two-worker stale-completion, accepted-submit/crash, delayed outcome, correlation-collision, and at-most-one-submit proof remains unimplemented; therefore no P02 result is promoted.


### Stage 3 interim increment — RB-01 production stale-finalization guard

`ProviderRuntime.finalize_claimed_result()` now owns the production token-and-generation finalization guard used by `submit_or_poll()`. The focused `TransactionTestCase` persists a simulated post-expiry takeover, invokes the production finalization method with the stale worker identity, and proves that the stale result cannot change lifecycle status, overwrite the new claim, or append an accepted observation.

The isolated validation reported **no Payments migration drift** and **13 focused tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-rb01-stale-finalization-validation-20260820.log`.

RB-01 remains open. This test proves stale persistence fencing, but the strict acceptance set still requires a barrier-controlled accepted-submit/crash and delayed-outcome two-worker scenario, correlation collision evidence, and a durable at-most-one-submit record across the actual worker handoff.


### Stage 3 interim increment — RB-01 durable one-submit admission fence

`PaymentExecutionIntent` now carries additive `submit_started_at` and `submit_admission_generation` markers. Under the attempt row lock, the first worker persists this marker before provider submission. A later worker that observes the marker takes the fail-closed polling branch rather than opening another submit admission, including after lease expiry. This preserves newly reserved commands as first-submit eligible while treating a committed submit admission as durable external-work ambiguity.

The isolated validation reported **no Payments migration drift** and **14 focused tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-rb01-submit-admission-validation-20260820.log`.

RB-01 remains open. The strict acceptance set still lacks the required barrier-controlled accepted-submit/crash and delayed-outcome scenario exercising the actual worker handoff, plus correlation-collision evidence. No P02 result is promoted.


### Stage 3 interim increment — RB-01 coordinated delayed-worker recovery proof

The provider runtime now exposes a no-op production synchronization seam used only by deterministic worker-race tests. The focused `TransactionTestCase` starts a delayed first worker after its provider call, expires the durable lease, runs a second recovery invocation, then releases the stale first completion. Assertions use persisted claim generation and execution-intent submit-admission state rather than a process-local provider object.

The isolated validation reported **no Payments migration drift** and **7 dedicated RB-01 tests passing**. The durable submit-admission marker remains generation `1` after takeover, the takeover generation advances, and the stale completion is fenced. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-rb01-two-worker-validation-20260820.log`.

RB-01 remains open pending correlation-collision coverage and an explicit accepted-submit/crash scenario through the published command-consumer handoff. The evidence materially covers delayed completion, expiry takeover, stale finalization, and the no-second-submit admission invariant, but does not yet meet the entire strict acceptance set.


### Stage 3 interim increment — RB-01 durable consumer acknowledgement and correlation collision

Focused `TransactionTestCase` coverage now proves that a bound published command has one durable attempt and intent before acknowledgement, redelivery persists one acknowledgement and schedules orchestration once, and a conflicting provider correlation fails closed without overwriting the original durable correlation.

The isolated validation reported **no Payments migration drift** and **6 focused tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-rb01-consumer-durable-validation-20260820.log`.

RB-01 remains open. The accepted-submit/crash scenario still needs a fully database-backed assertion that follows the real consumer-to-worker handoff through an ambiguous external result and demonstrates no second provider submit without relying on process-local adapter state.

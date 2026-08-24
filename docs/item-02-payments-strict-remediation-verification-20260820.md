# Item 02 — Payments strict remediation verification

**Date:** 2026-08-20  
**Stage:** 4 of 4 — two independent final code-and-plan verifications  
**Verification boundary:** Final committed repository code, strict plan, strict code review, focused local test evidence, and no external system. Neither verifier accessed a provider, staging environment, official suite, portal, credential, deployment, or testing site.

## Strict closure rule

A defect is closed only when concrete local evidence proves this mandatory chain:

> **Mounted mutation → trusted canonical admission → post-commit task → durable claim → process-safe configured adapter → exact observation/reconciliation → bounded authorised status.**

Models, migrations, helpers, policies, optional paths, process-local behavior, and isolated tests do not close a defect. The two independent reviewers agreed: **P02-01 through P02-09 remain open.**

## Evidence observed

The focused isolated run recorded **no pending Payments migration changes** and **203 focused tests passing** in `docs/govstack/testing/evidence/item-02-payments-strict-runtime-foundation-validation-20260820.log`. That evidence confirms a useful local runtime foundation, not strict end-to-end closure. The reviewers found no mounted, independently initialized web/worker, two-worker race/crash, universal mutation-route, or reconciliation security suite sufficient to satisfy the strict rule.

## Per-defect independent conclusion

| Defect | Verdict | Concrete code/evidence found | Why strict closure is not met |
|---|---|---|---|
| **P02-01 — Adapter process safety** | **Still open** | `apps/payments/provider_runtime.py` now resolves an active durable registration and can materialize a fresh deterministic adapter. Existing evidence is limited to `ProviderRuntimeIntegrationTests` in `apps/payments/tests/test_item02_runtime_wiring.py`. | No mounted route through independently initialized web/worker state; no restart/rotation, malformed/inactive/ambiguous registration, factory failure, tenant/operation isolation, or migration-upgrade evidence. |
| **P02-02 — Trusted tenant admission** | **Still open** | `apps/payments/provider_admission.py:admit_provider_attempt` and nonblank `PaymentAttempt.tenant_id` are present. | No mounted G2P request → instruction → task → attempt → observation → reconciliation proof of authenticated tenant derivation, immutable propagation, forged-task rejection, legacy behavior, or zero adapter call on denial. |
| **P02-03 — Real prepayment provider attempt** | **Still open** | Prepayment validation/callback paths remain in `govstack_views.py`, `govstack_services.py`, and `govstack_tasks.py`. | No mounted prepayment route creates a durable provider attempt and proves post-commit task, deterministic adapter, exact observation, reconciliation, and bounded status for success/failure/uncertainty. Validation remains distinct from financial execution. |
| **P02-04 — Durable in-flight claim** | **Still open** | `PaymentAttempt` now has token, generation, expiry, heartbeat, submission-intent, and attempt-count fields; `ProviderRuntime.submit_or_poll` fences stale results. | No barrier-controlled two-worker mounted-path proof of exactly one submission, crash-before/after-I/O handling, expiry takeover, stale completion, duplicate delivery, or bounded retry. |
| **P02-05 — Unified status-first recovery** | **Still open** | `ProviderRuntime.status_first_recovery` and a recovery task exist; uncertain attempts poll in `submit_or_poll`. | No proof that G2P, prepayment, P2G, due, retry, replay, timeout, uncertainty, and reconciliation-triggered recovery all use the sole command or cannot bypass it. |
| **P02-06 — Route-wide HTTP idempotency** | **Still open** | `IdempotencyLedger` and `IdempotencyService` have primitive/replay coverage in `test_item02_runtime_wiring.py`. | No explicit all-mutation URL inventory enforced in code; no mounted tests for each route’s replay, conflict, concurrency, in-progress, rollback, scope separation, or exactly-once side effect. |
| **P02-07 — Live batch lease/recovery** | **Still open** | `BatchLease` and initial lease primitives exist in the bulk path. | No live-worker renewal/revalidation, competing owner, renewal failure, expiry/takeover, stale generation, policy, settled exclusion, partial retry, crash/retry, or full accounting evidence. |
| **P02-08 — Reconciliation authorisation** | **Still open** | Finality/reconciliation primitives and a report surface exist. | No mounted proof of principal-derived tenant isolation, unauthenticated/unauthorised denial before access, forged/conflicting headers, wrong-tenant/cross-object denial, deterministic pages/order, or sensitive-field absence. |
| **P02-09 — Mandatory canonical mounted flow** | **Still open** | `admit_provider_attempt()` and `transaction.on_commit` task enqueue are present. | No URL-to-command proof that every G2P, prepayment, and P2G provider-executable mutation must use canonical admission and reaches exactly one task, claim, adapter, observation/reconciliation, and status path with no bypass. |

## Controls that remain valuable but insufficient

The verifiers found the following foundations worth retaining: fail-closed provider absence; explicit durable registration configuration; exact provider observation/finality boundary in `PaymentLifecycleService.record_provider_result()`; non-final treatment of uncertain/unverified outcomes; provider I/O outside database transactions; token/generation stale-result rejection; submission-intent/heartbeat persistence; and a durable idempotency primitive. These controls do not substitute for the mandatory mounted runtime proof.

## Aggregate conclusion

**Item 02 Payments remains `Partially aligned / remediation required`.** **No P02-01 through P02-09 defect is closed under the strict rule.** The additional runtime work and 203-test local regression run are committed, auditable foundations only. The next permitted work is another internal remediation cycle focused on the still-unwired mounted flows and proof suites; it is not staging, official testing, portal, deployment, certification, or submission work.

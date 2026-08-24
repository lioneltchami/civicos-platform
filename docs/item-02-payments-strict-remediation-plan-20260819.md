# Item 02 — Payments strict runtime-enforcement remediation plan

**Date:** 2026-08-19  
**Stage:** 1 of 4 — two shared-context strict gap analyses  
**Scope:** Internal code, local deterministic evidence, and repository validation only. No provider credential, staging, official-suite, portal, deployment, or submission action is authorised.

## Strict closure rule

Both reviewers applied the same rule: **a model, migration, helper, policy primitive, optional setting, or isolated test does not close a defect.** Closure requires an enforced mounted route → canonical admission → committed task → durable claim → process-safe configured adapter → exact observation/reconciliation → bounded status path, with migration, race, negative-security, and deterministic-adapter proof.

> **Stage 1 conclusion:** **P02-01 through P02-09 are all open.** Useful fail-closed foundations exist, but none currently meets the strict closure rule.

The official framework distinguishes auditable source/test evidence from observable running-system evidence and requires all REQUIRED requirements for a compliant solution.[1] This internal pass therefore makes no claim about official compliance, testing-site readiness, certification, or submission.

## Current defect plan

| ID | Exact current failing behavior and current paths | Required closure change | Required local proof |
|---|---|---|---|
| **P02-01 — adapter process safety** | `apps/payments/provider_runtime.py` uses process-local `ProviderRuntime._providers`; durable `ProviderRegistration` cannot independently materialize an executable adapter in a worker after restart/rotation. | Replace process-local materialization with a worker-resolvable, tenant/operation-scoped durable factory/configuration boundary. Deterministically reject missing, inactive, malformed, conflicting, ambiguous, or unsupported registrations. | Separately initialized web/worker runtime test through a mounted route and task; restart/rotation, tenant/operation isolation, malformed/inactive/ambiguous registration, factory failure, migration-upgrade, and deterministic adapter assertions. |
| **P02-02 — trusted tenant admission** | `PaymentAttempt.tenant_id` and `admit_provider_attempt()` reject blank scope, but trusted tenant derivation is not proven through mounted G2P → instruction → task → attempt → observation → reconciliation. Legacy paths can still create wrong-scope work. | Derive tenant from authenticated/authorised business context only; propagate it immutably through every command/task/object; reject blank, forged, changed, mismatched, legacy, and cross-tenant work before adapter invocation. | Mounted G2P tests for missing/conflicting identity, wrong-tenant objects, forged task payloads, immutable scope, legacy/backfill, concurrent enqueue, no adapter call on rejection, and cross-tenant observation/reconciliation denial. |
| **P02-03 — real prepayment provider attempt** | Current prepayment handling performs validation/callback work but no mounted route creates and executes a durable `PaymentAttempt`. Local validation can advance without provider observation or financial finality. | Define a prepayment provider operation that uses canonical admission, post-commit enqueue, common adapter claim/poll/submit, exact observation, reconciliation, and status projection. Keep beneficiary validation a prerequisite only. | Mounted route → commit → task → deterministic adapter → observation/reconciliation/status tests for success, rejection, timeout/network/uncertain, validation failure, callback failure, rollback/no-task, duplicate/retry, and no-provider failure. |
| **P02-04 — durable in-flight claim** | Current claim token/expiry/generation checks are foundations; concurrent workers, crash/takeover, submission intent, and duplicate provider-call prevention are not proven as a complete persisted protocol. | Persist atomic submission intent/claim before I/O, including owner token, generation, expiry/heartbeat, and attempt count; fence all writes and define expiry/takeover. Keep I/O outside transactions. | Barrier-based two-worker race on the mounted path proving one `submit()`; crash before/after I/O, expiry takeover, stale completion, duplicate delivery, timeout/uncertain, migration/backfill, and final result persistence. |
| **P02-05 — unified status-first recovery** | Poll-before-submit exists for some uncertain work, but due/retry/replay and G2P/prepayment/P2G paths are not proven to enter one authoritative recovery state machine. | Make one claim-driven runtime command the exclusive entry for initial due work, retry, replay, timeout, uncertainty, and reconciliation-triggered recovery. Poll first for known correlation; resubmit only explicit retryable work. | Per-workflow route-to-task tests for poll-before-resubmit, terminal/review/finality exclusion, malformed/conflicting/unknown result handling, duplicate observation, recovery/replay, and no legacy task bypass. |
| **P02-06 — route-wide HTTP idempotency** | `IdempotencyLedger` and replay primitives exist, but no universal guard protects every mounted mutating G2P, prepayment, P2G, manual, and related Payments route before side effects. | Add one mandatory durable HTTP guard before side effects, scoped by trusted tenant/principal, method, operation/route, key, and canonical fingerprint; reserve, replay exact completed response, conflict changed payload, define in-progress, and rollback safely. | Explicit mutating-URL inventory plus each-route tests for replay, changed-payload conflict, concurrent first writer, in-progress, rollback, empty/invalid key, tenant/method/route separation, and exactly-once task/provider side effects. |
| **P02-07 — live batch lease/recovery** | Bulk worker acquires initial `BatchLease`, but lacks proven renewal, revalidation before every child action, pause/threshold/partial retry policy, settled exclusion, expiry/takeover, and stale-owner fencing. | Require durable claim/renew/generation check first and before every child/provider/result/final transition; enforce policy and settled exclusion; define crash/takeover recovery and full-instruction accounting. | Live worker tests for competing claims, renewal failure, expiry/takeover, stale generation, crash/retry, threshold/pause/review, partial retry, settled exclusion, mixed/empty/replay batches, and deterministic adapter calls. |
| **P02-08 — reconciliation authorisation** | Reconciliation/report paths do not prove tenant scope derives from verified request identity; supplied header/selector conflict and cross-tenant security are insufficiently bounded/tested. | Derive effective tenant from authenticated principal or trusted resource binding; reject missing/malformed/conflicting/wrong scope before access; add deterministic pagination/order, permission matrix, and redacted allowlist. | Mounted tests for unauthenticated/unauthorised, forged/conflicting header, wrong tenant, cross-tenant object, denial before read/mutation, same-tenant pages/order, empty/mismatch, and sensitive-provider-field absence. |
| **P02-09 — mandatory canonical mounted flow** | `admit_provider_attempt()` and post-commit enqueue are primitives; G2P/P2G optional runtime wiring remains bypassable and prepayment is outside the flow. | Make canonical admission the sole provider-execution entry for every mounted G2P, prepayment, and P2G mutation; validate idempotency/tenant/registration/attempt atomically and enqueue only after commit. Encapsulate direct adapter access in worker boundary. | Mounted URL-to-command coverage for all three flows: one committed attempt/one task, rollback/no-task, disabled/malformed configuration, duplicate delivery, task registration/import, deterministic adapter, exact observation/reconciliation/status, wrong-tenant, and no bypass. |

## Required implementation invariants

The following are **do not touch** controls unless a defect test exposes an actual fault: `PaymentLifecycleService.record_provider_result()` as the finality boundary; exact observation binding; conservative non-final results for timeout/network/malformed/conflicting observations; provider I/O outside database transactions; stale-claim rejection; existing durable idempotency savepoint correction; and fail-closed provider absence. No default/mock settlement, process-local lock, synthetic provider success, external network call, or weakened tenant/authorization rule is permitted.

## Stage 3 acceptance gate

A defect may be claimed closed only when its listed mounted/runtime proof passes in a clean isolated database and proves the enforcement path. The plan must be updated after implementation with test names and observed results. If a defect remains helper-only, optional, process-local, or untested under race/security/recovery conditions, it remains open.

## References

[1]: [GovStack Requirements Model](https://specs.govstack.global/architecture/cfr-architecture-2.1.0/5-specification-framework/5.3-requirements-model)

[2]: [GovStack Working Group — Payments Building Block](https://github.com/GovStackWorkingGroup/bb-payments)

[3]: [Item 01–07 refreshed readiness reconciliation](item-01-07-govstack-readiness-reconciliation-refresh-20260819.md)

[4]: [Item 02 internal remediation verification](item-02-payments-internal-remediation-verification-20260819.md)

## Stage 3 interim implementation record — 2026-08-20

A focused runtime foundation has been integrated and validated locally. The change removes process-local adapter authority, stores a bounded durable adapter-factory configuration, materializes a fresh deterministic adapter in the worker, adds durable submission-intent and heartbeat fields, retains token/generation fencing, and exposes a status-first recovery task. The strict migration check reported no pending Payments changes, and the focused existing suite completed with **203 tests passing**; raw output is retained at `docs/govstack/testing/evidence/item-02-payments-strict-runtime-foundation-validation-20260820.log`.

This evidence is deliberately **not a closure claim** for P02-01, P02-04, P02-05, or P02-07. The required mounted-route, independently initialized worker, two-worker race/crash/takeover, exclusive recovery-path, and live batch-lease policy evidence remains to be implemented and tested. P02-02, P02-03, P02-06, P02-08, and P02-09 remain open pending the surface enforcement implementation.

| Defect group | Stage 3 state | Reason it remains open |
|---|---|---|
| P02-01, P02-04, P02-05, P02-07 | **Foundations integrated; open** | The production protocol must still be proved through mounted workflow, durable race/recovery, and live batch-worker tests. |
| P02-02, P02-03, P02-06, P02-08, P02-09 | **Open** | Trusted mounted routes, real prepayment execution, universal idempotency, reconciliation authorization, and mandatory admission integration are not yet complete. |

No external provider, staging system, official suite, portal, deployment, credential, or submission action was used.

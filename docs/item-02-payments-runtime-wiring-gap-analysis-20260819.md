# Item 02 — Payments runtime-wiring gap analysis

**Date:** 2026-08-19

**Stage:** 1 of 4 — two shared-context analyses

**Scope:** Internal engineering only. This analysis does not use provider credentials, staging, official suites, testing-site access, portals, or production deployment.

## Method and shared evidence

Two independent analysts received the same committed Payments source tree, the prior remediation plan, the dated independent verification record, and the isolated local-validation log. They were instructed to trace executable paths rather than infer runtime behavior from helper modules or unit tests. Both analysts concluded that the current code is **not ready for a readiness or submission claim**, but that the remaining blockers are bounded enough for a safe, focused implementation pass using a deterministic local adapter.

| Shared evidence | What it establishes | What it does not establish |
|---|---|---|
| `ProviderObservation`, `PaymentReconciliation`, and the provider-result recorder | A durable, fail-closed finality boundary exists. | That any G2P, prepayment, or P2G runtime path actually invokes it. |
| 199-test isolated validation log and no Payments model drift | The focused tested source state is internally executable. | Provider integration, authoritative status polling, end-to-end route execution, official validation, or submission readiness. |
| Status, idempotency, batch, and reconciliation modules | Reusable policy primitives exist. | Route/worker adoption, persistence, atomic concurrency control, or live end-to-end behavior. |

## Consolidated gap assessment

| Priority | Runtime gap | Source evidence | Required closure |
|---|---|---|---|
| **Critical** | G2P bulk, prepayment, and P2G attempts do not select an adapter, invoke `submit()`/`get_status()`, or persist results through the lifecycle recorder. | `govstack_provider.py` and `providers/deterministic.py` define a seam; `govstack_tasks.py` and P2G service paths do not compose the seam into a provider-result lifecycle. | Add one explicit, fail-closed adapter-selection boundary and a shared durable attempt dispatcher used by G2P, prepayment, and P2G workflows. |
| **Critical** | Uncertain or retryable work is not status-before-resubmission. | Due-attempt queries and recovery triage exist, but there is no demonstrated authoritative `get_status()` → observation → reconciliation → eligibility-gated resubmission chain. | Add an atomically claimed recovery workflow that polls first, records/reconciles the result, excludes terminal/final attempts, and only resubmits explicit retry-eligible work. |
| **High** | Canonical HTTP idempotency has no persisted enforcement at live endpoints. | `govstack_http_idempotency.py` is pure; no demonstrated reservation, response replay, conflict storage, or concurrent first-writer behavior appears in the applicable views. | Add a migration-backed idempotency ledger and a common endpoint wrapper with exact replay and same-key changed-payload conflict behavior. |
| **High** | Batch threshold, pause/kick-back, partial retry, and leases are not durable worker behavior. | `govstack_batch_policy.py` returns pure decisions and in-memory leases; the bulk worker does not persist or atomically claim them. | Extend the existing batch/attempt model or add narrow lease storage, then apply policy inside the locked worker flow with settled-item exclusion. |
| **High** | Tenant-scoped reconciliation/mismatch reporting is not a governed, queryable runtime surface. | P2G projects a single local attempt status; this is not a tenant-authorized report over durable observations/reconciliations. | Add a read-only, tenant-authorized reconciliation/mismatch endpoint with bounded, redacted fields and explicit classifications. |
| **Medium** | P2G distinguishes local notification from settlement but cannot progress to verified finality. | The transfer status projection is conservative, but P2G does not dispatch/poll an adapter. | Reuse the shared attempt dispatcher and recovery workflow for P2G, preserving local notification as non-authoritative. |
| **Medium** | Regression coverage is direct-helper focused. | `test_item02_production_wiring.py` invokes lifecycle code directly; existing focused suites do not prove view/task-to-adapter chains or persistent concurrency controls. | Add integration tests for actual tasks/views, adapter result handling, recovery, idempotency, batch leases, report authorization, and response projection. |

## Safe implementation design

The implementation must **reuse** `PaymentLifecycleService.record_provider_result()` as the sole finality transition boundary. No view, task, or provider adapter may set a financial final state directly. The adapter boundary should be explicit, tenant/operation scoped, and fail closed when no provider is configured. Web handlers should persist/queue work rather than call a provider synchronously.

| Component | Proposed narrow change | Required invariant |
|---|---|---|
| Adapter selection | Add a provider registry/factory with test injection and a typed disabled/unavailable result. | Missing configuration yields review/uncertain, never simulated settlement. |
| Attempt orchestration | Add one submission and one poll/recovery orchestrator that reloads attempts under lock. | All provider results flow through the exact-binding finality recorder. |
| Recovery | Claim due work durably, poll status first, reconcile, then resubmit only explicit retryable non-terminal attempts. | Timeouts and unknowns never directly cause a new external submission. |
| HTTP idempotency | Persist route-scoped tenant/key/fingerprint/status/body/completion state under a unique constraint. | Matching duplicates replay exactly; changed payloads conflict; concurrent first writers converge. |
| Batch control | Persist a lease/ownership state and apply batch policy inside the worker transaction. | Only one live claimant processes a batch; settled work remains excluded. |
| Reconciliation report | Add a tenant-authorized, read-only report route backed by observations and reconciliations. | Queries never leak another tenant’s identifiers or raw provider payloads. |

## Non-negotiable invariants

| Area | Invariant |
|---|---|
| Finality | Only verified, stable, exact-bound evidence can settle or reject an attempt. |
| Binding | Tenant, operation, request, amount, currency, and observation identity must match the intended attempt. |
| Retry | Poll first; do not retry a terminal, accepted-finality, review, or unresolved unknown outcome. |
| Side effects | Persist durable state before worker acknowledgement; external calls occur from bounded, idempotent background work. |
| Concurrency | Use database uniqueness, row locks, and durable lease expiry rather than process-local coordination. |
| Idempotency | Same tenant/scope/key plus same canonical payload replays the stored result; changed payload conflicts without side effects. |
| Privacy | Do not expose secrets, raw provider payloads, payee functional identifiers, or cross-tenant data in reports, audits, or test artifacts. |

## Recommended implementation order

The analysts agree that the following order reduces unsafe intermediate states:

1. Add the adapter registry and shared durable attempt dispatcher.
2. Add status-first recovery with durable claim semantics.
3. Persist and enforce HTTP idempotency at relevant entry points.
4. Persist batch leasing and apply threshold/pause/partial-retry policy in the actual worker.
5. Add the tenant-scoped reconciliation/mismatch report.
6. Add route/task integration coverage and run migration plus focused regression validation.

## Local acceptance matrix

| Requirement | Local completion possible | Required evidence | External evidence still required |
|---|---|---|---|
| Adapter dispatch and result recording from G2P, prepayment, and P2G workflows | Yes | Scripted deterministic-adapter integration tests and durable observation/reconciliation assertions | Real provider behavior and credentials |
| Status-first recovery | Yes | Poll-before-submit, terminal exclusion, and duplicate-delivery tests | Live provider status consistency |
| HTTP idempotency and batch leases | Yes | Migration-backed concurrent request/worker tests | Production load and deployment behavior |
| Reconciliation report and tenant isolation | Yes | Authorized/cross-tenant/mismatch response tests | Completeness of an external source feed |
| Provider, staging, official suite, testing site, certification, conformance, or submission result | No | None in this repository can establish it | Authorized external environment and applicable official evidence |

> **Stage 1 conclusion:** Proceed to blind code review of the current repository state and this implementation scope. The next pass must wire existing safety controls into live runtime paths; adding more standalone helpers would not close the verified blockers.

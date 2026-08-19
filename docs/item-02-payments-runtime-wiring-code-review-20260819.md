# Item 02 — Payments runtime-wiring blind code review

**Date:** 2026-08-19

**Stage:** 2 of 4 — two fresh blind code reviews

**Review input boundary:** Each reviewer received only the committed Payments source and the Stage 1 runtime-wiring gap-analysis document. They were blind to the Stage 1 conversations, raw analyst reports, prior review conversations, repository history, credentials, staging, portals, official suites, and external sources.

## Consolidated recommendation

Both fresh reviewers returned **implement with corrections**. They agree that the Stage 1 direction is correct: the critical problem is not the absence of finality primitives, but their absence from executable G2P bulk, prepayment, and P2G provider-result paths. However, a generic dispatcher, recovery worker, ledger, lease, or report must not be added without first defining canonical operation bindings, disabled-provider behavior, durable ownership, deployment sequencing, and compatibility constraints.

| Assessment | Independent conclusion |
|---|---|
| Runtime adapter dispatch | Missing; must be wired through one shared, binding-checked orchestration boundary. |
| Provider finality lifecycle | Retain the existing fail-closed recorder as the sole terminal transition boundary. |
| Status-first recovery | Missing; must poll an authoritative source before any resubmission. |
| Persistent idempotency and leases | Missing; pure helpers are insufficient under concurrent HTTP/task delivery. |
| Reconciliation reporting | Missing as a governed tenant-authorized runtime surface. |
| Implementation disposition | Proceed only with the corrected plan below and a feature-disabled/fail-closed default. |

## Review findings

| Severity | Finding | Code evidence | Required correction |
|---|---|---|---|
| **Critical** | A shared dispatcher has no canonical object-to-attempt contract. | G2P instructions, prepayment objects, P2G transfers, and `PaymentAttempt` have different identifiers and lifecycle states. | Define a typed command with operation, tenant, source request/business object, amount, currency, canonical identity, and provider correlation identity; derive values from locked database rows rather than request payloads. |
| **Critical** | Provider resolution is unspecified by tenant, operation, and configuration state. | `PaymentProvider` is a protocol; `DeterministicProvider` is a test double. | Add an explicit tenant/operation registry with injected test override and typed unavailable/disabled behavior. A deterministic provider must never be a production default. |
| **Critical** | Live G2P/prepayment/P2G paths do not call the finality recorder through a provider result. | `govstack_tasks.py` and related services do not compose `submit()`/`get_status()` with `record_provider_result()`. | Make a common dispatcher the only adapter invocation/result-recording boundary. Prevent views/tasks/adapters from setting financial terminal states directly. |
| **Critical** | Recovery lacks durable status-first ownership and retry gating. | Due query/triage behavior exists but no claim → provider poll → observation/reconciliation → explicit eligibility → resubmission chain. | Add durable claim/lease state, poll before submit, exclude terminal/accepted-finality/review work, bound attempts/backoff, and make duplicate delivery harmless. |
| **High** | Provider identifiers may be truncated or insufficiently bound. | `ProviderResult` normalizes bounded identifiers and accepts final-looking outcomes only with verification metadata. | Use stable identifier/hash representation as needed, require a verification method and observation identity, preserve exact tenant/operation/request/amount/currency binding, and reject conflicting reuse. |
| **High** | HTTP idempotency is a pure helper only. | `govstack_http_idempotency.py` has no migration-backed reservation/replay record or endpoint wrapper. | Add tenant/method/normalized-path/key ledger, canonical fingerprint, state, persisted status/body/headers, atomic first-writer reservation, exact replay, and changed-payload conflict. |
| **High** | Batch policy and leases are process-local rather than durable. | `govstack_batch_policy.py` returns pure decisions/in-memory leases; bulk worker has no durable claim. | Add database-backed owner/expiry/generation state, lock before policy application, re-check settled items under lock, and persist pause/partial/retry state before acknowledgement. |
| **High** | P2G status remains local and conservative but cannot progress to verified finality. | `govstack_status_views.py`/`govstack_views.py` project local attempt/reconciliation state without provider dispatch/poll. | Route P2G through the shared dispatcher and recovery workflow while retaining local notification as separate, non-authoritative state. |
| **High** | A reconciliation report risks tenant or sensitive-data leakage if added naively. | There is no existing dedicated report route, authorization implementation, or cross-tenant test. | Register one namespaced read-only route using existing tenant/BB authorization, mandatory tenant query filters, bounded pagination, and redacted classifications only. |
| **Medium** | Existing state meanings and migration rollout are at risk. | Batch completion, callback, and lifecycle terms have pre-existing semantics. | Use expand-migrate-contract sequencing, additive state/projection, nullable/backfill phases, duplicate preflight, and no change that equates legacy `completed` with verified provider settlement. |
| **Medium** | Helper-focused tests leave races and runtime paths uncovered. | Current new tests directly invoke lifecycle/helpers. | Add request-to-task-to-adapter integration tests, concurrency/duplicate-delivery tests, migration-upgrade tests, and negative binding/privacy tests. |

## Corrected implementation plan

### 1. Freeze contracts and compatibility mapping

Before implementation, inventory the current model choices, API response envelopes, callback flow, task signatures, and existing batch/instruction states. Add an explicit operation type and a compatibility mapping that keeps legacy local completion distinct from provider-verified settlement. Existing response fields and status codes remain compatible; new settlement/reconciliation fields stay additive and conservative.

### 2. Add explicit provider resolution

Introduce a dedicated provider registry/factory keyed by **tenant and operation**. It must support an injected deterministic adapter only in tests. In normal application execution, missing or disabled configuration must create a bounded unavailable/uncertain or review outcome without calling a provider and without pretending that payment settlement occurred. Store a redacted provider identity/configuration version only; never store credentials.

### 3. Add one durable submission boundary

Create a typed operation command and shared orchestrator that reloads the `PaymentAttempt` and related business object under a row lock, validates immutable binding fields, rejects terminal/review/accepted-finality attempts, persists a submission intent, invokes the adapter outside the lock where feasible, and then reacquires the attempt before calling `PaymentLifecycleService.record_provider_result()`. This service is the only place that may invoke `submit()` and record provider results. It must be reusable by G2P bulk, prepayment, and P2G workflows.

### 4. Add status-first recovery and durable claims

Claim due uncertain/retryable work with a database-backed owner token, expiry, generation/version, and bounded retry counter. Call `get_status()` first using the stored correlation identity. Persist every poll through the finality recorder/reconciliation boundary. Only a persisted explicit retryable outcome can schedule a subsequent submission. Unknown, malformed, unverified, conflicting, terminal, accepted-finality, and review outcomes never resubmit blindly.

### 5. Wire all three workflows while keeping callbacks separate

The real G2P bulk worker, prepayment entry path, and P2G transfer path must create or retrieve a stable attempt and enqueue the shared orchestrator only after their durable transaction commits. Callback delivery remains independently queued, retried, and dead-lettered; callback success must never be used as settlement evidence. Existing no-provider/harness behavior should remain feature-disabled and fail closed until explicit configuration exists.

### 6. Add persistent HTTP idempotency and batch ownership

Add an idempotency ledger scoped by tenant, method, normalized path/operation, and key. It stores the canonical request fingerprint, lifecycle state, response status/body/headers, and timestamps under a unique constraint. Matching duplicates replay exactly; same-key changed payloads conflict before side effects; concurrent first writers converge on one durable row.

Persist batch or instruction ownership using existing models where possible or a narrow lease table otherwise. Policy evaluation remains pure, but applying pause/kick-back, partial retry, and settled-item exclusion occurs under database locks. An expired owner cannot commit after lease loss, and a recovery worker can reclaim safely after expiry.

### 7. Add governed reconciliation reporting

Add and register a namespaced, tenant-authorized, read-only reconciliation/mismatch route. It must query observations/reconciliations only through mandatory tenant scope, return bounded classifications and stable identifiers, paginate, and exclude raw provider payloads, credentials, payee functional identifiers, and cross-tenant data.

## Required migration and deployment constraints

| Constraint | Required treatment |
|---|---|
| Existing data | Add new tables/fields additively and nullable/defaulted first. Backfill only from authoritative local fields; mark ambiguous legacy rows for review. |
| Uniqueness | Scope uniqueness by tenant and operation/business object. Perform duplicate preflight before imposing constraints on request IDs, provider IDs, event IDs, or idempotency keys. |
| Rolling deployment | Use expand → deploy compatible code → backfill → validate/index → contract sequencing. Old tasks must continue to deserialize and be safe under duplicate delivery. |
| State values | Do not redefine legacy batch/instruction `completed` as verified settlement. New lifecycle/reconciliation state remains an explicit projection. |
| Feature activation | Provider dispatch stays disabled/unavailable by default. Existing local validation, callbacks, audit fields, and response envelopes remain stable until configuration is explicit. |
| Privacy | Persist only bounded/redacted replay, audit, and reconciliation material. Do not add raw provider payloads, secrets, or unsafe callback targets. |
| Rollback | Avoid destructive schema changes; older code must tolerate additive rows and feature flags until parity is demonstrated. |

## Required validation matrix

| Layer | Required evidence |
|---|---|
| Provider selection | Missing/disabled configuration fails closed; explicit deterministic injection is test-only; no default mock provider. |
| Dispatch wiring | Actual G2P bulk, prepayment, and P2G paths create/reuse one attempt, call adapter once, record each result, and preserve callback separation. |
| Exact binding/finality | Wrong tenant/operation/request/amount/currency/event cannot settle an attempt; only verified exact observations establish finality. |
| Status-first recovery | Timeout/unknown poll first; settled/rejected/review work never resubmits; explicitly retryable work resubmits once under a durable claim. |
| HTTP idempotency | Matching request replay, changed-payload conflict, cross-tenant/route separation, in-progress policy, and concurrent first-writer behavior. |
| Batch leases | Concurrent claim, expiry takeover, stale-owner prevention, pause/partial policy, and settled-item exclusion under lock. |
| Reconciliation route | URL resolution, authorization, cross-tenant denial, pagination, classification, and absence of sensitive fields. |
| Migration/compatibility | Fresh and upgrade migration application, legacy rows, duplicate preflight, task signature compatibility, and existing Payments regressions. |
| Security/privacy | Redacted logs/audits/reports, bounded callback behavior, malformed provider result handling, and no external side effect on conflict/unavailable/terminal attempts. |

> **Stage 2 conclusion:** Implement the corrected plan, not the Stage 1 proposal verbatim. The implementation must first establish typed exact binding, explicit provider resolution, durable ownership, and backward-compatible migration behavior. No provider, staging, official-suite, conformance, certification, testing-site, or submission result is implied by this code-review record.

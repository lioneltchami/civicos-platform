# Item 02 — Payments production-wiring remediation plan

**Date opened:** 2026-08-18

**Stage 3 integration update:** 2026-08-19
**Scope:** Internal repository engineering only. This record excludes staging, official-suite, portal, credentials, production-provider configuration, and external submission work.

## Non-negotiable controls

| Existing control | Required disposition |
|---|---|
| Provider-neutral lifecycle vocabulary, evidence validator, and conservative status model | **Retained; do not weaken** |
| Callback outbox safety, bounded local retry/review triage, and no-blind-retry boundary | **Retained; do not weaken** |
| Explicit external-evidence and non-certification boundaries | **Retained; do not change** |

## Original gaps and final Stage 3 disposition

| Classification | Exact gap | Stage 3 disposition | Remaining closure work |
|---|---|---|---|
| **Must-wire-now** | Provider finality was not durable or fail-closed. | **Partially implemented.** `ProviderResult` now carries bounded observation/event and verification metadata. `ProviderObservation` is persisted by migrations `0030`/`0031`; `PaymentLifecycleService.record_provider_result()` accepts settlement/rejection only for a verified, stable, exact-bound observation, and otherwise moves the attempt to review, uncertain, or retryable. | Connect a configured provider/source adapter to the G2P bulk and prepayment task entry points, invoke `submit`/`get_status`, and feed its results through `record_provider_result()`. No configured external provider is supplied by this repository change. |
| **Must-wire-now** | Timeout/uncertain work did not have a source-status-before-retry path. | **Not closed.** The new recorder classifies timeout/network/uncertain outcomes as `uncertain` and does not settle them. | Poll authoritative provider/source status before resubmission; reconcile the result; then retry only work that remains eligible. Existing sweep code must be connected to the adapter. |
| **Must-wire-now** | Reconciliation and tenant-scoped status/mismatch reporting were disconnected from durable lifecycle evidence. | **Partially implemented.** Provider-result recording writes a `PaymentReconciliation` row. The existing tenant-scoped P2G transfer-status route now projects conservative `settlementStatus` and `reconciliationStatus` fields from the matching `PaymentAttempt`. | Add and test a governed tenant-scoped reconciliation/mismatch report route and connect it to authoritative source feeds. |
| **Must-wire-now** | Canonical idempotency was not uniformly adopted at HTTP entry points. | **Primitive implemented; integration open.** `govstack_http_idempotency.py` provides route-scoped, canonical tenant/method/path/key/payload fingerprints and first-writer/replay/conflict decisions. | Adopt the primitive in each G2P, prepayment, and P2G HTTP entry point with persisted replay bodies and concurrency tests. |
| **Must-wire-now** | Batch pause/kick-back and settled-item-safe partial retry were missing. | **Primitive implemented; integration open.** `govstack_batch_policy.py` supplies deterministic threshold, pause, retry-selection, and lease primitives. | Persist leases and apply the policy in the bulk worker; retain settled-item exclusion under concurrent retries. |
| **Must-wire-now** | P2G local notification could be mistaken for financial settlement. | **Partially implemented.** P2G continues to create a lifecycle attempt in explicit review rather than treat local notification as provider settlement; transfer-status now exposes the conservative internal projection when an attempt exists. | Source and verify provider/source finality for the P2G attempt before displaying a final settlement result. |
| **Must-wire-now** | Provider/reconciliation/finality regression coverage was incomplete. | **Partially implemented.** Three focused regression tests cover verified finality, unverified review, and timeout-to-uncertain behavior; they also verify reconciliation status after the transition. | Add configured-adapter, retry-polling, batch-policy, canonical HTTP idempotency, route authorization, and mismatch-report coverage. |
| **Deferred** | Operator SLA/escalation and expanded manual replay console. | **Deferred by design.** | Address after the internal provider-dispatch and route-integration work is complete. |

## Implemented artifacts

| Area | Artifact | Implementation effect |
|---|---|---|
| Provider result contract | `apps/payments/govstack_provider.py` | Adds stable observation identity, verification flag, and verification method while retaining fail-closed defaults. |
| Finality evidence | `apps/payments/govstack_models.py`; migrations `0030_provider_observation.py` and `0031_alter_providerobservation_created_at_and_more.py` | Persists immutable provider observations with exact-binding/finality constraints. |
| Lifecycle bridge | `apps/payments/govstack_failure_services.py` | Persists provider results, prevents unverified finality, classifies non-final outcomes, and records reconciliation after the committed transition. |
| Status projection | `apps/payments/govstack_status_views.py`; `apps/payments/govstack_views.py` | Projects conservative settlement/reconciliation information only within the existing tenant-scoped P2G status response. |
| Supporting primitives | `govstack_reconciliation.py`; `govstack_http_idempotency.py`; `govstack_batch_policy.py`; `providers/deterministic.py` | Supplies deterministic, review-safe integration seams; none is evidence of a real provider, staging run, official test, certification, or submission. |
| Regression coverage | `apps/payments/tests/test_item02_production_wiring.py` | Tests verified settlement, unverified review, timeout uncertainty, and resulting reconciliation state. |

## Final isolated validation evidence

The final validation was run from a refreshed archive of the uncommitted production-wiring worktree in the isolated sandbox on **2026-08-19**. The migration command reported **“No changes detected in app 'payments'”**. The focused lifecycle, recovery, primitive, task, P2G, and new finality suites completed with **199 tests passed**.

| Evidence | Result | Record |
|---|---|---|
| Payments migration-drift check | Passed: no pending model migration detected | `docs/govstack/testing/evidence/item-02-payments-production-wiring-validation-20260819.log` |
| Focused Payments regression suite | Passed: 199 tests | `docs/govstack/testing/evidence/item-02-payments-production-wiring-validation-20260819.log` |
| New finality regressions | Passed: verified settlement, unverified review, timeout uncertainty, reconciliation-state assertions | `apps/payments/tests/test_item02_production_wiring.py` |

> The migration command emitted a database-history connectivity warning because the isolated workspace has no usable non-test PostgreSQL credential. This did not prevent Django from reporting no model changes; the test command then created and destroyed its own test database successfully.

## Current acceptance conclusion

| Internal acceptance item | Current status |
|---|---|
| Existing primitives and safety boundaries preserved | **Aligned** |
| Durable verified provider-finality gate | **Implemented and regression-tested** |
| Provider-backed settlement/finality invoked by G2P and prepayment runtime flows | **Open** |
| Status-before-retry and reconciliation-before-resubmission | **Open** |
| Tenant-scoped reconciliation/status/mismatch lifecycle | **Partially implemented** |
| Canonical HTTP idempotency across G2P/prepayment/P2G | **Open — primitive only** |
| Batch pause/kick-back and safe partial retry | **Open — primitive only** |
| P2G local-notification versus financial-finality separation | **Partially implemented** |
| Full internal route and configured-adapter regression coverage | **Open** |

> **Stage 3 conclusion: partially aligned / remediation still required.** The committed internal finality boundary is stronger and validated, but this does **not** establish a real provider integration, staging validation, official-suite result, conformance, certification, testing-site readiness, or submission readiness.

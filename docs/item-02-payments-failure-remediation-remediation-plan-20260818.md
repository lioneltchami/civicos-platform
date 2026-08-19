# Item 02 — Payments Failure Remediation: Remediation Plan

**Date:** 2026-08-18
**Controlling source:** `docs/item-02-payments-failure-remediation-verification-20260818.md`
**Current conclusion:** **Not ready.** The lifecycle foundation is reliable locally, but provider-backed execution, reconciliation, batch policy, route idempotency, operational controls, and current official evidence remain incomplete.

## Preserve as aligned foundations

Do not rework durable `PaymentAttempt`, `CallbackDelivery`, and `PaymentReconciliation` persistence; guarded lifecycle transitions; scoped lifecycle idempotency; typed outcomes; callback outbox/retry/dead-lettering; uncertain/retryable triage registration; append-only implemented-path audit vocabulary; row locking/transaction-on-commit dispatch; callback URL safety; migrations `0027`–`0028`; or the existing focused local regression suite. The current scheduler jobs are a safe local deferment mechanism, not provider polling/reconciliation.

## Remaining gaps

| Gap | Evidence | Concrete closure work |
|---|---|---|
| Provider execution and settlement finality | `apps/payments/govstack_tasks.py`, `govstack_failure_services.py`, and the controlling verification record local review states rather than provider outcomes. | Add injectable provider adapter with persisted request/response/transaction IDs and provider-derived settled/rejected/uncertain transitions. |
| Execution failure taxonomy and safe retry | External invalid-account, insufficient-funds, rejection, timeout, network, compensation, and provider status-query evidence are absent. | Map provider outcomes; poll/reconcile before retry; prove no duplicate settlement; dead-letter unresolved cases to owned review. |
| Reconciliation/status APIs | Internal reconciliation exists without provider/source feeds, resolution workflow, report, or query endpoint. | Add feed ingestion, auditable mismatch resolution, and deterministic reconciliation/status reporting API tests. |
| Batch thresholds and partial resubmission | No typed provider item outcomes, threshold/pause/kick-back policy, or settled-item exclusion. | Add configurable policy, review/pause state, and tests preventing resubmission of settled instructions. |
| Canonical HTTP idempotency | Lifecycle semantics are not proven at all G2P/prepayment/P2G HTTP entry points. | Apply canonical replay/conflict/concurrency behavior to every relevant route and test slash/route variants. |
| External audit and operations | Provider/external-resolution/compensation/operator events, monitoring, queue age, ownership, SLA and runbook are incomplete. | Add non-PII events, authorised review/replay control, operational procedure and observability evidence. |
| Route/proxy/tenant and official evidence | Exact 301/500 diagnosis, tenant-isolation proof, current adapter/official-suite evidence are absent. | Capture raw request/response/proxy traces, add adversarial tenancy tests, rerun official harness against non-production adapter with full traceability. |

## Acceptance checklist — latest verification status

| Acceptance item | Status |
|---|---|
| GovStack error handling, reconciliation and orchestration alignment | **Partially aligned** |
| Explicit invalid-account, insufficient-funds, timeout, duplicate, partial-batch and network scenarios | **Partially aligned** |
| Retry, compensation, dead-letter and kick-back | **Partially aligned** |
| Reconciliation and success/failure status reporting APIs | **Partially aligned** |
| Full audit/logging trail | **Partially aligned** |
| Single and bulk/batch failure tests | **Partially aligned** |
| Scheduler retry/delayed-remediation integration | **Partially aligned** |
| No happy-path regression | **Partially aligned** |

## Stage 3 final-status update

**Pending.** Repository-only changes may close adapter abstractions, local policy, route, reporting, test, audit and runbook gaps. A real provider/source integration, authorised non-production adapter, current official-suite run and deployment-level tenant evidence remain separately required before any full-green claim.

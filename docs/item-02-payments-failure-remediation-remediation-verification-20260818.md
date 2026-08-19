# Item 02 — Payments Failure Remediation: Independent Verification

**Date:** 2026-08-18
**Outcome:** **Partially aligned / remediation required**

## Independent conclusion

Two totally new blind reviewers assessed the final remediation bundle against the remediation plan. Both confirmed meaningful deterministic repository-only additions but found the acceptance checklist not fully green. No provider, staging system, official suite, portal, credential, or external service was accessed.

## Verified improvements

| Area | Status | Evidence |
|---|---|---|
| Deterministic provider contract primitives | **Partially aligned** | `govstack_provider.py` and `providers/deterministic.py` define local submit/status/compensate behavior without credentials or network access. |
| Failure/idempotency/batch primitives | **Partially aligned** | Local timeout-status, canonical idempotency, status normalisation, batch settled-item exclusion, and review primitives have deterministic test evidence. |
| Local evidence boundary | **Fully aligned as a control** | Evidence validator and manifest expressly label outputs repository-only and do not claim real provider, staging, official or portal proof. |
| Existing lifecycle/outbox safeguards | **Fully aligned as foundations** | Durable lifecycle, callback outbox, guarded transitions, typed outcomes, local audit vocabulary and safe scheduler deferment remain preserved. |

## Remaining gap status

| Remediation gap | Status | Why it remains open |
|---|---|---|
| Provider execution and settlement finality | **Partially aligned** | No integrated real-provider request/response/transaction/finality path or authorised non-production run exists. |
| Failure taxonomy and safe retry | **Partially aligned** | Local primitives exist; end-to-end provider invalid-account, insufficient-funds, rejection, network, compensation, status-before-retry and duplicate-settlement proof does not. |
| Reconciliation/status APIs | **Still missing** | No provider/source feed ingestion, mismatch-resolution workflow, or integrated reporting/query API evidence was verified. |
| Batch threshold and partial resubmission | **Partially aligned** | Local policy primitive exists; integrated provider-item outcomes, pause/kick-back state and route/task resubmission proof are absent. |
| Canonical HTTP idempotency | **Still missing at route level** | Helper behavior exists but was not proven at every G2P/prepayment/P2G route, slash variant, or concurrent request path. |
| External audit and operations | **Partially aligned** | Local vocabulary/runbook exists; enforced authorisation, provider/external/compensation events, monitoring, ownership, queue age and SLA evidence are incomplete. |
| Route/proxy/tenant and official evidence | **Still missing** | No raw 301/500/proxy traces, deployment-level tenant evidence, current reproducible official-suite pass, or approval evidence exists. |

## Acceptance checklist

All eight original acceptance items remain **Partially aligned**, except that reconciliation/status reporting is independently assessed as **Still missing** at the integrated API/feed level. The deterministic additions do not create a full-green result because they are not route-integrated, provider-backed, deployed, or officially verified.

> The new deterministic primitives are useful safety and design controls. They are **not** evidence of provider settlement, staging execution, official conformance, certification, or testing-site submission.

## Required next conditions

Closure requires: route integration and complete deterministic coverage; authorised non-production provider/source endpoints and synthetic data; immutable deployed candidate/configuration identity; raw proxy/tenant traces; current pinned official-suite execution with retained raw output; owned operational approvals for replay/compensation; and only then any approval-gated testing-site action.

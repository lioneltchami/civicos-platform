# Item 02 — Payments Failure Remediation: Blind Code Review and Remediation Plan

**Date:** 2026-08-18  
**Reviewed revision:** `d2b30e2f10979fa66b476e8ffb6857c941183707`  
**Input contract:** Two fresh reviewers received only the current sanitized CivicOS source snapshot and [`item-02-payments-failure-remediation-gap-analysis-20260818.md`](item-02-payments-failure-remediation-gap-analysis-20260818.md). They did not receive the Stage 1 conversations, raw Stage 1 outputs, official-source bundle, or implementation discussion. No code, test, container, or external artifact was executed in this review.

## Review conclusion

Both blind reviewers independently rate Item 02 **Not ready**. They confirm that the Stage 1 gap analysis is conservative and materially accurate. The API, persistence, security, and append-only-audit foundations are real strengths. They do not, however, establish safe financial finality or the required failure-remediation lifecycle.

The central implementation distinction is as follows:

> A local validation result, local bill state, or recorded request must not be treated as proof that an external provider accepted or settled funds. Payment completion requires a traceable financial lifecycle with provider/source outcome, uncertain-completion recovery, reconciliation, durable callback delivery, and auditable remediation.

The remediation sequence below is therefore the controlling implementation brief for Stage 3. It consolidates the two blind reviews, corrects minor scope ambiguities in Stage 1, and deliberately avoids claiming current official-suite passage or certification.

## Confirmed strengths to preserve

| Area | Evidence | Why it should be preserved |
|---|---|---|
| Dedicated GovStack route surface | `config/urls.py`; `apps/payments/govstack_urls.py` | Separates GovStack G2P, voucher, and P2G contracts from internal payment routes. |
| Request validation and controlled error shaping | `apps/payments/govstack_views.py`; `apps/payments/govstack_exceptions.py` | Serializer validation, flattened error envelopes, tenant/header logic, and narrow exception conversion provide a useful API boundary. |
| Commit-safe asynchronous intake | `BulkPaymentView.post` and `PrepaymentValidationView.post` in `apps/payments/govstack_views.py` | `transaction.on_commit` prevents workers from consuming uncommitted acceptance records. |
| Local transactional locking and duplicate-row protection | `apps/payments/govstack_models.py`; `apps/payments/govstack_services.py`; `apps/payments/govstack_tasks.py` | Database uniqueness, `select_for_update()`, and terminal guards reduce duplicate local processing. They must be extended, not removed. |
| Callback transport hardening | `apps/payments/govstack_tasks.py` callback URL validation/dispatch | HTTPS-only policy, unsafe-address rejection, and redirect disabling are important SSRF controls. Durability must be added without weakening them. |
| Append-only audit persistence | `GovStackPaymentAuditEntry` in `apps/payments/govstack_models.py` | Immutable audit storage is a strong integrity foundation; it needs a fuller event vocabulary and more call sites. |
| Existing test surfaces | `apps/payments/tests/test_govstack_bulk_payment.py`, `test_govstack_p2g.py`, `test_govstack_tasks.py`, and scheduling tests | Existing boundary and happy-path tests provide regression anchors for the new lifecycle work. |

## Confirmed critical findings

| ID | Finding | Concrete review evidence | Required resolution |
|---|---|---|---|
| CR-C1 | **Settlement finality is conflated with local validation/notification.** | `process_bulk_payment_batch` marks a G2P instruction completed after local active-beneficiary lookup; `create_transfer_request` and `mark_bill_paid` can advance P2G local state without a distinct settlement-verification lifecycle. | Introduce a provider-neutral payment-attempt lifecycle. Local validation may validate eligibility/account metadata; it must not enter settled/completed financial state. |
| CR-C2 | **Uncertain completion is not represented or recoverable.** | `BulkPaymentBatch`/`CreditInstruction` do not persist provider attempt identity, unknown/uncertain status, timeout evidence, or status-poll/reconciliation outcome. | Add durable uncertain state, provider/correlation identifiers, safe timeout handling, status query, reconciliation, and review/dead-letter routing. |
| CR-C3 | **Callback failure is a durable-information loss path.** | Terminal batch state is committed before a best-effort callback. Callback exceptions are logged/swallowed, with no persisted delivery attempt, backoff, replay, or terminal disposition. | Add a callback-delivery ledger with idempotent replay, retry scheduling, dead-letter/review disposition, and append-only events. |
| CR-C4 | **Financial reconciliation does not exist.** | Prepayment/P2G status and G2P result methods project local record state; no provider/source-BB comparison, mismatch state, external ID, time window, or resolution trail exists. | Add reconciliation records/service/jobs and status/reporting that distinguish local state from reconciled financial outcome. |

## Confirmed high-priority findings

| ID | Finding | Concrete review evidence | Required resolution |
|---|---|---|---|
| CR-H1 | **No batch failure-rate policy or kick-back.** | Batch task derives `completed`, `partial`, or `failed` from local counters only; no configured threshold, pause, review queue, inconsistency policy, or safe resubmission exists. | Add a configurable failure-rate/inconsistency policy with review transition and per-item reason preservation. |
| CR-H2 | **Provider-neutral failure taxonomy is incomplete.** | Missing beneficiary becomes static failure text; voucher exceptions contain some vocabulary, but G2P/P2G lacks typed invalid-account, insufficient-funds, reject, network, timeout, and uncertain outcomes. | Persist stable code, category, retryability, provider/source action, and safe user/operator message for every outcome. |
| CR-H3 | **Idempotency is record-protective, not caller replay-safe.** | Uniqueness and `IntegrityError` conversion prevent selected duplicate rows, but duplicate requests commonly receive controlled error rather than canonical original result/status. | Fingerprint canonical request payload; atomically replay same key/same payload; conflict same key/different payload; prove concurrent one-operation semantics. |
| CR-H4 | **Audit lifecycle is incomplete.** | Audit is append-only and includes intake/local/terminal actions, but successful instructions are not always audited and no provider, timeout, retry, callback, reconciliation, dead-letter, compensation, or review action is evidenced. | Extend action vocabulary and emit immutable non-PII event for every lifecycle transition and external interaction. |
| CR-H5 | **P2G mark-paid path lacks a traceable external payment event.** | `mark_bill_paid` can change bill status without a corresponding `GovStackBillPayment`/settlement event lifecycle. | Create/link a payment event that records source, identity, amount/currency, bill/tenant, verification state, correlation, and reconciliation outcome before financial finality. |
| CR-H6 | **Batch accounting is not defensive for non-uniform/empty state.** | The batch worker processes only pending rows; an empty or pre-processed batch can produce misleading totals or terminal result. | Derive final totals across the complete instruction set, reject/flag empty batches, and test all mixed/replayed states. |

## Medium-priority verification and operational findings

| ID | Finding | Required resolution |
|---|---|---|
| CR-M1 | Payments-specific periodic remediation is absent. Existing periodic setup covers annual receipts/monthly reporting, not payment retries, timeout polling, callback delivery, reconciliation, or dead-letter aging. | Register idempotent, observable Celery Beat/Scheduler tasks, or document an owned and bounded manual-deferment procedure with trigger, SLA, and escalation. |
| CR-M2 | Batch state transitions are implicit. Declared validating/processing states are not a general guarded transition service. | Centralize transition validation; persist actor/time/reason/attempt context; forbid illegal terminal-state changes. |
| CR-M3 | Pending response semantics can conceal processing lag. Local status may expose zero failures while processing is pending. | Preserve external compatibility where necessary, but distinguish pending/processing/uncertain/reconciled result internally and document an authoritative polling contract. |
| CR-M4 | 301/500 and exact deployed route behaviour remain unproven. | Add route-slash/method/header/content-type/tenant negative tests and preserve raw adapter evidence. Do not infer causality from source alone. |
| CR-M5 | Tenant isolation requires deployed-setting proof. | Retain current compatibility choices, but test mandatory tenant mode, missing tenant, and cross-tenant negative cases with production-equivalent configuration. |
| CR-M6 | Callback DNS validation has residual connect-time resolution risk. | Keep all existing address/scheme/redirect controls, test DNS/address variants, and treat any stronger “TOCTOU safe” claim as unproven absent connect-address enforcement. |

## Implementation contract

Stage 3 must implement the following work packages as a coherent but incrementally committed remediation. A provider-neutral deterministic adapter is sufficient for this item; neither real financial-provider credentials nor external money movement is authorized or required.

| Package | Required change | Completion criteria |
|---|---|---|
| IP-1: Lifecycle foundation | Add `PaymentAttempt` or an equivalent durable provider-neutral state model and a guarded transition service for G2P and P2G. Include internal request, correlation, source-BB, provider-attempt, external transaction, amount, currency, version/claim, failure code/category, retry metadata, and timestamps. | Legal transitions are atomic and audited. Local account validation cannot mark financial settlement. Duplicate worker execution cannot create two attempts or two settled operations. |
| IP-2: Adapter and error taxonomy | Add an injectable/deterministic execution and status-query adapter. Model valid settlement, invalid/inactive/mismatched account, insufficient funds, provider rejection, retryable network/rate-limit failure, timeout before/after possible acceptance, and unknown state. | Each adapter outcome maps to documented non-PII persistence, external-safe envelope/message, retryability, audit event, and source/operator action. |
| IP-3: Idempotency | Add scoped request fingerprint and canonical response/status record for bulk, prepayment, and P2G paths where applicable. | Same scope/key/payload atomically returns original response/status. Changed payload with same key conflicts deterministically. Concurrent requests create exactly one financial operation. |
| IP-4: Recovery and callback durability | Add state-aware retry/backoff/attempt limits, dead-letter/review state, callback-delivery ledger, and operator-authorized replay/kick-back path. | Callback 2xx/4xx/5xx/timeout/network/redirect cases have durable records, retry policy, audit, and terminal disposition. Uncertain payment execution is reconciled before retry. |
| IP-5: Reconciliation and batch policy | Add reconciliation/service/reporting interfaces and batch failure-rate/inconsistency policy. Rework batch result derivation across all instructions and add per-item completion audit. | Internal/provider/source state mismatch is queryable and actionable. Threshold behaviour routes batches to review safely; resubmission excludes already settled work. |
| IP-6: P2G finality repair | Rework P2G `create_transfer_request` and `mark_bill_paid` to record a distinct externally-originated payment/verification lifecycle rather than immediately claiming financial finality. | Bill status is not terminal paid until documented verification/reconciliation policy permits it. Every notification is traceable, tenant-scoped, idempotent, and auditable. |
| IP-7: Scheduler and evidence | Register safe remediation jobs and expand tests. | Due retries, uncertain polls, callback replay, dead-letter review, reconciliation, and failure-rate scans are idempotent under duplicate dispatch. Raw test output and traceability are preserved. |

## Required test matrix

The implementation must add deterministic tests that do not require external payment credentials. At minimum, the test matrix must cover the following.

| Test class | Required cases |
|---|---|
| Single-payment/provider outcomes | Valid settlement; invalid/malformed/inactive/mismatched account; insufficient funds; provider rejection; retryable network/rate-limit error; timeout before provider acceptance; timeout after possible provider acceptance; status query resolution; unresolved review/dead-letter. |
| Bulk and batch | Empty/rejected batch; all success; all failure; mixed result; threshold boundary; partial batch kick-back; duplicate dispatch; mixed pre-existing instruction state; resubmission excludes already settled instructions. |
| Idempotency/concurrency | Same key/same payload replay; same key/different payload conflict; simultaneous request submission; simultaneous worker dispatch; callback replay; P2G and prepayment variants. |
| Callback delivery | 2xx, 4xx, 5xx, timeout, network error, DNS/unsafe address, redirect rejection, retry, dead letter, manual replay, payload hash/idempotency. |
| Reconciliation/status | Provider/internal match, settled mismatch, rejected mismatch, unknown timeout, source-BB status fallback, manual resolution, reports for both success and failure. |
| Audit/security | Immutable audit rows for intake, validation, attempt, provider response, timeout, retry, callback, reconciliation, review/dead-letter, operator action, and terminal outcome; no sensitive account/beneficiary/credential value in logs/audits. |
| Scheduler/operations | Due retry, stale uncertain record, callback redelivery, reconciliation scan, dead-letter aging, duplicate scheduled dispatch, locked/claimed work, configured manual-deferment assertion where applicable. |
| Route/regression | Exact method/path/slash variants; headers/content types; malformed JSON; validation/auth/tenant/not-found errors; existing GovStack happy paths; relevant legacy Payments paths; raw result archiving. |

## Implementation boundaries

Stage 3 must remain confined to **Item 02 – Payments failure remediation**. It must not claim official conformance, certification, or testing-site readiness until raw official-suite evidence exists. It must not submit anything to the external testing site without explicit human approval. No production secrets, production data, or live payment-provider execution are in scope.

Implementation may use small focused commits, each referencing **“Item 02 – Payments failure remediation”**. It must update the original gap-analysis document’s “Stage 3 final-status update” with commit IDs, tests, remaining limitations, and honest acceptance status. An explicit deferment is acceptable only where it is bounded, justified, owned, and tested; it cannot disguise an unimplemented core financial failure path.

## Current acceptance checklist

| Checklist item | Blind-review status | Rationale |
|---|---|---|
| Aligns with current GovStack Payments BB specification, including error handling, reconciliation, and orchestration | **Not met** | API/local-state foundations exist, but provider orchestration and reconciliation are absent or unproven. |
| Explicit failure scenarios are covered | **Partially aligned** | Local validation, duplicate controls, partial counters, and voucher vocabulary exist; provider/timeout/network/settlement lifecycle does not. |
| Retry, compensation, dead-letter, and kick-back are implemented or documented | **Not met** | Celery metadata is not a durable business-remediation design. |
| Reconciliation and status APIs work for successful and failed transactions | **Partially aligned** | Local status/results exist; authoritative settlement reconciliation and recovery do not. |
| Full audit/logging trail exists for every failure path | **Partially aligned** | Append-only audit foundation exists; operation and recovery event coverage remains incomplete. |
| Tests cover single and bulk/batch failures | **Partially aligned; execution unverified** | Test source exists, but the required matrix and raw evidence do not. |
| Scheduler integration is clean or explicitly deferred with justification | **Not met** | Existing schedules are unrelated to payment remediation. |
| No happy-path regression | **Unverified** | The blind review did not execute tests and had no raw regression results. |

## References

[1]: [Item 02 Payments Failure Remediation Gap Analysis](item-02-payments-failure-remediation-gap-analysis-20260818.md)
[2]: https://specs.govstack.global/payments/6-functional-requirements.md "GovStack Payments Building Block v3.0 — Functional Requirements"
[3]: https://specs.govstack.global/payments/8-service-apis.md "GovStack Payments Building Block v3.0 — Service APIs"
[4]: https://specs.govstack.global/payments/8-service-apis/8.1-government-to-person-g2p-payments.md "GovStack Payments Building Block v3.0 — Government-to-Person APIs"
[5]: https://specs.govstack.global/payments/8-service-apis/8.3-person-to-government-apis-p2g-bill-payments.md "GovStack Payments Building Block v3.0 — Person-to-Government Bill Payments APIs"

# Item 02 — Payments blueprint remediation plan

**Date:** 2026-08-20  
**Stage:** 1 of 4 — two shared-context route-inventory and gap-confirmation analyses  
**Controlling source of truth:** `docs/item-02-payments-official-source-deep-dive-20260820.md`  
**Scope:** Internal remediation only. No external provider, staging, official suite, testing portal, credential, deployment, or submission action is in scope.

## 1. Stage 1 conclusion

Both analyses reached the same conclusion: **P02-01 through P02-09 remain open.** CivicOS has useful runtime and lifecycle primitives, but no defect meets the deep-dive’s ten-point definition of strict closure because the mounted provider-executable surface is not universally forced through one production control path.

> **Required path:** mounted mutation → trusted `PaymentScope` → idempotency reservation and immutable command → durable intent/attempt and outbox → post-commit worker → durable provider factory → claimed execution or authoritative status poll → append-only observation → lifecycle/reconciliation → authorised redacted status.

The plan below must be completed in sequence. A later step cannot be claimed closed from an isolated helper, migration, service test, or optional route path.

## 2. Executable mounted route inventory

The production mounts are `config/urls.py` → `apps.payments.urls` at `/payments/` and `config/urls.py` → `apps.payments.govstack_urls` at `/govstack/payments/`. The final implementation must derive this inventory recursively from Django resolver data and fail if any mutation lacks a classification.

| Class | Resolver-discovered GovStack route | Current boundary | P02 relevance |
|---|---|---|---|
| Read-only | `GET reconciliation/report` | `platform_scope.ReconciliationReportView.get` | P02-02, P02-08, P02-09 |
| Domain-only mutation | `POST register-beneficiary` | `RegisterBeneficiaryView` → beneficiary service | P02-02, P02-06, P02-09 |
| Domain-only mutation | `POST update-beneficiary-details` | `UpdateBeneficiaryView` → beneficiary service | P02-02, P02-06, P02-09 |
| Provider-executable mutation | `POST bulk-payment` | `BulkPaymentView` → bulk service/task path | P02-01, P02-02, P02-04, P02-05, P02-06, P02-07, P02-09 |
| Domain-only mutation | `POST prepayment-validation` | `PrepaymentValidationView` → validation service/task | P02-02, P02-06, P02-09 |
| Provider-executable mutation | `POST prepayment-validation-response` | `PrepaymentValidationResponseView` → approval/response path | P02-02, P02-03, P02-04, P02-05, P02-06, P02-09 |
| Domain-only mutation | `POST vouchers/voucher_preactivation` | `VoucherPreactivationView` → voucher service | P02-02, P02-06, P02-09 |
| Provider-executable mutation | `POST vouchers/voucher_activation` | `VoucherActivationView` → voucher service | P02-02, P02-04, P02-05, P02-06, P02-09 |
| Provider-executable mutation | `POST vouchers/voucher_redemption` | `VoucherRedemptionView` → redemption service | P02-02, P02-04, P02-05, P02-06, P02-09 |
| Read-only | `GET vouchers/voucherstatuscheck/<serial>` | `VoucherStatusCheckView.get` | P02-02, P02-08, P02-09 |
| Provider-executable mutation | `PATCH vouchers/voucherstatuscheck/<serial>` | `VoucherStatusCheckView.patch` | P02-02, P02-04, P02-05, P02-06, P02-09 |
| Provider-executable mutation | `POST bills/<bill_id>/mark-paid` | `MarkBillPaidView` → P2G service → attempt/enqueue | P02-02, P02-04, P02-05, P02-06, P02-09 |
| Read-only | `GET bills/<bill_id>` | `BillInquiryView.get` | P02-02, P02-08, P02-09 |
| Provider-executable mutation | `POST billTransferRequests` | `BillTransferRequestView` → P2G service → attempt/enqueue | P02-02, P02-04, P02-05, P02-06, P02-09 |
| Read-only | `GET transferRequests/<transfer_request_id>` | `TransferRequestStatusView.get` | P02-02, P02-08, P02-09 |

The `/payments/` include also exposes donation, fee, refund, webhook, and portal operations. Stage 3 must classify all resolver-discovered internal Payments routes. Any internal route that can initiate, settle, refund, compensate, or invoke an external financial provider is provider-executable and belongs in the mandatory command boundary; it cannot be omitted because it is not under the `/govstack/payments/` prefix.

## 3. Confirmed defect matrix

| Defect | Strictly confirmed missing behavior | Mandatory implementation and closure proof |
|---|---|---|
| P02-01 | Durable deterministic provider reconstruction is not a production-approved registry or fresh worker proof. | Add allowlisted, versioned provider factory registry and registration schema; prove fresh-worker resolution, rotation, inactive/duplicate/malformed/factory-failure rejection, and zero provider calls on failure. |
| P02-02 | `tenant_id` and nonblank checks do not prove a trusted principal/resource scope through request, command, task, observation, and report. | Add immutable `PaymentScope` from authenticated caller + tenant/resource authorization; test missing/blank/unknown/conflicting/forged/cross-tenant scope before any mutation, enqueue, or provider call. |
| P02-03 | Prepayment validation/callback is not a real payment execution chain. | Separate validation from tenant-authorised `PrepaymentExecutionCommand`; validation alone must yield no provider call; execution must create one attempt/task/observation/reconciliation/status chain. |
| P02-04 | Claim token/generation fields do not prevent crash-after-acceptance or live-lease double submit. | Persist immutable execution intent/correlation before I/O; add single-flight ownership/heartbeat and status-poll recovery; use barrier-controlled two-worker and crash tests. |
| P02-05 | Recovery is not proven exclusive or intent-first across all workflows. | Add one `RecoverPaymentAttemptCommand` that inspects unresolved intent/correlation, polls before resubmit, and is used by all retry/replay/due/reconciliation paths; test source architecture and workflow matrix. |
| P02-06 | Ledger primitive is not an enforced pre-side-effect guard on every provider-executable route. | Add required mutation wrapper/base with canonical key/fingerprint, durable command link, stable replay/in-progress behavior, and resolver-driven all-route tests. |
| P02-07 | Batch lease primitives do not prove owner generation, heartbeat, takeover fencing, child accounting, or non-final treatment of uncertain children. | Add `BatchExecutionLease` conditional lifecycle and policy/audit records; prove competing workers, expiry/takeover, mixed children, threshold pause, retry, and exactly-once finalisation. |
| P02-08 | Status/reconciliation lacks complete principal-based tenant isolation, DTO redaction, deterministic pagination, and no-side-effect proof. | Add tenant-scoped attempt-status/report surfaces backed by `PaymentScope`, public DTO allowlist, cursor pagination, and request-security tests. |
| P02-09 | Canonical admission/outbox/runtime can be bypassed by legacy route/service paths. | Enforce resolver-derived classification and a single `PaymentCommandService.admit()` path; add architecture checks and table-driven mounted rollback/commit/replay/status tests. |

## 4. Locked implementation order

### Step 1 — Inventory first

Create a resolver-recursive inventory helper/test that expands `URLResolver` include chains, namespaces, HTTP methods, and DRF actions. Store the approved classification in code, not a standalone document. The test must fail for unclassified mutating routes and for provider-executable routes missing the canonical mutation boundary. This step unlocks P02-06 and P02-09 but does not close them alone.

### Step 2 — Shared trusted scope and admission base

Implement `PaymentPrincipal` and immutable `PaymentScope` resolution in one DRF permission/mixin. Production routes must derive scope from a registered authenticated caller and tenant/resource authorization; no raw header/body tenant can be trusted. Wrap both provider-executable and domain-only mutations with an explicit classification-aware base. This establishes the P02-02 prerequisite.

### Step 3 — Immutable command, idempotency, and outbox

Introduce a durable command that carries `PaymentScope`, operation, canonical idempotency key/fingerprint, request identity, payload reference, and exact status. Reserve it atomically before mutation. Link the command to attempt/batch as applicable and emit a single outbox/task handoff in `transaction.on_commit`. This is the sole implementation step permitted to make a provider-executable request eligible for worker dispatch.

### Step 4 — Versioned provider-factory registry

Replace test-oriented factory assumptions with an allowlisted factory registry, schema version validation, active-window selection, operation/tenant scope, and durable configuration reference. Only a worker may resolve an adapter. Request views and domain services must never import provider implementations or call adapter methods.

### Step 5 — Execution intent, durable claim, and exclusive recovery

Persist an execution intent before I/O. Claims require owner token, generation, expiry, and renewal/single-flight behavior. An unresolved intent or provider correlation always drives authoritative status polling before any new submit. All retry, replay, timeout, and reconciliation recovery paths use the same command.

### Step 6 — Real prepayment execution command

Keep prepayment validation state-only. Permit one payment execution command only after authorised validation/approval; use the command/outbox/runtime path and preserve non-final treatment of uncertainty.

### Step 7 — Live batch lease and accounting

Build owner/generation-fenced batch acquire/renew/takeover/finalise operations. Every child must have an authoritative terminal observation or explicit review result before batch finalisation. Persist threshold/pause/retry/return-funds decisions as audit events.

### Step 8 — Authorised redacted status and reconciliation

Expose generic attempt status and reconciliation reports only through `PaymentScope`. Use tenant-scoped repositories, public DTO allowlists, deterministic cursor ordering, and zero-side-effect reads.

### Step 9 — Full resolver-driven matrix

Run a table-driven mounted test matrix for every classified provider-executable route. It must cover trusted scope, idempotency, rollback/commit, duplicate delivery, provider settlement/reject/timeout, stale worker, recovery, observation/reconciliation, and read isolation. Only after this matrix passes may an independent verifier evaluate P02 closure.

## 5. Mandatory test fixtures and assertions

The deterministic provider double must support scripted accept-then-crash, delayed status, duplicate submit, status settlement/rejection, conflicting event replay, and provider-correlation collision. Tests must use `TransactionTestCase` when asserting `on_commit`, concurrent first-writer behavior, worker handoff, claim races, or lease takeover.

| Invariant | Required proof |
|---|---|
| No provider activity before commit | Hold an outer transaction, make mounted request, assert zero enqueue and adapter calls, then commit and assert one handoff. |
| Safe replay | Same tenant/operation/key/payload returns stable response and produces no second command/attempt/task/provider call. |
| Cross-tenant safety | Missing, conflicting, forged, unknown, or wrong-tenant requests have a 4xx response and create no sensitive side effect. |
| Fail-closed uncertainty | Timeout, malformed evidence, unavailable provider, or ambiguous status yields uncertain/review with a persisted observation/reconciliation record, never settlement. |
| Crash safety | Accepted-submit crash, duplicate delivery, expiry takeover, and stale completion create at most one accepted observation and never a duplicate external submit. |
| Read safety | Status/report reads are authorised, redacted, deterministic, and create no provider/task side effect. |

## 6. Stage 1 status

All nine defects are still open. Stage 2 reviewers must receive only the current codebase and this plan. They must reject changes that add isolated helpers without wiring the resolver-discovered provider-executable routes through the required chain.

## References

[1]: `docs/item-02-payments-official-source-deep-dive-20260820.md` — mandatory blueprint, especially sections 2–5.

[2]: `config/urls.py`, `apps/payments/urls.py`, and `apps/payments/govstack_urls.py` — actual Django mounted route sources.

[3]: `docs/item-02-payments-strict-remediation-verification-20260820.md` — prior strict verification showing the existing foundations did not close P02-01 through P02-09.

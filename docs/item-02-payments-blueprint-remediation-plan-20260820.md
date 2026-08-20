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


## Stage 3 interim implementation record — 2026-08-20

The first strictly validated implementation increment is complete but **does not close any P02 defect**. It adds `PaymentCommand` and `PaymentCommandOutbox` as additive durable models, a canonical JSON-safe command fingerprint and reservation service, and a scope resolver that treats `X-Platform-TenantId` only as a claim. In production mode, the resolver requires an active caller identity already established by the existing registered-BB permission and requires the claimed tenant to appear in that caller's nonempty `GovStackRegisteredBB.allowed_platform_tenant_ids` mapping. In explicitly disabled registered-BB harness mode, the adapter uses isolated non-authoritative synthetic scope values and still requires a canonical request identity.

`BulkPaymentView` now reserves the command before legacy batch acceptance. Duplicate commands with the same scope, operation, request identity, and fingerprint return the stable receipt path; a changed payload conflicts before batch acceptance. The reservation creates one durable outbox record and publication occurs only through `transaction.on_commit`. The publisher deliberately marks the durable handoff; provider-worker routing remains a later locked step and has not been claimed.

The final isolated run recorded **no Payments migration drift** and **196 focused Payments tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-command-boundary-validation-20260820.log`.

This increment has not yet satisfied the blueprint closure matrix. The following work remains mandatory before any P02 conclusion: resolver-driven classification across both mounted prefixes; representative-route tests for missing/forged/cross-tenant scope, replay/conflict, rollback and post-commit publication; an allowlisted versioned provider factory; immutable execution intent plus fenced claim/heartbeat and universal recovery; tenant-authorised prepayment execution; live batch lease/accounting; scoped redacted cursor status/reconciliation reads; and the full resolver-generated route-to-worker matrix. Stage 4 must not be run until those locked implementation steps are complete.


### Stage 3 interim increment — allowlisted worker-only provider registry

The second validated implementation increment replaces unrestricted dynamic adapter imports in `ProviderRuntime.resolve()` with an explicit allowlisted provider registry. `ProviderRegistration` now carries an allowlisted `factory_key`, `schema_version`, activation window, and redacted audit metadata. Existing legacy registration rows default to an unresolved factory key/schema and therefore fail closed until an operator re-registers an approved configuration. The deterministic provider is the sole local fixture factory; its bounded outcomes/statuses are reconstructed by a fresh worker from durable JSON configuration.

The registry rejects blank scope, missing/ambiguous/expired registration, unknown factory, malformed configuration, wrong tenant/operation, and factory/type failure before provider invocation. The isolated validation reported **no Payments migration drift** and **199 focused tests passing**, including fresh-worker resolution and zero-provider-call rejection cases. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-provider-registry-validation-20260820.log`.

This increment advances P02-01 only as a foundation; it does not close P02-01 because mounted route → command → outbox → worker execution and factory-rotation/fresh-worker end-to-end proofs are still incomplete. P02-02 through P02-09 remain open. The next locked work is immutable execution intent, claim heartbeat/fencing, and universal status-first recovery.


### Stage 3 interim increment — immutable execution intent persistence

A third validated increment adds `PaymentExecutionIntent`, an append-only one-to-one record for the canonical attempt scope, operation, request identity, payload fingerprint, and write-once provider correlation. `reserve_execution_intent()` obtains the record atomically before provider I/O and rejects a changed payload or a different attempt for the same canonical identity. `record_provider_correlation()` accepts only the first durable correlation value. The intent model prevents canonical identity mutation and deletion after creation.

The isolated validation reported **no Payments migration drift** and **203 focused tests passing**, including the execution-intent creation, replay, conflict, and correlation-immutability cases. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-execution-intent-validation-20260820.log`.

This does not yet connect intent creation to the worker path and therefore does not close P02-04 or P02-05. Claim acquire/heartbeat/takeover fencing, worker status-first polling, stale completion handling, universal recovery task routing, and two-worker crash evidence remain mandatory before a recovery closure claim.


### Stage 3 interim increment — recovery-command task consolidation

`RecoverPaymentAttemptCommand` is now the common worker-side command used by `orchestrate_attempt()` and the existing explicit status-first recovery task. Terminal and review attempts return a bounded no-op result; uncertain attempts delegate to the existing status-first runtime path; other nonterminal attempts delegate to the provider runtime’s fenced submit-or-poll primitive. This removes the immediate divergence between the two provider runtime task entry points without introducing request-path provider access.

The isolated validation reported **no Payments migration drift** and **196 focused tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-recovery-command-validation-20260820.log`.

This is a routing increment only. It does **not** establish execution-intent creation from the canonical route/command boundary, does not connect correlated intents to authoritative provider status polling, and does not prove lease heartbeat, expiry takeover, stale completion fencing, accepted-then-crashed recovery, or complete retry/replay/reconciliation caller coverage. P02-04 and P02-05 remain open.


### Stage 3 interim increment — resolver-derived full Payments surface inventory

`resolver_inventory.py` now walks Django's active resolver and returns every callback implemented by the Payments application, including the GovStack API, internal payments, donation, portal, fee, refund, and webhook surfaces. It records the full route, name, callable, supported methods, and whether the resolver identifies the endpoint as mutating. The corresponding test proves that both the internal `/payments/` and GovStack `/govstack/payments/` surfaces are present and that discovered mutations cannot disappear from the inventory silently.

The isolated validation reported **no Payments migration drift** and **198 focused tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-route-inventory-validation-20260820.log`.

This inventory is intentionally discovery-only. It does not classify every mutation into domain-only/provider-executable/unsupported policy and does not route every executable mutation through the command boundary. Therefore P02-09 remains open and no admission-bypass conclusion is implied.


### Stage 3 interim increment — canonical mounted bulk admission interface

`PaymentCommandService.admit()` now makes trusted scope resolution and durable command/outbox reservation one explicit request-facing boundary. `BulkPaymentView` calls this canonical interface rather than independently resolving scope and then calling `reserve()`. The boundary still performs no provider I/O and publishes only the durable command handoff after commit.

The isolated validation reported **no Payments migration drift** and **198 focused tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-canonical-admission-validation-20260820.log`.

This increment improves only the mounted bulk route. It does not prove full-surface admission because voucher, bill, fee, donation, refund, and any future provider-executable route remain outside the universal `PaymentCommandService.admit()` matrix. P02-02, P02-07, and P02-09 remain open.


### Stage 3 interim increment — prepayment execution eligibility gate

`PrepaymentExecution` now provides a durable one-to-one internal admission record for a completed `PrepaymentValidationRequest`. `admit_prepayment_execution()` locks the validation record, requires completed validation with both beneficiary and financial-address checks true, reserves a unique execution key, returns a stable same-key replay, rejects a changed key, and performs no provider I/O. The validation and validation-response HTTP contracts remain unchanged.

The isolated validation reported **no Payments migration drift** and **37 focused tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-prepayment-execution-validation-20260820.log`.

This is an eligibility/admission gate only. It does not create or bind a `PaymentAttempt` and `PaymentExecutionIntent`, does not publish a worker handoff, and does not establish financial finality. P02-03 remains open.


### Stage 3 interim increment — provider-finality-aware batch decision primitive

`govstack_batch_policy.evaluate()` now distinguishes terminal settled/rejected outcomes from `retryable`, `uncertain`, `review`, and `unresolved` work. Non-final outcomes cannot produce a `completed` or `partial` terminal decision; threshold excess produces explicit `paused`; only retryable, uncertain, and unresolved IDs are eligible for retry selection. The focused primitive tests cover all-settled, terminal settled/rejected mix, each non-final state, threshold pause, and mixed rejection/uncertainty.

The isolated validation reported **no Payments migration drift** and **27 focused tests passing**. Raw output is archived at `docs/govstack/testing/evidence/item-02-payments-batch-policy-validation-20260820.log`.

This is a pure policy increment only. `process_bulk_payment_batch` does not yet build provider-finality child inputs from `PaymentAttempt`/observations or use this decision under a fenced lease, so P02-06 and P02-08 remain open.

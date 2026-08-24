# Item 02 — Payments production-wiring independent verification

**Verification date:** 2026-08-19

## Scope and isolation

This record closes the independent-verification stage for the Item 02 Payments production-wiring remediation. Two fresh reviewers independently received only an archive containing the final Payments source tree, `manage.py`, the controlling remediation plan, and a file manifest. They did **not** receive earlier gap analysis, implementation-agent output, code-review reports, repository history, raw validation logs, staging access, credentials, provider access, or testing-site access.

The reviewers were asked to treat the remediation plan as an acceptance target rather than proof, to inspect runtime call paths as well as helper modules, and to avoid any conformance, certification, staging, provider, official-suite, portal, or submission claim.

| Evidence reviewed | Disposition |
|---|---|
| Final implementation commit | `7312c9f` — **Item 02 – Payments production-wiring remediation** |
| Validation-record commit | `3dfb43c` — **Item 02 – Record Payments production-wiring validation** |
| Blind-review input | Final Payments code, `manage.py`, Item 02 remediation plan, and manifest only |
| Local validation evidence, reviewed separately from the blind package | Migration drift check reported no pending Payments model changes; focused suite completed with 199 passing tests |

> The local validation result confirms that the tested source state was internally executable in the isolated test environment. It does not demonstrate that every required runtime path is wired, nor does it demonstrate a provider, staging, official-suite, testing-site, certification, conformance, or submission result.

## Independent conclusions

| Review | Conclusion | Core determination |
|---|---|---|
| Blind reviewer A | **Partially aligned / remediation required** | The verified provider-observation finality gate and durable reconciliation recording are materially stronger, but G2P, prepayment, and P2G runtime paths do not invoke the provider/source adapter lifecycle. |
| Blind reviewer B | **Not ready** | The repository contains helper primitives and focused unit coverage, but does not demonstrate executable production wiring from live GovStack entry points through provider submission/status, reconciliation, and safe retry. |
| Consolidated independent conclusion | **Partially aligned / remediation required — not ready for a readiness or submission claim** | The reviewers agree on the blocking facts. Their wording differs only in severity framing: the implementation is a valid partial remediation, but the unresolved critical runtime integration gaps make any readiness claim premature. |

## What the reviewers verified as implemented

The reviewers found that the finality boundary is fail-closed at the lifecycle level. `ProviderResult` carries observation/event and verification metadata, and `PaymentLifecycleService.record_provider_result()` accepts settlement or rejection only when the observation is stable, verified, and exactly bound to the internal attempt. Unverified final-looking results remain non-final, and timeout, network, or uncertain results are classified conservatively rather than blindly settled.

The migration-backed observation model adds durable evidence and guards against conflicting accepted finality. The new focused regressions cover verified settlement, unverified review, timeout-to-uncertain classification, and reconciliation state after the transition. The isolated validation record reports that the Payments migration-drift check found no changes and that the focused suite passed 199 tests.

| Verified partial control | Supporting code paths | Independent assessment |
|---|---|---|
| Durable provider-observation evidence | `apps/payments/govstack_models.py`; migrations `0030_provider_observation.py` and `0031_alter_providerobservation_created_at_and_more.py` | Implemented lifecycle persistence and constraints |
| Fail-closed provider result handling | `apps/payments/govstack_provider.py`; `apps/payments/govstack_failure_services.py` | Implemented and directly regression-tested |
| Conservative P2G status projection | `apps/payments/govstack_views.py`; `apps/payments/govstack_status_views.py` | Partially implemented; it reports local lifecycle/reconciliation state where the matching attempt exists |
| Reusable idempotency, batch, and reconciliation primitives | `govstack_http_idempotency.py`; `govstack_batch_policy.py`; `govstack_reconciliation.py` | Present as primitives, but not sufficient without runtime adoption |

## Blocking findings

| Severity | Consolidated finding | Evidence observed by the blind reviewers | Required remediation |
|---|---|---|---|
| **Critical** | No configured provider/source adapter is invoked by G2P bulk, prepayment, or P2G runtime paths. | `providers/deterministic.py` supplies an adapter seam, while `govstack_tasks.py` and the relevant service paths do not demonstrate calls to `submit()`, `get_status()`, or `record_provider_result()`. | Introduce an explicit, production-safe adapter selection boundary and invoke it from each relevant background/runtime path. Persist every adapter response through `record_provider_result()` under the exact tenant/request/amount/currency binding. |
| **Critical** | Uncertain/retry recovery is not authoritative-status-before-resubmission. | Existing recovery work identifies due uncertain/retryable attempts, but the reviewed runtime does not poll provider/source status, reconcile it, then resubmit only eligibility-proven attempts. | Implement a durable, atomic recovery task that polls first, records/reconciles the result, excludes terminal work, and resubmits only explicitly eligible attempts. |
| **High** | Canonical HTTP idempotency is not enforced in the actual GovStack G2P, prepayment, and P2G endpoints. | `govstack_http_idempotency.py` is a standalone fingerprint/decision helper; reviewers found no demonstrated route adoption, persisted response replay, or concurrent first-writer protection. | Integrate canonical idempotency across every required route, persist status/body for the first response, replay exact duplicates, reject same-key changed payloads, and add concurrency tests. |
| **High** | Batch threshold, kick-back, partial retry, and lease rules are not applied in the bulk worker. | `govstack_batch_policy.py` is pure and its lease is non-durable; reviewers found no task-level call site or database-level atomic claim. | Persist batch/lease state and apply the policy inside the real bulk worker, with transactional settled-item exclusion and concurrent retry tests. |
| **High** | There is no governed tenant-scoped reconciliation/mismatch reporting route tied to authoritative evidence. | The P2G response projects one local attempt status, but it is not a dedicated tenant-authorized report and has no demonstrated source-feed integration. | Add a tenant-authorized reconciliation/mismatch endpoint or defined equivalent, backed by durable provider observations and reconciliations; test tenant isolation and mismatch classification. |
| **Medium** | P2G keeps local notification distinct from financial finality but lacks an executable progression to verified finality. | The view can project a conservative status; no runtime provider/source dispatch advances the attempt to a verified final state. | Connect the P2G transfer attempt to the same configured provider/source result and recovery path as other payment flows. |
| **Medium** | New tests exercise lifecycle helpers rather than the complete entry-point-to-finality chain. | The focused new test module imports lifecycle code directly. The reviewers found no end-to-end G2P/prepayment/P2G dispatch, status-poll, idempotency, batch, authorization, and mismatch-route coverage. | Add integration tests from live tasks/views through adapter results, reconciliation, response projection, audit, and bounded callback/retry behavior. |

## Acceptance assessment

| Acceptance item | Independent status | Rationale |
|---|---|---|
| Existing safety boundaries preserved | **Implemented** | No reviewer identified a blind-settlement regression in the new finality recorder. |
| Durable verified provider-finality gate | **Implemented** | Verified exact observations can establish finality; unverified outcomes are non-final. |
| Provider-backed finality invoked by G2P and prepayment runtime flows | **Open** | Adapter/result lifecycle is not demonstrated in actual G2P/prepayment task paths. |
| Status-before-retry and reconciliation-before-resubmission | **Open** | No authoritative provider/source poll and eligibility-gated resubmission chain is demonstrated. |
| Tenant-scoped reconciliation/status/mismatch lifecycle | **Partially implemented** | Existing P2G projection is useful but not a full governed reporting/source-feed lifecycle. |
| Canonical HTTP idempotency across G2P, prepayment, and P2G | **Open** | The primitive is not shown in endpoint execution. |
| Batch pause/kick-back and settled-safe partial retry | **Open** | The primitive is not applied with durable concurrency controls in the worker. |
| P2G local-notification/final-settlement separation | **Partially implemented** | Local notification is conservative, but there is no provider/source finality progression. |
| Full runtime and route regression evidence | **Open** | The 199-pass focused suite does not cover the missing runtime integration chain. |

## Required next work before a later readiness claim

The next remediation pass must begin with runtime adapter integration, not more standalone primitives. It should add one configured provider/source boundary that is safe by default when no provider is configured, invoke it only from durable background workflows, and route all submit/status responses through the exact-binding finality recorder. The uncertain-recovery workflow must poll first and only resubmit after an authoritative non-final eligibility decision.

The same pass must persist and enforce canonical HTTP idempotency, batch pause/lease/retry decisions, and tenant-governed reconciliation reporting in the actual route and worker paths. It then needs integration coverage that starts at each live entry point and proves verified finality, unverified review, timeout polling, retry eligibility, batch exclusion, idempotency replay/conflict, tenant authorization, reconciliation classification, and P2G response semantics.

> **Final Stage 4 determination:** Item 02 Payments production-wiring remains **Partially aligned / remediation required**. It is **not** ready to be described as fully aligned, submission-ready, certified, conformant, or officially validated. No testing-site action is authorized by this record.

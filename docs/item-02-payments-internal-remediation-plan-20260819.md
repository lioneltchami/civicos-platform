# Item 02 — Payments internal runtime-enforcement remediation plan

**Date:** 2026-08-19
**Stage:** 1 of 4 — two shared-context gap analyses
**Scope:** Internal CivicOS implementation and local deterministic evidence only. Staging, official-suite execution, provider credentials, external testing, testing-site activity, release approval, and submission are excluded.

## Source of truth and conclusion

This plan implements the internal blocker list in `docs/item-01-07-govstack-readiness-reconciliation-20260819.md` and supersedes no external-evidence gate. Two shared-context reviewers independently inspected the current Payments code, tests, Item 02 records, and the reconciliation. Both found the same result:

> **All nine named internal runtime-enforcement defects remain open and are must-fix-now.** Existing finality, redaction, callback, idempotency, lease, and fail-closed primitives must be retained and mounted into a single enforced route-to-worker-to-adapter flow rather than bypassed.

The applicable official source remains the pinned [GovStackWorkingGroup Payments Building Block][1] revision `4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a`. This internal plan makes no official-conformance claim. The official requirements model distinguishes auditable implementation evidence from observable running-system evidence; this pass produces only the former.[2]

## Mandatory defect closure plan

| ID | Defect and current proof | Concrete internal change required | Required local proof | Priority |
|---|---|---|---|---|
| P02-01 | **Provider registration is process-local.** `ProviderRuntime._providers` in `apps/payments/provider_runtime.py` is an in-memory dictionary, so web and worker processes do not share an authoritative tenant/operation registration. | Add a durable tenant-and-operation-scoped provider-registration/configuration model and migration. Resolve the selected active registration consistently in web and worker code; persist only redacted provider identity/configuration version. Missing, inactive, malformed, or ambiguous registration must remain fail-closed. | Migration/upgrade; separately initialized worker resolution; tenant/operation isolation; inactive/malformed registration; adapter-selection integration. | **Must fix now** |
| P02-02 | **G2P can create executable attempts with blank tenant scope.** G2P lifecycle construction permits empty tenant identity while provider resolution is tenant/operation scoped. | Make tenant scope immutable and nonblank at the provider-executable attempt boundary. Derive it from trusted request/business context, propagate it through bulk/batch/instruction/attempt/enqueue/task/observation/reconciliation, and reject or park scope failures before adapter invocation. | Mounted G2P route; missing/blank/mismatch scope; cross-tenant bulk; task admission; database constraint/migration; concurrent enqueue tests. | **Must fix now** |
| P02-03 | **Prepayment is not a provider-attempt flow.** Current async prepayment performs beneficiary/account validation and callbacks but does not demonstrate a `PaymentAttempt` provider submission/poll lifecycle. | Define an explicit prepayment operation with durable attempt, tenant, request/idempotency, amount/currency, provider registration, status, observation, and post-commit enqueue semantics. Keep beneficiary validation as a prerequisite only; do not treat it as settlement. | Mounted prepayment route → attempt → worker → deterministic adapter; validation failure has no side effect; uncertain poll-before-submit; callback failure; duplicate/retry; finality projection. | **Must fix now** |
| P02-04 | **No durable in-flight submission claim.** `submit_or_poll()` releases its lock for network I/O without a persisted owner token/lease/generation. | Add a durable attempt-level claim with owner token, expiry, generation/version, attempt count, and bounded retry. Claim before I/O; fence stale result writes; persist submission intent; recover expiry by poll-first behavior rather than blind resubmit. Never hold a database transaction during network I/O. | Concurrent claimant; stale owner; lease expiry/takeover; crash/retry; duplicate deterministic adapter-call prevention; migration/backfill. | **Must fix now** |
| P02-05 | **Status-first recovery is not unified.** The shared runtime polls uncertain attempts first, but other retry/recovery paths are not proven to use the same durable state machine. | Create one claim-driven recovery/orchestration command used by G2P, prepayment, P2G, due-attempt work, retry, and replay. It must poll first, persist every observation through the existing finality/reconciliation boundary, and resubmit only from an explicitly persisted retryable state. | Poll-before-submit; terminal/review/accepted-finality exclusion; unknown/malformed/conflict; recovery/replay; duplicate observation; all-workflow reuse. | **Must fix now** |
| P02-06 | **HTTP idempotency is not enforced at every required mutation.** Ledger and canonical fingerprint helpers exist but are not a universal mounted-route guard. | Create one durable route-level idempotency enforcement boundary. Scope it by trusted tenant, method, normalized operation/route, and canonical fingerprint; replay exact completed responses; conflict changed payloads; define in-progress behavior; enforce it before all required side effects. | URL inventory coverage; exact replay; changed-payload conflict; tenant/route separation; in-progress; concurrent first writer; rollback; migration-upgrade. | **Must fix now** |
| P02-07 | **G2P bulk has no mandatory live batch lease.** `BatchLease` and policy primitives exist but are not enforced by the live bulk worker. | Require durable batch claim/renewal/generation validation as the first bulk-worker action and before each child provider attempt/result transition. Apply pause, threshold, partial-retry, settled-item exclusion, expiry, and takeover policy under durable locking. | Competing claim; live-holder exclusion; expiry/takeover; stale owner/generation; renewal failure; pause/partial retry; settled exclusion; crash/retry. | **Must fix now** |
| P02-08 | **Reconciliation route trust boundary is inadequate.** The route filters using a supplied tenant header without sufficient request-level authorization, isolation, pagination, and redaction proof. | Derive effective tenant from verified request identity/permission or a trusted resource binding. Reject absent/malformed/conflicting scope before query or mutation. Add tenant-scoped query enforcement, deterministic pagination/order, and bounded/redacted response serialization. | Route resolution; missing/invalid auth; wrong tenant; header/identity conflict; permission matrix; pagination/order; empty/mismatch; sensitive-field absence. | **Must fix now** |
| P02-09 | **Helpers and optional enqueue do not enforce the live flow.** `enqueue_attempt()` and conditional G2P/P2G wiring do not prove every mounted mutation creates a scoped attempt and reaches the only worker/adapter boundary. | Expose a canonical admission command that validates request proof, idempotency, tenant, registration, and attempt creation atomically, then queues the sole permitted worker after commit. Route G2P, prepayment, and P2G through it. Encapsulate direct adapter access and fail closed when execution configuration is unavailable. | Mounted route → transaction commit → task → adapter → observation/reconciliation → status projection for G2P, prepayment, P2G; no-task-on-rollback; disabled configuration; duplicate delivery; task registration/import; URL-to-command coverage. | **Must fix now** |

## Explicit do-not-touch controls

| Existing control | Preserve requirement |
|---|---|
| `PaymentLifecycleService.record_provider_result()` finality boundary | Views, tasks, and adapters must not set financial final states directly. Verified exact observation binding remains mandatory for accepted settlement/rejection. |
| Fail-closed provider absence | Never default to the deterministic provider or simulate settlement when provider configuration is absent or invalid. |
| Conservative non-final handling | Timeout, network, unverified, uncertain, malformed, and conflicting outcomes must remain review/retry/uncertain rather than silently settled. |
| Status-before-resubmission rule | Extend it to all recovery paths; do not replace it with direct resubmission. |
| Callback/finality separation | Keep callback delivery downstream and non-authoritative for financial finality. |
| Durable idempotency/duplicate-key savepoint correction | Promote it to route-wide use; do not fall back to process-local locks. |
| Exact tenant/payment/amount/currency observation binding | Add trusted request authorization around it; do not weaken exact matching. |
| Provider-result normalization and redaction | Do not persist raw provider secrets, unbounded payloads, or payee functional identifiers in operational responses. |

## Required implementation sequence

1. Establish durable provider registration, trusted nonblank tenant propagation, canonical attempt admission, and prepayment attempt semantics.
2. Add attempt claim fencing and one shared poll-first recovery worker.
3. Enforce durable idempotency at every required mutating route and make live batch leasing mandatory in the bulk worker.
4. Bind reconciliation authorization/tenant derivation to verified request identity and add bounded reporting behavior.
5. Prove the enforced live local route-to-worker-to-adapter path with mounted-route, task, migration-upgrade, deterministic-adapter, concurrency/race, and negative-security tests.

## Stage 3 completion rule

Stage 3 may mark a defect **closed** only when the required live local path is implemented and the associated test evidence exists. A model, migration, pure helper, service, or optional enqueue alone is insufficient. Stage 4 must independently verify each `P02-01` through `P02-09` as **Closed** with concrete code and test evidence before this internal pass is complete.

## References

[1]: [GovStackWorkingGroup Payments Building Block](https://github.com/GovStackWorkingGroup/bb-payments)

[2]: [GovStack requirements model](https://specs.govstack.global/architecture/5-specification-framework/5.3-requirements-model)

## Stage 3 implementation status — 2026-08-19

The focused implementation pass added an additive `ProviderRegistration` model/migration, nonblank model-level tenant configuration for provider-executable attempts, provider-attempt claim token/expiry/generation fields, a canonical `admit_provider_attempt()` service, durable-registration checks in the runtime, and an initial BatchLease claim/fencing path in the live bulk task. These changes are committed only as **internal remediation foundations** and must not be interpreted as external, staging, official-suite, certification, or submission evidence.

| ID | Stage 3 status | Concrete implementation evidence | Remaining internal closure condition |
|---|---|---|---|
| P02-01 | **Partially implemented** | `ProviderRegistration` and migration `0034_item02_core_runtime.py`; runtime now requires an active durable registration before resolving a configured adapter. | Adapter object materialization remains process-local; worker-safe configuration/factory resolution and registration rotation/restart evidence remain required. |
| P02-02 | **Partially implemented** | `PaymentAttempt.tenant_id` is no longer blank at model schema level; canonical admission rejects blank scope. | G2P request/bulk/task propagation, trusted request derivation, legacy-row treatment, immutable scope enforcement, and mounted negative tests remain required. |
| P02-03 | **Still open** | No mounted prepayment provider-attempt route was added. | Prepayment must be admitted, enqueued, claimed, dispatched, observed, and finalized through the shared lifecycle while retaining beneficiary validation as non-authoritative. |
| P02-04 | **Partially implemented** | Attempt claim token, expiry, generation, and stale-result fencing were added in `provider_runtime.py`; network I/O remains outside the transaction. | Durable submission intent, bounded retry/recovery, expiry takeover, stale-worker and crash/race evidence remain required. |
| P02-05 | **Partially implemented** | Existing uncertain-attempt poll-first behavior now runs under the attempt claim path. | One canonical state-machine entry point must be proven for G2P, prepayment, P2G, retry, replay, and due-attempt processing. |
| P02-06 | **Still open** | Durable idempotency primitives remain present. | A single route-level guard must be enforced on the complete required mutating-route inventory with replay, conflict, in-progress, and concurrent-first-writer tests. |
| P02-07 | **Partially implemented** | `process_bulk_payment_batch` now acquires/fences an initial durable `BatchLease` owner and generation before processing. | Lease renewal, per-child revalidation, policy enforcement, stale-owner protection across transitions, and live-worker race/crash coverage remain required. |
| P02-08 | **Still open** | No new verified request-identity-to-tenant authorization boundary was added. | Reconciliation must derive effective tenant from verified request authorization, reject conflicting headers, and prove route-level isolation, pagination, and redaction. |
| P02-09 | **Partially implemented** | `apps/payments/provider_admission.py` now exposes `admit_provider_attempt()` and queues only after commit. | G2P, prepayment, and P2G mounted routes must call the canonical admission boundary; worker-only adapter access and complete route-to-worker-to-adapter coverage remain required. |

### Local validation

The refreshed isolated source snapshot passed `python3 manage.py makemigrations --check --dry-run payments` with **No changes detected** and completed **203 focused Payments tests** successfully. Raw output is retained in `docs/govstack/testing/evidence/item-02-payments-internal-remediation-validation-20260819.log`.

> **Stage 3 decision:** This pass is **not complete** against the Stage 3 closure rule. The partial implementations are retained as fail-closed foundations, but the nine defects must remain open until independent verification determines otherwise. No status is promoted to ready.

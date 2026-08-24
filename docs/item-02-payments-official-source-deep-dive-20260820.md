# Item 02 — Payments official-source deep-dive and strict closure blueprint

**Date:** 2026-08-20  
**Scope:** Internal design and evidence review only. No provider, credential, staging, deployment, official test-suite execution, portal activity, or submission occurred.  
**Official source snapshot:** `GovStackWorkingGroup/bb-payments` default branch commit `4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a`, plus the official GovStack testing requirements page.

## Executive conclusion

The fourth review does identify the missing work precisely. **P02-01 through P02-09 are not nine unrelated defects.** They are missing segments of one mandatory production control path:

> **Mounted payment mutation → authenticated and authorised tenant scope → immutable command/idempotency reservation → durable intent/attempt and outbox → post-commit worker → durable provider registration and claim → submit or authoritative status poll → append-only observation → lifecycle/reconciliation → authorised, redacted status.**

CivicOS has useful isolated foundations on this chain: `PaymentAttempt`, `ProviderObservation`, `PaymentLifecycleService.record_provider_result()`, reconciliation records, `ProviderRegistration`, a deterministic provider, an idempotency ledger, task primitives, token/generation claim fields, and focused finality tests. It does **not** yet enforce and prove this chain for every mounted provider-executable route. That is why the previous four cycles have not closed Item 02.

The official Payments specification requires authenticated/authorised initiation, validation before acceptance, duplicate-payment prevention, transaction status, reconciliation, logging, controlled batch processing, and status retrieval. It does **not** prescribe the exact claim token, outbox, idempotency, retry, or reconciliation algorithm used here. Those details are therefore **CivicOS engineering controls required to make the official requirements demonstrably reliable**, not fabricated GovStack requirement identifiers. [1] [2] [3]

| Question | Answer |
|---|---|
| Is Item 02 ready for an official claim? | **No.** The required mounted route-to-worker evidence is not present for any P02 defect. |
| Are the existing runtime changes wasted? | **No.** They are reusable foundations, but they are not a complete live-path implementation. |
| Does the official harness alone prove these controls? | **No.** The public harness is API-contract evidence; the testing site separately distinguishes API, deployment, and requirements-compliance evidence. [4] [5] |
| What blocks closure now? | A route inventory, a single command/admission boundary, trusted tenant propagation, prepayment execution semantics, an outbox/claim/recovery protocol, live batch leases, reconciliation authorisation, and deterministic mounted/race tests. |

## 1. Official baseline: what GovStack requires and what it leaves to implementation

The Payments specification marks secure bulk request receipt, pre-acceptance validation, duplicate-payment rejection, authorised and digitally signed bulk initiation, status from an event log, reconciliation, completed-transaction retrieval, and batch handling as required. It also requires the Payments Building Block to validate external account/payment status and error conditions; describes `Batch_ID`/instruction flows, provider/bank-wise batching, status push plus polling, and P2G transfer status flows. [1] [2] [3]

| Official concern | Direct official requirement / behavior | CivicOS proof that is still required |
|---|---|---|
| Initiation authority | Bulk initiation must be authorised and digitally signed; API users are authenticated through the gateway. [1] [2] | A route-level trusted principal-to-tenant binding. A caller header alone must never create or select a payment tenant. |
| Validation and duplicate prevention | Validate batches before acceptance; duplicates must not be processed and must produce an error. [1] | One canonical per-tenant operation/request-key reservation, before all side effects, plus replay and conflict behavior. |
| Payment execution | A gateway initiates/receives transactions and can accept/reject and debit/credit accounts. [2] | A durable attempt/command accepted locally, then worker-only adapter invocation after commit. Validation must not be represented as settlement. |
| Status and logging | Payment status is available from an event log; callbacks and polling are described for G2P; P2G has transfer status/update behavior. [1] [3] | Provider observations, lifecycle/reconciliation projection, and a side-effect-free authorised status endpoint for the exact attempt. |
| Reconciliation | Regular balance reconciliation, partner disputes, and completed-batch retrieval are required or described. [1] | Tenant-authorised, redacted reporting; deterministic pagination; reconciliation matching/exception state; no cross-tenant report access. |
| Batch processing | Validate, partition, queue and process bulk work; record individual transaction status. [1] [2] | An owner/generation-fenced batch lease which cannot double-process or prematurely finalize mixed/uncertain batches. |

The public harness tests selected G2P and voucher HTTP/JSON-schema scenarios. It does not, based on the published feature inventory, establish durable idempotency, two-worker claims, actual settlement finality, reconciliation, immutable audits, deployment posture, or end-to-end authorisation. The testing site explicitly treats API, deployment, and requirement-specification compliance as separate evidence dimensions. [4] [5]

## 2. The current CivicOS control path and exact breakpoints

The mounted Payments include is `config/urls.py` → `apps/payments/govstack_urls.py`. Provider-relevant paths currently include G2P bulk, prepayment validation/response, voucher lifecycle mutations, P2G bill transfer/mark-paid, and reconciliation/status reads. The current code divides this behavior between `govstack_views.py`, `govstack_services.py`, `govstack_tasks.py`, `provider_admission.py`, `provider_runtime.py`, `govstack_failure_services.py`, and `platform_scope.py`.

| Chain segment | Existing CivicOS asset | Breakpoint that prevents strict closure |
|---|---|---|
| Route and serializer | Mounted GovStack views and serializers exist. | There is no single mandatory wrapper that every provider-executable POST/PATCH must cross. Some mutations remain service-driven and success responses do not prove a durable provider observation. |
| Tenant and caller context | Header permission classes and P2G tenant helpers exist. | A trusted principal is not turned into one immutable tenant/resource scope for every G2P, prepayment, P2G, voucher, task, observation, and reconciliation path. Harness-mode fallbacks cannot be production authority. |
| Idempotency | `IdempotencyLedger`/`IdempotencyService` exist. | The primitive is not universally mounted ahead of every side effect, and no URL inventory or transaction proof confirms it protects all relevant mutations. |
| Admission | `admit_provider_attempt()` creates a durable attempt and registers post-commit enqueueing. | Routes do not all call it; admission does not yet define the sole command boundary for G2P, prepayment, P2G, and voucher payment execution. |
| Adapter resolution | `ProviderRegistration` and `ProviderRuntime.resolve()` can rebuild the deterministic adapter from durable configuration. | The path is test-oriented; there is no approved factory registry/config schema, registration lifecycle, or worker-restart/malformed/ambiguous/tenant-isolation proof. |
| Claim and recovery | Runtime persists token, generation, expiry, heartbeat, and submission intent; result persistence is fenced. | A crash after provider acceptance but before result persistence can leave unresolved intent; recovery is not proven to poll based on intent/provider correlation before any resubmit; no heartbeat ownership proof exists. |
| Observation/finality | Finality is fail-closed: verified identified outcomes may settle/reject, and unverified/timeout outcomes remain non-final. | The good service-level finality behavior is not shown for mounted mutations through task execution and recovery. |
| Batch lease | `BatchLease` primitives are present. | There is no complete owner-token/generation/renew/takeover/finalize protocol tested under competing workers and mixed child outcomes. |
| Status/reconciliation | Transfer status projection and reconciliation primitives exist. | There is no generic tenant-scoped attempt status, nor authorised report tests for absent/conflicting identity, cross-tenant reads, redaction, deterministic pages, and no side effects. |

## 3. P02-01 through P02-09: exact missing changes and proof

### P02-01 — Process-safe adapter authority

**Current state.** `ProviderRuntime.configure()` persists an `adapter_path`; `resolve()` loads it from the one active `ProviderRegistration`. The deterministic provider can be reconstructed from bounded configuration, and a process-local registry was removed. This is a sound foundation.

**What is still missing.** The registration format is not a production-approved adapter contract. It has no allowlisted factory registry, versioned schema validator, configuration rotation rule, fail-closed tenant/operation uniqueness proof, or independently initialised web-versus-worker integration proof. The current deterministic fixture proves only a local test adapter can be reconstructed.

**Required implementation.** Introduce `ProviderFactoryRegistry` with allowlisted names, versions, JSON-schema validators, and a factory method. `ProviderRegistration` stores `factory_name`, `schema_version`, non-secret configuration reference/version, tenant, operation, status, and activation window. Configuration management alone can activate a registration. Worker resolution accepts exactly one active compatible record, creates the provider only in the worker, and fails closed without retrying a submit when resolution fails.

**Closure tests.** Add a `TransactionTestCase` that makes a request in one process context, invokes the task with fresh resolver state, and proves the adapter is created from the row rather than request memory. Cover missing, inactive, duplicate-active, mismatched schema, malformed factory, wrong tenant/operation, factory exception, and configuration rotation. In every rejected case assert **zero** adapter calls and exactly one non-final configuration observation.

### P02-02 — Trusted tenant and resource admission

**Current state.** Models carry tenant identifiers and some P2G permissions/header logic exist. `admit_provider_attempt()` rejects a blank tenant.

**What is still missing.** A nonblank string is not a trusted tenant. There is no universal route boundary that derives the tenant from an authenticated principal/registered caller plus authorised resource binding, freezes it into the command, and proves the task/reconciliation cannot be changed by caller input or stale work.

**Required implementation.** Define `PaymentPrincipal` and `PaymentScope` resolution once in a DRF permission/mixin. Production requests must have a registered caller identity and an authorised tenant/resource relationship. The resolver returns an immutable scope object; views pass it to commands; tasks re-load and verify it against the persistent command/attempt; objects are queried by `(tenant_id, object_id)` under lock. Harness compatibility, if retained, must be an explicitly isolated test adapter, not a production fallback.

**Closure tests.** Parametrise every provider-executable route over missing, blank, unknown, conflicting, forged, and cross-tenant scope. Assert a 4xx response before state mutation, no task enqueue, no adapter call, and no observation. Prove tenant A cannot update/read/reconcile/execute tenant B’s beneficiary, voucher, bill, attempt, or report row.

### P02-03 — Prepayment means validation unless a real payment command is admitted

**Current state.** `PrepaymentValidationView` stores a validation request and dispatches validation. Existing tests prove validation/callback behavior.

**What is still missing.** A validation request is not a provider payment attempt. No prepayment transition creates a canonical `PaymentAttempt`, runs worker-only provider execution, receives an observation, reconciles it, or safely exposes its status.

**Required implementation.** Make prepayment an explicit two-state domain: `PrepaymentValidationRequest` is validation only; a distinct `PrepaymentExecutionCommand` can be created only after a tenant-authorised approval/validation state. The execution command calls canonical admission atomically, stores the validation reference and instruction identity, and uses one post-commit task. Duplicate response/approval cannot create a second execution command.

**Closure tests.** Through the mounted API, prove validation alone makes no provider call. Then approve/execute with a deterministic verified settlement, rejection, timeout, duplicate response, rollback, callback failure, retry, no provider registration, and cross-tenant request. Assert one attempt, one task, correct observation/reconciliation, no false finality, and exact status response.

### P02-04 — Durable in-flight claim and crash-safe intent

**Current state.** Attempt rows store token, generation, expiry, heartbeat, submission intent, and count; result writes are fenced by token/generation.

**What is still missing.** A provider can accept a submit after the DB lock is released and before result persistence. A crash here leaves intent but no authoritative outcome. Claim expiry can also permit a second worker to submit while the first call is still live; stale-result fencing prevents stale persistence but not duplicate external debit.

**Required implementation.** Persist an immutable `ExecutionIntent` before I/O with a command id, idempotency key, phase, correlation id, owner generation, and provider request id. First try authoritative `get_status()` whenever an old submit intent or provider correlation exists. Permit a new submit only when a state machine confirms no prior external acceptance is possible. Add heartbeat/renewal or a single-flight outbox ownership protocol; do not rely on a five-minute timestamp alone.

**Closure tests.** Use a scripted deterministic adapter to simulate: accepted submit then worker crash; delayed submit; duplicate delivery; worker A expiry then worker B takeover; stale A completion; provider timeout; provider correlation collision. Assert one external submit, status poll before any replay submit, one accepted observation, and a non-final review/uncertain state whenever authority cannot be established.

### P02-05 — One exclusive status-first recovery command

**Current state.** `status_first_recovery()` exists and uncertain attempts poll in `submit_or_poll()`.

**What is still missing.** Recovery is not proven to be the exclusive entry for G2P, prepayment, P2G, due/retry/replay, timeout, uncertain, and reconciliation-triggered states. Its decision is currently too dependent on status rather than an unresolved intent/provider correlation.

**Required implementation.** Introduce `RecoverPaymentAttemptCommand`. It reads intent/correlation, locks the attempt, rejects terminal/review/compensated states, polls authoritative provider status first, records the result idempotently, and schedules a bounded retry/review only if policy permits. All Celery retries and cron/due work must enqueue this command; no task may call `submit()` directly after an existing intent.

**Closure tests.** A source-code import/architecture test rejects direct adapter use outside runtime. A table-driven mounted/task suite proves each workflow reaches the recovery command, never resubmits terminal/review/final attempts, records duplicate provider events once, and turns ambiguous outcomes into review rather than settlement.

### P02-06 — Mandatory HTTP idempotency on every provider-executable mutation

**Current state.** The durable ledger can reserve, replay, and reject a changed payload for a tested path.

**What is still missing.** No enforced route inventory maps all G2P, prepayment, P2G, voucher payment execution, manual/fee/refund or other provider-executable mutations to that guard. There is no proof the key is reserved before mutation, returned during in-progress work, tenant-scoped, rollback-safe, and linked to the exact command/attempt.

**Required implementation.** Create `@payment_mutation(operation=...)` or a base view mixin. It requires a canonical key, normalises a payload fingerprint, resolves `PaymentScope`, reserves an idempotency row before domain mutation, replays completed results, returns a stable in-progress envelope when work remains, and links the ledger row to the immutable command. Maintain a committed route inventory and enforce it in tests.

**Closure tests.** For every inventoried route: same key/same payload; same key/changed payload; two concurrent first writers; in-progress retry; outer-transaction rollback; tenant separation; no key; malformed key; task duplicate delivery. Assert at most one command/attempt/provider submission and stable replay payloads.

### P02-07 — Live batch lease, child accounting, and recovery

**Current state.** Batch rows and lease primitives exist.

**What is still missing.** There is no proven owner-token/generation/expiry/renewal protocol applied around every child dispatch, result, policy transition, and finalisation. A late worker may process or finalise after a lease takeover; a batch may finalise while a child is uncertain.

**Required implementation.** Add a `BatchExecutionLease` service with acquire, heartbeat, revalidate, takeover, cancel, and finalise operations conditioned on `(batch_id, owner_token, generation)`. Each child attempt must have an execution intent and terminal/review reconciliation before aggregate finalisation. Define policy for failure threshold, pause, retry, review, settled exclusion, and return-funds handling; record every decision.

**Closure tests.** Barrier-controlled two-worker tests: lease A acquires; A crashes/expiry occurs; B takes over; A cannot renew/process/finalise; B completes children. Include empty batch, mixed settled/rejected/uncertain children, retryable child, reconciliation mismatch, threshold pause, and duplicate finalise. Assert exact audit/event/accounting totals.

### P02-08 — Authorised reconciliation and bounded status access

**Current state.** Reconciliation models and projections exist; some status output is available.

**What is still missing.** There is no complete request-level proof that an authenticated principal is authorised before reading or mutating reconciliation/observation data, that tenant/object isolation is impossible to bypass, that fields are redacted, or that order/pagination are deterministic and side-effect free.

**Required implementation.** Add a tenant-scoped `PaymentAttemptStatusView` and `ReconciliationReportView` using the shared `PaymentScope`. Query only through tenant-scoped repositories; expose a versioned public DTO with lifecycle/reconciliation/limited observation evidence only. Provider raw payloads, secret data, full account identifiers, and internal error detail never leave the service. Use deterministic `(created_at, id)` cursor pagination.

**Closure tests.** Assert anonymous, unauthorised, missing/conflicting scope, cross-tenant, and cross-object requests are denied before any query beyond authorised lookup. Assert public DTO allowlist, no raw provider/account/secret fields, deterministic pagination, and zero adapter/worker calls on GET.

### P02-09 — Route-to-command coverage is mandatory, not optional

**Current state.** Canonical admission and post-commit enqueue primitives exist, but legacy view/service paths still exist beside them.

**What is still missing.** There is no route-to-command proof that every provider-executable route must use the same admission/outbox/runtime chain. As long as one direct service/external call or optional runtime setting survives, the control is bypassable.

**Required implementation.** Commit an executable route inventory. Classify every endpoint as read-only, domain-only mutation, provider-executable mutation, or explicitly unsupported. Provider-executable endpoints may only call `PaymentCommandService.admit()`. Request views and domain services cannot import `PaymentProvider` or call `ProviderRuntime.resolve()`/adapter methods. Route coverage is tested using Django URL resolver data, not a manually maintained list alone.

**Closure tests.** Parameterise the resolver-discovered provider-executable routes. Patch adapter methods and Celery enqueue. Under an outer transaction assert no adapter call before commit; after commit assert exactly one task/attempt; same-key replay creates none; status read has no side effect. Add an architecture test that fails if forbidden imports/calls reappear in views/services.

## 4. Locked implementation sequence

The next remediation must not begin by adding more isolated helpers. It should be performed in this dependency order:

| Order | Deliverable | P02 defects unlocked | Gate before proceeding |
|---:|---|---|---|
| 1 | Executable mounted route inventory and classification | P02-06, P02-09 | A test enumerates routes and fails on an unclassified mutation. |
| 2 | Shared `PaymentScope` plus route admission/mutation base | P02-02, P02-06, P02-08, P02-09 | Missing/conflicting/cross-tenant scope tests prove zero mutation/task/provider calls. |
| 3 | Immutable command/idempotency/outbox schema and service | P02-02, P02-06, P02-09 | Rollback, replay, conflict, concurrent first-writer, and post-commit tests pass. |
| 4 | Versioned production provider-factory registry and worker-only adapter boundary | P02-01, P02-09 | Fresh-worker resolution and malformed/inactive/ambiguous registration tests pass. |
| 5 | Execution-intent/claim/recovery state machine | P02-04, P02-05 | Crash-after-acceptance, duplicate delivery, expiry/takeover, status-first, stale completion tests pass. |
| 6 | Real prepayment execution command | P02-03 | Validation-versus-execution mounted workflow tests pass. |
| 7 | Live batch lease/accounting policy | P02-07 | Competing-worker/takeover/mixed-child/finalisation tests pass. |
| 8 | Authorised redacted attempt/reconciliation status surface | P02-08 | Security, redaction, pagination, and side-effect-free GET tests pass. |
| 9 | Full mounted matrix and final independent verification | All | Every route has command/attempt/task/observation/reconciliation/status evidence or is explicitly non-provider-executable. |

## 5. Definition of strict closure

A future verifier may mark an individual P02 defect **Closed** only if all applicable elements below are in committed code and named tests:

1. The route is discovered from the mounted URL configuration and classified.
2. The request obtains a trusted, immutable `PaymentScope`; no raw header/body value alone defines tenant authority.
3. An idempotency reservation and command/attempt are written atomically before side effects.
4. Rollback yields no task or provider call; commit yields exactly one outbox/task handoff.
5. The worker reconstructs an authorised adapter from durable configuration and cannot use request-memory provider state.
6. The worker claims/fences execution and records an append-only provider observation before lifecycle/reconciliation projection.
7. Every unresolved intent recovers by authoritative status poll before any resubmit.
8. Batches use owner/generation fencing and cannot finalise with unaccounted or uncertain children.
9. Status/reconciliation reads are authorised, tenant-scoped, redacted, deterministic, and side-effect free.
10. A deterministic mounted/race/security test proves success, reject, timeout, replay, rollback, duplicate delivery, stale worker, and cross-tenant denial as relevant.

## References

[1]: [GovStack Payments — Cross-Cutting Requirements](https://govstack.gitbook.io/bb-payments/5-cross-cutting-requirements) (official specification; secure API, validation, duplicate, status, reconciliation, mobile and bulk-payment requirements).

[2]: [GovStack Payments — Functional Requirements](https://govstack.gitbook.io/bb-payments/6-functional-requirements) (official specification; orchestration, payment request initiation, reconciliation, batch processing, event/audit logging, security).

[3]: [GovStackWorkingGroup/bb-payments](https://github.com/GovStackWorkingGroup/bb-payments) at commit `4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a` (official service API narratives, internal workflows, API YAMLs, and OpenAPI feature tests).

[4]: [GovStack Payments G2P Bulk Workflow](https://github.com/GovStackWorkingGroup/bb-payments/blob/main/spec/9-internal-workflows/9.1-g2p-bulk-payment.md) (official workflow: pre-validation, authorisation, bank-wise batches, callbacks/polling, exception handling).

[5]: [GovStack Testing Requirements](https://testing.govstack.global/en/requirements) (official testing-site page: distinct deployment, requirements-specification, and API compliance dimensions).

[6]: [GovStack Payments G2P Service APIs](https://github.com/GovStackWorkingGroup/bb-payments/blob/main/spec/8-service-apis/8.1-government-to-person-g2p-payments.md) and [P2G Bill Payments APIs](https://github.com/GovStackWorkingGroup/bb-payments/blob/main/spec/8-service-apis/8.3-person-to-government-apis-p2g-bill-payments.md).

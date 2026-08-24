# Item 02 — Payments runtime-wiring independent verification

**Verification date:** 2026-08-19

**Stage:** 4 of 4 — two totally fresh blind code reviews

## Isolation and evidence boundary

Each verifier received a final-code archive containing the committed application source, configuration, requirements, and test code. They did not receive the remediation plan, prior gap analyses, code-review reports, implementation-agent notes, validation log, repository history, provider access, credentials, staging access, official suites, portals, or external sources. Both reviewed the code read-only and were instructed to distinguish helper presence from mounted view/task execution.

| Evidence used in this record | Scope |
|---|---|
| Independent verifier A | Final-code archive only; conclusion: **Not ready** |
| Independent verifier B | Final-code archive only; conclusion: **Partially aligned / remediation required** |
| Separate local validation evidence | Isolated migration-drift check reported no pending Payments changes; expanded focused suite passed 203 tests |

> The local test result confirms the tested source state was executable in the isolated test environment. It does not establish external provider behavior, staging behavior, official-suite results, testing-site results, certification, conformance, or submission readiness.

## Consolidated conclusion

The consolidated result is **Partially aligned / remediation required — not ready for a readiness or submission claim**. The reviewers agree on the material blockers: the repository now has stronger fail-closed provider runtime, idempotency, lease, and reporting structures, but they are not consistently enforced across the acceptance-critical live G2P bulk, prepayment, and P2G execution paths.

The two reviewers use different severity wording, but their evidence is compatible. Where a reviewer did not recognize a newly mounted route or optional call site, this record preserves the verified code fact while retaining the underlying concern about enforcement and coverage. For example, `reconciliation/report` is registered in `apps/payments/govstack_urls.py`, but it lacks route-level authorization/isolation coverage and derives tenant scope from a request header. Similarly, G2P bulk and P2G conditionally enqueue the runtime when an explicit setting is enabled, but prepayment remains a local validation path and the provider registry is process-local.

## Implemented controls verified in code

| Control | Verified implementation | Independent assessment |
|---|---|---|
| Fail-closed finality recorder | Provider observations require stable verified exact binding before accepted settlement/rejection; non-final results become review, retryable, or uncertain. | **Implemented at the lifecycle boundary** |
| Runtime polling rule | `ProviderRuntime` calls `get_status()` for uncertain attempts before a new `submit()` call. | **Implemented in the shared runtime only** |
| Disabled provider behavior | Unconfigured provider resolution records a bounded non-final unavailable outcome rather than using a default/mock provider. | **Implemented; process-local configuration remains insufficient for operational readiness** |
| P2G/G2P conditional enqueue | Explicit runtime enablement queues an attempt after transaction commit for the covered local flows. | **Partially implemented; tenant propagation and complete path coverage remain open** |
| Idempotency persistence | `IdempotencyLedger` stores canonical request fingerprints and replay material; duplicate reservation is savepoint-safe. | **Partially implemented; not endpoint-enforced** |
| Batch/reconciliation persistence | `BatchLease` schema and a registered reconciliation report route exist. | **Partially implemented; worker claim use and route-level security proof are open** |
| Focused local regressions | Expanded suite passed 203 tests after the duplicate-ledger transaction fix. | **Positive local evidence; not end-to-end coverage** |

## Blocking findings

| Severity | Finding | Verified code evidence | Required remediation |
|---|---|---|---|
| **Critical** | Provider resolution is process-local and restart/worker unsafe. | `ProviderRuntime._providers` is a class dictionary with no durable configuration, startup loading, or worker health contract. | Provide a process-safe explicit configuration/registration mechanism for every tenant/operation worker scope; continue to fail closed when absent. |
| **Critical** | G2P bulk attempts use an empty tenant scope. | `_record_bulk_instruction_lifecycle()` calls `get_or_create_attempt(tenant_id="", ...)`, while runtime resolution is tenant/operation keyed. | Propagate the validated platform tenant into bulk/instruction/attempt data; reject empty tenant scope for provider-executable attempts and test cross-tenant separation. |
| **Critical** | Prepayment is not a provider-runtime execution path. | The prepayment task completes local ID Mapper validation and callback handling, but does not create/enqueue a financial provider attempt. | Define the correct prepayment provider operation and durable attempt semantics, then route it through the common worker boundary without implying local validation is settlement. |
| **High** | There is no durable in-flight claim for provider submission. | The runtime locks to read, releases the lock for network I/O, then locks to persist the result. Two workers can submit the same pending attempt. | Add an attempt-level durable claim/token/expiry or equivalent compare-and-set state; verify duplicate delivery, concurrent workers, worker loss, and stale claim behavior. |
| **High** | Recovery is safe only when callers invoke the shared runtime. | `ProviderRuntime` polls uncertain attempts first, but legacy due-attempt triage tasks are not unified with provider poll/retry orchestration. | Centralize all uncertainty/retry scheduling through the claimed runtime worker and assert no second submission occurs before authoritative polling. |
| **High** | Durable HTTP idempotency is not universally enforced at mounted mutating endpoints. | Ledger/service exist, but no common route guard wraps bulk, prepayment, P2G transfer, and manual mark-paid before side effects. | Add one endpoint guard/decorator/service integration with exact replay, changed-payload conflict, in-progress policy, and URL-level concurrency tests. |
| **High** | Durable batch lease/policy is not used by the live bulk worker. | `BatchLease` model exists; `process_bulk_payment_batch()` does not show claim/renew/generation enforcement before work. | Require a batch claim as the first worker step, apply policy under lock, protect settled items, and test competing workers and expired lease takeover. |
| **Medium** | Reconciliation route authorization and tenant derivation need stronger proof. | The report route filters by a supplied tenant header and uses a permission class, but lacks request-level authorization/cross-tenant/redaction tests. | Derive or verify the tenant from the authorized principal and add route tests for denial, isolation, pagination, and field redaction. |
| **Medium** | Callback outbox and provider finality remain separate but callback transport still occurs synchronously in execution tasks. | Task code persists a callback row and then posts it in the same task. | Keep callback state separate from finality and move transport to a dedicated retryable delivery worker if the runtime design requires strict side-effect isolation. |
| **Medium** | Tests do not exercise every mounted route and concurrency edge. | New tests directly exercise runtime/idempotency services; existing suites do not prove complete URL→task→adapter behavior, endpoint-wide idempotency, lease claims, or multi-worker races. | Add mounted-route and task integration tests with deterministic injected adapters and database-backed concurrency coverage. |

## Acceptance assessment

| Acceptance area | Independent status | Reason |
|---|---|---|
| Verified stable exact-bound evidence can establish finality | **Implemented at central lifecycle boundary** | Direct lifecycle and runtime tests support the recorder behavior. |
| Unverified, timeout, network, and uncertain outcomes remain non-final | **Implemented in central runtime/lifecycle path** | Finality recorder and shared runtime are conservative. |
| G2P bulk provider submission and status polling | **Remediation required** | Optional enqueue exists, but tenant scope is empty, configuration is process-local, and no durable in-flight claim exists. |
| Prepayment provider submission and status polling | **Remediation required** | Current prepayment behavior is local validation, not an executable provider-attempt path. |
| P2G provider submission and status polling | **Partially implemented** | Conditional post-commit enqueue exists, but process-safe configuration, durable claims, and complete integration coverage are absent. |
| Status-before-resubmission | **Partially implemented** | Correct within `ProviderRuntime`; not unified for all recovery/scheduler paths. |
| Endpoint-enforced durable HTTP idempotency | **Remediation required** | Persistence/service exists without universal live route adoption. |
| Durable batch policy/lease use | **Remediation required** | Schema exists without mandatory live-worker claim behavior. |
| Tenant-authorized redacted reconciliation reporting | **Partially implemented** | Route and redacted response exist, but authorization/tenant derivation and negative test evidence are incomplete. |
| Migration and regression evidence | **Partially implemented** | No model drift and 203 tests passed locally; migration-upgrade, route, race, and external evidence remain absent. |

## Minimum next implementation scope

A further Item 02 pass must first eliminate the critical provider runtime gaps. It must persist or otherwise reliably bootstrap provider resolution across worker processes, propagate non-empty validated tenant scope into executable attempts, define non-settlement prepayment/provider semantics, and add a durable attempt claim before any provider network call. It must then unify recovery scheduling with status-first polling.

The same pass must enforce the existing idempotency ledger at every mutating endpoint, make batch lease/policy a mandatory bulk-worker operation, and harden reconciliation report tenant derivation/authorization. Only after mounted-route, task, migration-upgrade, race, and negative-security tests are added should another internal readiness review occur.

> **Final determination:** Item 02 Payments runtime wiring remains **Partially aligned / remediation required** and is **not** ready to be described as fully aligned, provider-ready, staging-ready, officially validated, conformant, certified, testing-site-ready, or submission-ready. No external action is authorized by this record.

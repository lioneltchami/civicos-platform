# Item 03 — Scheduler internal durable-wiring and runtime-evidence remediation plan

**Date:** 2026-08-19
**Stage:** 1 of 4 — two shared-context gap analyses
**Scope:** Internal CivicOS code and local deterministic evidence only. This pass excludes staging, official-suite execution, real external dependencies, portal activity, deployment, certification, and submission.

## Conclusion and scope control

Two shared-context reviews examined the current Scheduler source, recipient/outbox core, delivery tasks, local fakes, operation inventory, focused tests, Item 03 records, and the Item 01–07 reconciliation. Both found that the six listed internal defects remain open. The durable recipient state machine is a valuable local foundation, but it is not yet the authoritative dispatch, publisher, topology, status, or full operation-trace system.

> **Stage 1 conclusion:** Every defect in this plan is **must-fix-now** for this internal remediation pass. A route inventory, helper, fake adapter, local status dataclass, optional feature flag, or focused service test is not closure. Closure requires the applicable enforced Django request → durable state → outbox/worker → bounded result/status path and its local race, negative, topology, and redaction evidence.

The pinned official authority is [GovStackWorkingGroup Scheduler Building Block][1] revision `d425be5cc0d6c606f351e5bf89be6d5c6c83c468`. This plan creates internal/auditable evidence only; the GovStack requirements model distinguishes that from observable running-system evidence.[2]

## Mandatory defect closure plan

| ID | Defect and current proof | Concrete internal change required | Required local proof | Priority |
|---|---|---|---|---|
| S03-01 | **Durable dispatch remains configuration-gated.** `apps/appointments/tasks.py:dispatch_alert_schedule()` defaults `GOVSTACK_SCHEDULER_DURABLE_RUNTIME_ENABLED` to false and can set the legacy schedule Boolean before old fan-out. | Make recipient/outbox materialization authoritative for every supported scheduler dispatch. Retain `GovStackAlertSchedule.dispatched` only as a compatibility projection during migration; it must not determine whether durable work exists. Add migration-safe legacy reconciliation/backfill and fail closed before any transport without durable recipient and outbox rows. | Default-settings dispatch; legacy `dispatched` true/false reconciliation; rollback/no rows; duplicate dispatch race; re-arm/cancel vs dispatch; no transport before durable commit; compatibility response tests. | **Must fix now** |
| S03-02 | **Outbox publication has no durable publisher claim.** `due_outbox()` returns unclaimed due rows and `publish_scheduler_outbox()` can enqueue before marking publication. | Add outbox publisher token, owner, expiry, generation/version, claim and outcome methods. Claim rows in short transactions; publish outside transaction; token-fence the published/failure write; recover expired/broker-ambiguous claims without treating `published_at` as the whole protocol. | Concurrent publishers; broker failure; crash before/after enqueue; lease expiry/redrive; stale publisher; duplicate delivery convergence; rollback; no lock during broker I/O. | **Must fix now** |
| S03-03 | **Complete Django request/runtime traces for 37 operations are absent.** Mounted URL and operation inventory exist, but no complete request-level evidence bundle proves response, authorization, correlation, state, task/outbox, and redaction behavior. | Build a deterministic test-only Django request/runtime trace harness parameterized by exactly the 37-operation inventory. Each record must contain safe request class, authorization result, response contract result, correlation ID, expected state/outbox/task delta, and redaction result. | Valid and relevant negative request for every operation; response/error schema; authorization/owner isolation; correlation/idempotency; state/no-op assertions; trace completeness validator; no sensitive fields. | **Must fix now** |
| S03-04 | **Payments/Consent fakes are helper-level.** `apps/appointments/services/local_evidence.py` contains disabled deterministic fakes but no mounted Scheduler topology invokes them. | Add an explicit test-only configuration/boundary that wires allowlisted fake Payments and Consent calls into local Scheduler request/service/task execution. Preserve disabled-by-default production behavior, strict authority ownership, synthetic data, correlation/idempotency propagation, and failure isolation. | Request → service/task → fake topology for success, timeout, rejection, malformed result, duplicate correlation, unknown authority, disabled fake, retry, redaction, and non-mutation of Payments/Consent records. | **Must fix now** |
| S03-05 | **No database-backed authorized non-PII operational status projection exists.** Current local status aggregation is iterable/in-memory and has no Scheduler route or verified owner/tenant scope. | Add a Scheduler-owned query/projection service backed by `SchedulerRecipientDelivery` and `SchedulerOutbox`, plus an authorized read-only route. Derive scope from authenticated persisted ownership, enforce query isolation, deterministic bounded pagination/retention, and only non-PII operational summaries. | Multi-owner/tenant fixtures; authorized same-scope; unauthenticated/wrong-scope/conflict denial; pagination; claim/retry/cancel/dead-letter/replay/ack status; schema rejects URLs, payloads, names, contact data, tokens, and credentials. | **Must fix now** |
| S03-06 | **Full operation-surface runtime evidence is incomplete.** Existing focused tests and operation matrix do not exercise lifecycle, authorization, ownership, duplicate, cleanup, topology, and trace conditions across all 37 IDs. | Generate reproducible local-only redacted evidence keyed by operation ID and source revision from the S03-03 harness. Validate exact completeness, stable synthetic correlation identifiers, deterministic order, checksum, failure/negative coverage, and no extra/missing operation IDs. | Complete 37-ID evidence bundle; lifecycle/race/unsafe URL/retry/dead-letter/replay/authorized-status cases; trace validator; deterministic output; explicit proof that all data are synthetic and local-only. | **Must fix now** |

## Controls explicitly marked “do not touch”

| Control | Current implementation evidence | Preservation requirement |
|---|---|---|
| Recipient-level durable state | `SchedulerRecipientDelivery`, `SchedulerOutbox`, and transactional materialization in `apps/appointments/services/scheduler_runtime.py` | Do not return to schedule-level Boolean or best-effort fan-out as the source of truth. |
| Generation and recipient lease fencing | `scheduler_runtime.claim()`, outcome transitions, and schedule modification/cancellation increment `delivery_generation` | Preserve token/generation checks for claim, success, failure, cancellation, replay, and publication transitions. |
| Safe outbound transport | `apps/appointments/scheduler_tasks.py` validates safe HTTPS public destinations, disables redirects, uses bounded timeout, and records bounded errors | Do not weaken SSRF/private-address protection, no-redirect, timeout, or redaction behavior. |
| Non-PII data boundary | Opaque recipient references and `local_evidence.redact_trace()` | Do not persist or expose recipient URLs, names, email, phone, message-sensitive content, credentials, authorization material, or lease tokens in traces or status projection. |
| Legacy compatibility during rollout | Existing schedule Boolean and view response semantics | Preserve compatibility until migration/backfill, authoritative durable dispatch, and rollback/recovery behavior are independently verified. |
| Short transaction boundary | Recipient claims/outcomes are durable before or after, not during, outbound transport | Outbox broker and recipient transport I/O must remain outside database transactions. |
| BB ownership boundaries | Local authority labels and existing architecture | Scheduler must not mutate Payments settlement records or Consent decision/audit records. |

## Required implementation order

1. Add the migration-safe outbox publisher claim protocol and make durable recipient/outbox materialization the enforced default dispatch path.
2. Add the read-only database-backed authorized operational status projection and local topology configuration boundary.
3. Build the 37-operation Django request/runtime harness, wire the test-only Payments/Consent fakes through representative Scheduler paths, and produce fail-closed local evidence validation.
4. Add race, crash, negative authorization, redaction, ownership, duplicate delivery, stale-generation, and rollback tests throughout.

## Stage 3 acceptance rule

Stage 3 must update this plan with per-ID final status and concrete code/test references. No `S03-*` defect may be described as closed unless the enforced runtime path and required local proof in the table exist. Stage 4 must independently validate each ID from the final code and this plan only.

## References

[1]: [GovStackWorkingGroup Scheduler Building Block](https://github.com/GovStackWorkingGroup/bb-scheduler)

[2]: [GovStack requirements model](https://specs.govstack.global/architecture/5-specification-framework/5.3-requirements-model)

## Stage 3 implementation status — 2026-08-19

The focused pass integrated an initial durable-default dispatch/outbox publisher protocol, an outbox publisher lease model/migration, initial local evidence/status helpers, and expanded local redaction. This is **partial implementation only**. The first isolated regression run exposed legacy dispatch-test incompatibilities after switching default execution to durable materialization; those tests require an explicit new durable-path expectation rather than an unreviewed legacy transport assertion. The source was also corrected to align the publisher-lease model index with its migration.

| ID | Status | Evidence and remaining condition |
|---|---|---|
| S03-01 | Partially implemented | `tasks.py` now routes through durable materialization by default. Legacy-dispatch reconciliation/backfill, complete default-path behavior tests, and full request-level evidence remain required. |
| S03-02 | Partially implemented | `SchedulerOutbox` publisher token/owner/lease/generation fields, migration `0020`, and initial claim/publish/failure functions exist. Race/crash/redrive tests and full stale-generation publication fencing remain required. |
| S03-03 | Partially implemented | Initial exact-operation local evidence helpers were added, but no complete Django request/runtime suite proves all 37 operations. |
| S03-04 | Partially implemented | Test-local fake boundary/redaction was expanded, but no end-to-end request→task→fake topology evidence is complete. |
| S03-05 | Partially implemented | Initial status helper/route artifacts were added; verified authenticated persisted ownership isolation and non-PII query coverage remain required. |
| S03-06 | Still open | Complete deterministic 37-operation evidence bundle, completeness validator evidence, and lifecycle/race/negative coverage remain required. |

> **Stage 3 decision:** Do not promote any S03 defect to closed. Preserve the durable recipient/outbox foundations and proceed to independent verification with the final code and plan only.

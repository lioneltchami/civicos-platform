# Item 03 — Scheduler durable-wiring independent verification

**Verification date:** 2026-08-19
**Stage:** 4 of 4 — two totally new independent code reviews
**Scope:** Internal Django/Celery wiring, local runtime evidence, and local fakes. External staging, official suites, credentials, real recipients, deployment, certification, conformance, testing-site activity, and submission were excluded.

## Isolation and evidence boundary

Each reviewer received only the final code package and the controlling Item 03 remediation plan. They did not receive earlier analyses, Stage 2 notes, implementation-agent outputs, raw validation logs, repository history, external sources, providers, credentials, or portals. Both inspections were read-only.

| Evidence source | Scope | Result |
|---|---|---|
| Independent review A | Final code plus controlling plan only | **Partially aligned / remediation required** |
| Independent review B | Final code plus controlling plan only | **Partially aligned / remediation required** |
| Separate isolated local validation | Migration drift and focused test execution | No pending Appointments model changes; 60 focused Scheduler tests passed |

> The isolated local test result confirms the packaged source passed the stated local migration and regression checks. It does not demonstrate staging behavior, real recipient delivery, official validation, certification, conformance, testing-site readiness, or submission readiness.

## Consolidated conclusion

The independent conclusion is **Partially aligned / remediation required**. The implementation closes the previous absence of a migration-backed, schedule-linked recipient work record and adds a credible local claim/lease/lifecycle foundation. It does **not** close the full internal engineering target because the durable path remains opt-in, publisher recovery is not claim-fenced, the 37-operation runtime topology is incomplete, local cross-BB fakes are helper-level rather than topology-wired, and no database-backed authorized non-PII status projection exists.

## Per-gap verification

| Gap | Status | Concrete verification evidence | Residual condition |
|---|---|---|---|
| S03-01 — durable schedule-linked per-recipient lifecycle | **Closed** | `apps/appointments/migrations/0019_scheduler_runtime_core.py` creates `SchedulerRecipientDelivery` linked to `GovStackAlertSchedule`, with generation, opaque recipient reference, idempotency/correlation keys, retry/lease state, cancellation, dead-letter, and acknowledgement fields. `SchedulerOutbox` is also migration-backed. `scheduler_runtime.materialize()` writes delivery and outbox rows transactionally. `test_scheduler_runtime.py` covers duplicate materialization. | The legacy Boolean remains as a compatibility projection; its rollout impact is assessed separately under S03-02. |
| S03-02 — Boolean crash window / durable work as the authoritative dispatch path | **Still open** | `apps/appointments/tasks.py` checks `GOVSTACK_SCHEDULER_DURABLE_RUNTIME_ENABLED`; the durable materialization bridge sets the compatibility Boolean only with durable work. | The setting defaults to false and preserves the old Boolean-before-fan-out path. A full migration-safe default rollout and legacy reconciliation/backfill evidence are absent. |
| S03-03 — durable retry, cancellation, dead-letter, replay, acknowledgement | **Closed for the local durable state machine** | `scheduler_runtime.py` implements claim, success, bounded retry/dead-letter, cancel, acknowledge, replay, and expired-lease reaping. `scheduler_tasks.py` claims prior to transport and records outcomes after transport. Focused tests cover stale-token fencing, acknowledgement separation, retry/dead-letter, replay, cancellation, and materialization. | Multi-worker/broker and crash-after-HTTP behavior remain unproven; acknowledgement is an internal state transition, not external recipient proof. |
| S03-04 — duplicate task, partial-failure, and worker-loss convergence | **Still open** | Unique schedule/generation/recipient constraint, lease token, `acks_late`, and `reject_on_worker_lost` are present. | No complete test coverage for duplicate broker delivery, timeout/4xx/5xx/exception through the task, cancellation-versus-completion, partial multi-recipient convergence, worker loss, or crash after transport. |
| S03-05 — transactional outbox and publish recovery | **Still open** | Delivery and outbox records are created together; `publish_scheduler_outbox` and `reap_scheduler_leases` tasks exist. | The publisher has no durable claim token/lease/state transition. Concurrent publishers and broker failure/crash-before-or-after-enqueue/re-drive behavior lack complete implementation evidence and tests. |
| S03-06 — executable local runtime evidence for all 37 operations | **Still open** | The 37-operation inventory and route surface remain available. | No complete Django request/runtime trace suite verifies all 37 IDs with response schema, correlation ID, durable state delta, and redacted trace. The inventory is not runtime evidence. |
| S03-07 — disabled deterministic Payments/Consent fakes and local contract wiring | **Still open** | `local_evidence.py` adds disabled-by-default deterministic Payments/Consent fake helpers, allowlisted outcomes, correlation duplicate handling, authority labels, and redaction helpers. | The fakes are not wired into a complete Scheduler request/task topology. Authority-preserving failure isolation and Scheduler non-mutation of BB-owned data are not demonstrated end to end. |
| S03-08 — database-backed authorized non-PII operational status | **Still open** | Delivery records contain useful operational fields, and `local_evidence.py` has iterable-based local aggregation. | No Scheduler-owned database query/projection/route derives authorized owner/tenant scope and reports queue age, latency, attempts, error class, retry, cancellation, dead-letter age, or acknowledgement without PII. |
| S03-09 — non-push channels and real acknowledgement/recipient proof | **Deferred** | The code remains push-oriented and the controlling plan explicitly defers email, SMS, polling, real recipients, and external acknowledgement. | This remains outside the internal remediation pass and must not be described as closed. |

## Controls confirmed by both reviews

| Control | Verification result |
|---|---|
| Schedule-linked migration-backed recipient state | Present and additive. |
| Recipient-specific idempotency, generation, and claim fencing | Present in the durable local service. |
| No transport while the dedicated delivery task holds its state transaction | Present by task/service ordering. |
| Safe push URL validation, bounded timeout, and no-redirect transport | Reused by the dedicated recipient task. |
| Separate acknowledgement state | Present; not treated as HTTP success. |
| Focused local migration and regression evidence | Present in the separately archived isolated validation log. |

## Ranked residual findings

| Priority | Finding | Required next internal change |
|---|---|---|
| **High** | The durable path is opt-in while the unsafe Boolean-before-fan-out default remains. | Make durable delivery/outbox materialization authoritative through a migration-safe rollout, including legacy schedule reconciliation and enforcement that no scheduler dispatch bypasses durable rows. |
| **High** | Outbox publishing is not durably claim-fenced. | Add publish token/version/expiry fields and conditional publisher transitions; test concurrent publisher, broker failure, crash before/after enqueue, and re-drive. |
| **High** | Complete 37-operation local runtime evidence is absent. | Add a Django request-level local topology covering every operation ID with response/schema, correlation, durable-state, and redacted trace assertions. |
| **High** | Authorized persistent operational status is absent. | Add a database-backed Scheduler status service/projection with explicit owner/tenant authorization, non-PII metrics, retention, pagination, and isolation tests. |
| **Medium** | Fault/concurrency coverage remains narrow. | Add task-path tests for duplicate execution, timeout, 4xx/5xx, exception, cancellation race, stale lease, partial fan-out, worker loss, and crash-after-HTTP semantics. |
| **Medium** | Local fakes are not topology-wired. | Use explicit test-only settings to wire allowlisted Payments/Consent fakes into the local 37-operation topology; prove correlation/idempotency propagation and BB authority boundaries. |

## Final determination

> **Item 03 Scheduler durable wiring is Partially aligned / remediation required.** It is not internally complete, so the workflow may not advance to another item. It must not be described as external-delivery-ready, staging-ready, officially validated, conformant, certified, testing-site-ready, or submission-ready.

No external action is authorized by this verification record.

# Item 03 — Scheduler internal durable-wiring remediation plan

**Date:** 2026-08-19
**Stage:** 1 of 4 — precise gap confirmation
**Scope:** Internal Django/Celery delivery wiring, local runtime evidence, and local cross-BB fakes only. External staging, official tests, credentials, real recipients, deployment, certification, and testing-site submission are explicitly deferred.

## Confirmed baseline

The existing Scheduler implementation has a useful deterministic delivery-state prototype, safe outbound URL controls, a 37-operation route inventory, schedule-level persistence, and Celery schedule enqueue/reschedule behavior. These controls remain valuable and **must not be weakened or re-described as external delivery proof**. The remediation target is the missing persistence and runtime boundary between `GovStackAlertSchedule` and recipient-specific delivery work.

| Existing control | Evidence | Stage 1 treatment |
|---|---|---|
| Deterministic state prototype | `apps/appointments/services/delivery_state.py`; `apps/appointments/tests/test_delivery_state.py` | **Do not touch** — retain its state semantics and focused tests. |
| Scheduler schedule persistence and CRUD | `apps/appointments/models.py` (`GovStackAlertSchedule`); `apps/appointments/services/govstack_alert_schedule.py` | **Do not touch** — extend rather than replace. |
| Celery schedule dispatch and reschedule/revocation | `apps/appointments/services/govstack_alert_schedule.py`; `apps/appointments/tasks.py` | **Do not touch** — preserve post-commit scheduling and task limits. |
| Safe recipient dispatch controls | `apps/appointments/tasks.py` URL validation, bounded transport, no-redirect handling, and outbound I/O after the DB lock | **Do not touch** — preserve SSRF/transport safeguards and the no-I/O-under-lock rule. |
| 37-operation inventory and local preparation artifacts | `apps/appointments/govstack_urls.py`; `examples/civicos-scheduler/operation-matrix.json`; `scripts/validate_scheduler_harness.py` | **Do not touch** — keep the inventory and clearly distinguish it from runtime proof. |

## Remaining internal gaps

| ID | Exact remaining gap | Path and symbol evidence | Concrete work required | Priority |
|---|---|---|---|---|
| S03-01 | **No durable per-recipient delivery lifecycle exists.** The in-memory prototype is not linked to schedules or recipients. | `apps/appointments/services/delivery_state.py` (`DeliveryStore` and state transitions); `apps/appointments/models.py` (`GovStackAlertSchedule.dispatched`) | Add migration-backed Scheduler-owned delivery and recipient-attempt records with correlation/idempotency keys, status, lease/version, retry timing, error class, cancellation/dead-letter/replay/ack fields, and safe non-PII operational fields. Bridge the proven state machine to transactional model operations. | **must-wire-now** |
| S03-02 | **The schedule-level `dispatched` Boolean is committed before recipient I/O and can strand partially delivered work after a crash.** | `apps/appointments/tasks.py` (`dispatch_alert_schedule`) | Replace Boolean-only gating with an atomic per-recipient outbox/claim protocol. Persist recipient work before enqueue; claim individual records atomically; write outcomes after transport; reclaim expired in-flight records. Do not hold a DB lock across HTTP. | **must-wire-now** |
| S03-03 | **Retry, cancellation, dead-letter, replay, and acknowledgement are not connected to Django/Celery recipient work.** | `apps/appointments/services/delivery_state.py`; `apps/appointments/tasks.py`; `apps/appointments/services/govstack_alert_schedule.py` | Add a dedicated recipient-delivery task and durable scheduling/reconciliation path. Persist bounded backoff and error classifications; fence stale workers with claim tokens; make cancellation prevent new sends; permit auditable eligible replay; define acknowledgement separately from request acceptance. | **must-wire-now** |
| S03-04 | **Duplicate task, partial-failure, and worker-loss convergence are not durable.** | `apps/appointments/tasks.py` aggregate best-effort fan-out and schedule lock; existing tests cover aggregate behavior rather than recipient rows | Use uniqueness and conditional transitions for recipient work. Test timeout, 4xx/5xx, exception, duplicate task delivery, cancellation-versus-completion, stale lease, partial success, and worker-loss recovery. | **must-wire-now** |
| S03-05 | **There is no Scheduler-owned transactional outbox or publish recovery.** | `apps/appointments/services/govstack_alert_schedule.py` uses Celery enqueue; no Scheduler outbox model/reaper is present | Commit delivery work and outbox events in the originating transaction. Publish after commit, persist publish outcome, and add an idempotent outbox publisher/re-drive path for broker enqueue failure. | **must-wire-now** |
| S03-06 | **The 37-operation surface has inventory evidence but lacks executable local runtime evidence for the durable path.** | `apps/appointments/govstack_urls.py`; `examples/civicos-scheduler/operation-matrix.json`; `scripts/validate_scheduler_harness.py` | Build a local non-external test topology using Django requests, the durable task path, deterministic recipient fakes, and captured traces. For every operation retain operation ID, response status/schema, correlation ID, durable state change, and redacted trace. Keep inventory and runtime evidence separate. | **must-wire-now** |
| S03-07 | **Cross-BB fakes/contracts are incomplete.** | Existing Scheduler tests mock task enqueue/HTTP; no executable Scheduler-side local Payments/Consent contract boundary is evidenced | Add disabled-by-default provider-neutral local Payments and Consent fakes with allowlisted operations, deterministic success/timeout/rejection/duplicate behavior, correlation/idempotency propagation, and authority-preserving failure isolation. Prove Scheduler cannot mutate BB-owned data. | **must-wire-now** |
| S03-08 | **Durable Scheduler operational reporting is absent.** | `apps/appointments/tasks.py` returns/logs aggregate counts only | Add owner/tenant-scoped, non-PII delivery-status queries sourced from durable records: queue age, latency, attempts, error class, retry timing, cancellation, and dead-letter age. Add privacy/retention and escalation tests. | **must-wire-now** |
| S03-09 | **Non-push channels and real acknowledgements are not backed by the present dispatch contract.** | Subscriber channel preferences and push-oriented task implementation | Keep unsupported email/SMS/poll and external acknowledgement/recipient evidence explicitly marked unsupported or deferred; do not claim channel completeness. | **deferred** |

## Acceptance checklist

| Acceptance item | Status | Required Stage 3 result |
|---|---|---|
| Deterministic delivery-state prototype and unit tests | **Closed — do not touch** | Preserve semantics and tests. |
| Schedule CRUD, schedule-level persistence, and Celery schedule enqueue | **Closed — do not touch** | Preserve existing behavior while adding durable delivery work. |
| Safe outbound URL and no-I/O-under-lock controls | **Closed — do not touch** | Preserve with additional persisted outcomes. |
| Durable per-recipient Scheduler delivery state | **Open** | S03-01 implemented with migrations and integration tests. |
| Atomic outbox and broker recovery | **Open** | S03-02/S03-05 implemented with re-drive tests. |
| Durable retry/cancel/dead-letter/replay/ack lifecycle | **Open** | S03-03/S03-04 implemented through Celery/Django work records. |
| Local runtime evidence for all 37 operations | **Open** | S03-06 operation-level trace/evidence test passes. |
| Local Payments/Consent fake contracts | **Open** | S03-07 contracts and authority/failure tests pass. |
| Durable non-PII operational status | **Open** | S03-08 queries and isolation tests pass. |
| Email/SMS/poll, real recipients, staging, official tests, credentials, and submission | **Deferred — do not touch** | No external claim or action. |

> **Stage 1 conclusion:** The remaining work is internally actionable but substantial. The Stage 2 review must prioritize a minimal, migration-safe durable delivery/outbox design that preserves the existing route contract, task safety controls, and local-only evidence boundary.

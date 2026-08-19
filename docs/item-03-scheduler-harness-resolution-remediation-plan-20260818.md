# Item 03 — Scheduler Harness Resolution: Remediation Plan

**Date:** 2026-08-18
**Controlling source:** `item-03-scheduler-harness-resolution-verification-20260818.md`.

## Controls already aligned — do not reopen

| Control | Status | Evidence |
|---|---|---|
| Route inventory and local traceability | **Fully aligned for preparation** | 37 routes across nine Scheduler API groups are mapped with source/test references. |
| Local-only safety configuration | **Fully aligned** | Loopback enforcement, SSRF protections, bounded requests, no redirects, task limits, and non-claiming candidate boundary are present. |
| Documentation boundary | **Fully aligned for preparation** | Registration, cleanup, authority boundaries, deferred channels, and non-certification language are documented. |
| Focused local regression | **Fully aligned as regression evidence** | Isolated Scheduler suite previously passed 502 tests; this is not harness or official evidence. |

## Remaining remediation gaps

| Priority | Gap | Evidence / paths | Repository-safe work needed |
|---|---|---|---|
| P0 | Durable per-recipient alert outcome lifecycle is absent. | `apps/appointments/tasks.py`; alert/message models and tests. | Add migration-safe recipient delivery state, correlation/idempotency, bounded retry/backoff, dead-letter/manual replay, cancellation, worker-loss and acknowledgement semantics with deterministic tests. |
| P0 | Executable local topology and 37-operation runtime evidence are absent. | `examples/civicos-scheduler/`; `scripts/validate_scheduler_harness.py`; matrix. | Add local web/worker/broker/recipient-fake topology, pin versions, bind validated config at runtime, run operation-level scenarios and retain redacted traces/checksums. |
| P1 | Cross-BB invocation is only documented. | Scheduler README/config; no provider-neutral adapter. | Add disabled-by-default local Payments/Consent fakes with correlation/idempotency propagation, authority boundaries, timeout/rejection/duplicate tests, and redacted traces. |
| P1 | Durable scheduler status/observability and operational remediation are incomplete. | `apps/appointments/tasks.py`; task tests. | Add non-PII delivery/job status, ownership, queue/latency/retry/dead-letter metrics, escalation and retention controls with tests. |
| P1 | Lifecycle contract execution evidence for event/entity/resource/subscriber/alert routes is incomplete. | GovStack views, routes, focused test modules, matrix. | Add local adapter scenarios for lifecycle/auth/ownership/duplicate/cleanup/failure paths and exact response evidence. |
| External | Official harness and external proof remain unavailable. | Official run manifest records harness dependency block; no staging/official suite/portal result. | Do not alter the official suite. Obtain maintainer-supported pinned harness dependency, authorised staging/topology and external test evidence after local remediation. |

## Acceptance checklist status

| Acceptance item | Current status |
|---|---|
| Event/entity/resource/subscriber management lifecycle | **Partially aligned** |
| Alert/message delivery, status and recovery | **Partially aligned** |
| Failure handling and safe retries | **Partially aligned** |
| Configuration externalisation and runtime topology | **Partially aligned** |
| Harness/runtime tests and operation evidence | **Partially aligned** |
| Cross-BB boundary and invocation evidence | **Still missing** |
| Operational status/reporting and documentation | **Partially aligned** |
| Current official-suite/staging evidence | **Still missing** |

> Local implementation and deterministic evidence may close repository controls, but they cannot establish official conformance, external recipient behavior, deployment evidence, or testing-site readiness.

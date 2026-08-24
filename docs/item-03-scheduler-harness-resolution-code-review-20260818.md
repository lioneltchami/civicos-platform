# Item 03 — Scheduler Harness Resolution: Blind Code Review

**Date:** 2026-08-18  
**Review input:** Current CivicOS source and `item-03-scheduler-harness-resolution-gap-analysis-20260818.md` only. Two fresh reviewers were blind to the Stage 1 conversation and source authority bundle.  
**Current conclusion:** **Partially aligned / remediation required.**

## Independent review result

The blind review confirms that CivicOS already implements a significant Scheduler surface in `apps/appointments/`. The route table provides 37 concrete routes across Event, Entity, Alert Schedule, Message, Resource, Subscriber, Affiliation, Appointment, and Log. The source includes separate views, services, models, Scheduler authentication/throttling, Celery scheduling, and focused tests. Therefore, the candidate must not be described as lacking a Scheduler implementation.

The reviewers also converge on material gaps: the candidate adapter remains disabled; there is no pinned operation-by-operation OpenAPI traceability artifact; alert delivery has only a pre-dispatch Boolean and non-durable per-recipient outcomes; external integration/configuration/operational reporting are incomplete; Payments and Consent are not invoked through Scheduler-owned integration contracts; and neither a current official suite nor a fresh local regression run has been archived. The result does not support a certification or testing-site readiness claim.

## Alignment with the Stage 1 record

| Stage 1 finding | Blind-review confirmation | Concrete code evidence |
|---|---|---|
| Scheduler API implementation exists | **Confirmed.** The route/service/view/test surface is real and extensive. | `apps/appointments/govstack_urls.py:86-152`; `apps/appointments/govstack_views.py:334-3600`; `apps/appointments/services/govstack_*.py`; focused test modules. |
| Event, Entity, Alert Schedule, Resource, Subscriber, Affiliation, Appointment, Message, and Log operations need official traceability proof | **Confirmed.** Route presence does not prove parameter, schema, response-code, or error-shape compatibility. | 37 routes at `govstack_urls.py:87-151`; no pinned operation matrix/validator under `examples/civicos-scheduler/`. |
| Harness has not been proven against an enabled local adapter | **Confirmed.** Candidate metadata deliberately sets the adapter to disabled and limits its claim boundary. | `examples/civicos-scheduler/candidate-manifest.json:17-22`; `examples/civicos-scheduler/result/preflight.json`. |
| Alert dispatch safety exists but reliable delivery lifecycle is incomplete | **Confirmed and elevated to P0.** Idempotency and SSRF controls are strong, but failures are logged rather than persisted and a Boolean is committed before delivery. | `apps/appointments/tasks.py:338-475`, `478-680`; `apps/appointments/models.py:2651-2694`. |
| Configuration and cross-BB integration remain incomplete | **Confirmed.** Candidate target/port are local defaults; Scheduler has no provider-neutral Payments/Consent adapter or contract tests. | `examples/civicos-scheduler/candidate-manifest.json:8-22`; `docker-compose.yml`; `test_entrypoint.sh`; `apps/appointments/`. |
| Fresh evidence remains necessary | **Confirmed.** Test files exist, but this blind review did not run them and the candidate preflight is not an official result. | `apps/appointments/tests/test_govstack_*.py`; `examples/civicos-scheduler/result/preflight.json`. |

## Detailed code-review findings

### 1. API and cancellation/negative-path contract

The internal route topology is coherent. In particular, `govstack_urls.py` deliberately places subpaths before bare delete paths, and service-backed APIViews cover the full resource family. `event_delete` soft-cancels rather than hard-deletes, preserving linked data and excluding cancelled events from default listings. This is a defensible implementation choice, but it must be explicitly mapped to the pinned official DELETE/cancellation contract and response semantics.

The Log family exposes modification/delete routes while the service layer intentionally treats audit records as append-only. The candidate must therefore represent the intentional 405/error behavior in its traceability matrix rather than treating route registration as full mutable CRUD alignment. Auth failures, malformed payloads, duplicate data, unknown objects, expired events, cancellation races, and dispatch errors likewise require exact operation-level response evidence.

### 2. Alert delivery reliability is the primary application gap

`dispatch_alert_schedule` is designed carefully to avoid duplicate citizen notifications: it row-locks the schedule, marks it dispatched before outbound traffic, performs HTTP after releasing the lock, and uses late acknowledgements/worker-loss rejection. The helper validates HTTPS URLs at dispatch time, fails closed on unsafe address space, suppresses redirects, and keeps a failure to one recipient from stopping the rest.

However, `GovStackAlertSchedule.dispatched` is a single Boolean. The worker sets it before recipient resolution and network calls. A crash, time limit, timeout, or partial recipient failure can therefore leave a schedule permanently marked dispatched with no durable success/failure/retry state. `_attempt_alert_delivery` logs non-2xx/exception failures and returns a value indicating only that an attempt happened; aggregate task counters do not represent confirmed delivery. This needs a durable schedule-and-recipient lifecycle rather than a change to the Boolean timing alone.

### 3. Candidate harness and configuration are preparation-only

The candidate correctly pins the official repository revision and calls itself local-only preparation, but the adapter is disabled. The candidate uses a loopback target and the wrapper specifies port 3333. The deployment lacks a fully validated configuration model for API base, Information Mediator/PubSub/Messaging/BB dependencies, authentication mode, channel selection, timeouts, retry/backoff, timezone, operational thresholds, and feature flags. The harness cannot credibly demonstrate current compliance until those inputs are externally injected and the exact configuration is archived.

### 4. Cross-BB and operational gaps

No Scheduler-specific, authority-preserving contract connects scheduled/alert actions to Payments or Consent. A correct design must preserve boundaries: Scheduler orchestrates/records scheduling and alerts; Payments retains financial execution/finality; Consent retains consent decision/audit authority. Outages or rejections from either dependency must not corrupt Scheduler appointment or dispatch state.

Status reporting is presently limited to Scheduler Log API plus application log lines and aggregate task counters. Required operational information—queue depth, latency, failures, retries, resource consumption, anomaly thresholds, escalation, and durable delivery status—is not evidenced as a Scheduler-owned, queryable reporting capability.

## Precise implementation backlog

| Priority | Required change | Target source area | Acceptance evidence |
|---|---|---|---|
| P0 | Create a pinned Scheduler OpenAPI operation matrix and validator covering every official path/method, parameters, request schema, success/error status, CivicOS route/view/service, and regression test. | `scripts/`, `docs/`, `tests/govstack/`, `examples/civicos-scheduler/` | Machine-readable matrix with every operation `full`, `partial`, or `missing`; validator run result; no unexplained required path. |
| P0 | Replace Boolean-only alert delivery tracking with durable schedule and recipient delivery state: correlation/idempotency key, attempt count, timestamps, outcome, HTTP/result class, error category, retryability, next attempt, terminal/dead-letter/manual-review state. | `apps/appointments/models.py`, migrations, `tasks.py`, services, tests | Tests for success, timeout, non-2xx, unsafe URL, mixed-recipient partial failure, duplicate task, worker-loss recovery, retry exhaustion, cancellation, and queryable status. |
| P0 | Build an enabled, isolated local candidate adapter and reproducible official-run topology. | `examples/civicos-scheduler/` | Pinned source/harness revision; external config hash; health endpoint; raw local official-harness output; image/dependency versions; no testing-site submission. |
| P1 | Externalise and validate all candidate/integration configuration. | Candidate Compose/env template/launcher and Django settings | Schema + fail-fast validation for target, dependencies, auth, channels, timeouts, retry policy, timezone, feature flags, and thresholds; no production endpoint hard-coded. |
| P1 | Implement provider-neutral Scheduler integration contracts for Payments and Consent. | `apps/appointments/` integrations/services/tasks/tests | Allowlists, correlation/idempotency, timeouts, failure isolation, local fake adapters, and tests proving authority boundaries are preserved. |
| P1 | Add Scheduler operational metrics, alert status reporting, anomaly thresholds, escalation, and operator query/report surface. | Models/services/views/tasks/tests | Bounded metrics/audit schema; query/report endpoints; privacy/retention policy; threshold/escalation tests. |
| P1 | Define supported channels and an acknowledgement/status strategy. | Scheduler service/task configuration and docs | Push, email, SMS, poll, and acknowledgement capability matrix; unsupported channels recorded as non-success/deferred rather than silently treated as delivered. |
| P2 | Document how an external BB registers resources/subscribers. | `examples/civicos-scheduler/README.md` or equivalent | Local-only registration guide covering entity/affiliation prerequisites, IDs, ownership, token mode, cleanup, and Payments/Consent boundaries. |
| P2 | Produce fresh local and official-harness evidence after remediation. | `docs/govstack/testing/evidence/` | Archived command output, dependency/version/configuration metadata, and a clear separation between local and official results. |

## Mandatory implementation constraints

The Stage 3 implementation must remain restricted to Item 03. It must not re-open Consent or Payments implementation except to add an explicit Scheduler integration boundary that is necessary to test Item 03 and that cannot alter the other BB’s authority. It must use non-production/local-only topology, preserve secret hygiene, never submit externally without human approval, and not describe a local run as certification.

## Readiness and scope

**Readiness: Not ready.** The next step is targeted Item 03 remediation, followed by focused local validation and an independently verified record. Item 01 (Consent) and Item 02 (Payments failure remediation) are already completed. Items 04–07 are not started yet and remain out of scope.

## References

[1]: [Item 03 Gap Analysis](item-03-scheduler-harness-resolution-gap-analysis-20260818.md)
[2]: `apps/appointments/govstack_urls.py` — Scheduler route table
[3]: `apps/appointments/tasks.py` — Celery scheduling and alert dispatch
[4]: `apps/appointments/models.py` — Scheduler supplementary models
[5]: `examples/civicos-scheduler/candidate-manifest.json` — Candidate manifest
[6]: `examples/civicos-scheduler/result/preflight.json` — Candidate preflight record

# Item 03 — Scheduler Harness Resolution: Gap Analysis

**Date:** 2026-08-18  
**Official authority:** GovStack Scheduler specification version **1.1** and the pinned official `GovStackWorkingGroup/bb-scheduler` repository revision `d425be5cc0d6c606f351e5bf89be6d5c6c83c468`.[1] [2]  
**Scope:** Scheduler harness resolution only. This analysis cross-checks two shared-context reviews against the directly inspected CivicOS Scheduler source, because the app is implemented under `apps/appointments/` rather than a directory named `scheduler`.

## Executive result

CivicOS has a **substantial Scheduler Building Block implementation** rather than merely a candidate shell. The `apps/appointments/govstack_urls.py` route table exposes 37 concrete endpoints spanning the nine official API families: Event, Entity, Alert Schedule, Alert Message, Resource, Subscriber, Affiliation, Appointment, and Log. The implementation has dedicated views, service modules, models, authentication/throttling controls, Celery tasks, migrations, and a sizeable focused test suite.

> **Current status: Partially aligned / remediation required.** The core API surface and many lifecycle controls are present, but Item 03 is not ready because the official harness is not yet reproducibly connected to the candidate; cross-BB invocation is not established; configuration and harness topology have hard-coded/local-only elements; durable dispatch outcome, retry, and operations reporting evidence are incomplete; and no current official-suite result is available.

The official specification requires RESTful OpenAPI v3+ JSON APIs across these functional areas and states that the official repository’s `test` directory holds the Scheduler tests.[2] The implementation should therefore be assessed as a candidate with meaningful code coverage, not as certified or testing-site ready.

## Official requirement-to-CivicOS comparison

| Official capability | CivicOS direct evidence | Stage 1 assessment |
|---|---|---|
| Event Management: create, modify, cancel/delete, find/list, overlap protection, no changes after event ends | Four event routes in `apps/appointments/govstack_urls.py:87-91`; concrete event views at `govstack_views.py:1723-2042`; lifecycle service methods `event_create`, `event_modify`, `event_delete`, and `event_list` in `services/govstack_event.py`; 75 focused event tests. | **Partially aligned.** The API/lifecycle surface is present; full proof against every official rule and harness response shape still requires contract execution. |
| Entity Management: unique entity, organizer resource, resource affiliations, CRUD/list | Entity routes `govstack_urls.py:93-97`; views `govstack_views.py:334-623`; entity service and 40 focused tests. | **Partially aligned.** CRUD and test coverage exist; official harness/auth/error-shape proof is missing. |
| Alert Schedule and Message Management: unique schedules, target category, schedule epoch, template reuse, delete cleanup | Alert routes `govstack_urls.py:99-117`, views `govstack_views.py:2624-3008` and `3011-3354`, services `govstack_alert_schedule.py`/`govstack_message.py`, and Celery dispatch task `tasks.py:478-650`. | **Partially aligned.** Scheduling and best-effort push dispatch are real; delivery outcome persistence, durable retry/dead-letter, all channels, and official harness proof are incomplete. |
| Resource, Subscriber, Affiliation, and Appointment Management | Routes `govstack_urls.py:119-142`; dedicated views/services and focused tests: Resource 60, Subscriber 49, Affiliation 49, Appointment 69. | **Partially aligned.** The requisite surfaces exist; a complete official contract matrix and current integration evidence are absent. |
| Status Logging and Reporting | Log routes `govstack_urls.py:144-151`, views `govstack_views.py:3357-3600`, and `services/govstack_log.py`; 44 focused log tests. | **Partially aligned.** Application log API exists, including an intentional append-only design, but the required metrics, retry/error indicators, anomaly escalation, and operational reporting are not demonstrated. |
| External interfaces, configuration, failure handling, and administration | Scheduler auth in `govstack_auth.py`, throttling in `govstack_throttling.py`, Celery/Beat configuration in `config/settings/base.py`, and candidate files in `examples/civicos-scheduler/`. | **Partially aligned.** Token modes and deployment scope are configurable, but Scheduler external endpoint/integration configuration, observability, and production-equivalent harness topology need work. |

## What is well done

The codebase’s strongest Item 03 asset is the **complete internal route family**. `apps/appointments/govstack_urls.py` explicitly documents 37 concrete endpoints and registers all nine official resource groups. This directly counters an overly narrow directory-name search: the CivicOS Scheduler Building Block is implemented through the appointments application.

The functional layering is also sound. Dedicated services separate Event, Entity, Resource, Subscriber, Affiliation, Appointment, Alert Schedule, Message, and Log business logic; views are concrete APIViews; auth and throttling are separated into `govstack_auth.py` and `govstack_throttling.py`; and the migrations show an established data model. Existing focused tests total at least **502 test methods** across the eleven GovStack Scheduler test modules enumerated below. This is strong local regression substrate, though it is not a current official-suite result.

| Test module | Focused test methods |
|---|---:|
| `test_govstack_event.py` | 75 |
| `test_govstack_appointment.py` | 69 |
| `test_govstack_resource.py` | 60 |
| `test_govstack_alert_schedule.py` | 55 |
| `test_govstack_subscriber.py` | 49 |
| `test_govstack_affiliation.py` | 49 |
| `test_govstack_log.py` | 44 |
| `test_govstack_entity.py` | 40 |
| `test_govstack_message.py` | 35 |
| `test_govstack_auth.py` | 21 |
| `test_govstack_throttling.py` | 5 |
| **Total** | **502** |

The asynchronous alert work demonstrates several appropriate safety choices. `dispatch_alert_schedule` is idempotent under a row lock; marks schedules before outbound dispatch to prefer under-delivery over duplicate citizen-facing notifications after a worker crash; performs no outbound HTTP while holding the database lock; validates outbound HTTPS destinations against private/special address ranges; prevents redirect following; and makes per-recipient failure non-fatal to other recipients.[3]

## Gaps and remediation priorities

| Priority | Gap | Exact evidence | Required outcome |
|---|---|---|---|
| P0 | Official-harness execution remains blocked/unproven | `examples/civicos-scheduler/candidate-manifest.json` declares the adapter disabled; `result/preflight.json` is preparation evidence only; the official runner expects configurable API health topology. | Resolve the dependency/topology blocker; run the pinned official harness against a local non-production adapter; archive raw output, image/version digests, route configuration hash, and traceability rows. |
| P0 | No end-to-end official operation/response traceability matrix | CivicOS has 37 routes, but no machine-readable mapping from the pinned official OpenAPI operations, parameters, response codes, and schemas to CivicOS routes/tests. | Add a pinned OpenAPI operation matrix and validator; record `full`/`partial`/`missing` per operation without overstating compliance. |
| P0 | Alert dispatch is best effort, not a durable failure lifecycle | `tasks.py:409-475` catches network and non-2xx failures and logs warnings; `tasks.py:478-650` marks a schedule dispatched before recipient delivery. | Persist per-recipient dispatch outcome/correlation/error; add bounded retry/backoff/dead-letter or explicit manual remediation; expose reliable status/log evidence. |
| P1 | Cross-BB scheduled or alert invocation is not established | The official functional requirements call for endpoint configuration and Pub/Sub interaction with other BBs; no Scheduler-specific Payments/Consent adapter contract is evidenced. | Add provider-neutral Scheduler integration adapters with allowlisted configuration, correlation/idempotency, failure isolation, and tests. Payments must remain the settlement authority; Consent must remain the consent authority. |
| P1 | Scheduler configuration is not fully externalised | Candidate manifest uses `http://127.0.0.1:3333/`; candidate Docker Compose contains local topology; direct public target URLs are stored with participants. | Define validated external configuration for Scheduler API base, dependency endpoints, channel policy, timeouts, retry policy, time zones, and feature flags. Prove no production endpoint or schedule is hard-coded. |
| P1 | Required status/operational reporting is incomplete | Log CRUD exists, but Stage 1 finds no evidence that Scheduler periodically measures queue depth, latency, errors, retry count, resource consumption, anomaly thresholds, or escalation as required by the functional specification. | Add bounded metrics/audit records, query/report surface, anomaly/escalation policy, and tests. |
| P1 | Channel support and acknowledgement path are narrow | `tasks.py` deliberately supports push webhook only; email/SMS/poll are skipped and no incoming acknowledgement/status path is evidenced. | Document deferred channels; implement/configure required channel adapters or a reliable inbound status mechanism. |
| P2 | Registration documentation for other BB resources/subscribers is absent or not discoverable | Candidate manifest identifies source/harness files but does not explain registration as a resource/subscriber or the entity/affiliation prerequisites. | Add clear local-only integration documentation and examples for other BB registration, ownership, required IDs, token mode, affiliation, and cleanup. |
| P2 | Current regression evidence is not yet produced for Item 03 | The test inventory is substantial but was not rerun in this Stage 1 record; official suite result is absent. | Run focused unit/integration/failure tests in the isolated environment, preserve raw output, and rerun after remediation. |

## Scheduler harness resolution checklist

| Checklist item | Current status | Evidence and gap |
|---|---|---|
| Aligns with current GovStack Scheduler BB key functionalities: Event Management, Entity Management, Alert Schedule Management | **Partially aligned** | Concrete APIs/services/tests exist, but formal current OpenAPI matrix and harness proof are missing. |
| Harness can create, schedule, trigger, update, and cancel events | **Partially aligned** | Event CRUD and alert scheduling/dispatch code exists; candidate harness has not run successfully against the endpoint/adapter topology. |
| Harness can register and manage entities, resources, and subscribers | **Partially aligned** | All corresponding API groups and services are present; no current official-harness execution proves the contract. |
| Can invoke other Building Blocks, especially Payments and Consent, on schedule or via alerts | **Still missing** | No Scheduler-owned integration adapter/contract/trace is evidenced. |
| Status tracking and failure handling for scheduled jobs exist and are reliable | **Partially aligned** | Alert idempotency, SSRF guard, timeout/non-fatal handling, and log API exist; durable per-recipient delivery state, retry/dead-letter, status callback, metrics, and escalation are absent. |
| Configuration is fully externalised, with no hard-coded schedules or endpoints | **Partially aligned** | Several settings are externalised, but the candidate topology has a local endpoint and no complete dependency/channel/retry configuration schema. |
| Unit and integration tests for the harness pass, including failure scenarios | **Partially aligned** | 502 focused Scheduler tests are present; this record contains no fresh Stage 1 run and no current official harness result. |
| Clear documentation explains how other BBs register as resources/subscribers | **Still missing** | No dedicated Scheduler integration-registration guide is included in the candidate package. |
| No regressions introduced to existing Scheduler or dependent functionality | **Partially aligned** | Broad tests exist, but no current full Item 03 regression artifact has been captured. |

## Pre-implementation completion target

“Scheduler harness resolution” can be considered complete only when the pinned official API operation matrix has no unexplained required gaps; the non-production adapter/harness topology is reproducible; local focused plus failure tests pass; and a current pinned official suite has been executed with raw evidence. This document makes no certification, conformance, or testing-site readiness claim.

**Item 01 (Consent) and Item 02 (Payments failure remediation) are already completed. Items 04–07 are not started yet – out of scope for this run.**

### Stage 3 final-status update

**Status as of Stage 1:** Pending implementation. Stage 3 must preserve the analysis above and update this section with each remediation’s completed, deferred, or rejected status, including commits and validation evidence.

| Remediation area | Final status | Evidence / commit / test |
|---|---|---|
| P0–P2 Scheduler harness remediation backlog | Pending Stage 3 | To be updated after implementation and validation. |

## References

[1]: https://specs.govstack.global/scheduler/1-version-history.md "GovStack Scheduler — Version History"
[2]: https://specs.govstack.global/scheduler/8-service-apis.md "GovStack Scheduler — Service APIs"
[3]: https://specs.govstack.global/scheduler/6-functional-requirements.md "GovStack Scheduler — Functional Requirements"
[4]: https://specs.govstack.global/scheduler/7-data-structures.md "GovStack Scheduler — Data Structures"
[5]: https://specs.govstack.global/scheduler/9-workflows.md "GovStack Scheduler — Internal Workflows"
[6]: https://github.com/GovStackWorkingGroup/bb-scheduler/tree/d425be5cc0d6c606f351e5bf89be6d5c6c83c468 "Pinned official Scheduler repository"

# Item 05 — Per-Block Requirements Assessments: Gap Analysis

**Date:** 2026-08-18
**Scope:** Consent, Payments, Scheduler, and File Management only
**Assessment source revision:** `f901f2f`
**Status:** Stage 1 assessment complete; Stage 2 blind review pending

## Purpose and evidence boundary

This document consolidates two independent, shared-context assessments of the CivicOS Building Block areas that already have source code. The review used only the official GovStack specification portal and pinned source repositories in the `GovStackWorkingGroup` organisation. It does not use `govstack.com` and it does not claim official conformance, certification, testing-site acceptance, or production readiness.

The official authority was pinned for reproducibility: Consent `7af4b62a1c0b0d7073d42b37c71b0f08bea63dda`; Payments `4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a`; Scheduler `d425be5cc0d6c606f351e5bf89be6d5c6c83c468`; and File Management `cf50bf4952491bd1ede775aa3c90a228319c3977`.[1] [2] [3] [4]

> **Interpretation rule.** A CivicOS candidate package, source-level matrix, local runner result, or archived test log is evidence of the stated local implementation only. It is not interchangeable with an official-harness, testing-site, certification, deployment, or blanket Building Block claim.

## Executive assessment

| Area | Current bounded position | Highest-priority remaining work | Dependency significance |
|---|---|---|---|
| Consent | A fail-closed GovStack boundary, audit/history model, operation matrix, and local Consent-suite evidence exist. The 42-operation matrix remains `partial`. | Convert the partial operation matrix into verified official-contract behavior and close remaining API/evidence gaps before relying on consent in a multi-block rehearsal. | Governs lawful data/purpose context for Scheduler notifications, Payments personal data, and File Management artefacts. |
| Payments | G2P/P2G/bulk/voucher models and a failure-remediation foundation exist; the final Item 02 verification remains **Not ready**. | Provider-settlement confirmation, reconciliation/status reporting, batch remediation, route-wide idempotency, and current official-suite evidence. | High-risk financial dependency for Scheduler fee flows and receipt/voucher documents. |
| Scheduler | CivicOS appointment/reminder capabilities and Item 03 harness traceability exist; final Item 03 verification remains **Partially aligned / remediation required**. | A complete, authoritative Scheduler API surface, durable recipient-level delivery/retry/dead-letter behavior, and executable inter-block contracts. | Orchestrates time-based reminders and may trigger consent-sensitive communications or payment-related booking flows. |
| File Management | Document lifecycle capabilities and the Item 04 local/CI runner are complete for the stated test-runner checklist. | Official API/harness mapping and deployment-grade storage/security evidence if a GovStack-facing claim is later required. | Stores consent artefacts, receipts, vouchers, and supporting records; provides retention, audit, download, and access-control dependencies. |

## Assessment method

Each implemented area was assessed across the following dimensions: functional requirements and workflows; API and route surfaces; data models and auditability; error/failure behavior; non-functional or cross-cutting controls; testability and retained evidence; and dependencies on the other implemented areas. The assessment is intentionally more conservative than the presence of a similarly named app: the official source requires specific contracts, workflows, and evidence, rather than an app label alone.

## Consent

### Official requirement frame

The official Consent source contains functional requirements, service API definitions, workflows, and a test plan for consent/data-agreement lifecycle behavior.[1] The CivicOS comparison is therefore limited to its consent domain, including GovStack-specific URLs/views, the integration boundary, serializers, services, task behavior, audit/history models, and tests under `apps/consent/`.

### Current alignment

| Dimension | Well aligned evidence | Assessment |
|---|---|---|
| Functional and workflow foundation | `apps/consent/models.py`, `services.py`, `govstack_views.py`, and `govstack_urls.py` provide a dedicated consent domain and GovStack-facing surface. | Substantial implementation exists rather than a documentation-only placeholder. |
| Fail-closed integration control | `apps/consent/integration_boundary.py` and `tests/test_integration_boundary.py`. | Strong cross-cutting control: integration behavior is designed to reject unsupported/unsafe boundary states rather than silently falling back. |
| Auditability and history | Consent models/migrations, including `0011_consent_record_history.py`, `0017_consent_record_is_current_unique_constraint.py`, and `0019_alter_consentrevision_serialized_hash.py`. | Strong evidence of durable revision/history handling and integrity-oriented design. |
| Testability | `tests/test_govstack_api.py`, `test_govstack_candidate_fixture.py`, `test_integration_boundary.py`, and the committed Item 01 operation matrix/verification documentation. | Local official-suite evidence exists; the evidence boundary is correctly narrower than a certification claim. |

### Incomplete or uncertain alignment

| Dimension | Gap or limitation | Priority | Concrete evidence / reason |
|---|---|---|---|
| Official API completeness | The Item 01 42-operation matrix remains `partial`; local suite success does not itself demonstrate full endpoint, payload, error, or authentication alignment for all listed operations. | **Must-fix** before a broad official readiness claim | `docs/item-01-consent-v23q4-operation-matrix.json`; Item 01 verification record. |
| Failure and withdrawal propagation | Cross-block effects of withdrawal, revocation, and policy change are documented but not shown as executable contracts with Scheduler, Payments, and File Management. | **Should-fix** | No shared contract/evidence package demonstrates deterministic downstream propagation. |
| Operational non-functional evidence | No current staging-grade proof is retained here for tenant isolation, auth/RBAC, rate-limit, timeout, replay, trace correlation, or incident runbooks across the GovStack surface. | **Should-fix** | Existing unit/local evidence is not equivalent to an authorised staging rehearsal. |
| Official execution traceability | Pinning, raw test output, adapter hash, and deployment topology must remain current for each official rerun. | **Nice-to-have** for local maintenance; **Must-fix** before a submission | Preserve committed evidence protocol consistently. |

### Consent prioritised work

| Priority | Required assessment outcome |
|---|---|
| **Must-fix** | Replace `partial` operation claims with endpoint-by-endpoint verified status and evidence; retain the exact official-suite/adapter/version record for any submission scope. |
| **Should-fix** | Define and test consent withdrawal/purpose propagation contracts for Scheduler notifications, payment records/callbacks, and stored consent artefacts. |
| **Nice-to-have** | Add operational dashboards and controlled fault-injection evidence for webhook replay, timeouts, and rate limiting. |

## Payments

### Official requirement frame

The official Payments source defines G2P, voucher, P2G, G2B, and B2G API/workflow material, including bulk-payment feature tests and API YAML definitions.[2] The Item 05 assessment considers CivicOS `apps/payments/` and its GovStack models, views, services, tasks, lifecycle remediation, periodic-task registration, and test surfaces.

### Current alignment

| Dimension | Well aligned evidence | Assessment |
|---|---|---|
| Domain and API foundation | `govstack_models.py`, `govstack_serializers.py`, `govstack_views.py`, `govstack_urls.py`, `govstack_services.py`, and `govstack_tasks.py`. | CivicOS has dedicated G2P/P2G/voucher/bulk-related domain surfaces rather than a generic donation-only implementation. |
| Idempotency and lifecycle groundwork | GovStack payment records use `request_id`; Item 02 adds `govstack_failure_services.py`, lifecycle migrations, callback outbox and remediation tasks. | A substantial foundation exists for duplicate handling, delayed remediation, and traceability. |
| Audit and failure remediation | Audit vocabulary/migrations, callback outbox, periodic remediation configuration, P2G settlement traceability, and Item 02 recovery tests. | Item 02 materially improved the failure/audit path. |
| Testability | Focused Item 02 validation evidence and existing GovStack task/P2G tests are retained. | Tests support local claims but the official Payments suite remains incomplete. |

### Incomplete or poorly aligned requirements

| Dimension | Gap or limitation | Priority | Concrete evidence / reason |
|---|---|---|---|
| Settlement and uncertain outcomes | Provider-neutral lifecycle records do not replace an authoritative provider settlement query/polling mechanism for uncertain financial outcomes. | **Must-fix** | Item 02 verification explicitly records this blocker. |
| Reconciliation and status APIs | Durable reconciliation records exist, but complete externally usable reconciliation/status-reporting APIs and evidence for both success/failure are not demonstrated. | **Must-fix** | Item 02 verification conclusion: not ready. |
| Batch partial failure and retry/compensation | Callback outbox/review triage improves reliability, but failure-rate policy, resubmission/kick-back/compensation rules, and dead-letter operations are incomplete. | **Must-fix** | Official bulk-payment workflows and Item 02 gap assessment. |
| Route-wide business semantics | Official-suite failures included redirect and 500-response mismatches; canonical error/status/auth semantics and universal idempotency are not yet demonstrated across the external route surface. | **Must-fix** | Prior official-suite record: 487 passed, 74 failed, 225 skipped. |
| Cross-cutting operations | Provider outage behavior, payment correlation across callback/retry/reconciliation, operational alerting, and staging runbooks are not validated. | **Should-fix** | Local lifecycle tests do not prove an end-to-end provider topology. |

### Payments prioritised work

| Priority | Required assessment outcome |
|---|---|
| **Must-fix** | Implement/verify settlement confirmation and reconciliation/status contracts, deterministic error mapping, batch partial-failure policy, replay-safe resubmission/kick-back/compensation, and canonical idempotency at every externally claimed route. |
| **Should-fix** | Add provider outage/timeouts/duplicate/invalid-account/insufficient-funds test matrices, correlation dashboards, and operator remediation runbooks. |
| **Nice-to-have** | Extend payment-file linkage and report/export observability after File Management integration contracts are defined. |

## Scheduler

### Official requirement frame

The official Scheduler source provides functional requirements, API material, workflows, and a test plan.[3] CivicOS’s Scheduler comparison is deliberately focused on the Scheduler/GovStack surfaces within `apps/appointments/`, including GovStack URLs/views, scheduler models/tasks, periodic registration, harness manifest, traceability matrix, and tests. Generic appointments functionality alone is not treated as a complete official Scheduler API implementation.

### Current alignment

| Dimension | Well aligned evidence | Assessment |
|---|---|---|
| Appointment/reminder foundation | `apps/appointments/models.py`, views, services, tasks, and tests. | CivicOS has substantial domain functionality for appointments and alert behavior. |
| Harness traceability and boundary | Item 03 additions include the Scheduler operation matrix, local-only configuration/loopback constraints, harness validator, and candidate registration guide. | Strong improvement in traceability and safe local candidate posture. |
| Current test surface | Existing Scheduler/appointment tests and focused Item 03 validation evidence. | Testable local functionality exists, but the full official API/harness result is absent. |

### Incomplete or poorly aligned requirements

| Dimension | Gap or limitation | Priority | Concrete evidence / reason |
|---|---|---|---|
| Official API surface | The Item 03 operation matrix records 37 official operations as `partial`; CivicOS does not demonstrate a complete official Scheduler endpoint and schema surface. | **Must-fix** before official readiness or cross-block rehearsal claims | Item 03 final verification. |
| Delivery lifecycle | Recipient-level outcomes, durable retry/dead-letter behavior, acknowledgement semantics, and operational reporting remain incomplete. | **Must-fix** for dependable notifications | Item 03 gap and verification records. |
| Inter-block contracts | Scheduler-to-Payments and Scheduler-to-Consent behavior is conceptual/implicit rather than a versioned, executable integration contract with correlation/idempotency/error semantics. | **Must-fix** before combined rehearsals | Cross-block dependency analysis and Item 03 verification. |
| Harness/evidence | No current official Scheduler suite result or authorised staging deployment topology is retained. | **Must-fix** before a test-site/deployment claim | Item 03 final conclusion remains partially aligned. |
| Cross-cutting controls | Tenant/auth boundary, reminder suppression after consent withdrawal, observability, and outage recovery need production-like evidence. | **Should-fix** | Current tests are local/focused. |

### Scheduler prioritised work

| Priority | Required assessment outcome |
|---|---|
| **Must-fix** | Complete the pinned official operation matrix with actual endpoints/schema/error behavior; implement durable delivery/retry/dead-letter/reporting; and create executable Scheduler↔Consent and Scheduler↔Payments contracts. |
| **Should-fix** | Add correlation IDs, tenant/auth checks, opt-out suppression, observability, and controlled provider-outage exercises. |
| **Nice-to-have** | Add reusable synthetic event packs and visual operational reports for rehearsal operators. |

## File Management

### Official requirement frame

The official File Management repository contains functional, service-API, workflow, test-plan, and OpenAPI-harness material.[4] CivicOS’s File Management assessment covers `apps/documents/`, its upload/download/version/retention services, audit/task/HTTP surfaces, candidate package, and Item 04 local/CI runner documentation and evidence.

### Current alignment

| Dimension | Well aligned evidence | Assessment |
|---|---|---|
| Functional lifecycle | `services/upload.py`, `download.py`, `versioning.py`, `retention.py`, `tasks.py`, models, views, and broad tests. | Strong project-specific document lifecycle coverage including scanning, quarantine, retention, audit, and access behavior. |
| Error/safety behavior | `test_upload_content_gating.py`, `test_wave3_clamav.py`, `test_scan_audit_and_promotion.py`, token/download tests, and retention tests. | Good local error/security test coverage for the documented capabilities. |
| Testability and evidence | `tools/run_file_management_tests.py`, layer manifest, Make targets, CI wiring, runner contract test, contributor guide, operation matrix, and portable local evidence archive. | Item 04 is fully aligned for its local/CI test-runner acceptance checklist. |
| Audit and data lifecycle | Document audit/retention and protected download/access-token surfaces. | Strong project-specific evidence, but not automatically a match to each official File Management API. |

### Incomplete or uncertain alignment

| Dimension | Gap or limitation | Priority | Concrete evidence / reason |
|---|---|---|---|
| Official API contract comparison | The local runner and operation matrix do not demonstrate a complete request/response/error/auth comparison against the official File Management API/harness. | **Must-fix** before an official claim | Item 04 deliberately scoped to runner enablement, not official execution. |
| Official harness evidence | No current official File Management harness or testing-site result exists; the local candidate adapter is disabled. | **Must-fix** before submission | Item 04 verification preserves this boundary. |
| Deployment-grade storage controls | Production-like proof for malware scanning availability, object storage outage handling, encryption/key lifecycle, retention/legal-hold execution, and access/tenant isolation is not retained. | **Should-fix** before staging of sensitive documents | Local tests do not prove a production-like storage topology. |
| Cross-block artefact contracts | Consent evidence, receipts/vouchers, and Scheduler attachments need explicit metadata, retention, authorization, correlation, and deletion propagation contracts. | **Should-fix** | Cross-block linkage is plausible but not an executable contract set. |

### File Management prioritised work

| Priority | Required assessment outcome |
|---|---|
| **Must-fix** | Build a pinned official API/operation/error/auth mapping and retain an approved official-harness result before any File Management official submission claim. |
| **Should-fix** | Define executable cross-block artefact contracts and prove secure storage/scan/outage/retention behavior in a production-like rehearsal. |
| **Nice-to-have** | Add data-classification and retention dashboards for artefacts referenced by Consent, Payments, and Scheduler. |

## Cross-block dependency map

| Source area | Dependent area | Dependency that must be explicit before a combined rehearsal |
|---|---|---|
| Consent | Scheduler | Reminder/notification purpose, opt-in/opt-out, withdrawal suppression, auditable policy decision, and correlation identity. |
| Consent | Payments | Personal-data handling, authorization/purpose context, audit access, withdrawal/change behavior, and lawful record retention. |
| Consent | File Management | Storage of signed consent evidence/exports, retention/deletion/hold rules, access control, integrity, and audit linkage. |
| Scheduler | Payments | Booking/fee or payment-intent lifecycle, correlation and idempotency keys, cancellation/refund/compensation semantics, and delayed-remediation ownership. |
| Scheduler | File Management | Appointment artefact attachment/access/retention behavior and notification-related document links. |
| Payments | File Management | Receipts, vouchers, supporting documents, immutable references, retention, authorization, and provider-callback correlation. |
| All four | Platform operations | Shared tenant/auth boundaries, correlation IDs, audit schema, error vocabulary, retry/dead-letter ownership, observability, synthetic data, and rollback/replay runbooks. |

## Areas without implemented CivicOS Building Block code

Beyond Consent, Payments, Scheduler, and File Management, no additional CivicOS application area was treated as an implemented GovStack Building Block in this Item 05 assessment. Other official Building Blocks or platform areas are **Not started yet** for this Item 05 scope and require their own authoritative mapping rather than an inferred claim.

## Gate before Items 6 and 7

Items 6 and 7 must not start from a blanket “platform ready” assumption. An authorised staging rehearsal should be scheduled only after each participating block has a pinned authority/version, an approved environment and adapter topology, an explicit route/auth/data contract, reproducible raw test evidence, safe synthetic data, observability and correlation IDs, operational rollback/replay ownership, and clear owner approval. Payments requires closure of the Must-fix financial lifecycle/reconciliation/status gaps; Scheduler requires closure of the Must-fix official operation, delivery lifecycle, and executable cross-block contract gaps. Consent and File Management must be limited to their proven scope until their official/API evidence gaps are closed.

A later item must also define cross-block tenant/auth semantics, consent decision propagation, payment/appointment cancellation and compensation, secure artefact lifecycle, provider/object-store/queue outage behavior, rate/timeout limits, audit and reconciliation reporting, and incident/recovery runbooks. No staging or external test-site submission is authorised by this assessment.

## Per-Block requirements assessments checklist

| Checklist item | Current status | Evidence |
|---|---|---|
| Gap analysis vs latest official GovStack specs (or project requirements) written for every implemented Building Block / area | **Complete** | This document assesses Consent, Payments, Scheduler, and File Management against pinned official source revisions. |
| Clear Must-fix / Should-fix / Nice-to-have prioritisation for each assessed block | **Complete** | Prioritised sections under every block. |
| Dependencies between blocks explicitly mapped | **Complete** | Cross-block dependency map. |
| Assessment stored in well-structured Markdown | **Complete** | This dated Markdown record. |
| Functional, cross-cutting, APIs, data models, error handling, and testability covered where applicable | **Complete** | Per-block current-alignment and gap tables. |
| Assessment reviewed for completeness and accuracy | **Pending Stage 2 blind review** | Stage 2 must validate the narrative against the current codebase. |
| No contradictions with completed Items 1–4 | **Complete, subject to Stage 2 confirmation** | Bounded Item 01–04 conclusions are preserved throughout. |
| Clear statement of what must still be done before Items 6 and 7 proceed safely | **Complete** | “Gate before Items 6 and 7” section. |

## Stage 3 final-status update

**Status at Stage 1:** This assessment is a structured draft awaiting blind code review. Stage 3 must correct any validated inaccuracies, make the checklist fully green, and replace this paragraph with a final status and commit references. It must not re-open Items 1–4 except to correct a factual error.

## References

[1]: https://github.com/GovStackWorkingGroup/bb-consent/tree/7af4b62a1c0b0d7073d42b37c71b0f08bea63dda "Pinned official Consent Building Block source"
[2]: https://github.com/GovStackWorkingGroup/bb-payments/tree/4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a "Pinned official Payments Building Block source"
[3]: https://github.com/GovStackWorkingGroup/bb-scheduler/tree/d425be5cc0d6c606f351e5bf89be6d5c6c83c468 "Pinned official Scheduler Building Block source"
[4]: https://github.com/GovStackWorkingGroup/bb-file-management/tree/cf50bf4952491bd1ede775aa3c90a228319c3977 "Pinned official File Management Building Block source"
[5]: https://specs.govstack.global/consent/readme.md "Consent specification portal index"
[6]: https://specs.govstack.global/payments/readme.md "Payments specification portal index"
[7]: https://specs.govstack.global/scheduler/readme.md "Scheduler specification portal index"

# Item 01 — Consent Submission Package Gap Analysis

**Date:** 2026-08-18
**Scope:** Item 01 only — CivicOS Consent submission package
**CivicOS baseline reviewed:** `dfadc36680d39a2bb800324054ff9980b9cfb9fd`
**Official authority boundary:** GovStack Consent Specification 1.3.0 (December 2025), published `v23Q4` OpenAPI commit `3f7d2e2fa2b55b1f36890bfc20316227c9ee98ca`, and separately pinned latest official `main` commit `7af4b62a1c0b0d7073d42b37c71b0f08bea63dda`.[1] [5] [6]

> **Assessment conclusion:** CivicOS has a substantial Consent implementation and a meaningful local official-suite preparation foundation. It is **partially aligned**, but the Consent submission package is **not complete**. No external GovStack testing-site submission, certification, or overall-platform conformance claim is supported by this document.

## 1. Method and authority boundary

Two shared-context analyses independently compared the committed CivicOS Consent source against the official GovStack Consent repository, its published OpenAPI, official mock/reference implementation, Gherkin tests, and current official specification pages. Both analyses reached the same central conclusion: CivicOS has useful domain, privacy, audit, and candidate-control foundations, but lacks a complete, version-pinned, operation-by-operation proof that its Config, Service, and Audit APIs match the published GovStack Consent contract.

The current public specification identifies Consent **1.3.0** as the latest published specification. Its Service APIs page uses the published `v23Q4` OpenAPI as the release-facing API reference and separately directs implementers to `main` for the latest unreleased API definition.[1] [4] This analysis therefore treats `v23Q4` as the submission-facing compatibility baseline and treats any latest-`main` difference as a separately labelled delta. It does not silently combine published and unreleased contracts.

## 2. What CivicOS does well

CivicOS is not an empty or superficial Consent implementation. It has a mature local domain with a substantive migration history, including GovStack alignment, signature, history, webhook, replay, and audit-related changes in `apps/consent/migrations/0007_govstack_alignment.py` through `0020_remove_consentauditentry_consent_audit_valid_action_and_more.py`. The current codebase contains models, serializers, services, receivers, tasks, authenticated views, GovStack-oriented routes, candidate fixtures, and targeted tests under `apps/consent/`.

| Area | Concrete CivicOS evidence | Assessment |
|---|---|---|
| Authenticated local Consent operations | `apps/consent/api_views.py`, `api_urls.py`, `services.py`, and `tests/test_api.py` implement category discovery, citizen-scoped records, grant/withdraw actions, export requests, authentication failures, and object-level isolation. | **Well implemented local foundation.** This supports privacy and citizen-boundary control, but does not alone establish official API equivalence. |
| Consent domain and evolution | `apps/consent/models.py`, `serializers.py`, service modules, and the GovStack-related migration series provide policies, records, revisions/history, signature-related work, webhooks, and current-record constraints. | **Strong directional alignment.** The required official field/invariant mapping remains incomplete. |
| Audit foundations | `apps/audit/models.py`, `services.py`, `handlers.py`, `management/commands/verify_audit_chain.py`, and tests provide hash-chain-oriented audit infrastructure; Consent has additional history and audit-related migrations. | **Meaningful tamper-evidence foundation.** Completeness per official Consent action and official Audit API is not yet proven. |
| Local candidate safety | `examples/_common/candidate_common.sh`, `examples/civicos-consent/`, `scripts/govstack_testing_preparation.py`, and launcher tests establish local-only target enforcement, generated secrets, fixed service selection, and limited fixture seeding. | **Well designed local test safety control.** It is preparation evidence, not a submission outcome. |
| Local official-suite evidence | `docs/govstack/testing/runs/consent-2026-08-19-revalidated-pinned-7af4b62/` records the pinned Consent Gherkin suite’s local passing result: 2 features, 4 scenarios, and 16 steps. | **Useful evidence for the executed scope.** It is not a complete proof of every published Config, Service, and Audit operation. |
| Failure-path testing | `apps/consent/tests/test_api.py` covers unauthenticated access, IDOR isolation, invalid/missing actions, unknown categories, required-category rejection, duplicate pending export, and export isolation. | **Good local failure coverage.** It must be extended to official contract failures, signatures, revisions, integrations, and audit semantics. |

## 3. Official GovStack Consent baseline

The official functional requirements require version-controlled consent agreements; agreement creation, viewing, updating, and termination; tamper-proof revisions and signatures for Consent Agreements, Consent Policies, and Consent Records; notification configuration; and logging of administrative and individual actions. Auditor-facing requirements include tamper-proof consent logs, verification of shared agreements, and filterable/sortable revision histories.[2]

The official resource model makes Consent Record, Consent Agreement, Data Policy, revisions, and cryptographic signatures minimum-integrity concerns. A Consent Record must reference the Agreement consented to or later withdrawn, while changes must remain auditable.[3]

| Official API group | Published `v23Q4` capability categories |
|---|---|
| Config APIs | Policy CRUD and revisions; Data Agreement CRUD/list; individual configuration; webhook CRUD/list. |
| Service APIs | Individual/Data Agreement/Policy lookup; verification; consent-record creation, retrieval, amendment, signature, and removal. |
| Audit APIs | Consent-record and Data-Agreement audit list/detail retrieval. |

The official Consent test plan expects Gherkin coverage across Config, Service, and Audit APIs. The official mock/reference implementation and test suite are part of the required comparison evidence, but they must be treated as pinned, inspectable sources rather than silently altered local tests.[4] [7]

## 4. Detailed gaps before the Consent submission package can be complete

### 4.1 Published API contract completeness

CivicOS exposes local API routes under its own structure, including `apps/consent/api_urls.py`, `govstack_urls.py`, `api_views.py`, and `govstack_views.py`. The presence of these routes does **not** prove parity with the published `v23Q4` route, method, request schema, response schema, status-code, identifier, security, and error contract. Both independent analyses found no completed one-to-one mapping for every official Config, Service, and Audit operation.

The high-priority gap is a source-controlled matrix that accounts for every published OpenAPI operation. The matrix must identify the CivicOS route or adapter path, input/output serializer, status/error behavior, required security context, test location, published version applicability, and disposition: **match**, **partial**, **renamed via constrained adapter**, **missing**, or **intentionally out of scope with approval**. Unverified operations must remain unverified; they must not be inferred from similarly named CivicOS endpoints.

### 4.2 Agreement, Consent Record, Policy, Purpose, and Data Agreement model integrity

CivicOS has policy/category/record/history/signature concepts, but the official baseline requires a coherent Agreement–Consent Record–Data Policy/Purpose model with revision and cryptographic-signature semantics. The existing source does not demonstrate a completed field-level mapping for identifiers, cardinality, version/revision links, subject/actor identity, agreement and data-agreement relationship, purpose representation, lifecycle state, withdrawal, signature verification, and audit provenance.

`ConsentCategory` must not be assumed to satisfy the official Policy or Purpose resource merely because it participates in a local grant/withdraw workflow. The required work is to document the exact mapping and implement any missing first-class fields, relationships, invariants, migration/data backfill, serializers, and transition checks. Every divergence must have an explicit, evidence-backed compatibility decision.

### 4.3 Identity and Information Mediator integration boundary

CivicOS local authentication is proven through token/authenticated test helpers and `request.user`, but that is not proof of the official Identity integration contract. Likewise, webhook/task code and GovStack-oriented views show integration intent, not a complete Information Mediator implementation.

Before acceptance, Item 01 must either implement and test the required Identity and Information Mediator flows or document each unavailable external dependency as an explicit stub with an approved justification and bounded test. The documentation must cover subject mapping, endpoint configuration, credentials/secrets, message/event schema, retries, idempotency, replay protection, failure handling, and the evidence provided by the official mock/test flow. Local authentication alone is insufficient.

### 4.4 Consent audit, revisions, and signature proof

CivicOS audit infrastructure is stronger than a generic application log, but official acceptance requires proof that all Consent administrative and individual mutations have complete, retrievable, tamper-evident provenance. This must include actor and subject, action and outcome, resource identifier, Consent Agreement/Data Policy/Purpose/Record revision, old/new state, timestamp, source channel, signature/verification result, withdrawal/revocation rationale when applicable, and webhook delivery outcome.

The final package must expose the official Audit list/detail behavior, including filter/sort semantics where required, and test both retrieval and tamper/invalid-transition failures. Audit-chain infrastructure is valuable evidence, but it does not independently prove every required Consent event is emitted or every official Audit route is correct.

### 4.5 Test and evidence completeness

The current pinned local Consent Gherkin run is retained as evidence and should remain in the repository. It does not eliminate the need for a complete Config/Service/Audit operation matrix, official contract validation, negative-path coverage for all mapped operations, model/revision/signature tests, and integration/mock evidence. The official test plan expects coverage across all API groups; local unit and smoke passes must not be overrepresented as full official compliance.[7]

The final evidence package needs immutable source pins, command lines, environment-safe configuration metadata, raw official suite logs/results, hash manifests, candidate artifact hashes, migration state, test summaries, route/schema mapping, and an evidence index. Results must be repeatable after the final remediation; prior passing runs do not substitute for a fresh, final run.

### 4.6 Submission package and functional assessment

The project already contains useful documents such as `CONSENT-SUBMISSION-CHECKLIST.md` and `docs/govstack/testing/`. However, the submission package is incomplete until it combines the accepted technical mapping and evidence with the official functional-requirements assessment, accurate product metadata, repository/documentation links, declaration of limitations, approved integration statement, and authorised account holder review. The testing site treats API test evidence as a prerequisite for the API portion of a software-requirements assessment; it does not automatically submit, certify, or approve the implementation.[8]

## 5. Ordered Item 01 remediation plan

| Priority | Required Item 01 work | Deliverable and acceptance evidence |
|---:|---|---|
| 1 | Freeze the authority boundary. | A version record naming Consent 1.3.0, published `v23Q4` OpenAPI revision, latest-main revision, retrieval date, and explicit release-to-main delta policy. |
| 2 | Build the complete published-contract matrix. | A source-controlled Config/Service/Audit route, schema, status, security, and test mapping for every `v23Q4` OpenAPI operation. |
| 3 | Close model and lifecycle gaps. | Field/invariant map and migrations/serializers/services/tests for Agreement, Record, Policy, Purpose, Data Agreement, revisions, signature, verification, and withdrawal. |
| 4 | Close or explicitly stub integrations. | Tested Identity and Information Mediator adapters, or bounded stub documentation with configuration, failure semantics, and approved justification. |
| 5 | Prove audit/revision completeness. | Consent event inventory, append-only/tamper tests, Audit API list/detail tests, and revision/signature/verification provenance. |
| 6 | Expand testing from local proof to full Item 01 proof. | Happy and failure-path tests for every matrix row, official pinned-suite rerun, mock/interoperability evidence, and preserved checksums/logs. |
| 7 | Assemble and internally review submission artefacts. | Evidence index, functional-requirements matrix, product metadata, documentation/configuration guide, known limitations, and approved local/staging readiness record. |
| 8 | Conduct authorised external testing/submission only after all prior rows are green. | Account-holder action and explicit final approval. This analysis does not authorise or perform submission. |

## 6. Consent submission package checklist — current status

| Acceptance checklist item | Current status | Concrete current evidence and remaining requirement |
|---|---|---|
| Aligns with current GovStack Consent BB specification (latest version) | **Partially aligned** | Consent domain, candidate controls, and local evidence exist. A completed 1.3.0 / published-`v23Q4` matrix and separately labelled latest-main delta are missing. |
| Core APIs present and match official OpenAPI definitions (agreements, records, opt-in/opt-out, audit) | **Partially aligned / unverified** | CivicOS exposes Consent and candidate GovStack routes, and the local Gherkin scope passed. Full published Config, Service, and Audit operation/schema/status/error mapping is not complete. |
| Data models for Agreement, Consent Record, Policy, Purpose are complete | **Partially aligned** | Models, migrations, records, policy/category structures, history, and signature work exist. Complete field, relationship, lifecycle, Purpose, Data Agreement, and revision mapping is missing. |
| Integration points with Identity and Information Mediator are documented and working (or clearly stubbed with justification) | **Still missing for acceptance** | Local authentication and webhook/task foundations exist. A Consent-specific Identity/IM contract, configuration, mock proof, or explicit bounded stub record is needed. |
| Full submission package ready (code + config + docs + tests) | **Not ready** | Candidate packages, evidence folders, and guidance exist. Required authoritative mapping, integration proof, complete test evidence, functional assessment, and authorised submission review are incomplete. |
| Happy-path + failure-path tests pass | **Partially aligned** | Useful local happy/failure tests and a passing pinned Gherkin scope exist. Complete official operation, integration, audit, signature, revision, and negative-path coverage is not demonstrated. |
| Audit/logging of consent actions is present and complete | **Partially aligned** | Hash-chain audit and Consent history/signature foundations exist. Complete event inventory, official Audit API behavior, revision linkage, and mutation coverage must be proven. |

## 7. Ordered work items outside Item 01

| Ordered work item | Status |
|---|---|
| Item 02 | Not started yet – out of scope for this run |
| Item 03 | Not started yet – out of scope for this run |
| Item 04 | Not started yet – out of scope for this run |
| Item 05 | Not started yet – out of scope for this run |
| Item 06 | Not started yet – out of scope for this run |
| Item 07 | Not started yet – out of scope for this run |

## References

[1]: [GovStack Consent Building Block Specification](https://specs.govstack.global/consent/readme.md)
[2]: [GovStack Consent Functional Requirements](https://specs.govstack.global/consent/6-functional-requirements.md)
[3]: [GovStack Consent Data Structures](https://specs.govstack.global/consent/7-data-structures.md)
[4]: [GovStack Consent Service APIs](https://specs.govstack.global/consent/8-service-apis.md)
[5]: [Published `v23Q4` Consent OpenAPI](https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/v23Q4/api/consent-openapi.yaml)
[6]: [Latest official Consent OpenAPI on `main`](https://github.com/GovStackWorkingGroup/bb-consent/blob/main/api/consent-openapi.yaml)
[7]: [Official GovStack Consent test plan](https://github.com/GovStackWorkingGroup/bb-consent/blob/main/test/plan.md)
[8]: [GovStack Testing — Software Requirements Compliance](https://testing.govstack.global/requirements)

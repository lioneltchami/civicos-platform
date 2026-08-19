# Item 01 — Consent Deep Code Review and Remediation Plan

**Date:** 2026-08-18
**Scope:** Item 01 only — Consent submission package
**Review input:** Current CivicOS source snapshot plus `docs/item-01-consent-gap-analysis-20260818.md`
**Review method:** Two fresh, independent, blind code reviews. Reviewers did not receive Stage 1 conversations or reports.

> **Review disposition:** The two blind reviews corroborate the Stage 1 conclusion. CivicOS has substantial Consent foundations, but Item 01 is **partially aligned and not ready for external submission**. The central acceptance issue is the lack of a source-controlled, operation-by-operation proof against the published GovStack Consent `v23Q4` contract, followed by incomplete model/integration/audit completeness evidence.

## 1. Alignment with the gap analysis

| Gap-analysis topic | Blind-review conclusion | Current alignment |
|---|---|---|
| Official authority boundary | The dated gap analysis correctly separates Consent 1.3.0, published `v23Q4`, and latest `main`; the source has no committed comprehensive authority/delta record. | **Partially aligned** |
| Config, Service, and Audit APIs | `apps/consent/govstack_urls.py` and `govstack_views.py` contain broad route families, including policy, data-agreement, individual, webhook, service, verification, record/signature, and audit concepts. | **Partially aligned** — route presence is proven; parity is not. |
| Agreement, Record, Policy, Purpose, and Data Agreement model | `apps/consent/models.py`, serializers, services, and migrations `0007`–`0020` provide a substantive directional model. | **Partially aligned** — no full field/invariant/lifecycle mapping. |
| Identity and Information Mediator | Local authentication, webhooks, tasks, and candidate controls are real; no Consent-specific external contract or approved bounded stub was found. | **Still missing for acceptance** |
| Audit, revisions, and signatures | Hash-chain audit, Consent revisions/history/signature concepts, and audit route classes exist. | **Partially aligned** — complete action provenance and official Audit behavior are unproven. |
| Testing and evidence | Local happy/failure tests, candidate guardrails, and a bounded passing Consent Gherkin run exist. | **Partially aligned** — not complete published operation/integration evidence. |
| Submission package | Checklist, candidate artefacts, test evidence, and documentation exist. | **Still missing** — no complete functional assessment, contract matrix, integration statement, or authorised internal review. |

## 2. Exact remaining gaps and evidence

### 2.1 Operation-level published contract matrix is missing

**Evidence.** `apps/consent/govstack_urls.py` and `apps/consent/govstack_views.py` expose many Config, Service, and Audit views. `apps/consent/models.py` also includes a `ConsentPolicy.harness_alias_id` compatibility control for a reference harness path. None of the reviewed code or documentation provides one source-controlled row for **every** published `v23Q4` OpenAPI operation that identifies the CivicOS route/adapter, request serializer, response serializer, identifier format, status/error handling, security requirement, test, and version disposition.

**Required change.** Add a machine-readable authoritative operation matrix and a human-readable rendered matrix. The matrix must treat the published `v23Q4` OpenAPI as the acceptance baseline and track latest-main differences separately. Each operation must be explicitly classified as `match`, `partial`, `constrained_adapter`, `missing`, or `approved_out_of_scope`; no implicit equivalence is permitted.

### 2.2 Model and lifecycle equivalence is not demonstrated

**Evidence.** `apps/consent/models.py` maps local Policy, Category/Data Agreement, Record, Revision, Signature, and Webhook concepts to GovStack language. `ConsentCategory` contains purpose labels and a generic attributes field, while the migration history includes alignment, signatures, history, replay, and audit work. The blind reviews found no source-controlled field/invariant mapping proving official Agreement, Purpose, Policy, Data Agreement, Consent Record, revision, signature, verification, withdrawal, actor/subject, and provenance semantics.

**Required change.** Add a model-mapping document and machine-readable model inventory that connects every official resource/schema field to the CivicOS model/field/serializer or records a deliberate constrained adapter/approved gap. Add targeted tests for identifier/cardinality, lifecycle transition, withdrawal/revocation, signature verification, revision linkage, and Purpose/Data Agreement relationships. Any need for a new first-class resource is a design decision that must be recorded rather than inferred.

### 2.3 Identity and Information Mediator boundary is not acceptance-ready

**Evidence.** `apps/consent/api_views.py` and tests prove local authentication and object isolation. `apps/consent/tasks.py`, `receivers.py`, webhook migrations, and candidate controls provide integration foundations. Neither review found a Consent-specific Identity subject-mapping contract, Information Mediator message contract, configuration schema, replay/idempotency policy, mock proof, or an approved bounded stub.

**Required change.** Add explicit Item 01 integration-boundary documentation and a fail-closed configuration/stub contract for unavailable Identity and Information Mediator dependencies. The contract must state subject mapping, credential/configuration ownership, input/output message shape, retries, idempotency, replay protection, and failure mode. Add tests proving the stub cannot be mistaken for a working external integration. External credentials or endpoint access remain a decision dependency and must not be fabricated.

### 2.4 Consent audit and provenance completeness is unproven

**Evidence.** `apps/audit/` has hash-chain foundations and verification tooling; Consent has revisions, signatures, history, audit-related migrations, and candidate Audit views. Neither blind review found an event inventory that establishes all administrative and individual Consent mutations are recorded with required actor, subject, resource, revision, before/after state, outcome, source, signature/verification, withdrawal rationale, and webhook-delivery provenance. Official Audit list/detail filters, sorting, schemas, and error behavior lack a complete proof.

**Required change.** Add a Consent event inventory, an event-to-mutation matrix, missing event emission/test coverage, and a published-contract Audit mapping. Add tests for append-only/tamper failure, invalid transition, retrieval/filter/sort, list/detail authorization, revision linkage, signature/verification, and webhook outcome.

### 2.5 Testing and final evidence are incomplete

**Evidence.** `apps/consent/tests/` provides substantial local API, GovStack, model, service, task, and view coverage. `docs/govstack/testing/runs/consent-2026-08-19-revalidated-pinned-7af4b62/` contains a passing local pinned Gherkin result with 2 features, 4 scenarios, and 16 steps. That result is bounded and does not prove every Config, Service, and Audit operation, full mock interoperability, all errors, or final-source repeatability.

**Required change.** Generate tests from the operation matrix or explicitly link one or more tests to every matrix row. Include happy and failure paths, status/error schemas, authorization, malformed identifiers, revision/signature failures, audit retrieval, and integration-stub behavior. Rerun the pinned official suite after final remediation and archive command, input/configuration hashes, raw stdout/stderr/result, candidate artefact hashes, and checksums.

### 2.6 Functional assessment and submission package are incomplete

**Evidence.** `CONSENT-SUBMISSION-CHECKLIST.md`, `docs/govstack/testing/`, candidate manifests, and stored evidence provide a credible starting package. They do not comprise a complete functional-requirements assessment, approved integration statement, contract matrix, evidence index, product metadata validation, known-limitations record, or authorised internal review.

**Required change.** Add an Item 01 submission-package index, functional-requirements assessment template populated only with evidence-backed statements, authoritative version record, contract/model/audit mappings, integration boundary statement, evidence links, known limitations, and reviewer sign-off fields. Keep the explicit no-submission/no-certification boundary until an authorised account holder approves external action.

## 3. Precise Item 01 implementation backlog

| Order | Change | Affected paths or new artefacts | Definition of done |
|---:|---|---|---|
| 1 | Commit the authority/version record. | New `docs/item-01-consent-authority-20260818.md` and machine-readable pin data. | Records 1.3.0, `v23Q4`, latest-main, retrieval dates, hashes, and release/main delta policy. |
| 2 | Add the complete published-operation matrix. | New `docs/item-01-consent-v23q4-operation-matrix.json` and rendered Markdown. | Every Config, Service, and Audit operation is represented with path/method/schema/status/security/test/disposition. |
| 3 | Add the resource and lifecycle mapping. | New `docs/item-01-consent-model-mapping-20260818.md` and test fixtures. | Maps Agreement, Record, Policy, Purpose, Data Agreement, Revision, Signature, Webhook, identifiers, cardinality, lifecycle, and deliberate differences. |
| 4 | Implement bounded Identity/IM integration stubs. | New Consent integration-boundary module/config/docs/tests. | Fails closed without approved configuration; documents and tests all missing external dependencies without falsely claiming interoperability. |
| 5 | Add Consent audit event inventory and contract tests. | New event inventory, tests, and only necessary Consent/audit source changes. | Every mutation is mapped to an auditable event; append-only/tamper/list/detail/filter/authorization coverage is explicit. |
| 6 | Link tests and evidence to each operation row. | New/extended tests in `apps/consent/tests/`, operation-matrix validation tool, updated local test evidence. | Every claimed matrix row has happy/failure test evidence; unimplemented rows remain visible as non-green. |
| 7 | Build internal submission-package artefacts. | New package index and functional-assessment template in `docs/`. | Code/config/docs/tests/evidence/limitations/approval fields are indexed; no external submission claim. |
| 8 | Rerun and archive final local evidence. | New dated run folder under `docs/govstack/testing/runs/`. | Latest source and artefact hashes are archived with fresh command output; only actual results are marked passing. |

## 4. Acceptance checklist before implementation

| Acceptance checklist item | Current review status | Implementation target |
|---|---|---|
| Aligns with current GovStack Consent BB specification (latest version) | **Partially aligned** | Fully mapped published contract, separately labelled latest-main delta, and evidence-backed disposition for every row. |
| Core APIs present and match official OpenAPI definitions (agreements, records, opt-in/opt-out, audit) | **Partially aligned** | Complete operation matrix plus matching implementation/tests or explicit remaining non-green rows. |
| Data models for Agreement, Consent Record, Policy, Purpose are complete | **Partially aligned** | Field/invariant/lifecycle map and code/tests demonstrating every required relationship or an approved bounded gap. |
| Identity and Information Mediator integration is documented and working (or clearly stubbed with justification) | **Still missing** | Fail-closed documented stubs/adapters with configuration and failure tests; working integration only if actual authorised dependencies exist. |
| Full submission package ready (code + config + docs + tests) | **Still missing** | Indexed authoritative artefacts, functional assessment, final test evidence, limitations, and approval fields. |
| Happy-path + failure-path tests pass | **Partially aligned** | Every claimed published operation and declared integration/audit behavior has linked happy/failure evidence. |
| Audit/logging of consent actions is present and complete | **Partially aligned** | Event inventory, provenance coverage, tamper/invalid-transition behavior, and Audit API evidence are complete. |

## 5. Review claim boundary

This code review identifies the concrete work required to reach Item 01 acceptance. It does not state that external dependencies, credentials, staging access, functional-requirements approvals, or testing-site submission authority are available. It does not authorise external submission and does not make a certification claim.

## References

[1]: `docs/item-01-consent-gap-analysis-20260818.md` — dated Stage 1 analysis and current acceptance checklist.
[2]: `apps/consent/models.py`, `serializers.py`, `services.py` — Consent model, mapping, serializer, and lifecycle evidence.
[3]: `apps/consent/govstack_urls.py`, `govstack_views.py`, `api_urls.py`, `api_views.py` — candidate and local Consent route/view evidence.
[4]: `apps/consent/tests/` — existing Consent local tests.
[5]: `apps/audit/` and Consent migration series `0007`–`0020` — audit, history, signature, and schema-evolution evidence.
[6]: `docs/govstack/testing/` and `examples/civicos-consent/` — local candidate and bounded test evidence.

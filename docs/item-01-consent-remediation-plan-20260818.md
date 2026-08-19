# Item 01 — Consent Submission Package: Remediation Plan

**Date:** 2026-08-18
**Controlling source:** `docs/item-01-consent-verification-20260818.md`
**Current conclusion:** Local evidence controls are complete; the package remains **not ready for official GovStack conformance, staging, testing-site submission, or certification claims**.

## Preserve as fully aligned

The following current controls are **Fully aligned** under the latest verification and must not be reopened without new authority or integrity evidence: pinned/traceable published v23Q4 authority; representation of all 42 official operation identities; method/path/documented-status comparison; prevention of unsupported green claims; portable YAML validation and JSON-to-Markdown matrix synchronization; declared PyYAML dependency; and current authority/claim-hash evidence. The fail-closed Identity/Information Mediator boundary is also an implemented safeguard; it is not real interoperability evidence.

## Remaining gaps

| Gap | Evidence | Concrete closure work |
|---|---|---|
| Official operation wire-contract equivalence | All 42 rows in `docs/item-01-consent-v23q4-operation-matrix.json` remain `partial`; latest verification identifies absent route/serializer/status/security/test proof. | Bind every official operation to route/adapter, request/response serializer, identifiers, auth, error/status behavior, and executable success/failure tests. Run the pinned suite against an authorised non-production candidate. |
| Model/lifecycle and audit/provenance parity | `docs/item-01-consent-model-mapping-20260818.md` and `docs/item-01-consent-audit-event-inventory-20260818.md` retain partial mappings. | Complete field/relationship/revision/signature/withdrawal/transition mapping and mutation/audit provenance tests; declare any approved non-equivalence. |
| Identity and Information Mediator interoperability | `docs/item-01-consent-integration-boundary-20260818.md` records a fail-closed local stub because endpoints/credentials were unavailable. | Obtain approved non-production endpoints, credentials and synthetic data; execute identity/mediator success/failure/retry/idempotency/replay tests. |
| Authorised staging and full official-suite execution | `docs/item-01-consent-verification-20260818.md` marks staging and full official results open; recorded suite evidence is bounded local scope. | Deploy an approved candidate, freeze artifact/configuration/migration identity, execute pinned suite, and retain raw results/logs/manifests/checksums. |
| Functional assessment and submission package | `docs/item-01-consent-submission-package-index-20260818.md` records no submission/certification. | Complete product-owner-approved functional assessment, product metadata, limitations, evidence links, and review fields. Only after all evidence is green obtain explicit approval for portal submission. |
| Matrix regression contradiction | `tests/govstack/test_item01_consent_matrix.py` uses `json.loads`; assessment found a missing import in the reviewed source. | Correct the import if still absent, then rerun dependency-complete focused tests and refresh the evidence record. |

## Acceptance checklist — latest verification status

| Checklist item | Status |
|---|---|
| Aligns with current GovStack Consent BB specification | **Partially aligned** |
| Core APIs match official OpenAPI definitions | **Partially aligned** |
| Agreement, Consent Record, Policy, Purpose models complete | **Partially aligned** |
| Identity and Information Mediator integration working or accepted complete stub | **Still missing** |
| Full submission package ready | **Still missing** |
| Happy-path and failure-path tests pass | **Partially aligned** |
| Audit/logging complete | **Partially aligned** |

## Stage 3 final-status update

**Complete for repository-only scope.** The Stage 3 implementation closes the focused regression/import defect and improves evidence documentation. External integration, authorised staging, full official-suite execution, functional assessment approval, and testing-site submission cannot be completed or claimed without separately authorised access and evidence.

# Item 01 — Consent Remediation: Focused Code Review

**Date:** 2026-08-18
**Scope:** Only gaps listed in `item-01-consent-remediation-plan-20260818.md`; fully aligned authority/matrix controls remain untouched.

## Repository-closeable work

| Priority | Change | Exact paths |
|---|---|---|
| P0 | Correct the matrix regression import contradiction and regenerate only evidence derived from the exact test source. | `tests/govstack/test_item01_consent_matrix.py`; `docs/item-01-consent-remediation-plan-20260818.md` |
| P1 | Add a machine-readable operation-contract map linking every official operation to local route/view/serializer/auth/status/test evidence while retaining every disposition as `partial`. | `docs/item-01-consent-v23q4-operation-contract-map.json`; `scripts/validate_item01_consent.py`; `tests/govstack/` |
| P1 | Add local operation-contract tests for map integrity, route/view binding, and negative local outcomes. Do not imply official equivalence. | `tests/govstack/test_item01_consent_operation_contracts.py`; existing Consent test modules if necessary |
| P1 | Harden the fail-closed Identity/Information Mediator boundary with deterministic local configuration/schema/idempotency tests and documentation. | `apps/consent/integration_boundary.py`; `apps/consent/tests/test_integration_boundary.py`; `docs/item-01-consent-integration-boundary-20260818.md` |
| P1 | Add a local audit/lifecycle evidence guard that inventories still-partial mappings rather than falsely promoting them. | `docs/item-01-consent-audit-event-inventory-20260818.md`; local tests only if existing behavior can be asserted safely |
| P2 | Add deterministic local candidate/evidence identity fields and `NOT_READY` guardrails to prevent local artifacts being presented as staging/official/submitted. | `examples/civicos-consent/candidate-manifest.json`; `docs/item-01-consent-submission-package-index-20260818.md`; Item 07 validator integration where safe |

## External blockers — not implementable locally

| Blocker | Required external condition |
|---|---|
| Identity/Information Mediator interoperability | Approved non-production endpoints, credentials, schemas, synthetic data, and observed results. |
| Staging candidate and full official suite | Authorised deployment, immutable candidate/configuration/migration identity, pinned-suite execution, and archived raw evidence. |
| Functional assessment and portal submission | Product-owner review/approval and explicit human confirmation at the official portal action. |

## Non-actions

Do not add network calls, endpoints, credentials, releases, portal actions, staging claims, official-pass claims, or certificate claims. Do not convert any `partial` matrix row to `match` based only on local code/tests.

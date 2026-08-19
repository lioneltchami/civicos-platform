# Item 01 — Consent Submission-Package Verification Record

**Date:** 2026-08-18
**Scope:** Consent only. Payments, Scheduler, File Management, and any other GovStack Building Block are out of scope for this Item 01 record.
**Disposition:** **Locally evidence-complete; not ready to claim official GovStack API conformance, testing-site submission, or certification.**

> This record distinguishes verifiable source and local-test facts from unexecuted external work. A complete matrix of published operations is not proof that CivicOS implements or interoperates with every operation.

## 1. Authority Baseline

The Item 01 evidence package uses the published GovStack Consent **v23Q4** OpenAPI authority, SHA-256 `5d35ed438e60293046522cb69229133ddb0bf23fccafec4d1e9fcf11e0b542cf`. Current-main material is recorded as a separate delta and is not substituted for the acceptance baseline. The matrix contains **42** published operation identities, each retained as `partial` until its route, serialization, status, authentication, and end-to-end behavior are demonstrated against the relevant official test authority.[1] [2]

| Authority component | Local evidence | Result |
|---|---|---|
| Published v23Q4 operation identities | `docs/item-01-consent-v23q4-operation-matrix.json` | 42 of 42 represented. |
| Exact method, path, and status metadata | YAML-based `scripts/enrich_item01_consent_matrix.py` | Regenerated from the supplied pinned OpenAPI. |
| Exact identity/status comparison | `scripts/validate_item01_consent.py --validate --openapi <pinned-openapi>` | Passed during final local validation. |
| Readable evidence rendering | `docs/item-01-consent-v23q4-operation-matrix.md` | Generated from the JSON evidence matrix. |

## 2. Multi-Agent Review and Corrective Work

Two shared-context analysts compared CivicOS Consent source with the pinned official authority. Their dated synthesis is `docs/item-01-consent-gap-analysis-20260818.md`. Two fresh blind reviewers then examined the current codebase only against that gap document; their dated remediation synthesis is `docs/item-01-consent-code-review-20260818.md`.

Two implementation agents received the CivicOS source plus both dated documents. Their integrated work added the fail-closed integration boundary, operation matrix, evidence documents, and focused tests. Two new independent verification agents assessed the resulting source. Their findings required a corrective loop: matrix status values, rendered Markdown synchronization, portable OpenAPI input handling, dependency declaration, and claim hashes were repaired rather than being accepted on assertion.

| Corrective control | Implemented outcome |
|---|---|
| Portable authority input | The enrichment and validator tools accept `--openapi` or `GOVSTACK_CONSENT_OPENAPI`; no developer-specific authority path is hard-coded. |
| Exact metadata parsing | Both tools use YAML parsing for the pinned OpenAPI rather than indentation-sensitive regular expressions. |
| Non-green evidence boundary | All 42 rows remain `partial`; zero rows are recorded as `match`. |
| Matrix/rendering integrity | The Markdown matrix is regenerated from the machine-readable JSON matrix. |
| Dependency declaration | `PyYAML==6.*` is declared in test and development input files and pinned at `pyyaml==6.0.3` in their lockfiles. |
| Evidence integrity | `docs/item-01-consent-claim-hashes.json` records current hashes for the matrix, authority manifest, local source surfaces, validators, enrichment tool, and regression test. |

## 3. Final Local Verification

The following checks passed in the dependency-complete isolated CivicOS environment. Existing Django deprecation warnings were emitted during model loading; no warning was suppressed or represented as a passing external conformance result.

| Check | Result | Evidence boundary |
|---|---|---|
| Exact v23Q4 method/path/status comparison | Passed for 42 operations | Validates authority metadata only. |
| Independent YAML comparison | Passed for 42 operations | Confirms matrix operation identity and status-code values. |
| Item 01 matrix/lifecycle/audit regression module | 3 passed | Confirms local evidence structure and model/audit inventory only. |
| Ruff on changed tools and test | Passed | Source-quality check only. |
| Python compilation | Passed | Syntax check only. |
| Working-tree whitespace check | Passed | Repository hygiene only. |

## 4. Acceptance Checklist

| Requirement | Status | Evidence or required next action |
|---|---|---|
| Published v23Q4 Consent OpenAPI is pinned and traceable | **Pass** | Matrix authority hash and authority manifest. |
| Every published operation is represented or visibly omitted | **Pass** | 42 of 42 are represented; none is silently omitted. |
| Method, path, and documented response statuses equal official source | **Pass** | YAML comparison and validator pass. |
| Unsupported equivalence is blocked from becoming green | **Pass** | All rows remain `partial`; validator rejects unsupported `match` claims. |
| CivicOS implementation matches every official operation wire contract | **Open** | Requires route/view/serializer/status/authentication assertions per operation and official-suite execution. |
| Real Identity integration and credentials | **Open** | Requires approved non-production identity configuration and secrets. |
| Real Information Mediator integration | **Open** | Requires approved non-production mediator endpoint, credentials, and test data. |
| Staging candidate execution | **Open** | Requires deployed authorised non-production environment. |
| Official GovStack testing-suite result | **Open** | Requires the applicable pinned official suite to be executed against the staging candidate. |
| Functional-requirements assessment and product evidence | **Open** | Requires product owner approval, documentation links, and completed assessment form. |
| Testing-site submission | **Open** | Requires all prior evidence plus authorised account access and explicit human approval. |

## 5. What May Be Claimed Now

CivicOS may state that it has a **locally verified, source-traceable Consent v23Q4 evidence package** with full published-operation inventory and intentionally non-green dispositions. It may not state that it is GovStack API conformant, interoperable with a deployed Identity or Information Mediator, testing-site submitted, certified, or ready for certification.

## 6. Required Next Work

The next technical increment is operation-level proof, beginning with a narrow subset of public Candidate operations. For each operation, add an explicit CivicOS route/view/serializer/status mapping and an executable regression test, then rerun the pinned official suite against an authorised non-production candidate. Do not bulk-promote rows from `partial` to `match`.

The next operational increment is to obtain approved non-production Identity and Information Mediator dependencies, synthetic test data, testing-site credentials, and a release owner. Submission must occur only after the official test result, completed software-requirements assessment, and explicit human approval.[2] [3]

## References

[1]: https://specs.govstack.global/consent/readme.md "GovStack Consent Building Block specification index"
[2]: https://govstack-global.atlassian.net/wiki/spaces/GH/pages/221085697/Steps+to+check+compliance+against+a+GovStack+API+spec "GovStack API compliance-testing procedure"
[3]: https://testing.govstack.global/requirements "GovStack software requirements compliance and testing-site requirements"

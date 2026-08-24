# CivicOS Preparation for GovStack Testing

**Prepared:** 2026-08-18  
**Scope:** Local, non-production preparation for the public GovStack testing workflow.  
**Claim boundary:** This document is **not** an API Compliance result, functional self-assessment, testing-site submission, official repository contribution, or certification claim. In plain terms: this is not an API Compliance result.

## Official workflow

The public testing site separates **Software Requirements Compliance** from **API Compliance Testing**. API results are a prerequisite for the API portion of the self-assessment, while the requirements form also expects product information, documentation links, containerization/deployment evidence, functional-requirement self-assessment, and cross-cutting attestations.[1] [2]

The official API procedure asks each contributor to supply a candidate in the relevant Building Block repository under `examples/<candidate>/`, launch it through `test_entrypoint.sh --config api-suite`, normally with Docker Compose, run the pinned suite locally, then open a reviewed pull request to the official specification repository. The public platform displays results only after the official workflow is completed.[3]

> An adaptor is candidate-specific and provides inbound URL and payload mapping between the GovStack API and a product's native interface. It must not be treated as a universal proxy or an outbound-integration mechanism.[4]

## CivicOS candidate packages

The repository now contains local-only candidate packages under [`examples/`](../../../examples/README.md). Each package pins the same official revision recorded in [`docs/govstack/authority-manifest.json`](../authority-manifest.json), generates an ephemeral local Django secret, accepts only `--config api-suite`, refuses non-local targets, starts a disposable Docker Compose candidate, waits for `/health/`, and writes a preflight record. No package embeds credentials, uses production data, opens public ingress, or makes a testing-site submission or certification claim. The pinned Consent suite has been run locally with its raw artifacts preserved under [`runs/consent-2026-08-18-pinned-7af4b62/`](runs/consent-2026-08-18-pinned-7af4b62/); the other three candidates have not yet run their official suites.

| Building Block | Candidate package | Pinned official source | Official suite | Local candidate URL | Current local state |
|---|---|---|---|---|---|
| Consent | `examples/civicos-consent/` | `bb-consent@7af4b62a1c0b0d7073d42b37c71b0f08bea63dda` | `test/gherkin` | `https://host.docker.internal:8888/` | Local-only candidate, observed-mismatch adapter, and deterministic fixture complete. Pinned suite run passed: 2 features, 4 scenarios, and 16 steps. Raw evidence is archived under `runs/consent-2026-08-18-pinned-7af4b62/`. |
| Payments | `examples/civicos-payments/` | `bb-payments@4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a` | `test/openAPI` | `http://127.0.0.1:3333/` | Candidate wrapper and stub boundary complete; official suite has not run. |
| Scheduler | `examples/civicos-scheduler/` | `bb-scheduler@d425be5cc0d6c606f351e5bf89be6d5c6c83c468` | `test/openAPI` | `http://127.0.0.1:3333/` | Candidate wrapper complete; scope remains single-government; official suite has not run. |
| File Management | `examples/civicos-file-management/` | `bb-file-management@cf50bf4952491bd1ede775aa3c90a228319c3977` | `test/openAPI` | `http://127.0.0.1:3003/` | Candidate wrapper and synthetic-data boundary complete; official suite has not run. |

## Local validation sequence

Run this sequence in a disposable development environment. It is designed to preserve evidence without manufacturing a test outcome.

```bash
# 1. Validate all manifests, entrypoints, local-only policies, ports, and pinned revisions.
python3 scripts/govstack_testing_preparation.py --validate \
  --output docs/govstack/testing/preparation-evidence.json

# 2. Start one candidate. This creates result/preflight.json.
./examples/civicos-consent/test_entrypoint.sh --config api-suite

# 3. From a separately reviewed checkout of the matching pinned official source,
#    run its documented test entrypoint. Preserve raw stdout, stderr, generated
#    report, dependency lockfile versions, and exact command in the candidate result/.
```

Do not alter the official test suite, substitute local CivicOS tests for it, or mark `preflight.json` as a compliance report. If the official suite reveals a specific mismatch, record the failing case first. Add an adapter only for the documented inbound URL, header, or payload delta, with a narrow allowlist and redacted logs.

## Requirement self-assessment preparation

The official form evaluates Required, Recommended, and Optional functional requirements. Enter only evidence that is true for the selected Building Block; leave unknown answers blank as the instructions direct.[2] This repository's local evidence map is [`docs/govstack/traceability.json`](../traceability.json), but it is not a substitute for the functional-requirement form or official API result report.

| Evidence required by the form | CivicOS preparation status | Authorised next action |
|---|---|---|
| Product information, documentation, deployment/containerization links | Locally preparable; Dockerfile, Compose and deployment documentation exist. | Provide the canonical product/version and public documentation URLs approved by the product owner. |
| Functional requirement answers and comments | Requires requirement-by-requirement review. | Select a single Building Block and complete the official Required/Recommended/Optional matrix against current code and evidence. |
| API test result link | Consent local evidence is available at `runs/consent-2026-08-18-pinned-7af4b62/`; no testing-site result link exists. | Retain the raw local report, then obtain an authorised official testing-site result before completing the form. |
| Cross-cutting self-attestation | Not yet completed. | Review each requirement with the security and product owners; do not infer a pass from local tests. |
| Form verification and submission | Not performed. | An authorised testing-site account holder must review and submit after evidence is complete. |

## Authorised gates that cannot be implemented from source alone

| Gate | Why it is external | Required input |
|---|---|---|
| Staging deployment and network exposure | Candidate reachability, public TLS, firewall and environment ownership are deployment decisions. | A non-production target, owner-approved route, TLS policy, and test window. |
| Test credentials and integration configuration | Real identities, IM, processor, wallet, callback and cloud settings are secrets or business configuration. | Sanitized, revocable test credentials and explicit scope of use. |
| Official candidate onboarding / pull request | The official procedure associates a candidate with the relevant specification repository and review process. | Maintainer-approved fork/PR path and repository collaboration authority. |
| Testing-site form submission | Submission is an external compliance representation reviewed by GovStack. | Authorised account access and explicit final approval before submission. |

## References

[1]: [GovStack Testing — Software Requirements Compliance](https://testing.govstack.global/requirements)  
[2]: [Instructions for Software Requirements Compliance](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/376012801/Instructions+for+Software+Requirements+Compliance)  
[3]: [Steps to check compliance against a GovStack API spec](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/221085697/Steps+to+check+compliance+against+a+GovStack+API+spec)  
[4]: [GovStack Adaptor Concept](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/215318576)

# GovStack Testing Preparation — Verification Record

**Date:** 2026-08-18  
**Repository baseline before this preparation work:** `20bc151736d93a7df9a49f3685b687868c38c6cc`  
**Scope:** Local, non-production candidate preparation and one pinned official Consent suite execution.  
**Claim boundary:** **This is not a testing-site submission, public certification, or cross-version conformance claim.** It records a reproducible local execution of the stated pinned source and candidate only.

## Official basis

GovStack's public requirements page describes a per-Building-Block self-assessment. The API Compliance result report is a prerequisite for the API portion of the compliance assessment.[1] The official API procedure asks contributors to provide an `examples/<candidate>/test_entrypoint.sh --config api-suite` launcher, normally using Docker Compose, run the relevant suite locally, and submit the candidate through the official repository-review process.[2] The official adaptor guidance describes a product-specific inbound URL/payload mapper rather than a universal or outbound proxy.[3]

## Implemented local controls

| Control | Status | Verification |
|---|---|---|
| Four pinned candidate manifests | Passed | Each manifest matches the repository/revision in `docs/govstack/authority-manifest.json`. |
| Local-only entrypoints | Passed | Each entrypoint accepts `--config api-suite`; every entrypoint rejects `GOVSTACK_TEST_TARGET=staging` with exit code `65` before it can call Docker. |
| Ephemeral test secret | Passed by inspection and structural test | The shared launcher generates a temporary Django secret and does not write it to the repository. |
| Expected harness ports | Passed | Consent `8888`; Payments `3333`; Scheduler `3333`; File Management `3003`. |
| Candidate Compose configuration | Passed | `docker compose config -q` completed for all four candidate overlays on the connected user desktop. No services were started during that configuration validation. |
| Evidence generator | Passed | `python3 scripts/govstack_testing_preparation.py --validate` returned no structural errors and generated `preparation-evidence.json`. |
| Regression suite | Passed | `tests.govstack.test_testing_site_preparation` passed all 4 tests in the isolated dependency-complete environment. |
| Static quality | Passed | Ruff formatting/lint and Bash syntax checks passed for the preparation tool and candidate launchers. |

## Local Consent candidate and pinned official suite

The first Consent candidate startup initially exposed two legitimate local-environment issues: the `clamav/clamav:1.4` image did not publish the host's ARM64 platform, and the official pinned gherkin harness constructs an HTTPS URL for `host.docker.internal:8888`. The candidate was corrected without changing CivicOS's production route surface. The local-only Consent overlay now excludes the unnecessary antivirus service, exposes CivicOS internally on `18000`, provides Caddy internal TLS on `8888`, permits only `host.docker.internal` in the development candidate allowlist, rewrites only the observed `/config`, `/service`, and `/audit` inbound paths to `/api/v1/consent/`, and seeds only deterministic local harness records.

The unmodified `test/gherkin/test_entrypoint.sh` from pinned `GovStackWorkingGroup/bb-consent@7af4b62a1c0b0d7073d42b37c71b0f08bea63dda` was then run against `https://host.docker.internal:8888`.

| Official-suite result | Outcome |
|---|---|
| Features | **2 passed, 0 failed** |
| Scenarios | **4 passed, 0 failed** |
| Steps | **16 passed, 0 failed, 0 skipped, 0 undefined** |
| Serialized result statuses | **22 passed** |
| Raw evidence | `docs/govstack/testing/runs/consent-2026-08-18-pinned-7af4b62/` |

The official harness emitted an amd64-on-arm64 image warning and an obsolete Compose `version` warning. Both are preserved in the raw stderr artifact. Neither changed the passed feature, scenario, or step results.

## Explicitly not performed

No action in this preparation work created a GovStack account, started a testing-site record, uploaded an API report to the testing site, opened an official Building Block repository pull request, used a production database or credential, exposed a public endpoint, ran a staging environment, completed the functional self-assessment, or submitted a compliance form.

## Next controlled action

The technically justified next action is to review the archived Consent evidence with the product and security owners, complete the Consent functional-requirements matrix, and obtain a product-owner-approved public/staging candidate URL and testing-site account workflow. The other three candidate packages remain prepared but have not run their corresponding pinned official suites. Do not infer their compliance from the passed Consent execution.

## References

[1]: [GovStack Testing — Software Requirements Compliance](https://testing.govstack.global/requirements)  
[2]: [GovStack — Steps to check compliance against a GovStack API spec](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/221085697/Steps+to+check+compliance+against+a+GovStack+API+spec)  
[3]: [GovStack — Adaptor Concept](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/215318576)

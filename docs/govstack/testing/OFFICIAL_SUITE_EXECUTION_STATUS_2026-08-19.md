# GovStack Official Suite Execution Status

**Date:** 2026-08-19  
**Boundary:** These are local executions against pinned official source snapshots and local-only CivicOS candidates. They are **not** testing-site submissions, certification results, staging tests, or cross-version compliance claims.

## Authority and procedure

The candidate packages follow the official `examples/<candidate>/test_entrypoint.sh --config api-suite` convention described in the GovStack API testing procedure.[1] The testing site identifies an API Compliance result report as a prerequisite for the API portion of a Building Block self-assessment.[2]

## Observed executions

| Building Block | Pinned official source | Command/result | Outcome | Current interpretation |
|---|---|---|---|---|
| Consent | `bb-consent@7af4b62a1c0b0d7073d42b37c71b0f08bea63dda` | Unmodified gherkin entrypoint with its supported local `CONSENTBB_API_HOST=host.docker.internal:8888` override against the constrained HTTPS candidate | **2 features, 4 scenarios, 16 steps passed** | Initial and post-correction local evidence are archived in `runs/consent-2026-08-18-pinned-7af4b62/` and `runs/consent-2026-08-19-revalidated-pinned-7af4b62/`. |
| Payments | `bb-payments@4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a` | Unmodified `test/openAPI/test_entrypoint.sh` against the local candidate | **Not passing.** The generated message/XML artifacts contain 487 `PASSED`, 74 `FAILED`, and 225 `SKIPPED` result statuses. | The raw result shows root-path redirect responses (`301`) and application-level response mismatches (`500` versus expected suite statuses). No adapter or product fix is justified until each request/response expectation is reviewed against the pinned specification. |
| Scheduler | `bb-scheduler@d425be5cc0d6c606f351e5bf89be6d5c6c83c468` | Official entrypoint executed via `bash` because the Git checkout did not preserve its executable bit | **Harness blocked before scenarios.** The pinned Docker harness installs a published placeholder package rather than a usable local binary. | This is an official-harness dependency/reproducibility blocker, not a CivicOS pass. It must be resolved with the official maintainers or a reviewed, pinned dependency correction; CivicOS must not silently substitute an altered suite. |
| File Management | `bb-file-management@cf50bf4952491bd1ede775aa3c90a228319c3977` | Local candidate entrypoint attempted before the official suite | **Candidate blocked.** `clamav/clamav:1.4` has no `linux/arm64/v8` manifest for the connected host. | This is a local candidate platform blocker. The File Management suite has not run and no compliance inference is possible. |

## Raw local artifacts

The raw Payments and Scheduler harness logs and generated result files remain in their checked-out pinned test directories pending archival into the project run-evidence layout. They must be copied without alteration, accompanied by a manifest recording source revision, command, local-only endpoint, artifact hashes, and the uncommitted candidate source state. The File Management startup diagnostic is preserved by this status record until a candidate run can generate a test artifact.

## Requirements before any submission

A testing-site submission remains inappropriate until the relevant Building Block has a reviewed local/staging candidate, preserved official-suite evidence, a completed functional-requirements matrix, approved product metadata/documentation links, and an authorized testing-site account holder.[1] [2]

## References

[1]: [GovStack — Steps to check compliance against a GovStack API spec](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/221085697/Steps+to+check+compliance+against+a+GovStack+API+spec)  
[2]: [GovStack Testing — Software Requirements Compliance](https://testing.govstack.global/requirements)

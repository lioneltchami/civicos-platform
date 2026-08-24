# Item 04 — File Management Test-Runner Enablement: Gap Analysis

**Date:** 2026-08-18  
**Official reference:** Pinned `GovStackWorkingGroup/bb-file-management` revision `cf50bf4952491bd1ede775aa3c90a228319c3977`.[1]  
**Scope classification:** File Management is a CivicOS project-specific capability, not asserted to be a current core published GovStack Building Block. The official repository is used for applicable testing/harness patterns only.

## Executive result

CivicOS already has a substantial File Management application and test surface. `apps/documents/tests/` contains focused models, services, task, API, integration, security, accessibility, upload, download, retention, audit, and view suites. The existing project runner supports discovery through pytest and Django settings, including a scoped Docker command: `make test-docker-app APP=documents`. The repository CI executes the broader Django suite and therefore includes File Management tests, provided the relevant test modules are discoverable.

> **Current status: Partially aligned / remediation required.** The principal gap is not lack of test code. It is lack of an explicit, documented, File Management-specific test-runner product: one deterministic local/CI command, a test-layer convention, structured report artifacts, a core-operation coverage map, and current scoped execution evidence. The existing local candidate/harness metadata correctly preserves a non-conformance boundary, but the adapter is disabled and no current official harness output exists.

## Applicable official testing pattern

The pinned official repository supplies a useful layered pattern: Docker Compose deployment; Information Mediator/adaptor/API behavior as separate concerns; a health-gated test runner; and persisted result output. Its `test/plan.md` is skeletal but explicitly lists deployment, IM interaction, adapter deployment, and request/response verification as distinct levels.[2] The OpenAPI harness accepts a configurable API URL, waits for health, runs Cucumber, and mounts a result directory for a message-format report.[3] [4]

CivicOS must use these as **engineering references**, not as proof that it has official scenarios or that local tests are official conformance evidence.

## What is already well implemented

| Area | Direct CivicOS evidence | Assessment |
|---|---|---|
| Existing framework | `pyproject.toml:53-68` configures pytest discovery under `apps`, test settings, strict markers, summary reporting, and short tracebacks. | Strong project-level discovery baseline. |
| File Management test breadth | `apps/documents/tests/` includes 26 focused modules, including API, integration, models, upload/versioning/retention services, tasks, audit, scan/promotion, download, view/HTTP contracts, and accessibility. | Strong test substrate; static file presence is not a pass result. |
| Scoped local execution | `Makefile:101-119` provides `pytest`, Docker pytest, and `make test-docker-app APP=documents`. | The application can already be targeted using existing infrastructure; it needs a stable named File Management runner/report contract. |
| CI baseline | `.github/workflows/ci.yml:101-140` installs test dependencies, migrates, checks migration consistency, runs the Django test suite, and validates the OpenAPI schema. | CI covers broad regressions; no dedicated File Management report/artifact lane is present. |
| Candidate safety/provenance | `examples/civicos-file-management/` pins the official revision, limits test data to synthetic ephemeral documents, is local-only, and disclaims compliance. | Appropriate test-safety boundary. The adapter is disabled and no official suite result exists. |
| Existing failure/security coverage | Test modules cover scan gates, soft-deleted/unavailable documents, invalid uploads, authorization, download tokens, quarantine, retention, and task failure behavior. | Credible happy/failure-path source coverage; current scoped pass evidence is required. |

## Gaps requiring remediation

| Priority | Gap | Direct evidence | Required outcome |
|---|---|---|---|
| P0 | No named File Management test-runner command that generates a stable scoped report. | General runner and parameterized `test-docker-app` exist, but no dedicated File Management target/script/report path is documented. | Add one non-production local/CI runner that selects the File Management test suite, returns the native test exit code, records raw output, and emits JUnit/XML (or equivalent) plus coverage where supported. |
| P0 | Current scoped happy/failure execution evidence is absent. | Existing historical output does not establish a current File Management-only result; candidate preflight says `official_suite_executed: false`. | Run the documented File Management runner in the isolated test environment; archive redacted raw output and structured report. |
| P0 | Test-layer boundaries are implicit rather than enforced. | Files such as `test_integration.py` and `test_wave6_integration.py` suggest integration coverage; pytest only defines generic `slow` and `integration` markers. | Adopt documented File Management unit/integration/e2e-or-contract markers (or equivalent runner selections) and validate each selector. |
| P1 | Actionable structured failure reports are not part of the File Management runner contract. | Pytest `-ra` and `--tb=short` help local diagnosis; no dedicated JUnit/report artifact or safe context capture is configured. | Generate a scoped JUnit artifact and raw text log with file/node/line/assertion context; document safe redaction and how to inspect a failure. |
| P1 | Contributor documentation is absent. | Candidate directory has manifest/Compose/launcher/preflight but no focused test-authoring guide. | Add a File Management testing guide: setup, runner commands, layer selection, synthetic fixture policy, naming, failure-path expectations, and CI artifacts. |
| P1 | Local/CI parity is not explicit. | CI runs broad Django tests; candidate launcher is separate and local-only. | Have the same File Management runner selector/report generation execute locally and in CI; keep official-harness execution separately labeled and opt-in. |
| P2 | Core-operation coverage is not enumerated as a test-runner contract. | Tests are broad but dispersed across modules. | Create a machine-readable/Markdown operation matrix mapping core upload/read/download/list/attach/version/delete/scan/quarantine/retention/audit paths to happy and failure tests. |
| P2 | Candidate/official harness evidence is not linked to the local runner. | Official test plan/harness patterns are pinned; CivicOS preflight has no official run and adapter is disabled. | Document the future local-only adapter/harness boundary and preserve its report separately; do not make an official-suite claim. |

## Acceptance checklist

| File Management test-runner enablement checklist | Current status | Evidence and gap |
|---|---|---|
| Test-runner can discover, execute, and report tests specifically for File Management functionality | **Partially aligned** | Pytest discovery and `make test-docker-app APP=documents` exist; no named stable File Management runner with scoped report artifact. |
| Supports the project’s existing test framework (or a clear, documented migration path) | **Fully aligned** | The existing pytest/Django test framework is configured and the File Management suite uses it. No framework migration is needed. |
| Can run both locally and in CI | **Partially aligned** | Local and broad CI execution paths exist; File Management-specific selector/report parity is not established. |
| Clear separation between unit, integration, and end-to-end tests | **Partially aligned** | Integration-named modules and a generic marker exist; the File Management test contract does not define/enforce all three layers. |
| Failure reports are actionable (file + line + expected vs actual + useful context) | **Partially aligned** | Native pytest failures are useful, but no scoped structured artifact/redaction/report-retention contract exists. |
| Documentation exists on how to add new tests for the File Management area | **Still missing** | No focused File Management test guide or fixture/layer policy exists. |
| Basic happy-path and failure-path tests for core File Management operations pass | **Partially aligned** | Strong source coverage is present; Item 04 needs current scoped execution evidence. |
| No regressions introduced to existing File Management or shared test infrastructure | **Partially aligned** | Broad CI and the local suite exist; no current Item 04 before/after scoped and shared regression record has been captured. |

## Stage 3 completion target

Item 04 test-runner enablement will be complete only when the dedicated runner is documented, runs both locally and in CI, selects/labels File Management test layers, produces actionable artifacts, has current happy/failure-path pass evidence, and records no regressions in the shared suite. A local runner is distinct from official-harness evidence. No external submission, conformance, certification, or testing-site claim is authorized by this work.

**Items 1–3 are Already completed. Items 5–7 are Not started yet – out of scope for this run.**

### Stage 3 final-status update

**Stage 3 status:** Implemented and locally validated in an isolated, dependency-complete test environment. The runner is a local/CI CivicOS test product. It does not start containers, contact an external service, run the optional official harness, or make a conformance claim.

| Remediation area | Final status | Evidence / commit / test |
|---|---|---|
| Canonical File Management runner and stable Make targets | **Implemented** | `tools/run_file_management_tests.py`; `make test-file-management`; `make test-file-management-collect`. The runner fixes selection to `apps/documents/tests`, preserves pytest exit status, refuses zero collection, and writes redacted raw/collection logs, JUnit, coverage XML, and metadata. |
| Deterministic unit/integration/e2e separation | **Implemented** | `tools/file_management_test_layers.json` maps every current File Management test module exactly once to an explicit layer without unsafe mass marker reclassification. The static contract validates manifest completeness. |
| Local/CI parity and actionable artifacts | **Implemented in source; remote CI not invoked** | `.github/workflows/ci.yml` invokes the same `make test-file-management` target after the broad Django regression lane and uploads its artifact directory with `if: always()`. No external CI run was triggered in this local-only work. |
| Contributor documentation, fixture/report safety, and official-harness boundary | **Implemented** | `docs/testing/file-management.md` documents synthetic/ephemeral fixtures, cleanup, layers, commands, report redaction, artifacts, and the separately labeled local-only candidate/official-harness boundary. |
| Core happy/failure operation map | **Implemented** | `docs/testing/file-management-operation-matrix.md` maps upload, read/list, download/token, attach/version, delete/retention, scan/quarantine, audit, and contract/accessibility areas to existing happy and failure source tests. |
| Runner regression protection | **Implemented and passed locally** | `tests/test_file_management_runner_contract.py`: **5 passed**. It verifies fixed scope, report policy, zero-test guard, manifest exactness, guide policy, matrix coverage, and Make targets. |
| Full File Management local execution evidence | **Passed locally** | Canonical all-layer run: **1,159 passed**, 387 warnings, 81.90% scoped coverage. Layer runs: **317 unit**, **637 integration**, and **205 e2e** tests passed. Redacted raw/JUnit/coverage/metadata evidence is archived at `docs/govstack/testing/evidence/item04-file-management-20260818/`. |
| Optional official harness / testing-site evidence | **Intentionally not run** | File Management remains a project-specific capability. Candidate metadata remains local-only with its adapter disabled; no external submission, official-suite, conformance, or certification claim was made. |

## References

[1]: https://github.com/GovStackWorkingGroup/bb-file-management/tree/cf50bf4952491bd1ede775aa3c90a228319c3977 "Pinned official File Management repository"
[2]: https://raw.githubusercontent.com/GovStackWorkingGroup/bb-file-management/cf50bf4952491bd1ede775aa3c90a228319c3977/test/plan.md "Official File Management test plan"
[3]: https://raw.githubusercontent.com/GovStackWorkingGroup/bb-file-management/cf50bf4952491bd1ede775aa3c90a228319c3977/test/openAPI/docker-compose.yaml "Official OpenAPI harness Compose configuration"
[4]: https://raw.githubusercontent.com/GovStackWorkingGroup/bb-file-management/cf50bf4952491bd1ede775aa3c90a228319c3977/test/openAPI/docker/entrypoint.sh "Official OpenAPI harness entrypoint"

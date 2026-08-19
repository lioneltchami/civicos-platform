# Item 04 — File Management Test-Runner Enablement: Independent Verification

**Date:** 2026-08-18
**Scope:** Item 04 only — CivicOS File Management test-runner enablement
**Final source revision reviewed:** `c2d00f4`
**Outcome:** **Fully aligned / ready**

## Verification conclusion

Two totally new, blind verification agents independently reviewed the final CivicOS source, the original Item 04 gap analysis, the File Management guide and operation matrix, the portable evidence manifest, and the archived local execution artifacts. They were intentionally not provided the Stage 1/2/3 conversations, implementation patches, or code-review record. Both agents concluded that the eight Item 04 acceptance items are **fully aligned**.

> **Readiness boundary.** “Fully aligned / ready” applies to the stated **Item 04 local/CI test-runner enablement acceptance checklist**. It does **not** claim official GovStack-harness passage, testing-site submission readiness, external deployment verification, certification, or conformance. The File Management candidate remains local-only and its official adapter remains disabled.

| Review area | Independent result | Evidence boundary |
|---|---|---|
| Canonical runner, deterministic layers, Make targets, static CI parity, reporting, redaction, zero-test handling | Fully aligned | Final source and archived local artifacts |
| File Management operation matrix, contributor/fixture guidance, happy/failure mapping, contract coverage | Fully aligned | Final source and archived local artifacts |
| Official-harness, testing-site, certification, and external deployment evidence | Not asserted | Outside Item 04 acceptance scope; no external execution was performed |

## Verification method and evidence integrity

The final verification package contained only the final CivicOS source and the original Item 04 gap analysis. It included final code artifacts required to assess Item 04: the runner, deterministic-layer manifest, Make targets, CI workflow, contributor guide, operation matrix, static runner-contract test, and local evidence archive. The archive’s `SHA256SUMS.txt` uses paths relative to its own directory and was validated successfully with `sha256sum -c SHA256SUMS.txt` before final review.

The review used the official File Management repository only as the earlier **pattern baseline**—not as a claim that CivicOS executed its harness. The pinned authority is `GovStackWorkingGroup/bb-file-management` revision `cf50bf4952491bd1ede775aa3c90a228319c3977`, including its test-plan and OpenAPI-harness layout.[1] [2] [3]

| Evidence class | Location | Verified meaning |
|---|---|---|
| Canonical execution code | `tools/run_file_management_tests.py` | Fixed `apps/documents/tests` scope, all/unit/integration/e2e selection, collection-first guard, pytest exit preservation, JUnit/coverage/raw/metadata outputs, and redaction behavior |
| Deterministic taxonomy | `tools/file_management_test_layers.json` | Every current `test_*.py` File Management module is listed exactly once in a non-empty unit, integration, or e2e layer |
| Static regression protection | `tests/test_file_management_runner_contract.py` | Validates runner scope, layer-manifest exactness, report contract, guide policy, operation matrix, and Make targets |
| Local and CI entrypoints | `Makefile`; `.github/workflows/ci.yml` | `make test-file-management` is canonical locally and invoked by CI; CI retains the artifact directory even on failure |
| Documentation and operation coverage | `docs/testing/file-management.md`; `docs/testing/file-management-operation-matrix.md` | Contributor instructions, synthetic/ephemeral fixture policy, redaction policy, candidate/official boundary, and happy/failure operation mapping |
| Archived local evidence | `docs/govstack/testing/evidence/item04-file-management-20260818/` | Portable checksums plus all/unit/integration/e2e raw, collection, JUnit, coverage, and metadata artifacts; runner-contract JUnit |

## Independent acceptance checklist

| # | Acceptance item | Final status | Final evidence |
|---:|---|---|---|
| 1 | A dedicated File Management test runner discovers, executes, and reports only the intended test scope. | **Fully aligned** | `tools/run_file_management_tests.py` fixes scope to `apps/documents/tests`; `Makefile` exposes `test-file-management`; archived all-layer metadata records the scoped selector and 1,159 collected tests. |
| 2 | The runner uses the established pytest/Django framework without an unsafe migration. | **Fully aligned** | The runner invokes `python -m pytest` with `config.settings.test`; no replacement framework or generic test-path rewrite was introduced. |
| 3 | The runner can be invoked locally and is wired into CI with actionable artifact retention. | **Fully aligned** | Local `make test-file-management` and `make test-file-management-collect` targets are present; CI invokes the same canonical target and uploads `test-results/file-management/` with `if: always()`. Static wiring is verified; a remote CI run was not requested or claimed. |
| 4 | Unit, integration, and e2e selections are explicit, deterministic, non-empty, and maintained against actual test files. | **Fully aligned** | The manifest defines exact module sets; the contract test verifies one-time, complete matching against `apps/documents/tests/test_*.py`; evidence records 317 unit, 637 integration, and 205 e2e tests. |
| 5 | Failure reporting is useful and safe. | **Fully aligned** | The runner generates redacted raw/collection logs, JUnit XML, coverage XML, and non-sensitive metadata. The guide requires test node/file/line/assertion context while prohibiting secrets, tokens, cookies, authorization headers, document contents, and environment values. |
| 6 | Contributors have clear guidance for new File Management tests. | **Fully aligned** | `docs/testing/file-management.md` documents commands, layers, manifest maintenance, synthetic/ephemeral fixtures, cleanup, artifact handling, redaction, and the local-only candidate boundary. |
| 7 | Happy-path and failure-path coverage for core operations is traceable and passed in local scoped execution. | **Fully aligned** | The operation matrix maps upload, read/list, download, attach/version, delete/retention, scan/quarantine, audit, and view/accessibility to happy and failure/rejection tests. The archived all-layer run passed 1,159 tests with 81.90% scoped coverage; each layer’s JUnit reports zero errors, failures, and skips. |
| 8 | The change set protects existing File Management and shared test infrastructure from regression. | **Fully aligned** | The dedicated static contract reports 5 passing tests; the scoped runner uses a fixed app path and separate report directory; the CI workflow keeps the broad Django regression lane before the File Management runner. This is a bounded local/static regression conclusion, not a claim of a current remote CI run. |

## Local validation record

All results below came from the isolated, dependency-complete local test environment. The report archive is checksum-protected using the portable manifest at `docs/govstack/testing/evidence/item04-file-management-20260818/SHA256SUMS.txt`.

| Execution | Result | Result details | Archived evidence |
|---|---:|---|---|
| Canonical all-layer runner | **Passed** | 1,159 passed; 387 warnings; 81.90% scoped coverage; exit code 0 | `all/` |
| Unit layer | **Passed** | 317 passed; 67 warnings; exit code 0 | `unit/` |
| Integration layer | **Passed** | 637 passed; 182 warnings; exit code 0 | `integration/` |
| E2E layer | **Passed** | 205 passed; 272 warnings; exit code 0 | `e2e/` |
| Runner static contract | **Passed** | 5 passed; 31 warnings | `runner-contract.junit.xml` |
| Make collection target | **Passed** | Canonical `make test-file-management-collect` collected 1,159 scoped tests and emitted metadata | Captured in the Stage 3 validation record and archive metadata |
| Evidence manifest | **Passed** | All 21 archived files validated with the portable relative-path checksum manifest | `SHA256SUMS.txt` |

## Constraints preserved

The work preserved the required safety and evidence boundaries. It did not start Docker or other containers, send data to external services, use production data or secrets, contact the testing site, submit a Building Block, run the optional official harness, or make a conformance/certification claim. The local candidate remains separate from the official harness, as documented in the final guide and candidate metadata.

## Commit traceability

| Commit | Change |
|---|---|
| `aa731ab` | Canonical Item 04 File Management runner, deterministic layers, static regression contract, Make targets, and CI wiring |
| `e1ba472` | File Management contributor guide and core-operation coverage matrix |
| `e0ae287` | Stage 3 final-status update and archived redacted local validation evidence |
| `c2d00f4` | Portable relative-path evidence checksum manifest |
| _pending commit_ | This independent verification record |

## Next-step gate

The Item 04 acceptance gate is satisfied. Item 05 may be opened only under its own explicitly scoped workflow and must not inherit any official-harness or testing-site claim from this local/CI runner work.

## References

[1]: https://github.com/GovStackWorkingGroup/bb-file-management/tree/cf50bf4952491bd1ede775aa3c90a228319c3977 "Pinned official GovStack File Management repository"
[2]: https://raw.githubusercontent.com/GovStackWorkingGroup/bb-file-management/cf50bf4952491bd1ede775aa3c90a228319c3977/test/plan.md "Official File Management test plan"
[3]: https://raw.githubusercontent.com/GovStackWorkingGroup/bb-file-management/cf50bf4952491bd1ede775aa3c90a228319c3977/test/openAPI/docker-compose.yaml "Official File Management OpenAPI harness configuration"

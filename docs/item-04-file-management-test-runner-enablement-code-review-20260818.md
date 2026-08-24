# Item 04 — File Management Test-Runner Enablement: Blind Code Review

**Date:** 2026-08-18  
**Review input:** Current CivicOS source and the committed Item 04 gap-analysis record only. Two fresh reviewers were blind to Stage 1 conversation and official-source bundle.  
**Current conclusion:** **Partially aligned / remediation required.**

## Independent result

The blind review confirms that File Management already has a credible test corpus and uses the project’s established Django/pytest infrastructure. The generic runner is not absent: pytest discovers tests under `apps`, and `make test-docker-app APP=documents` can target the File Management application. The candidate package also has appropriate local-only, synthetic-data, non-certification guardrails.

The reviewers converge that these existing mechanisms do not yet form a complete **File Management test-runner product**. In particular, the current local selector is parameterized rather than named/stable; CI uses Django’s runner rather than the local pytest contract; no scoped raw/JUnit/coverage artifact contract exists; test layer selection is not enforced; and no current Item 04 scoped run ties happy/failure coverage to the reviewed commit. Candidate preflight is preparation evidence only.

## Alignment with Stage 1

| Stage 1 claim | Blind-review determination | Direct evidence |
|---|---|---|
| Existing File Management test framework and corpus are substantial | **Confirmed.** File Management uses project-native test infrastructure and broad test modules. | `pyproject.toml:53-68`; `apps/documents/tests/`; `apps/api/documents/`; `Makefile:101-119`. |
| Existing generic app runner can target documents | **Confirmed with qualification.** `make test-docker-app APP=documents` is usable but not a stable Item 04 runner/report contract. | `Makefile:109-112`. |
| CI offers broad regression but not scoped local/CI parity | **Confirmed.** CI runs Django tests after migration, not the pytest selector or structured File Management reports. | `.github/workflows/ci.yml:109-140`. |
| Test layers are implicit | **Confirmed.** Integration-named modules exist but no File Management-specific unit/integration/e2e marker convention was found. | `pyproject.toml:65-68`; `apps/documents/tests/`. |
| Candidate preflight is not test-pass or official evidence | **Confirmed.** Candidate remains local-only, adapter-disabled, synthetic-data-only, and explicitly non-conformance preparation. | `examples/civicos-file-management/candidate-manifest.json:16-22`; `result/preflight.json`. |

## Code-review findings

### Runner and CI parity

Pytest’s configuration is a strong baseline: conventional test discovery, test settings, strict marker behavior, `-ra`, and short tracebacks are configured. The Makefile provides full-suite, Docker full-suite, parameterized app selection, coverage, and parallel modes. However, the parameterized target does not ensure an intended app value, create a report directory, emit JUnit/XML, capture a stable raw log, or define test-layer selections. A dedicated, backward-compatible runner must be added rather than changing generic targets.

CI’s broad Django lane should remain the shared regression gate. Item 04 needs a separate File Management lane/step that invokes the same canonical runner command used locally, has explicit test settings, and uploads redacted raw/JUnit/coverage artifacts regardless of success or failure. This should not be confused with the future optional official harness.

### Test taxonomy and coverage traceability

The source contains units that are logically models/services/tasks, integration-style suites, and view/API contract tests. File names alone do not create an enforceable taxonomy. The implementation must provide unit, integration, and e2e-or-contract selectors. A marker strategy should be selected only if the existing suites can be accurately categorized; otherwise a checked-in selection manifest is acceptable. The runner must reject an empty selector result to prevent false-green executions.

The test corpus has meaningful happy and failure tests, especially across upload, scanning, audit, storage errors, authorization, download token, retention, and deletion behavior. It nevertheless lacks a concise operation map that tells maintainers which exact node(s), layer(s), and expected positive/negative behavior cover each core operation.

### Reporting, documentation, and safety

Native pytest failure messages can be actionable, but Item 04 needs a repeatable artifact contract: JUnit plus raw redacted output, scoped coverage where possible, and a documented report directory. Artifacts must not expose document names, object keys, credentials, or sensitive test values. The contributor guide must define synthetic-only fixtures, temporary storage/cleanup, settings/service requirements, naming, layer selection, expected failure paths, running the suite, and reading CI artifacts.

## Required implementation backlog

| Priority | Required change | Acceptance evidence |
|---|---|---|
| P0 | Add one canonical File Management runner and stable Make target. Select `apps/documents/tests`, set deterministic test settings, preserve native exit code, write raw output, emit JUnit, and emit scoped coverage when supported. | One documented command; a testable script with a unique result directory; non-zero propagation; collection summary; no zero-test success. |
| P0 | Use the same runner in CI without removing broad Django regression coverage. Upload redacted raw, JUnit, and coverage artifacts on success and failure. | CI job/step with parity command, `always()` artifact upload, and no official-suite label or claim. |
| P0 | Run the canonical runner in the isolated environment and archive current happy/failure evidence linked to commit and settings. | Redacted raw log, JUnit, coverage, environment metadata, and pass/fail count. |
| P1 | Establish File Management unit/integration/e2e-or-contract taxonomy and independently runnable selectors. | Marker/selection manifest, documented commands, expected collection counts, and no-empty-selection checks. |
| P1 | Add File Management testing/fixture/report guide. | Versioned guide covering setup, tests/layers, synthetic data, temporary storage, cleanup, service mocking, adding tests, and report inspection. |
| P1 | Establish safe report redaction/retention policy. | Runner redacts known sensitive patterns; tests/validation demonstrate no credentials or fixture PII are retained. |
| P2 | Add a core-operation matrix linked to test node IDs and test layers. | Matrix covers upload/read/download/list/attach/version/delete/scan/quarantine/retention/audit with happy and failure paths. |
| P2 | Document the distinct boundary between local Item 04 runner evidence and optional candidate/official-harness evidence. | Candidate README/guide language and separate result namespaces. |
| P2 | Preserve generic runner behavior with regression checks. | Existing generic targets remain unchanged; focused runner validation plus broad test lane passes. |

## Implementation constraints

Stage 3 must work only on File Management test-runner enablement. It may modify shared test infrastructure only when necessary and only in a backward-compatible way. It must not treat local runner output as a testing-site submission or conformance certificate, must not submit externally, and must use synthetic/ephemeral data only. Items 1–3 are complete; Items 5–7 remain not started and out of scope.

## Readiness

**Not ready.** The next step is bounded implementation of the runner, taxonomy, reporting, documentation, and evidence path described above, followed by focused validation and independent blind verification.

## References

[1]: [Item 04 Gap Analysis](item-04-file-management-test-runner-enablement-gap-analysis-20260818.md)
[2]: `Makefile` — generic and parameterized test targets
[3]: `pyproject.toml` — pytest discovery and reporting defaults
[4]: `.github/workflows/ci.yml` — CI test lanes
[5]: `examples/civicos-file-management/candidate-manifest.json` — candidate boundary
[6]: `apps/documents/tests/` — File Management test corpus

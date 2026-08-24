# CivicOS GovStack Testing and Submission Implementation Guide

**Version:** 1.0  
**Prepared:** 2026-08-19  
**Author:** Manus AI  
**Purpose:** This guide defines the repeatable path from a CivicOS Building Block candidate to an authorised GovStack testing-site submission. It applies the official testing-site requirements to the CivicOS Consent, Payments, Scheduler, and File Management candidates while preserving a strict distinction between local evidence, staging evidence, and an external submission.[1] [2]

> **Core rule:** A local suite result is evidence, not a certification or submission. Each Building Block must independently satisfy the technical test evidence, functional-requirements assessment, product metadata, authorised account, and human approval gates before it is submitted.[1]

## 1. Current baseline and release decision

The repository contains local-only candidate packages under `examples/`, reproducible preparation checks, pinned-run evidence, and a common candidate launcher. The combined implementation is committed as `25c0f15` (`test: prepare GovStack API candidates and evidence`). The current Block status is deliberately uneven; the correct next stage depends on the individual Block rather than on a platform-wide readiness declaration.

| Building Block | Current technical evidence | Current decision | Next stage |
|---|---|---|---|
| Consent | Pinned official local gherkin suite: 2 features, 4 scenarios, 16 steps passed. | Eligible to begin the requirements-assessment and submission-preparation stages, but **not yet submit**. | Stage 8. |
| Payments | Pinned official OpenAPI suite ran; the recorded result contains passing, failed, and skipped cases. | Not ready. Product/API mismatches must be triaged from raw evidence. | Stage 5. |
| Scheduler | Pinned official harness reached health checking but its published test dependency was a dependency-confusion placeholder. | Not ready. Obtain an official-maintainer-supported, pinned harness remedy. | Stage 6. |
| File Management | Candidate startup was blocked by the current ARM64 host’s inability to pull the required ClamAV image. | Not ready. Establish an approved compatible local or CI test runner. | Stage 3. |

The detailed status record is `OFFICIAL_SUITE_EXECUTION_STATUS_2026-08-19.md`; the implementation and independent-review evidence is `IMPLEMENTATION_REVIEW_VERIFICATION_2026-08-19.md`.

## 2. Roles, environments, and non-negotiable safeguards

| Role | Responsibility | May approve or perform |
|---|---|---|
| CivicOS engineering owner | Owns source changes, candidate adapters, fixture design, and regression coverage. | Local and staging technical changes. |
| Security and privacy owner | Reviews credentials, synthetic data, TLS, logs, retention, and outbound routing. | Staging test-data and secret-use approvals. |
| Product/Building Block owner | Completes the functional-requirements matrix and chooses the claimed product scope. | Functional compliance statements and documentation. |
| GovStack testing-site account holder | Maintains the authorised account and completes the external form. | External upload and submission only after approval. |
| Release approver | Reviews the final evidence package and accepts residual risk. | Final submission decision. |

All local candidate runs must use synthetic data, generated local secrets, loopback-only or explicitly approved local endpoints, and the repository’s source-controlled candidate packages. No candidate launcher may contact a production-like target; the shared launcher rejects `GOVSTACK_TEST_TARGET` values other than `local`.

## 3. Stage 1 — Select one Building Block and lock the authority

The team must select **one** Building Block at a time. Do not combine failures, evidence, or claims from separate Blocks. Record the official repository, exact Git commit, test-suite directory, requirements version, selected CivicOS module, and local candidate package before any test begins.

| Building Block | CivicOS candidate | Official authority | Pinned suite convention |
|---|---|---|---|
| Consent | `examples/civicos-consent/` | [GovStack Consent](https://github.com/GovStackWorkingGroup/bb-consent) | `test/gherkin/` |
| Payments | `examples/civicos-payments/` | [GovStack Payments](https://github.com/GovStackWorkingGroup/bb-payments) | `test/openAPI/` |
| Scheduler | `examples/civicos-scheduler/` | [GovStack Scheduler](https://github.com/GovStackWorkingGroup/bb-scheduler) | `test/openAPI/` |
| File Management | `examples/civicos-file-management/` | [GovStack File Management](https://github.com/GovStackWorkingGroup/bb-file-management) | `test/openAPI/` |

**Exit criteria.** The owner has created or confirmed a run manifest that identifies the official commit and records the candidate’s intended local endpoint. No mutable branch name may be used as the sole authority.

## 4. Stage 2 — Run structural candidate preparation checks

From the CivicOS repository root, execute the local structural checks before starting containers. These commands validate source-controlled metadata; they do not claim that an official suite passed.

```bash
bash -n examples/_common/candidate_common.sh \
  examples/civicos-{consent,payments,scheduler,file-management}/test_entrypoint.sh \
  tests/test_candidate_launcher_allowlist.sh

./tests/test_candidate_launcher_allowlist.sh
python3 scripts/govstack_testing_preparation.py \
  --validate \
  --output docs/govstack/testing/preparation-evidence.json
```

The candidate manifests must retain the official revision, the local-only target policy, a clear non-claim boundary, and the known CivicOS GovStack surface. The shared launcher must use only the fixed service mapping and only the approved Consent fixture command. A change to any manifest, adapter, route, fixture, or launcher requires repeating this stage.

**Exit criteria.** Shell syntax, allowlist regression tests, and the preparation validator pass; the generated preparation evidence names the current source state accurately.

## 5. Stage 3 — Provision the disposable local candidate

Start only the candidate under test. The entrypoint accepts only the official candidate convention and always creates a temporary local Django secret. It should be invoked from the CivicOS repository root.

```bash
./examples/civicos-consent/test_entrypoint.sh --config api-suite
./examples/civicos-payments/test_entrypoint.sh --config api-suite
./examples/civicos-scheduler/test_entrypoint.sh --config api-suite
./examples/civicos-file-management/test_entrypoint.sh --config api-suite
```

Do not set `GOVSTACK_TEST_TARGET` to anything but `local`. Do not override `GOVSTACK_CANDIDATE_SERVICES` or `GOVSTACK_CANDIDATE_SEED_COMMAND`; the launcher will reject values that are not the exact fixed per-Block allowlist. The Consent candidate is intentionally the only candidate that can seed its deterministic local fixture.

For ARM64 development hosts, Payments and Scheduler reset the unrelated root ClamAV dependency. File Management retains it because scanner behaviour belongs to its scope. If the scanner image cannot run on the selected architecture, move File Management testing to an approved compatible runner or use a reviewed compatible scanner design. Do not bypass scanning merely to make the test harness start.

**Exit criteria.** The candidate health endpoint is reachable, any local-only fixture is seeded, and the candidate is confirmed not to contain production credentials or production data.

## 6. Stage 4 — Execute the unmodified pinned official suite and archive it

Clone the authoritative repository and check out the exact source revision recorded in the candidate manifest. Do not edit the official suite. If macOS source control did not preserve the executable bit, invoke the script through `bash` rather than changing official source files.

```bash
# Example: Consent, using the official suite's supported local host override.
cd /path/to/bb-consent/test/gherkin
CONSENTBB_API_HOST=host.docker.internal:8888 \
CONSENTBB_API_PATH='' \
bash ./test_entrypoint.sh
```

The API testing procedure expects the official test suite to be executed against the candidate, with the resulting report retained as evidence.[2] Archive, without alteration, the raw stdout, stderr, result payloads, XML/HTML if generated, exact command, candidate endpoint, official revision, relevant candidate artifact hashes, and SHA-256 checksum manifest. Use the repository pattern:

```text
docs/govstack/testing/runs/<block>-<date>-pinned-<short-sha>/
├── run-manifest.json
├── official-suite.stdout.log
├── official-suite.stderr.log
├── official-suite.result.json or official-suite.message
├── candidate-artifact-sha256.txt
└── SHA256SUMS
```

A harness exit code alone is not enough. Inspect feature/scenario/step results or serialized status records. The Scheduler evidence demonstrates why: a harness may exit zero even though an unusable placeholder dependency prevented scenarios from executing.

**Exit criteria.** Either a complete passing result is archived, or a complete non-passing/blocking result is archived with a precise classification.

## 7. Stage 5 — Triage non-passing results without guessing

Classify every non-passing case into one of four categories. The assigned owner must link each row to the raw evidence and the pinned specification before any source change is proposed.

| Category | Example | Required action |
|---|---|---|
| Candidate routing or transport mismatch | A harness requires a root path or TLS endpoint while CivicOS exposes a scoped internal route. | Implement a narrow inbound adapter only for the observed request mapping; add boundary tests. |
| Product API behaviour mismatch | An official scenario expects a defined response/status while CivicOS returns an error or divergent body. | Compare exact request, response, status, auth, and state transition to the pinned specification; change product code only after review. |
| Official harness blocker | A pinned suite dependency is unusable or no scenarios execute. | Preserve evidence and obtain an official-maintainer-supported remedy; do not silently fork the suite. |
| Local platform/infrastructure blocker | A required image or runtime cannot run on the current architecture. | Use an approved compatible runner or reviewed portable implementation; preserve the blocker evidence. |

Payments is currently in the second category. Its 74 failed statuses must be converted into a request-by-request matrix before an adapter or code change is selected. Scheduler is in the third category, and File Management is in the fourth.

## 8. Stage 6 — Implement a reviewed correction and add regression coverage

A correction must be constrained to the observed failure. It must neither broaden external access nor conceal a failing official expectation. The preferred pattern is:

1. Add a focused regression test that reproduces the observed request and expected local boundary.
2. Implement the smallest product or local-only adapter change that satisfies the approved mapping.
3. Re-run the CivicOS regression suite, candidate structural checks, and the **unmodified pinned official suite**.
4. Archive a new run rather than overwriting the prior failed result.

For example, the Consent adapter maps only the observed `/config`, `/service`, and `/audit` inbound paths to CivicOS’s existing Consent mount. It is local-only, uses internal TLS, and retains the original CivicOS application surface. This follows the principle that an adapter is justified by an observed interoperability boundary, not created pre-emptively.[3]

**Exit criteria.** The correction has code review approval, focused regression coverage, a passing rerun or clearly preserved remaining failure, and no unsupported compliance claim.

## 9. Stage 7 — Repeat until the Block has a complete passing technical evidence package

A Block can progress only when the latest archived official result is fully passing and all prior relevant failures have traceable resolutions. The final technical package must contain the authority lock, candidate manifest, adapter/configuration hash, raw official results, checksums, regression results, and a concise evidence summary.

| Technical completion gate | Required evidence |
|---|---|
| Official source fixed | Repository URL and immutable commit SHA. |
| Candidate isolated | Local/staging endpoint, synthetic data statement, secret handling, and adapter/config hash. |
| Official suite complete | Raw report plus scenario/step totals and serialized status interpretation. |
| Results repeatable | Fresh rerun after the final correction, not merely an earlier passing result. |
| Boundaries explicit | Statement that the result is Block-specific and does not certify the overall platform. |

## 10. Stage 8 — Complete the functional-requirements assessment

Technical API evidence is only one part of the testing-site workflow. The Building Block product owner must complete the official software-requirements compliance assessment, identifying the product, repository/documentation links, implementation version, feature-level compliance statements, evidence references, and any declared limitations.[1]

The assessment must use the exact Block scope tested in the technical package. Do not write "CivicOS is GovStack compliant"; state the specific Building Block, exact official revision, test date, evidence package, and any remaining limitations. The technical owner should review links and hashes, while the product/privacy owner reviews functional, data-protection, and policy assertions.

**Exit criteria.** The requirements matrix is complete, internally reviewed, and references the final passing technical evidence package.

## 11. Stage 9 — Run the authorised staging rehearsal

Before submitting a Block, run the final candidate against an approved non-production staging environment if the testing workflow or deployment topology requires it. The environment must use test-safe credentials, synthetic records, controlled outbound integrations, and an approved rollback/cleanup plan. Record the staging URL only in the secured evidence location if it is not public.

The same official source revision and test command must be used. Any difference from the local candidate—identity provider, payment sandbox, queue, database, storage, scanner, TLS terminator, or adapter—must be included in the evidence manifest and reviewed by security.

**Exit criteria.** The technical owner, security owner, and product owner agree that the staging evidence is equivalent to the submitted candidate scope.

## 12. Stage 10 — Authorised testing-site submission

Only the authorised account holder should enter or upload material to the external testing site. Before opening the form, convene a short submission review and confirm the following checklist.

| Submission checklist | Required answer |
|---|---|
| Is the named Building Block the only scope claimed? | Yes. |
| Is the official revision pinned and the latest archived result fully passing? | Yes. |
| Is the functional-requirements matrix complete and internally approved? | Yes. |
| Are product documentation, repository, contact, and version fields accurate? | Yes. |
| Are no production secrets, personal data, private endpoints, or unreviewed logs included? | Yes. |
| Has the release approver explicitly authorised submission? | Yes. |

The submitted result must identify the specific Building Block and version. If the testing site asks for an external test account, token, or staging endpoint, obtain it through the approved account owner and record only non-sensitive identifiers in the evidence manifest. Submission must be separately confirmed immediately before it occurs.

## 13. Recommended implementation order for CivicOS

1. **Consent:** Complete the functional-requirements assessment and submission package first, because its pinned local official suite has a passing result.
2. **Payments:** Build a detailed failure matrix from the archived 74 failed statuses, approve a narrow remediation plan, and rerun the pinned suite until fully passing.
3. **Scheduler:** Ask the GovStack maintainers for the supported pinned package/dependency resolution or a corrected official harness revision. Do not infer a replacement test implementation.
4. **File Management:** Establish an approved ARM64-compatible scanner/runtime or execute on an approved amd64 runner, then run the pinned suite and start failure triage.

## 14. Operational commands and cleanup

After each local run, bring down the disposable candidate stack and retain the evidence directory. Do not delete result files until their checksums have been verified.

```bash
# Example cleanup; use the matching candidate and local environment file.
docker compose \
  --project-name civicos-consent-testing \
  --env-file /path/to/local-generated.env \
  -f docker-compose.yml \
  -f examples/civicos-consent/docker-compose.yml \
  down --remove-orphans
```

The candidate’s temporary secret file is removed by the launcher. The generated run artefacts, manifests, and checksums are durable audit evidence and should be committed only after they have been reviewed for synthetic-data and secret-safety compliance.

## References

[1]: [GovStack Testing — Software Requirements Compliance](https://testing.govstack.global/requirements)  
[2]: [GovStack — Steps to check compliance against a GovStack API spec](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/221085697/Steps+to+check+compliance+against+a+GovStack+API+spec)  
[3]: [GovStack — Adaptor Concept](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/215318576)

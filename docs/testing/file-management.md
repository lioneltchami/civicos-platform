# File Management test-runner guide

This guide defines the **local/CI candidate runner** for `apps/documents`. It is not an official GovStack harness and produces no conformance or certification result.

## Commands

Run `make test-file-management` locally, or invoke `python tools/run_file_management_tests.py`. Use `make test-file-management-collect` to validate deterministic discovery without executing tests. Reports are written under `test-results/file-management/` as a redacted raw `pytest-<layer>.log`, a redacted collection log, JUnit XML, scoped coverage XML, and non-sensitive JSON metadata. Pass `--report-dir` to use an ephemeral directory. The runner returns pytest's native exit code; a selection that collects zero tests returns code `5` and is never treated as a success.

Layer selectors are `--layer all`, `--layer unit`, `--layer integration`, and `--layer e2e`. The authoritative layer manifest is `tools/file_management_test_layers.json`; it intentionally maps every current `apps/documents/tests/test_*.py` module exactly once without relabeling established tests through generic pytest markers. Update the layer manifest, operation matrix, and this guide together when adding or moving a File Management test. The current suite is predominantly unit/service and Django integration coverage.

| Layer | Meaning | Safe data boundary |
|---|---|---|
| `unit` | Isolated model, service, validation, and task logic | In-memory or pytest temporary data |
| `integration` | Django/API/database behavior and service boundaries | Test database and synthetic files only |
| `e2e` | Browser or full HTTP workflow contract | Local test server and ephemeral fixtures only |

## Fixtures and failure reports

Use factories or temporary paths; never use production documents, PII, object-storage credentials, or persistent shared volumes. Fixtures must clean up files and database records. Failure reports should contain the pytest node id, file and line, assertion output, and safe synthetic identifiers. Do not capture tokens, cookies, authorization headers, document contents, or environment values. CI uploads the scoped raw/collection logs, JUnit XML, coverage XML, and non-sensitive metadata. Artifacts must be treated as diagnostic output rather than external evidence; redact tokens, cookies, authorization headers, environment values, object keys, document contents, and other sensitive values before retaining them.

## Operation matrix

The matrix at `docs/testing/file-management-operation-matrix.md` is the review contract. New tests should map to at least one operation and include a happy path plus a relevant rejection, authorization, unavailable, or failure path where applicable.

## Candidate/official boundary

`examples/civicos-file-management/` remains local-only. Its pinned revision and disabled adapter are provenance metadata, not an instruction to run containers or contact an external harness. The official-harness execution path, if ever approved, must be a separately labeled, opt-in workflow with separately retained results.

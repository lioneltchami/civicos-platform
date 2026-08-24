# CivicOS PR #1 — CI Ruff Version Alignment

**Date:** 2026-08-24
**Scope:** PR #1 post-public CI remediation, Ruff Increment 1 only
**Status:** Local validation complete; pending normal push and GitHub Actions confirmation

## Finding

The first post-remediation CI run for PR #1, `32701021372`, installed unpinned Ruff `0.16.4` and failed `ruff check .` with 83 findings, including rule families not present in the repository-validated Ruff `0.8.4` baseline. The same repository state passes both configured lint commands under Ruff `0.8.4`.

## Focused Remediation

The CI workflow now installs `ruff==0.8.4`. The required commands are unchanged:

```bash
ruff check .
ruff format --check .
```

No Ruff rule, exclusion, ignore, workflow job, test, dependency, or source-code behavior was relaxed or removed. This is a deterministic tool-version alignment so CI evaluates the exact lint baseline used for the repository-wide Ruff remediation.

## Local Verification

The following commands completed successfully with Ruff `0.8.4`:

```bash
ruff check .
ruff format --check .
```

The PR remains review-only. This record does not authorize merge, deployment, staging, secret access, release, submission, payment/provider activation, or any GovStack certification or conformance claim.

# Item 06 — Authorised Staging Rehearsals: Independent Verification

**Date:** 2026-08-18
**Final source revision reviewed:** `0886b88`
**Outcome:** **Partially aligned / remediation required**

## Independent conclusion

Two totally new blind verifiers examined only the final source/configuration/documentation package and the original Item 06 gap analysis. Both concluded that CivicOS now has **materially stronger, fail-closed repository preparation** for a future authorised staging rehearsal, but the actual authorised staging-rehearsal checklist is not complete. No remote staging action was authorised or performed.

> **Safety boundary.** A repository manifest, validator, runbook, control template, and static test do not prove an environment exists, a deployment is authorised, a rollback works, observability is usable, or a rehearsal passed. The remaining checklist rows require explicitly approved remote execution and retained evidence.

## Checklist verification

| # | Acceptance item | Independent status | Concrete evidence |
|---:|---|---|---|
| 1 | Staging environment provisioned and topology-comparable | **Still missing** | `docker-compose.prod.yml` is a repository topology definition, but no named remote inventory, TLS/DNS, data/service, resource, storage, or monitoring evidence exists. |
| 2 | Items 1–5 deployable through one reliable command or pipeline | **Partially aligned** | `.github/workflows/ci.yml`, Compose files, and deployment notes provide preparation; no protected staging promotion, release manifest completion, immutable image verification, or executed run exists. |
| 3 | Authorisation and access control correct/enforced | **Still missing** | `docs/staging/CONTROL_TEMPLATES_AND_REGRESSION_MATRIX.md` defines the required role/MFA/OIDC/approval controls but truthfully marks remote evidence blocked. |
| 4 | End-to-end rehearsal scripts documented and pass | **Still missing** | `scripts/validate_item06_staging_preflight.py` is an offline-only preflight guard; the future rehearsal sequence is documented but no authorised target-bound pass result exists. |
| 5 | Rollback documented, tested, reliable | **Still missing** | The control template records the required backup/restore/RPO/RTO/rollback evidence but no authorised restore or rollback execution is available. |
| 6 | Observability usable in staging | **Still missing** | The evidence policy specifies redacted logs, metrics, traces, dashboards, alerts, acknowledgement, and audit evidence; no staging artifacts or dashboards exist. |
| 7 | Secrets and configuration managed securely | **Partially aligned** | Existing Compose hygiene and production hardening plus manifest secret-key rejection provide repository safeguards; remote secret-store, rotation, access-audit, and injection evidence are absent. |
| 8 | No regressions to Items 1–5 | **Partially aligned** | The Item 1–5 regression matrix is complete and correctly blocked pending authorised staging evidence; local/previous-item results cannot substitute for a staging regression run. |
| 9 | Evidence of successful authorised full rehearsal | **Still missing** | `docs/staging/rehearsal-manifest.example.json` is intentionally non-authorised, network-disabled, synthetic-only, unresolved, and `NOT_RUN`; no approval, timestamped execution, redacted raw result, rollback result, defect disposition, or sign-off exists. |

## Verified repository-safe improvements

| Control | Evidence |
|---|---|
| Non-secret, non-executing rehearsal contract | `docs/staging/rehearsal-manifest.example.json` defaults to `authorized=false`, disables network action, requires synthetic data, and records `NOT_RUN`. |
| Offline fail-closed preflight | `scripts/validate_item06_staging_preflight.py` rejects unresolved required fields, unsafe execution settings, secret-like keys, and non-blocked outcomes without making network calls. |
| Controlled future process | `docs/staging/CONTROL_TEMPLATES_AND_REGRESSION_MATRIX.md` provides access/approval, rollback/restore, evidence/redaction, observability, and regression templates. |
| Regression safety | `tests/test_item06_staging_rehearsal.py` checks that the example fails closed and preserves `NOT RUN`/`BLOCKED` semantics. |
| Local validation | Syntax compilation and standard-library assertions passed; the example manifest correctly exits non-zero with an expected `BLOCKED` result. |

## Remaining authorised remote conditions

Before Item 06 can be **Fully aligned / ready**, an authorised human must define the staging target, permitted operators, time window, synthetic/approved data policy, deployment and rollback authority, evidence destination, and the remote actions allowed. The authorised execution must then retain evidence for topology parity, protected promotion/image integrity, IAM/MFA/OIDC and audit records, secret-store/configuration controls, deployment/migration, Items 1–5 regression and failure paths, backup/restore/rollback, logs/metrics/traces/dashboards/alerts, redaction, defect disposition, and release-authority sign-off.

This document does not start Item 07 and does not authorise testing-site submission.

## Commit traceability

| Commit | Purpose |
|---|---|
| `1391ae1` | Item 06 gap analysis |
| `5b7f9b8` | Blind staging code-review remediation plan |
| `3de17c3` | Staging rehearsal controls and regression matrix |
| `74779c6` | Offline preflight validator and static regression test |
| `0886b88` | Truthful Stage 3 preparation status |
| _pending_ | This verification record |

## References

[1]: https://specs.govstack.global/technical-specifications/building-blocks.md "GovStack Building Blocks"
[2]: https://specs.govstack.global/readme.md?ask=What%20cross-cutting%20security%2C%20deployment%2C%20operational%2C%20observability%2C%20and%20testing%20evidence%20requirements%20apply%20to%20a%20GovStack%20Building%20Block%20staging%20rehearsal%3F "GovStack cross-functional staging-rehearsal requirements"

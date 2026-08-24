# Item 06 — Authorised Staging Rehearsals: Blind Code Review

**Date:** 2026-08-18
**Reviewed assessment:** `docs/item-06-authorised-staging-rehearsals-gap-analysis-20260818.md`
**Conclusion:** The Stage 1 assessment is directionally accurate and appropriately conservative. It requires the corrections below to become a definitive safe implementation plan.

## Confirmed findings

The repository contains local/prod-style Compose definitions, settings modules, a CI workflow, deployment notes, candidate launchers, and secret-hygiene tests. These are meaningful preparation assets. The review confirms that they do not demonstrate a provisioned staging environment, protected promotion, remote IAM, deployed secret store, executed rollback/restore, usable observability, or a successful authorised rehearsal. The assessment correctly does not make such claims.

## Required corrections

| ID | Priority | Correction | Evidence |
|---|---|---|---|
| CR-01 | Must-fix | Add a versioned staging-rehearsal manifest/schema that binds commit SHA, intended image digest, migrations, configuration fingerprint, authorised target alias, data classification, required approvals, abort criteria, rollback owner, and evidence location. It must fail closed if placeholders or approval state are missing. | No release/promotion manifest is visible; `docker-compose.prod.yml` and deployment notes are insufficient as an executed record. |
| CR-02 | Must-fix | Add a repository-safe preflight command that validates manifest completeness, allowlisted target aliases, synthetic-data-only intent, required evidence paths, and no secret values. It must never make a network call or deployment. | Existing candidate launchers are local-focused; no target/approval/evidence contract exists. |
| CR-03 | Must-fix | Add a rehearsal runbook and evidence bundle template covering preflight, deploy authorization, smoke/rehearsal steps, redacted output, abort/rollback/restore, audit/observability checks, approval, and post-run sign-off. | `docs/DEPLOY_NOTES.md` is deployment guidance, not a controlled rehearsal runbook. |
| CR-04 | Must-fix | Add rollback/restore and migration-compatibility templates with RPO/RTO, backup ownership, restore verification, and explicit prohibition on recording a test as completed without authorised execution. | No remote rollback/restore test evidence exists. |
| CR-05 | Must-fix | Add an Item 1–5 staging regression matrix that names each bounded acceptance evidence and requires a redacted result reference in a future authorised rehearsal. | Prior item records are local/bounded; no staging regression matrix exists. |
| CR-06 | Should-fix | Add a log/artifact redaction policy and static validation that rejects secret-like key/value names from rehearsal manifests/evidence. | `tests/test_compose_secret_hygiene.py` protects Compose, not rehearsal artifact content. |
| CR-07 | Should-fix | Add CI trust/supply-chain hardening plan: least-privilege workflow permissions, pinned action/tool versions, provenance/SBOM/image-signature evidence requirements. | `.github/workflows/ci.yml` uses floating major action tags and CI checks alone do not prove promotion integrity. |
| CR-08 | Should-fix | Add staging control inventory: environment roles, MFA/OIDC, break-glass, access expiry/review, security/audit log destination, alert routing/on-call, evidence custodian. | No remote IAM/observability evidence is available from repository. |

## Safe Stage 3 implementation scope

Stage 3 may add only local documentation, schemas, validators, static tests, and CI/readiness policy changes that make unauthorised staging activity fail closed. It must not create or use remote credentials, call a staging host, run Docker against a remote context, migrate a remote database, alter DNS/TLS, trigger deployment, access real logs, or fabricate a successful rehearsal.

| Path | Required safe implementation |
|---|---|
| `docs/staging/` | Rehearsal runbook, rollback/restore template, evidence policy, remote-authorisation checklist, regression matrix. |
| `docs/staging/rehearsal-manifest.example.json` | Non-secret, non-routable example manifest with explicit `authorised=false` default. |
| `scripts/validate_staging_rehearsal_manifest.py` | Standard-library offline validator; reject unapproved/non-allowlisted/secret-like fields and validate required evidence/ownership fields. |
| `tests/test_item06_staging_rehearsal.py` | Static regression tests for manifest, runbook markers, no-secret policy, regression matrix, and explicit non-execution boundary. |
| `docs/item-06-authorised-staging-rehearsals-gap-analysis-20260818.md` | Update final status with repository-safe controls completed and remote checklist rows truthfully still blocked/not evidenced. |

## Remote authorisation requirements

The following cannot be completed in the repository-only scope: environment provisioning/inventory, access assignment/review, secret-store verification, protected pipeline promotion, image signature verification in target registry, deployment, migration, backup/restore execution, DNS/TLS check, external-provider integration, observability dashboard/alert check, or end-to-end rehearsal. The authorising human must supply target identity, approved operators, time window, data policy, deployment/rollback owner, permitted test actions, evidence destination, and explicit consent to conduct remote operations.

## Definitive Item 06 readiness condition

Repository controls can be made fully prepared, but **Authorised staging rehearsals cannot be fully complete without an actual separately approved staging execution and retained evidence**. Stage 3 must document this distinction rather than change checklist status by assertion.

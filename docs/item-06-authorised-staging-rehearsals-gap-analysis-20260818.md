# Item 06 — Authorised Staging Rehearsals: Gap Analysis

**Date:** 2026-08-18
**Scope:** Repository-visible preparation for authorised staging rehearsals only
**Status:** Stage 1 assessment complete; no remote staging action authorised or performed

## Evidence boundary

This assessment uses the official GovStack cross-functional guidance and repository-visible CivicOS code, Compose files, CI, settings, deployment notes, local candidate packages, and prior Item 01–05 records. No staging URL, cloud account, deployment credential, access-control inventory, remote secret manager, pipeline run, rollback execution, observability dashboard, or authorised test window was supplied. Therefore, absence of this evidence is recorded as **not evidenced**, not as proof that an external resource does not exist.

> **Authorisation boundary.** This Item 06 work has not deployed, provisioned, accessed secrets, altered DNS/TLS, run migrations, executed a remote test, triggered a pipeline, or called an external integration. An actual authorised staging rehearsal requires a separate human approval and environment access.

## Official authority

GovStack describes Building Blocks as independently deployable, composable modules that expose REST services.[1] Its cross-functional requirements make security and deployment controls observable or auditable: TLS 1.3+, least-privilege access, secure configuration, sanitised logs, CI security gates, tested incident/DR procedures, reproducible builds, documented backup/restore and rollback, image integrity, idempotent deployment, resource limits, and automated functional tests.[2]

## Repository-visible baseline

| Area | Evidence observed | Bounded assessment |
|---|---|---|
| Container topology | `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`, `nginx/Dockerfile` | Reproducible local/production-style topology definitions exist; parity with an actual staging deployment is not evidenced. |
| CI | `.github/workflows/ci.yml` | A CI workflow exists for tests and checks; no deployment/promotion or protected-environment run is visible. |
| Configuration | `config/settings/base.py`, `development.py`, `production.py`, `test.py`; `docker-compose.prod.yml` | Environment-specific configuration exists; remote secret-store injection and authorised access policy are not evidenced. |
| Secret hygiene | `tests/test_compose_secret_hygiene.py`, Compose config | Repository-level secret hygiene is tested; actual secret ownership, access expiry, rotation, and audit are not evidenced. |
| Deployment guidance | `docs/DEPLOY_NOTES.md`, `docs/DOCKER_COMPOSE_PROD_UPLOAD.txt`, `docs/CIVICOSBB_API_VPS_MIGRATION.md` | Informational deployment material exists; an approved staging runbook, preflight, rollback/restore proof, and rehearsal record do not. |
| Building Block local evidence | Item 01–05 records and candidate packages under `examples/` | Items 1–5 are already completed in their own bounded scopes; local evidence is not a complete staging deployment. |

## What is already well prepared

CivicOS has a useful repository foundation for future rehearsal work: containerised service definitions, production settings, CI checks, local candidate packages for the four assessed Building Blocks, secret-hygiene tests, and retained local/official-suite evidence within its respective scope. The Item 05 assessment also provides a blocked, accountable gate for later staging and testing-site work. These are preparation assets, not proof of a deployed staging environment.

## Gaps before authorised staging rehearsals

| Domain | Gap | Priority | Evidence / consequence |
|---|---|---|---|
| Staging topology | No provisioned, named, access-controlled staging environment or topology-parity evidence. | **Must-fix** | Compose is repository configuration; no remote inventory, image digest, resource limit, TLS, DNS, database, Redis, worker, storage, or monitoring evidence is retained. |
| Repeatable deployment | No single approved staging promotion command/pipeline, protected environment, image-signing verification, or release manifest. | **Must-fix** | CI has no demonstrated deployment workflow; deployment notes are not an executed promotion record. |
| Access control | No evidence of deployer/tester/log/secret roles, least privilege, MFA/OIDC policy, break-glass handling, access review, or audit destination. | **Must-fix** | Repository cannot prove external IAM control. |
| Rehearsal scripts | No fail-closed, target-bound, authorised end-to-end rehearsal script with synthetic-data mode, evidence sink, abort conditions, and outcome manifest. | **Must-fix** | Existing tests/candidate adapters are local-focused and do not represent a complete staging rehearsal. |
| Rollback and restore | No approved rollback/restore procedure, migration compatibility policy, restore test, RPO/RTO target, or execution evidence. | **Must-fix** | Deployment notes do not prove recovery. |
| Observability | No demonstrated centralized logs, metrics, traces, dashboards, alert routing, alert acknowledgement, or redaction validation in staging. | **Must-fix** | Local logs/tests do not prove operational observability. |
| Secrets/config | No evidence of a remote secret manager, rotation, access audit, staging-specific configuration inventory, or secret-free rehearsal artifact policy. | **Must-fix** | Repository hygiene is necessary but insufficient. |
| Regression/rehearsal evidence | No timestamped, authorised full rehearsal covering Items 1–5 and failure paths, with approval and redacted raw output. | **Must-fix** | No truthful completion claim can be made without an approved remote run. |

## Prioritised remediation

| Priority | Required change |
|---|---|
| **Must-fix** | Define a protected staging environment, approved identity/role model, reproducible promotion procedure, secret/config contract, rehearsal manifest, rollback/restore test procedure, observability evidence contract, and a human-approved remote rehearsal record. |
| **Should-fix** | Add a repository-safe preflight/rehearsal evidence schema that fails closed without explicit target/approval and retains only redacted artifacts; add a release-readiness manifest linking commit, image digest, migrations, configuration version, approvals, and evidence. |
| **Nice-to-have** | Add topology diagrams, ownership matrix, automated evidence hashes, synthetic-data packs, dependency fault-injection scenarios, and rehearsal trend reports. |

## Items 1–5 and Item 7 status

| Ordered item | Status for Item 06 context |
|---|---|
| Items 1–5 | **Already completed** in their documented, bounded scopes. No Item 06 statement upgrades them to staging, official, or submission readiness. |
| Item 7 | **Not started yet – out of scope for this run.** |

## External authorisation required to complete remote-only conditions

The following actions cannot be completed safely from this repository alone: provisioning or inspecting staging; viewing or changing secrets; assigning or reviewing IAM; configuring DNS/TLS; deploying/promoting an image; executing migrations; testing backup/restore/rollback; viewing real logs/metrics/traces/alerts; triggering external integrations; and executing or approving a full rehearsal. A future human approval must specify the target, environment owner, authorised identities, permitted time window, synthetic/approved data policy, abort criteria, rollback owner, evidence retention location, and explicit permission to perform the intended remote actions.

## Authorised staging rehearsals checklist

| Checklist item | Current status | Evidence |
|---|---|---|
| Staging environment provisioned and topology-comparable | **Still missing** | Repository-only Compose definitions; no authorised remote inventory. |
| Items 1–5 deployable with one reliable command/pipeline | **Partially aligned** | CI/Compose and deployment notes exist; no protected promotion pipeline/run. |
| Authorisation and access control correct/enforced | **Still missing** | No remote IAM, role, approval, or audit evidence. |
| End-to-end rehearsal scripts documented and pass | **Still missing** | Local tests/candidates exist; no authorised staging rehearsal script/result. |
| Rollback documented, tested, reliable | **Still missing** | Notes exist; no rollback/restore execution evidence. |
| Observability usable in staging | **Still missing** | No dashboards/logs/metrics/traces/alerts evidence. |
| Secure secrets/configuration management | **Partially aligned** | Repository secret hygiene exists; remote secret manager/access controls unverified. |
| No regressions to Items 1–5 | **Partially aligned** | Local CI/test evidence exists; staging regression evidence absent. |
| Clear evidence of successful authorised full rehearsal | **Still missing** | No approved remote rehearsal was performed. |

## Stage 3 final-status update

**Status at Stage 1:** Documentation and safe repository controls may be strengthened in Stage 3. Completion of remote-only checklist rows requires external authorisation and evidence; Stage 3 must not fabricate, simulate as real, or claim a staging run.

## References

[1]: https://specs.govstack.global/technical-specifications/building-blocks.md "GovStack Building Blocks"
[2]: https://specs.govstack.global/readme.md?ask=What%20cross-cutting%20security%2C%20deployment%2C%20operational%2C%20observability%2C%20and%20testing%20evidence%20requirements%20apply%20to%20a%20GovStack%20Building%20Block%20staging%20rehearsal%3F "GovStack cross-functional staging-rehearsal requirements"


---

## Stage 3 final-status update

**Repository-safe preparation completed:** `docs/staging/rehearsal-manifest.example.json` provides a non-secret, synthetic-data-only, network-disabled manifest with `authorized=false`. `scripts/validate_item06_staging_preflight.py` is an offline fail-closed validator; it rejects unresolved approvals/targets/releases/ownership/evidence, unsafe execution permissions, non-synthetic data intent, secret-like keys, and any outcome other than `NOT_RUN` or `BLOCKED`. `docs/staging/CONTROL_TEMPLATES_AND_REGRESSION_MATRIX.md` adds access/authorisation, rollback/restore, redaction/evidence, and Items 1–5 regression controls. `tests/test_item06_staging_rehearsal.py` protects the non-execution boundary.

**Remote-only conditions remain blocked/not evidenced:** provisioning, protected promotion, IAM/MFA/OIDC, remote secret configuration, TLS/DNS, deployment, migrations, backup/restore execution, centralized observability, external integrations, and a successful authorised full rehearsal. No external action or completion claim was made. These controls now have an explicit future rehearsal contract, but their remote acceptance checklist rows cannot turn green until a human authorises an environment and evidence-producing execution.

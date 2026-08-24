# CivicOS Platform — Four-Reviewer Validated Implementation Brief

**Repository revision:** `254936445ae9e66cb80fb701f63e99981ec81bee`  
**Source snapshot SHA-256:** `8d0bbfdfa40d1bfc16ac3159df99032fad8707bf267e69ce8495e5c4b2b80e4e`  
**Purpose:** This is the single implementation input synthesized from two independent codebase deep dives and two new, code-by-code validation reviews. It deliberately distinguishes source-proven work from hypotheses and disputed claims.

> Implement only the approved items in this brief. Recheck every cited location against the checkout you receive. If the checkout differs from the stated revision or an item cannot be implemented without a broader product decision, record the discrepancy rather than expanding scope.

## Review convergence

The original deep dives identified a Django/Wagtail modular monolith with server-rendered portals, a DRF API, Celery/Redis workflows, a separate Vite frontend, extensive domain applications, and multiple deployment paths. The two validating reviewers then independently checked the material claims against the immutable source snapshot. The table below records the synthesis; it does not simply repeat unverified findings.

| Area | Final assessment | Evidence basis | Implementation disposition |
|---|---|---|---|
| Root `test.db` file | **Confirmed repository-hygiene defect.** The file is present and empty. | Repository root `test.db`; `.gitignore:22` contains `db.sqlite3` but does not cover `test.db`. | **Approved.** Remove the tracked artifact and prevent it returning. |
| Frontend quality checks in CI | **Confirmed coverage omission.** The frontend declares `lint` and `build`, while the reviewed CI path runs Python-oriented checks without a frontend dependency-install/lint/build lane. | `civicos-site/package.json:6-10`; `.github/workflows/ci.yml:85-120`. | **Approved.** Add a bounded frontend CI job or steps using the package manager indicated by the lockfile. |
| Production-settings module absent | **Rejected.** One initial report incorrectly asserted that `config.settings.production` was missing. The source contains it. | `config/settings/production.py:17-44`; `config/asgi.py:11`; `config/wsgi.py:11`; `docker-compose.prod.yml:98,145,176,215`. | **Do not create or replace the production settings module.** |
| Production-settings CI smoke check | **Confirmed release guardrail.** Production settings exist and intentionally fail closed, yet the ordinary CI test lane uses test settings. | `config/settings/production.py:20-50`; `config/settings/test.py:49-127`; `.github/workflows/ci.yml:87-102`. | **Approved, with care.** Add a minimal import/system-check gate with explicit non-secret CI-safe configuration. Do not weaken production checks. |
| Development Compose placeholder secret | **Confirmed development hygiene concern; not demonstrated production vulnerability.** | `docker-compose.yml:112-116,155-158,190-193,212-215`; services select development settings. | **Not approved for this implementation pass.** Do not change local-developer behavior without an agreed secrets policy. |
| SQLite/eager-Celery default test topology | **Confirmed coverage limitation, not a proven runtime defect.** | `config/settings/test.py:49-55,81-96`; `config/settings/base.py:172-180,347-398`; CI provisions PostgreSQL and Redis. | **Approved as an incremental guardrail only.** Add a non-destructive migration/production-settings verification first; do not invent a large asynchronous integration harness. |
| Migration graph complexity | **Confirmed operational risk; no broken migration proved.** | Migration inventory and active chains, including `apps/payments/migrations/` and `apps/volunteers/migrations/`; project verification targets. | **Approved.** Add a safe CI migration-consistency/plan check if it can run without external deployment state. |
| Frontend/API contract, authorization, payment/webhook, task idempotency | **No concrete defect established.** | The reviews found architecture surfaces but no failing path, exploit, or broken invariant. | **Out of scope.** Do not change domain behavior or security policy based on conjecture. |

## Approved implementation scope

The implementation team should make the smallest coherent set of repository changes required for the following four outcomes.

| ID | Required outcome | Target locations | Acceptance criteria |
|---|---|---|---|
| I-01 | Remove the committed database artifact and prevent accidental reintroduction. | Repository-root `test.db`; `.gitignore` | `test.db` is deleted from version control; `.gitignore` ignores root/local SQLite artifacts without excluding intentional migration code; repository tests remain runnable. |
| I-02 | Exercise the existing frontend quality contract in CI. | `.github/workflows/ci.yml`; `civicos-site/package.json`; existing lockfile, if present | CI installs frontend dependencies via the repository-supported package manager, then executes the declared `lint` and `build` scripts from `civicos-site`. The new job is independent of Python test state where practical. |
| I-03 | Add a production-settings startup/deploy-check guardrail without altering runtime policy. | `.github/workflows/ci.yml`; optionally a focused settings test only if needed | A CI-safe step imports Django under `config.settings.production` and runs a suitable Django deployment/system check. It supplies only dummy non-secret required values, must not contact external payment/storage/email systems, and must preserve production fail-closed behavior for missing real deployment configuration. |
| I-04 | Add lightweight migration consistency validation. | `.github/workflows/ci.yml`; existing test/management-command conventions | CI detects model changes lacking migrations and validates the migration plan/graph using the existing supported test topology. Do not squash or rewrite historic migrations. |

## Implementation constraints

| Constraint | Requirement |
|---|---|
| Source of truth | Re-open the current source and line references before patching. Treat this brief as a validated task list, not a substitute for code inspection. |
| Scope control | Do not add new domain features, change payment behavior, modify authorization, or loosen production settings/security to make checks pass. |
| Secrets | Do not commit real credentials, production hostnames, keys, or certificates. Use deterministic dummy values that satisfy only configuration parsing where a CI command needs environment values. |
| CI compatibility | Reuse the package manager, action versions, and dependency-cache conventions already present in the workflow and frontend directory. Avoid adding a toolchain that the repository does not use. |
| Test topology | Preserve the fast test settings. New checks should be additive and bounded; a full worker/broker integration suite is not required in this pass. |
| Documentation | Update comments or contributor documentation only when it prevents confusion about a newly added CI verification step. |

## Required verification evidence

Before returning a patch, implementation agents must provide the exact commands run, their exit status, and any command that could not run with a reason. At minimum, attempt the commands supported by the local checkout after installing only declared dependencies.

| Verification | Expected evidence |
|---|---|
| Frontend | From `civicos-site`, run the declared dependency install command, then the `lint` and `build` scripts. |
| Django static checks | Run the project’s established lint/type/test or management checks from `Makefile`/CI where available. |
| Production settings guardrail | Run the exact import or `manage.py check --deploy --settings=config.settings.production` command used in CI with the same dummy environment values. Verify that ASGI/WSGI import behavior is compatible with the selected settings module. |
| Migrations | Run `python manage.py makemigrations --check --dry-run --settings=config.settings.test` or the repository-equivalent command, plus a non-destructive migration-plan check. |
| Diff audit | Confirm that `test.db` is removed, no credentials are added, and only the approved files changed. |

## Explicit non-goals

The previous review work did **not** establish any payment-webhook bypass, cross-tenant authorization bypass, task idempotency failure, migration failure, frontend/API mismatch, unsafe production default, or documentation misrepresentation. These topics may warrant separately scoped adversarial or integration testing, but they are intentionally excluded from this patch to avoid unsupported changes.

## Evidence references

[1]: `civicos-site/package.json:6-10` — frontend `lint` and `build` scripts.  
[2]: `.github/workflows/ci.yml:47-120` — service setup and current CI checks.  
[3]: `config/settings/production.py:17-50` — existing production configuration and fail-closed validation.  
[4]: `config/settings/test.py:49-127` — deliberate SQLite/eager-Celery/test-security overrides.  
[5]: `config/settings/base.py:172-180,347-398` — base database and Celery topology.  
[6]: `.gitignore:22`; repository-root `test.db` — database-artifact hygiene evidence.  
[7]: `Makefile:85-123` — repository verification targets.

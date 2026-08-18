# CivicOS Platform — Low-Priority Four-Agent Implementation Brief

**Committed baseline:** `284ce7d` (`test: add service-backed integration contracts`)
**Remediation under review:** `LOW_PRIORITY_REMEDIATION_2026-08-17.patch`
**Purpose:** This is the sole implementation input synthesized from two independent remediation passes and two fresh blind code-by-code reviews. It lists only convergent, evidence-backed corrections for a final isolated implementation pass.

> Reinspect the actual checkout before editing. Implement only the approved work below. Do not commit secrets, weaken production validation, introduce a replacement fixed secret, provision an external secret manager, or alter service topology.

## Review convergence

| Area | Convergent conclusion | Evidence |
|---|---|---|
| Compose services | The remediation safely replaces the known development literal for the `web`, `worker-webhooks`, `worker-receipts`, and `beat` services with required Compose interpolation. | `docker-compose.yml` four application-service environment blocks. |
| Developer onboarding | `.env.example` and `README.md` correctly direct developers to generate and use an uncommitted local secret. | `.env.example` Django-core block; README setup block. |
| Production boundary | Production settings and deployment configuration are not changed or coupled to the development Compose secret. | `config/settings/production.py`; patch scope. |
| Direct development startup | `config/settings/development.py` retains a known `default=` fallback for `DJANGO_SECRET_KEY`, allowing non-Compose development startup to use a known value. This is an inherited limitation and a patch omission for complete development-path hygiene. | `config/settings/development.py:19-22`; `config/settings/base.py`. |
| Validation strength | Current source guardrails validate text but do not prove direct Django missing-secret behavior, service names, YAML parsing, or runtime Compose interpolation. Docker was unavailable to the reviewers. | `tests/test_compose_secret_hygiene.py`; reviewer validation logs. |

## Approved implementation scope

| ID | Required outcome | Acceptance criteria |
|---|---|---|
| FLI-01 | **Remove the direct development-settings secret fallback.** | `config.settings.development` must require `DJANGO_SECRET_KEY` from the environment without a known default. The ordinary test settings must continue to supply their test-only value through their intended configuration. No new literal fallback may be introduced. |
| FLI-02 | **Strengthen focused guardrails for the complete development secret contract.** | Update/add tests that name the four Compose application services, verify required interpolation/no fallback, verify the blank `.env.example` plus documented generation step, and demonstrate that direct development settings fail without `DJANGO_SECRET_KEY` and load successfully with a process-local generated/review-only value. Tests must not persist a secret or require Docker. |
| FLI-03 | **Retain clear runtime Compose validation instructions without overclaiming execution.** | Keep documentation/validation guidance for empty and populated `docker compose config` checks. Do not claim these commands executed if Docker is unavailable. If Compose is available in the final environment, run both checks without starting services. |

## Constraints

| Area | Constraint |
|---|---|
| Development | Preserve the documented copy/generate/paste/start developer workflow. `.env` remains untracked. |
| Production | Do not change production settings, Compose production files, payment secrets, OAuth credentials, database passwords, or deployment workflows. |
| Test settings | Do not remove or weaken test-only configuration needed by the existing suite. Keep test and development behavior explicitly separate. |
| Secrets | Use only process-local review/test values. Never write generated secrets to repository files, artifacts, or logs. |
| Scope | Do not add Docker as a project dependency or invent a secret-manager integration. |

## Required verification

| Verification | Minimum standard |
|---|---|
| Secret guardrails | Run focused low-priority tests, `git diff --check`, and lint/format checks for changed tests. |
| Development settings | Prove a process with `DJANGO_SETTINGS_MODULE=config.settings.development` fails when `DJANGO_SECRET_KEY` is absent and loads/checks with a process-local generated/review-only value. |
| Compose | If Docker Compose is available, run empty/unset and populated `docker compose config` validations without starting services. Otherwise document the unavailable tooling; do not fabricate a runtime result. |
| General | Verify no known development literal or replacement fallback remains in Compose/development settings and the README/.env template guidance is coherent. |

## Explicit non-goals

This low-priority pass does not rotate real secrets, remediate unrelated template placeholders, use an external secret manager, alter production deployment, or report a confirmed production incident.

## References

[1]: `docker-compose.yml` — development service environment configuration.
[2]: `.env.example` and `README.md` — local onboarding contract.
[3]: `config/settings/development.py` — remaining direct development fallback.
[4]: `config/settings/base.py` and `config/settings/test.py` — shared and test-setting secret behavior.
[5]: `tests/test_compose_secret_hygiene.py` — current static guardrails.

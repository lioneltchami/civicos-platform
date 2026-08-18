# CivicOS Platform — Medium-Priority Four-Agent Implementation Brief

**Committed baseline:** `29a873e` (`api: document GovStack schema credentials`)
**Remediation under review:** `MEDIUM_PRIORITY_REMEDIATION_2026-08-17.patch`
**Purpose:** This is the single implementation input synthesized from two independent medium-priority remediation passes and two fresh blind code-by-code reviews. It contains only convergent, evidence-backed corrections for the final isolated implementation pass.

> Reinspect the actual checkout before editing. Implement only the approved work below. Do not weaken payment verification, authentication, authorization, encryption, production settings, or rate limits in order to make a test pass.

## Review convergence

| Area | Convergent conclusion | Evidence |
|---|---|---|
| Integration settings profile | The new profile correctly uses PostgreSQL, Redis cache/broker, and non-eager Celery while inheriting test safety defaults. | `config/settings/integration.py`; `config/settings/test.py`; CI service configuration. |
| CI service wiring | The added PostgreSQL/Redis job is internally coherent: dependencies, health checks, environment variables, migration command, and test label align. | `.github/workflows/ci.yml` integration job; `requirements/test.txt`/`requirements/base.txt`. |
| Runtime service coverage | The current test module only uses `SimpleTestCase` settings assertions and source-text inspection. It does not connect to PostgreSQL/Redis, publish/consume a Celery task, or verify a worker. | `tests/test_medium_priority_contracts.py`; current integration job. |
| Stripe/webhook coverage | The current test verifies implementation text only, not runtime signature validation, event parsing, malformed input handling, or normalized event output. | `tests/test_medium_priority_contracts.py`; `apps/payments/gateways/stripe_gateway.py`. |
| Frontend contract smoke | The built-bundle smoke command is correctly wired after the Vite build and validates that documented API-boundary literals survive the bundle. It is a static asset check, not an HTTP/API availability contract. | `civicos-site/scripts/contract-smoke.mjs`; `.github/workflows/ci.yml`; `civicos-site/src/pages/ConsentDocs.jsx`. |
| Authorization coverage | Existing volunteer/coordinator tests already cover substantial role and scope isolation. No new concrete authorization bypass was demonstrated by the remediation/review evidence. | `apps/volunteers/tests/test_api.py`; `apps/volunteers/tests/test_views_coordinator.py`. |

## Approved implementation scope

| ID | Required outcome | Acceptance criteria |
|---|---|---|
| FMI-01 | **Make the service-backed integration job exercise real service behavior.** | Add deterministic, isolated integration tests that run under `config.settings.integration` and perform: a real PostgreSQL write/read via the project’s Django ORM; a Redis cache set/get round trip; and a non-eager Celery broker/worker execution or equivalent supported test harness that proves a task is not run eagerly and is consumed. Use only test data/keys and clean up after execution. If a worker is started in CI, bind it only to the declared CI Redis service and terminate it reliably. |
| FMI-02 | **Replace Stripe source-text assertions with offline behavioral tests.** | Import the real Stripe gateway/adapter and test source-proven behavior at the Stripe boundary using mocks: a valid signature, invalid signature, malformed payload, and representative event parsing/normalization. Do not call a live gateway or commit webhook secrets. Do not change payment business rules unless an actual failing path is demonstrated. |
| FMI-03 | **Add an HTTP-level local API contract test for documented consent endpoints.** | Use Django’s local test client or a repository-supported in-process mechanism to request the actual documented consent routes. Assert route registration, expected status category, content type, and a minimal response/schema property where source behavior supports it. This test must not call the public internet. Retain the existing built-bundle check as a separate static asset guardrail. |
| FMI-04 | **Make CI labels accurately reflect what the job proves.** | After FMI-01 is implemented, retain an accurate service-backed name. If any service is not exercised, rename the job/test to topology/configuration validation rather than claiming a runtime contract. |

## Implementation constraints

| Area | Constraint |
|---|---|
| Database | Use the integration settings profile and CI PostgreSQL service. Do not use production databases or persistent user data. Prefer transaction-safe test cases and deterministic cleanup. |
| Redis/Celery | Use a test-only Redis DB/key prefix and a bounded worker or repository-supported in-process harness. Do not use production queues or allow orphan worker processes. |
| Payments | Mock only at the Stripe library/network boundary. Keep signature verification and parsing behavior intact; tests must validate it rather than bypass it. |
| Consent HTTP | Derive routes and expectations from existing URL/view code. Do not assert external host availability or invent response contracts. |
| Frontend | Preserve `npm ci`, lint, build, and the existing `contract-smoke` command. Do not add a browser framework absent from declared dependencies solely for this check. |
| Scope | Do not upgrade dependencies, rewrite migrations, change authorization rules, alter gateway configuration, or hide unrelated schema warnings. |

## Required verification

| Verification | Minimum standard |
|---|---|
| Focused backend contracts | Run every added/updated medium-priority test under `config.settings.integration` against the declared PostgreSQL/Redis services. |
| Stripe behavior | Show valid, invalid, and malformed mocked Stripe cases pass/fail as expected without network access. |
| Consent routes | Run the local HTTP contract tests under test/integration settings and verify route/response assertions. |
| Celery | Demonstrate non-eager dispatch plus deterministic consumption/result state, with worker shutdown/cleanup. |
| Frontend | Run `npm ci`, `npm run lint`, `npm run build`, and `npm run contract-smoke` from `civicos-site`. |
| General | Run `git diff --check`, relevant lint/format checks, and migration consistency checks if model/migration files change. |

## Explicit non-goals

This work does not assert an existing payment vulnerability, authorization bypass, broker defect, or frontend production outage. It closes specific coverage-quality gaps. The remaining broad drf-spectacular diagnostics, third-party Wagtail discovery warnings, unrelated serializer work, npm dependency advisories, and production deployment validation are outside this medium-priority patch.

## References

[1]: `config/settings/integration.py` — service-backed settings profile.
[2]: `.github/workflows/ci.yml` — integration and frontend CI topology.
[3]: `tests/test_medium_priority_contracts.py` — current settings/source-text checks.
[4]: `apps/payments/gateways/stripe_gateway.py:448-523` — Stripe adapter behavior to exercise.
[5]: `civicos-site/scripts/contract-smoke.mjs` — static built-bundle guardrail.
[6]: `apps/volunteers/tests/test_api.py` and `apps/volunteers/tests/test_views_coordinator.py` — existing authorization coverage.

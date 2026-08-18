# CivicOS Platform — High-Priority Four-Agent Implementation Brief

**Committed baseline:** `07a9c0d` (`ci: add quality and production guardrails`)  
**Remediation under review:** `HIGH_PRIORITY_REMEDIATION_2026-08-17.patch`  
**Purpose:** This is the single implementation input synthesized from two independent high-priority remediation passes and two fresh blind code-by-code reviews. The final implementation agents receive this brief and the patched source, but no prior reports or agent identities.

> Implement only the approved corrections below, after rechecking the current source. Do not weaken runtime authentication, authorization, encryption, production security, or schema validation merely to reduce warning volume.

## Evidence convergence

| Topic | Convergent conclusion | Evidence |
|---|---|---|
| Extension registration | The startup import in `ApiConfig.ready()` is a reasonable, narrowly scoped way to register project-owned drf-spectacular extensions. It reduced the measured production deploy-check output from 164 to 127 issues when validated in the integration environment. | `apps/api/apps.py`; `config/settings/base.py`; production check result. |
| Runtime security behavior | The remediation does not alter credential validation, authorization, or production settings. The remaining issue is documentation/schema fidelity, not a demonstrated authorization bypass. | `apps/appointments/govstack_auth.py`; patched `apps/api/schema.py`. |
| Scheduler schema fidelity | Runtime scheduler authentication requires both non-empty `requestor_id` and `request_token` query parameters. A single `apiKey` scheme for `request_token`, with `requestor_id` only in prose, is incomplete. | `apps/appointments/govstack_auth.py:166-186`; `apps/api/schema.py` scheduler extension. |
| Citizen schema fidelity | The BB credential pair remains mandatory. An Authorization Bearer JWT is conditionally validated when supplied and can change the resulting subscriber scope; it is not an alternative that can replace the BB gate. | `apps/appointments/govstack_auth.py:329-354`; citizen schema extension. |
| Test coverage | Existing tests exercise extension class constants only. They do not establish that Django startup registers extensions or that the generated public schema includes accurate security metadata and required parameters. | `apps/api/tests/test_schema.py`; `apps/api/apps.py`. |
| CI coverage | CI does not generate and validate the OpenAPI document. The test and production-settings lanes otherwise remain intact; no PostgreSQL/Redis topology defect was established. | `.github/workflows/ci.yml`. |

## Approved implementation scope

| ID | Required outcome | Constraints and acceptance criteria |
|---|---|---|
| FI-01 | **Machine-readably document the required BB query credential pair.** | For every operation protected by the scheduler/citizen GovStack authenticators, the generated OpenAPI document must expose both `requestor_id` and `request_token` as required query parameters or an equally accurate supported representation. Do not invent a composite token or claim either value alone authenticates a request. |
| FI-02 | **Faithfully document the citizen JWT behavior.** | The generated schema must state that the BB query pair is mandatory and that a Bearer JWT, when sent, is validated and affects subscriber scope. Do not model the JWT as an alternative that permits bypassing the BB pair. If OpenAPI cannot express every conditional effect mechanically, use explicit required BB parameters and accurate endpoint descriptions; do not publish a misleading security requirement. |
| FI-03 | **Add generated-schema integration tests.** | Tests must boot Django, trigger extension registration, generate the schema through the supported drf-spectacular path, and assert that representative scheduler and citizen operations contain the approved security schemes/parameters. Retain or improve focused unit tests, but do not rely solely on direct `__new__` construction. |
| FI-04 | **Add a bounded schema-generation validation guardrail to CI.** | Add a CI step that runs the supported schema-generation/validation command under test settings and appropriate existing CI-safe variables. Preserve existing frontend, migration, test, and production-check guardrails. The command must not require external payment, storage, email, Redis, or production credentials. |

## Implementation guidance

| Area | Guidance |
|---|---|
| Affected operations | Determine actual views/routes using `GovStackSchedulerAuth` and `GovStackCitizenAuth` from source rather than placing schema parameters on unrelated endpoints. Prefer reusable project mechanisms if they accurately apply only to those views. |
| drf-spectacular use | Use supported `extend_schema`, `OpenApiParameter`, authentication extensions, `AutoSchema` customization, or settings hooks. Avoid global warning suppression and false serializers. |
| Public schema assertions | Assert against the generated document’s `paths`, `parameters`, `components.securitySchemes`, and operation descriptions/security. Cover at least one scheduler-protected endpoint and one citizen-protected endpoint. |
| Runtime coverage | Where existing fixtures/helpers permit it, add or extend tests for missing query values and optional valid/invalid JWT behavior. Do not create new test infrastructure merely to redesign existing authentication. |
| Warning scope | The 127 remaining deploy-check issues include third-party Wagtail discovery, serializer discovery, and other unrelated schema warnings. Do not introduce fictional serializers or suppress all warnings to reach zero. |

## Required verification

| Verification | Minimum standard |
|---|---|
| Schema unit/integration tests | Run new and existing schema tests using `config.settings.test`. |
| Schema document | Run `python manage.py spectacular --file /tmp/civicos-schema.yml --validate --settings=config.settings.test` (or the repository-equivalent supported command) and inspect the relevant generated operations. |
| Django checks | Run `python manage.py check --settings=config.settings.test`, plus migration consistency checks if changed models or migrations are involved. |
| CI static validation | Verify workflow formatting/diff hygiene and ensure the new schema step uses declared dependencies and no external secrets. |
| Security non-regression | Confirm runtime authenticator code and production settings are unchanged unless a source-proven, necessary correction is explicitly made. |

## Explicit non-goals

This implementation must not change payment processing, webhook flows, authorization policy, encryption keys, credentials, migrations, dependency versions, broad serializer contracts, or third-party Wagtail source. The original warning volume is not a license to hide warnings. Where complete machine representation is impossible without false claims, prioritize truthful required parameters and descriptions over misleading security alternatives.

## References

[1]: `apps/appointments/govstack_auth.py:166-186` — scheduler query credential enforcement.  
[2]: `apps/appointments/govstack_auth.py:329-354` — citizen BB gate and conditional JWT validation.  
[3]: `apps/api/apps.py` — extension registration hook.  
[4]: `apps/api/schema.py` — project OpenAPI extensions.  
[5]: `apps/api/tests/test_schema.py` — present schema test coverage.  
[6]: `.github/workflows/ci.yml` — established test and production-check topology.

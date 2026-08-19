# Item 05 — Per-Block Requirements Assessments: Blind Code Review

**Date:** 2026-08-18
**Reviewed assessment:** `docs/item-05-per-block-requirements-assessments-gap-analysis-20260818.md`
**Review method:** Two fresh reviewers received only the current CivicOS source and the Stage 1 assessment. They did not receive Stage 1 conversations, official-source archives, or implementation plans.
**Overall assessment accuracy:** **Partially accurate and structurally strong; documentation correction is required before it can be called definitive.**

## Review conclusion

The Stage 1 assessment correctly identifies Consent, Payments, Scheduler, and File Management as the implemented Building Block areas and preserves the broad Item 01–04 boundaries. It correctly treats Payments and Scheduler as blocked for official/staging claims and treats Item 04’s result as limited to its local/CI runner checklist. The assessment also provides useful priorities, dependency coverage, and a conservative Items 6–7 gate.

However, both blind reviewers found that several claims are not anchored precisely enough to current source, that the dependency map mixes code-observed behavior with future contract requirements, and that priority words are absolute when their urgency changes by scope. The definitive assessment must make those distinctions explicit. It must also record inherited Item 01–04 conclusions as inherited evidence rather than reasserting them without exact document/commit references.

> **Required Stage 3 posture:** This is a documentation/evidence-correction task. It must not initiate Items 6–7 feature work or reverse completed Items 1–4. Any code change must be a small, supporting validation aid only and must not alter Business Block behavior.

## Blind-review findings

| ID | Severity | Finding | Concrete evidence | Required correction |
|---|---|---|---|---|
| CR-01 | Must-fix | The assessment names official requirements but does not give every high-priority claim a route, model, task, or symbol-level CivicOS anchor. | Payments lifecycle code exists in `apps/payments/govstack_failure_services.py`; Scheduler routes are in `apps/appointments/govstack_urls.py`; root includes are in `config/urls.py`; Consent boundary is `apps/consent/integration_boundary.py`. | Add a per-block evidence inventory that maps each Must-fix claim to current CivicOS paths/symbols, an official authority reference, and an evidence state. |
| CR-02 | Must-fix | The assessment can be read as treating inherited Item 01–04 statements as independently reconfirmed in the current review. | Verification records exist at `docs/item-01-consent-verification-20260818.md`, `item-02-payments-failure-remediation-verification-20260818.md`, `item-03-scheduler-harness-resolution-verification-20260818.md`, and `item-04-file-management-test-runner-enablement-verification-20260818.md`. | Add an inherited-status table with record path, key commit, exact bounded conclusion, and label **inherited / not re-executed by Item 05**. |
| CR-03 | Must-fix | The dependency table does not distinguish a dependency already exercised by code from a required future cross-block contract. | `config/urls.py` mounts separate Consent, Payments, and Scheduler surfaces, but a mount is not proof of an executable inter-block contract. | Split dependencies into **observed repository linkage**, **required contract before combined rehearsal**, and **not evidenced**. Avoid describing a desired future contract as a current integration. |
| CR-04 | Must-fix | Must/Should/Nice labels are unconditional even though priority differs for isolated local use, combined rehearsal, authorised staging, and official submission. | The current assessment applies the same priority words while also acknowledging local-only evidence for Item 04. | Add a scope-based priority legend and annotate each Must-fix requirement with the gate it blocks: local assessment, combined rehearsal, authorised staging, official harness, or submission. |
| CR-05 | Must-fix | The prose gate for Items 6–7 is comprehensive but not auditable or assignable. | The Stage 1 “Gate before Items 6 and 7” section lists controls but has no owner/evidence/status rows. | Replace/add a binary gate table: condition, affected block(s), evidence required, accountable role, current status, and whether it blocks Item 6, Item 7, or both. |
| CR-06 | Should-fix | Cross-cutting coverage is uneven. Consent/Scheduler risks are sometimes category claims rather than source-anchored evidence, while Payments/File Management are more detailed. | Consent boundary class is present; Payments lifecycle classes are present; Scheduler URL surface and appointment tasks exist; File Management runner files exist. | Add a uniform per-block control matrix for auth/RBAC, tenant isolation, rate/timeout, idempotency/replay, correlation, audit immutability, failure taxonomy, retry/dead-letter, observability, retention, and retained raw evidence. Use `implemented`, `locally tested`, `staging tested`, `officially mapped`, or `not evidenced` only. |
| CR-07 | Should-fix | Scheduler wording risks conflating broad appointment functionality with a complete official Scheduler implementation. | `apps/appointments/govstack_urls.py` contains named Scheduler routes; Item 03 matrix/verification records a partial official operation position. | Add a route inventory and explicitly state that route presence and appointment functionality are not proof of official schema/workflow equivalence. |
| CR-08 | Should-fix | Payments remediation is described accurately at a high level but lacks direct evidence rows for settlement, reconciliation reporting, batch compensation, and externally canonical errors. | `PaymentLifecycleService`, `PaymentAttempt`, `PaymentReconciliation`, and `CallbackDelivery` are in `apps/payments/govstack_failure_services.py`; Item 02 remains not ready. | Add a Payments lifecycle evidence matrix showing current foundation, absent/demonstrated contract, failure semantics, and the precise Item 02 limitation. |
| CR-09 | Should-fix | File Management’s Item 04 success could be mistaken for official BB conformance. | `tools/run_file_management_tests.py`, layer manifest, guide, matrix, and local evidence are present; Item 04 verification explicitly limits its conclusion. | Mark every Item 04 “fully aligned” reference as **fully aligned for Item 04 local/CI test-runner acceptance only** and keep official API/harness evidence as a separate future gate. |
| CR-10 | Nice-to-have | The assessment lacks a compact machine-checkable index of its evidence assertions. | Current content is prose/tables only. | Add `docs/item-05-per-block-requirements-evidence-20260818.json` with records for block, dimension, CivicOS path, official authority reference, evidence state, priority, dependency type, and gate. Add a static validation test or script that prevents broken paths/status vocabulary. |

## Definitive assessment correction plan

### 1. Evidence and inherited-status corrections

Stage 3 must make the document self-auditing. The first section must add a table that distinguishes the Item 05 review’s direct source observations from conclusions inherited from Items 01–04. Each inherited row must name the evidence record and commit, state whether Item 05 re-executed it (it did not), and retain the existing bounded conclusion.

| Item | Required retained wording | Evidence record | Status in Item 05 |
|---|---|---|---|
| Item 01 — Consent | Local Consent-suite evidence and a 42-operation matrix exist; the matrix remains `partial`; no blanket conformance claim. | `docs/item-01-consent-verification-20260818.md` | Inherited; not re-executed |
| Item 02 — Payments | Failure-remediation foundations exist, but Payments remains **Not ready** for testing-site submission. | `docs/item-02-payments-failure-remediation-verification-20260818.md` | Inherited; not re-executed |
| Item 03 — Scheduler | Harness/traceability work exists, but Scheduler remains **Partially aligned / remediation required**. | `docs/item-03-scheduler-harness-resolution-verification-20260818.md` | Inherited; not re-executed |
| Item 04 — File Management | **Fully aligned / ready** only for the stated local/CI test-runner acceptance checklist; no official harness or certification claim. | `docs/item-04-file-management-test-runner-enablement-verification-20260818.md` | Inherited; not re-executed |

### 2. Uniform per-block evidence model

For Consent, Payments, Scheduler, and File Management, Stage 3 must add the same control matrix with the following permitted states:

| Control domain | Permitted status values |
|---|---|
| Functional/workflow | `implemented`, `locally tested`, `officially mapped`, `not evidenced` |
| API/route/schema | `implemented`, `locally tested`, `officially mapped`, `not evidenced` |
| Data/audit | `implemented`, `locally tested`, `staging tested`, `not evidenced` |
| Error/failure/retry | `implemented`, `locally tested`, `officially mapped`, `not evidenced` |
| Security/tenant/auth | `implemented`, `locally tested`, `staging tested`, `not evidenced` |
| Observability/recovery | `implemented`, `locally tested`, `staging tested`, `not evidenced` |
| Test/evidence | `locally tested`, `staging tested`, `officially mapped`, `not evidenced` |

Every `implemented`, `locally tested`, or `officially mapped` state must cite a real CivicOS path/symbol or a real official authority reference. `not evidenced` must be used where the source bundle cannot demonstrate the control. The assessment must not infer a deployment, official run, or external contract from a candidate package or route registration.

### 3. Scope-sensitive priority model

Stage 3 must add this legend and use it consistently.

| Scope | Meaning of Must-fix |
|---|---|
| Block-isolated local work | Required to make the stated local assessment internally accurate or safely runnable. |
| Combined cross-block rehearsal | Required before the dependency is exercised across block boundaries. |
| Authorised staging rehearsal (Item 6) | Required before placing real staging infrastructure, credentials, or multi-service topology under test. |
| Official harness or testing-site work (Item 7) | Required before a claim or submission against the relevant official authority. |

For example, File Management official API mapping is not a Must-fix for its completed Item 04 local-runner checklist; it becomes a Must-fix for official submission. Scheduler recipient-level retry/dead-letter and Consent propagation become Must-fix for a rehearsal that sends cross-block notifications, even if an isolated local appointment test does not exercise them.

### 4. Dependency correction model

The revised assessment must use the following dependency classes instead of a single undifferentiated map.

| Dependency class | Definition | Example |
|---|---|---|
| Source-observed linkage | A real import, route, model relationship, task, or test in current source. | `config/urls.py` mounting distinct GovStack Payments and Scheduler namespaces. |
| Required rehearsal contract | A versioned, executable contract required before a proposed combined flow. | Scheduler booking/fee event to Payments idempotency, cancellation, compensation, and correlation contract. |
| Not evidenced | A plausible relationship with no current code/evidence sufficient to assert it. | A claimed automated consent-withdrawal propagation to all Payment/File Management records absent an executable test/contract. |

### 5. Auditable Items 6–7 gate

Stage 3 must add a status table using `ready`, `blocked`, `not evidenced`, or `not applicable`. The current expected status should be `blocked` or `not evidenced` for every production-like, official, staging, and external item until a later authorised scope supplies evidence.

| Gate condition | Affected area(s) | Evidence required | Accountable role | Item 6 | Item 7 |
|---|---|---|---|---|---|
| Pinned official authority and approved claim scope | All | revision record and signed scope decision | BB owner / release authority | Blocked | Blocked |
| Deployed route/schema inventory and authentication boundary | All | environment route inventory and auth tests | platform/security owner | Blocked | Blocked |
| Tenant isolation, correlation, idempotency, and replay ownership | All; especially Payments/Scheduler | cross-block contract tests and trace evidence | architecture owner | Blocked | Blocked |
| Consent propagation and notification suppression | Consent/Scheduler/Payments/File Management | executable policy propagation tests | consent owner | Blocked | Blocked |
| Settlement, reconciliation, compensation, and batch remediation | Payments | provider-backed tests, reports, operator runbook | payments owner | Blocked | Blocked |
| Durable delivery, retry/dead-letter, reporting, and acknowledgement | Scheduler | recipient-level evidence and runbook | scheduler owner | Blocked | Blocked |
| Secure artefact lifecycle and storage failure handling | File Management | storage/scan/retention/authorization staging evidence | documents/security owner | Blocked | Blocked |
| Raw reproducible evidence, rollback/replay, and explicit approval | All | archived output, topology, rollback plan, approval | release authority | Blocked | Blocked |

## Required documentation/supporting-file implementation

| Path | Required Stage 3 change |
|---|---|
| `docs/item-05-per-block-requirements-assessments-gap-analysis-20260818.md` | Add inherited-status table, source-observed evidence tables, scope-sensitive priorities, dependency classes, uniform control matrices, and auditable Items 6–7 gate. Update the Stage 3 final status. |
| `docs/item-05-per-block-requirements-evidence-20260818.json` | Add evidence records with a schema for block, dimension, evidence state, CivicOS path, authority reference, priority, dependency class, and gate scope. Do not place secrets or environment URLs in it. |
| `tests/test_item05_per_block_assessment.py` | Add static checks for evidence-path existence, allowed evidence-state vocabulary, required four-block coverage, required inherited Item 01–04 entries, permitted priority/gate values, and correct relative-doc references. |
| `scripts/validate_item05_per_block_assessment.py` | Add a portable validator for the evidence JSON and the required checklist/gate markers in the assessment Markdown. It must not call external services. |

## Stage 3 acceptance conditions

The assessment can be considered definitive only when the following are true:

1. Every assessed block has source-observed evidence rows for functional, API, data/audit, error/failure, security/cross-cutting, testability, and dependency dimensions.
2. Every high-priority official-gap statement is explicitly tagged as source-observed, inherited, required-contract, or not evidenced.
3. Item 01–04 conclusions have exact evidence records and are described with their original bounded scope.
4. Priorities state the scope they block; no Item 04 local-runner success is used to imply official File Management conformance.
5. The dependency map separates present code linkage from required future contracts and from unproven relationships.
6. The Items 6–7 gate is table-driven, accountable, and currently blocks unauthorised staging, external testing, or submission.
7. The evidence JSON and static validator/tests pass without external services.

## References

[1]: `docs/item-05-per-block-requirements-assessments-gap-analysis-20260818.md` — reviewed Stage 1 assessment
[2]: `config/urls.py` — root route includes and separate GovStack namespaces
[3]: `apps/consent/integration_boundary.py` — Consent fail-closed boundary
[4]: `apps/payments/govstack_failure_services.py` — payment attempt, callback delivery, and reconciliation lifecycle foundation
[5]: `apps/appointments/govstack_urls.py` — Scheduler GovStack route surface
[6]: `apps/documents/services/` and `tools/run_file_management_tests.py` — File Management lifecycle and local/CI runner evidence
[7]: `docs/item-01-consent-verification-20260818.md`; `docs/item-02-payments-failure-remediation-verification-20260818.md`; `docs/item-03-scheduler-harness-resolution-verification-20260818.md`; `docs/item-04-file-management-test-runner-enablement-verification-20260818.md` — inherited bounded conclusions

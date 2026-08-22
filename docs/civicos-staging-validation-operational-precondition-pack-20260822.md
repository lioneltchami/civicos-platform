# Operational-Precondition Evidence Pack — Payments RB-02 Candidate

## Control posture

**Documentation-only evidence inventory.** The controlling state remains deferred. This pack does not authorise staging, deployment, secret access, official-suite execution, release, submission, production use, or external distribution.

The candidate continuity record is exact:

| Field | Value |
|---|---|
| Label | `Civicos-Payments-RB02-OptionB-ScopePure` |
| Canonical SHA-256 | `3fb5a4327e3ce5b0b9c076f3d2426d4e663ae178f7091ed6c19515c8c5b4ddca` |
| Manifest SHA-256 | `15b56e3bc3caf858522ce5196c70b61cc32efa476411455df367560a8d653ff9` |
| Path | `artifacts/payments-rb02-option-b-scope-pure/` |
| Mixed anchor | `49e69fb8a3051de6c1cf7adff8e16c928cf84412` — provenance only |

## Required preconditions

| ID | Required condition | Classification | Repository-backed evidence or exact limitation | Owner and unblock action |
|---|---|---|---|---|
| ENV-01 | Isolated non-production target, topology, network boundary, baseline, and approved validation window | **UNAVAILABLE / BLOCKED** | `docs/item-06-authorised-staging-rehearsals-gap-analysis-20260818.md` states no remote staging URL, topology inventory, promotion record, or authorised test window was supplied and marks topology/promotion must-fix. | Platform/deployment owner: provide target identifier, topology/isolation inventory, baseline record, and a separately approved bounded window. |
| OPS-01 | Named operators, approver, observers, least-privilege roles, expiry/revocation, and access audit | **UNAVAILABLE / BLOCKED** | Item 06 states no remote access-control inventory was supplied. `docs/DEPLOY_NOTES.md` documents production-admin MFA only; it is not staging role evidence. | Access/IAM owner: provide time-bounded role-to-action matrix, approval, expiry/revocation, MFA/access-review evidence, and audit destination without credentials. |
| SEC-01 | Approved human-controlled staging secret mechanism, non-secret configuration inventory, rotation/revocation and ownership | **UNAVAILABLE / BLOCKED** | `docs/DEPLOY_NOTES.md` documents production configuration and encrypted-secret patterns; Item 06 says remote secret manager/configuration evidence is absent. | Security/platform owner: identify approved mechanism/reference and workload binding, rotation/revocation owner, and redacted validation evidence; never provide values. |
| DATA-01 | Allowed/prohibited data classes, synthetic/masked data proof, retention/deletion and cleanup plan | **UNAVAILABLE / BLOCKED** | The decision brief defines this requirement but no staging data classification, retention/deletion evidence, or cleanup record exists. Production PII-filtering/logging notes are not staging data evidence. | Data/privacy owner: provide field/classification matrix, allowed synthetic/masked dataset evidence, retention/deletion schedule, audit/access boundary, and cleanup attestation. |
| SCOPE-01 | Future bounded Payments RB-02-only actions, limits, dependencies, non-goals, and no-live-payment boundary | **UNAVAILABLE / BLOCKED** | Artifact identity proves local component scope only; it does not define a future operational endpoint/action/window limit. | Payments service owner: provide versioned scope sheet with allowed actions, hard limits, dependencies, feature/kill controls, success criteria, and exclusions. |
| RB-01 | Candidate-specific rollback/recovery procedure, trigger, owner, baseline, verification and rehearsal evidence | **UNAVAILABLE / BLOCKED** | `docs/DEPLOY_NOTES.md` contains general production rollback guidance; Item 06 states rollback/restore execution is not evidenced. | Release/service owner: supply RB-02 rollback runbook, trigger matrix, owner, known-good baseline, data/side-effect handling, and separately authorised rehearsal evidence. |
| OBS-01 | Staging health/log/metric/trace/alert coverage, correlation, stop threshold, alert route and stop authority | **UNAVAILABLE / BLOCKED** | `docs/DEPLOY_NOTES.md` documents production health endpoints, JSON logs, and PII filtering; Item 06 says staging observability is not evidenced. | Observability owner: provide redacted staging signal map, thresholds, alert route, named stop authority/backup, and evidence-capture instructions. |
| EVD-01 | Evidence IDs, evidence location, redaction review, failure-register ownership and retention handling | **UNAVAILABLE / BLOCKED** | Existing records define requirements but do not establish a remote evidence sink, custodian, named failure owner, or redaction review for a run. | Evidence custodian/security reviewer: provide evidence index schema, storage/access/retention controls, redaction checklist, failure owner, escalation path, and UTC provenance rule. |
| CONT-01 | Candidate continuity | **EVIDENCED** | The exact candidate label, canonical SHA-256, manifest SHA-256, path, identity record, assembly manifest, and reconstruction log are retained in `docs/civicos-payments-rb02-scope-pure-*20260822.md` and `artifacts/payments-rb02-option-b-scope-pure/`. | Preserve unchanged; independently re-hash before any future human decision. |
| EXC-01 | SCH-02.2 and File Management exclusions; provenance-only mixed anchor | **EVIDENCED** | Candidate attachment, decision, deferral, and artifact records explicitly retain both exclusions and the provenance-only mixed anchor. | Preserve unchanged; reject any evidence pack that expands scope. |

## Existing preparation evidence — limited scope

| Area | Classification | Source-bound fact and limitation |
|---|---|---|
| Production topology and health/logging patterns | **EVIDENCED** | `docs/DEPLOY_NOTES.md` documents production Docker/health/logging patterns only. It does not establish a staging target or prove a rehearsal. |
| Production MFA and secret-handling patterns | **EVIDENCED** | `docs/DEPLOY_NOTES.md` documents production-admin MFA and configuration/encrypted-secret patterns only. It does not establish staging roles, access, or secret mechanism. |
| General rollback guidance | **EVIDENCED** | `docs/DEPLOY_NOTES.md` includes production rollback instructions only. It is not candidate-specific recovery proof. |
| Staging evidence boundary | **EVIDENCED** | Item 06 records absence of remote target, credentials, IAM, secret manager, promotion, rollback execution, observability, and authorised rehearsal window; absence is not proof an external resource does not exist. |

## Source and safety rules

No secret values, tokens, keys, connection strings, production/customer/payment data, or inferred remote facts are included. SCH-02.2 and File Management remain excluded. Completing this pack does not authorise staging; it prepares human gap-closure work only.

## Documentation-derivable control drafts

The following documents are repository-grounded planning controls. None is operational evidence or an authorisation.

| Control | Document | Classification | Remaining boundary |
|---|---|---|---|
| SCOPE-01 | `docs/civicos-staging-validation-rb02-scope-sheet-20260822.md` | **DRAFTED / PENDING HUMAN APPROVAL** | No future target, window, operator, provider, or live-money action is approved. |
| RB-01 | `docs/civicos-staging-validation-rb02-rollback-runbook-draft-20260822.md` | **DRAFTED / PENDING HUMAN APPROVAL** | Rollback rehearsal remains **BLOCKED** pending target, access, authority, baseline, observability, and evidence. |
| OBS-01 | `docs/civicos-staging-validation-rb02-observability-map-draft-20260822.md` | **DRAFTED / PENDING HUMAN APPROVAL** | Alert routing, thresholds, named stop authority, target signals, and operational validation remain **BLOCKED**. |
| EVD-01 | `docs/civicos-staging-validation-evidence-control-templates-20260822.md` | **DRAFTED / PENDING HUMAN APPROVAL** | Evidence sink, custodian, retention, access model, redaction reviewer, and failure owner remain **BLOCKED**. |

ENV-01, OPS-01, SEC-01, and DATA-01 remain **UNAVAILABLE / BLOCKED**. The controlling status remains deferred and staging remains not authorised.

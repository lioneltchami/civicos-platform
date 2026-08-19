# Item 07 — Testing-Site Submissions: Blind Code Review

**Date:** 2026-08-18
**Conclusion:** **Red / not ready for official submission.** The Stage 1 gap analysis is accurate. Candidate structure and retained local evidence are useful preparation, but do not meet the complete deployment/API/requirements/staging/portal evidence threshold.

## Alignment with the gap analysis

The review confirms that all four candidates provide a local package surface (`candidate-manifest.json`, Compose configuration, and entrypoint), and that Items 1–6 maintain useful evidence records. The review also confirms that these assets are not unified release-bound submission packages and that no receipt or portal record exists. The Stage 1 statement that no submission has occurred is accurate.

## Exact remaining gaps

| ID | Priority | Required change | Evidence |
|---|---|---|---|
| CR-01 | Must-fix | Create a standard immutable **submission dossier** for each candidate containing package/BB identity, source revision, version/release identifier, artifact hashes, license, OpenAPI/spec links, test/evidence references, limitations, and portal state. | `examples/civicos-*/candidate-manifest.json` is not a final portal dossier; no release tag exists. |
| CR-02 | Must-fix | Create a standard **portal checklist and receipt ledger** defaulting to `NOT SUBMITTED`; it must forbid recording a receipt, Jira ticket, endpoint, date, or pass claim before an authorised portal action occurs. | No portal receipt/Jira/form evidence exists. |
| CR-03 | Must-fix | Create a standard per-candidate limitation/deviation declaration covering local/staging/official scope, known failures/skips/warnings, blockers, and deferred work. | Item 01–06 limitations are distributed and inconsistent for portal review. |
| CR-04 | Must-fix | Build a deterministic source/evidence inventory and offline validator, validating all four dossiers and rejecting `submitted`, portal receipt, staging pass, or official pass fields unless evidence is explicitly supplied. | Provenance, artifact hashes, and version binding are not unified. |
| CR-05 | Must-fix | Preserve `NOT READY` status for Payments, Scheduler, and File Management; Consent must remain local-evidence-only until a release-bound staging/portal path exists. | Item 02 recorded failures; Item 03 partial alignment; Item 04 local runner only; Item 06 lacks staging execution. |
| CR-06 | Should-fix | Add references for CI/security/code-quality reports and a clean-room reproducibility procedure, with truthful `not available` status where absent. | Existing tests and CI configuration are not package-bound retained submission provenance. |
| CR-07 | External gate | Run/retain current official suites, carry out authorised staging rehearsal, freeze release/tag/image identity, and collect actual portal form/receipt only after human confirmation. | Cannot be generated safely from repository-only context. |

## Safe Stage 3 scope

Stage 3 may create dossiers, manifests, inventories, validators, templates, limitation declarations, evidence ledgers, and static regression tests. It may not create a release tag, submit a form, obtain a Jira ticket, upload artifacts, change a portal record, use account credentials, run external test infrastructure, or claim a candidate is ready/submitted.

## Full-alignment condition

The dossier controls can make submission preparation reproducible and truthful. They cannot turn the Acceptance Checklist fully green: official API/requirements evidence, authorised staging evidence, portal completion, and a submission receipt remain externally gated. The final records must state this explicitly.

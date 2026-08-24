# Payments RB-02 Rollback and Recovery Runbook — Draft Pending Human Approval

## Status and boundary

This candidate-specific runbook is **DRAFTED / PENDING HUMAN APPROVAL** for `Civicos-Payments-RB02-OptionB-ScopePure`. It is not an execution instruction. Any rehearsal, target access, deployment reversal, migration action, provider action, or remote rollback remains **UNAVAILABLE / BLOCKED** until the precondition pack is closed and a fresh human decision authorises a bounded activity.

## Fail-closed entry gates

| Gate | Evidence required before any future use | Current state |
|---|---|---|
| Candidate and known-good baseline | Exact deployed candidate and approved recovery identity, with configuration-compatibility record | Candidate identity retained; deployed/known-good remote baseline **BLOCKED**. |
| Target and access | Named non-production target, authorized operator, least-privilege access and approver | **BLOCKED**. |
| Window and decision authority | Approved bounded window, rollback decision authority, stop authority, and escalation route | **BLOCKED**. |
| Configuration/secrets | Approved non-secret configuration compatibility check and secret mechanism validation | **BLOCKED**. |
| Observability/evidence | Health/log/error access, approved evidence sink, redaction process and retention | **BLOCKED**. |

## Candidate-specific trigger and recovery logic

A future authorised rollback must be considered if the approved RB-02 acceptance checks fail: lease/fencing integrity fails; finality/policy or durable decision assertions fail; duplicate/empty-batch finalisation becomes non-idempotent; competing-worker stale finalisation is not rejected; candidate continuity differs; or an integrity, security, sensitive-data, or unapproved-provider condition appears. Exact thresholds and named decision-makers are **BLOCKED**.

The recovery procedure is deliberately command-free: freeze forward change through the future approved control; capture redacted pre-action baseline; verify the approved known-good identity and compatibility; obtain explicit authorisation; use only the later-approved deployment/recovery mechanism; compare post-action health, logs/error signals, and approved non-sensitive RB-02 acceptance checks to the baseline; then record a recovery or continued-incident decision. Any ambiguity, missing evidence, or sensitive-data exposure is a stop condition.

## Recovery acceptance template

| Check | Required evidence | Result |
|---|---|---|
| Candidate/known-good identity | Exact artifact/build/config references | `[BLOCKED until supplied]` |
| Health | Approved target health response and timestamp, redacted | `[BLOCKED until supplied]` |
| Logs/error signals | Redacted JSON-log/error-monitoring comparison | `[BLOCKED until supplied]` |
| RB-02 functional integrity | Approved non-sensitive check of lease/finality/policy/idempotency scope | `[BLOCKED until supplied]` |
| Evidence/redaction | Indexed evidence IDs and review | `[BLOCKED until supplied]` |
| Recovery decision | Named authority and disposition | `[BLOCKED until supplied]` |

## Rehearsal disposition

**BLOCKED.** `docs/DEPLOY_NOTES.md` contains general production rollback guidance; it is not evidence of a candidate-specific or staging rollback rehearsal. Any future rehearsal requires the separately approved target, access, secret mechanism, data policy, window, observability, stop authority, and evidence sink.

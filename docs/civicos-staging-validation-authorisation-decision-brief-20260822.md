# Human Staging-Authorisation Decision Brief — Payments RB-02 Candidate

## Decision status

**Human-owner decision recorded: Defer.** The attached candidate `Civicos-Payments-RB02-OptionB-ScopePure` remains available for future review but is not rejected. Operational preconditions remain pending and not evidenced. Attaching the candidate and recording this Defer do **not** authorise staging, deployment, secret access, official tests, release, submission, production access, payment processing, or any other execution.

## Required conditions before a later bounded staging review can be authorised

| Required condition | Required human evidence before authorisation | Current state |
|---|---|---|
| Isolated environment | Named non-production target, connectivity/isolation boundary, approved baseline, and validation window. | Pending; not asserted. |
| Operators and least privilege | Named operators/approver/observers, time-bounded scoped roles, expiry/revocation, and audit route. | Pending; not asserted. |
| Secret mechanism | Approved human-controlled secret mechanism, owner, rotation/revocation design; **no secret values in this brief or evidence**. | Pending; not asserted. |
| Data and retention | Synthetic or explicitly approved test data, prohibited data classes, retention/deletion plan, and owner. This Defer record authorises no new data processing, transfer, retention extension, deletion, or retention-policy change. | Pending; not asserted. |
| Bounded window and scope | Start/end UTC, permitted actions, hard action limits, excluded actions, and expiry. | Pending; not asserted. |
| Rollback/recovery | Reversible baseline, rollback owner/triggers, validation and abort criteria. | Pending; not asserted. |
| Observability | Logs, metrics, traces, alerts, correlation IDs, timestamp convention, monitoring owner, and sensitive-data controls. | Pending; not asserted. |
| Stop authority | Named stop authority and backup, communication path, stop triggers, and immediate pause authority. | Pending; not asserted. |
| Evidence/redaction/failure rules | Evidence IDs, capture locations, redaction/retention standard, failure classification, and failure register owner. | Pending; not asserted. |

## Decision options

- [ ] **Authorize later bounded staging review.** This may be selected only after every required condition is independently evidenced, a named human approver records a separate time-bounded authorisation, and scope remains limited to the attached candidate.
- [x] **Defer.** Selected by the human owner because required evidence remains incomplete. No staging activity is authorised.
- [ ] **Reject candidate.** The candidate or control package is unsuitable. No staging activity is authorised.

**Default while evidence is incomplete: Defer.** Absence of a recorded decision is not authorisation.

## Non-authorisation and stop boundary

No production credentials, production data, real payment rails, customer funds, external side effects, or secrets may be used under this brief. Stop or hold immediately for scope drift, access ambiguity, secret/sensitive-data exposure, environment uncertainty, missing telemetry, unexpected external effect, integrity/safety concern, rollback failure, untrustworthy evidence, or instruction from the named stop authority.

| Human approver | Decision | Exact scope/window reference | Record ID | UTC time |
|---|---|---|---|---|
|  | Authorize later bounded staging review / Defer / Reject candidate |  |  |  |

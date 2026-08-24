# Item 06 Staging-Rehearsal Control Templates

## Non-execution boundary

This document prepares an authorised future rehearsal. It does not approve or execute a deployment, migration, remote test, rollback, restore, DNS/TLS change, secret access, or external integration call. Until a human-approved manifest is completed in an authorised environment, the outcome must remain **NOT RUN** or **BLOCKED**.

## Authorisation and access checklist

| Control | Required evidence | Status before remote approval |
|---|---|---|
| Target alias and topology owner | Approved inventory; no raw endpoint in repository | BLOCKED |
| Deploy/test/log/secret roles | Least-privilege role matrix, MFA/OIDC, expiry/review and audit destination | BLOCKED |
| Change approval and time window | Approval reference, approver role, allowed actions, abort authority | BLOCKED |
| Synthetic/approved data | Data classification and deletion/retention plan | BLOCKED |
| Evidence custody | Redacted evidence location, custodian, retention period | BLOCKED |

## Rollback and restore record template

| Field | Required future value |
|---|---|
| Release commit and immutable image digest | Required |
| Migration set and compatibility decision | Required |
| Backup owner, timestamp, integrity result | Required |
| RPO/RTO target and restore verification | Required |
| Abort trigger and rollback owner | Required |
| Redacted outcome/evidence reference | Required |

A rollback/restore may only be marked **PASS** after an authorised execution against the approved target. A planned procedure is not test evidence.

## Observability and evidence policy

Collect only redacted, UTC-timestamped artifacts: deployment/preflight output, version/digest confirmation, structured service logs, metrics/traces/dashboard export references, alert delivery/acknowledgement evidence, audit events, rollback/restore outcome, and explicit sign-off. Do not retain secrets, authorization headers, cookies, tokens, private keys, document contents, payment data, or personal data. Record unavailable controls as **NOT RUN** or **BLOCKED**, never as passed.

## Items 1–5 regression matrix

| Item | Required future staging regression evidence | Current Item 06 status |
|---|---|---|
| 1 Consent | Pinned candidate/version, consent operation matrix result, withdrawal/error and audit traces | BLOCKED — local bounded evidence only |
| 2 Payments | Provider-safe synthetic flow, failure/retry/reconciliation/rollback evidence, no real funds | BLOCKED — Item 02 remains not ready |
| 3 Scheduler | Synthetic reminder/delivery failure and retry/dead-letter/consent-suppression evidence | BLOCKED — Item 03 remains partially aligned |
| 4 File Management | Synthetic upload/scan/quarantine/retention/download controls and redacted artifacts | BLOCKED — Item 04 local runner scope only |
| 5 Per-block assessments | Approved release manifest, dependency/gate evidence, no overclaim | BLOCKED — assessment gate requires external evidence |

## Rehearsal sequence (future authorised use)

1. Obtain human approval, access-role confirmation, a declared target alias, and an approved synthetic-data policy.
2. Complete a non-placeholder manifest outside the repository, run the offline preflight, and retain the redacted result.
3. Promote only the approved immutable release using the protected deployment process; capture digest, migrations, and configuration fingerprint.
4. Execute the agreed Items 1–5 regression matrix and failure cases while observing logs, metrics, traces, alerts, and audit events.
5. Abort on security, data, availability, or evidence-policy breach; execute the approved rollback/restore procedure if required.
6. Record truthful outcome, evidence references, defect disposition, and release-authority sign-off. No outcome is a successful authorised rehearsal until all required remote evidence exists.

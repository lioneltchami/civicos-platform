# GovStack Payments Failure Operations

This runbook covers **repository-only deterministic controls**. It does not authorize provider replay, compensation, production action, or external evidence claims.

## Triage

An uncertain, timeout, network, or duplicate result must be queried by provider transaction/request identity before any resubmission. A settled item is excluded from partial resubmission. Unknown or mismatched reconciliation results enter owned review/defer state and retain a redacted audit event.

## Operator controls

Only an authorized operator may review, defer, or replay an attempt. Record the opaque attempt identifier, owner, reason, and action; never record beneficiary identifiers, financial addresses, voucher secrets, credentials, or raw provider payloads. Review queue age should be monitored locally and escalated according to the service SLA once deployment monitoring is available.

## Evidence boundary

Local adapter tests and manifest validation are deterministic repository evidence. Real provider semantics, credentials, staging deployment, proxy traces, tenant-equivalent deployment evidence, official harness execution, and production replay/compensation approval remain deferred.

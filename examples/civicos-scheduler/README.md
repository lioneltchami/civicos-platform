# CivicOS Scheduler harness preparation

This directory contains **local-only harness preparation** for the pinned GovStack Scheduler repository revision `d425be5cc0d6c606f351e5bf89be6d5c6c83c468`. It is not a certification result and must not be pointed at a production endpoint.

## Local configuration

Set `SCHEDULER_API_BASE` to an HTTP loopback URL when overriding the default. Use the values in [`config.schema.json`](config.schema.json) for auth mode, push-only channel testing, bounded timeout/retry/backoff, UTC timezone, fake dependency mode, and queue threshold. The entrypoint rejects no external target itself; operators must run the validator before invoking the pinned suite and preserve the resulting output as local evidence.

```sh
SCHEDULER_API_BASE=http://127.0.0.1:3333/ \
  python3 scripts/validate_scheduler_harness.py
examples/civicos-scheduler/test_entrypoint.sh --config api-suite
```

The operation matrix in [`operation-matrix.json`](operation-matrix.json) records every 37 CivicOS Scheduler route with method, source view, focused regression test, and an honest `partial` status pending contract execution. A `partial` entry is traceability, not conformance evidence.

For deterministic repository-only evidence, run `python3 scripts/scheduler_local_topology.py --output examples/civicos-scheduler/result/local-topology.jsonl`. This starts an ephemeral HTTP server bound only to `127.0.0.1`, exercises health, entity/resource/subscriber lifecycle, duplicate idempotency, ownership rejection, dispatch, Payments and Consent authority-preserving calls, and cleanup, then writes redacted JSONL plus a SHA-256 sidecar. The fake never contacts an external endpoint and is not a substitute for the official suite.

## Registering another Building Block

An external Building Block registers as a Scheduler **entity**, then creates or identifies its owned **resource**, and finally registers a **subscriber**. The registration sequence is: create entity; create resource with the entity as owner; create an affiliation where required; create subscriber with a stable, caller-owned external identifier; create alert message/schedule referencing the subscriber; and remove schedules before deleting subscribers/resources during cleanup. Store only non-secret local identifiers in fixtures, and use the harness token mode rather than production credentials.

The registering Building Block owns its resource and subscriber identifiers and remains responsible for its endpoint availability and acknowledgement semantics. Scheduler owns schedule lifecycle, dispatch correlation, retry/dead-letter status, and audit records. Scheduler does not perform settlement or consent decisions. Any future provider-neutral cross-BB adapter must call **Payments as the settlement authority** and **Consent as the consent authority**, pass a correlation/idempotency key, isolate timeouts and failures, and use local fakes in tests. No such external call is enabled by this preparation package.

## Supported channels and evidence boundary

Only push delivery is enabled in this local preparation. Email, SMS, poll, and inbound acknowledgement are deferred and must be represented as unsupported/deferred rather than successful delivery. The local topology records HTTP status and response state without PII; it does not claim external recipient delivery. The official suite has not been executed by this package; local validation and focused tests do not establish certification or testing-site readiness.

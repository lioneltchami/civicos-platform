# Item 03 — Scheduler Gap Analysis Independent Review

**Date:** 2026-08-20  
**Stage:** Fresh independent code review of the current codebase and Stage 1 gap analysis

> **Review conclusion: The Stage 1 analysis is accurate with targeted refinements.** Item 03 remains **Partially aligned / remediation required**. CivicOS has durable primitives and an exact 37-route inventory, but not one authoritative, crash-safe, recovery-complete, owner/tenant-authorized Scheduler lifecycle.

## Confirmed and corrected findings

| Area | Independent conclusion | Correction or refinement |
|---|---|---|
| Exact operation inventory | **Confirmed.** `apps/appointments/govstack_urls.py` registers event 4, entity 4, alert schedule 4, message 4, resource 5, subscriber 4, affiliation 4, appointment 4, and log 4 routes: **37 total**. | This is a route-inventory fact only. It is not runtime proof, official validation, or conformance. |
| Durable recipient/outbox foundation | **Confirmed.** Recipient delivery and outbox rows, idempotency/generation fields, lease/token fencing, claims, recipient reaping, and outbox publication transitions are real foundations. | The current state is best described as **durable primitives plus partially wired orchestration**, not as a closed durable runtime. |
| Split dispatch authority | **Confirmed P0.** `GovStackAlertSchedule.dispatched`, Celery scheduling identity, current generation, recipient rows, and outbox rows can act as overlapping lifecycle signals. | SCH-01 must make one locked generation-scoped admission transition authoritative, including a migration-safe role for the legacy Boolean. |
| Transactional materialization | **Confirmed.** Recipient and outbox rows can be materialized together with generation/recipient uniqueness. | The caller contract must prevent arbitrary stale generation materialization, explicitly converge duplicates, preserve rollback atomicity, and record deterministic zero-recipient outcomes. |
| Publisher recovery | **Confirmed as incomplete, not absent.** Publisher token/generation/owner/lease primitives are present. | SCH-02 must cover publisher lease-expiry reclaim, bounded retry/backoff, failure exhaustion, broker crash windows, and stale publisher completion. It must not describe these primitives as missing. |
| Recipient recovery and retry taxonomy | **Confirmed P0.** Recipient fences/reaping/replay exist but are not a proven closed loop. | Define a normalized accepted/duplicate/transient/terminal/timeout/auth/schema outcome contract. Bind retryability, persisted backoff, retry scheduling, dead letter, and replay to that single durable boundary. |
| Generation/cancellation/deletion/re-arm | **Confirmed P0/P1.** Generation fields and some cancellation/re-arm behavior exist. | A single transition contract must cover queued, in-flight, retry, dead-letter, and outbox work; safe history retention; deletion propagation; and stale generation suppression at every later side effect. |
| Status/log/metric projection | **Confirmed P1.** Status helpers, local evidence, and logs are useful but not a lifecycle projection. | SCH-03 must project and expose durable schedule, recipient, outbox, acknowledgement, retry, dead-letter, replay, cancellation, and stale-work facts with redacted correlation and metrics. |
| Persisted owner/tenant authorization | **Confirmed P1.** Auth/role gates exist, but durable query and transition scope is not uniformly proven. | Status queries, replay, acknowledgement, cancellation, reaping, and administrative recovery require persisted owner/tenant predicates and authority checks, not only request-entry authentication. |
| Cross-BB fakes | **Confirmed P1.** Disabled local fakes and redaction/authority helpers exist. | They are helper-level only until a real Scheduler request → service → task → fake topology proves deterministic outcomes and no Payments/Consent-owned-state mutation. |

## Verified remediation order

| Order | Increment | Purpose and required scope boundary |
|---:|---|---|
| 1 | **SCH-01 — Authoritative durable schedule admission** | Establish one locked, generation-scoped, transactional schedule admission path; recipient/outbox materialization; legacy-field contract; duplicate, rollback, zero-recipient, modification/cancel/delete/re-arm semantics; no pre-commit transport. |
| 2 | **SCH-02 — Publisher and recipient fault/recovery protocol** | Close both publisher and recipient claim/expiry/backoff/dead-letter/replay/crash windows. This explicitly includes publisher-row recovery, not merely recipient reaping. |
| 3 | **SCH-03 — Status/history/authorization projection** | Build owner/tenant-authorized durable lifecycle projection, redacted immutable history, and operational metrics. |
| 4 | **SCH-04 — Normalized local adapter/fake topology** | Mount disabled deterministic adapters and Payments/Consent fakes through actual Scheduler request/task paths without external calls or domain mutation. |
| 5 | **SCH-05 — Complete internal 37-operation harness** | Drive all registered official-inventory operations through real Django requests with valid/negative/schema/auth/correlation/durable-delta/redaction evidence. |

## Recommended first increment

The first strict increment remains **SCH-01 — Authoritative durable schedule admission**. It must be limited to database/admission semantics: current-generation enforcement under lock, idempotent recipient/outbox fan-out, deterministic zero-recipient treatment, duplicate convergence, rollback with no orphan rows, no pre-commit broker/transport I/O, and generation-fenced modification/cancellation/deletion/re-arm transitions. It must define whether `dispatched` becomes derived compatibility state or a non-authoritative projection; it must never remain an independent admission gate.

The first increment must not add a new adapter, external endpoint, fake topology, publisher/recipient failure matrix, status projection, full 37-operation harness, staging, official-suite execution, or submission.

## References

The independent review assessed only the current codebase and the Stage 1 analysis. The official-source citations governing the Stage 1 assessment remain: [Scheduler description][1], [functional requirements][2], [service APIs][3], and the [official `bb-scheduler` repository][4].

[1]: https://specs.govstack.global/scheduler/2-description
[2]: https://specs.govstack.global/scheduler/6-functional-requirements
[3]: https://specs.govstack.global/scheduler/8-service-apis
[4]: https://github.com/GovStackWorkingGroup/bb-scheduler

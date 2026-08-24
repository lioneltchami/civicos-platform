# Item 03 — Scheduler internal durable-wiring verification

**Date:** 2026-08-19
**Stage:** 4 of 4 — two independent final code reviews
**Scope:** Final Scheduler code and controlling plan only. No external service, staging, official suite, portal, credential, deployment, certification, or submission activity was performed.

## Method and conclusion

Two totally new reviewers received only the final codebase and `item-03-scheduler-internal-remediation-plan-20260819.md`. Both applied the plan’s closure requirement: helpers, inventories, routes, optional flags, and focused unit tests do not close an item without enforced runtime behavior and deterministic local proof.

> **Independent conclusion:** **0 of 6 defects are closed.** S03-01 through S03-05 are **Partially implemented**; S03-06 remains **Still open**. The internal foundations are useful but insufficient for Item 03 completion.

## Defect-by-defect determination

| ID | Status | Final-code evidence | Why closure is not established |
|---|---|---|---|
| S03-01 | **Partially implemented** | Durable materialization is present in the Scheduler task/runtime and the legacy Boolean is treated as a projection in the new path. | No verified default-settings path, migration-safe legacy reconciliation/backfill, rollback/no-row, duplicate/race, re-arm/cancel, no-transport-before-commit, or compatibility proof. The first isolated run exposed legacy dispatch-test incompatibilities after changing default behavior. |
| S03-02 | **Partially implemented** | Migration `0020` plus publisher token/owner/lease/generation fields and `claim_outbox`, `mark_outbox_published`, and `mark_outbox_failed` establish a foundation. | Required concurrent-publisher, broker-failure, crash-before/after-enqueue, expiry/redrive, stale-publisher, duplicate-convergence, rollback, and stale-generation publication evidence is absent. |
| S03-03 | **Partially implemented** | Exact 37-ID helper/validator, trace builder, and operation-matrix validator exist. | No completed Django request/runtime suite or trace bundle proves valid and negative requests, authorization, schemas, correlation/idempotency, state/task/outbox deltas, and redaction for every operation. |
| S03-04 | **Partially implemented** | Disabled-by-default `LocalPaymentsFake`, `LocalConsentFake`, authority contract, duplicate handling, and redaction helpers exist. | No mounted Scheduler request → service/task → fake topology proves success/failure/retry/correlation handling or Payments/Consent non-mutation. The loopback topology is not this Django boundary. |
| S03-05 | **Partially implemented** | Initial database helper and status route artifacts exist. | Query/route scope is not proven from authenticated persisted owner/tenant authority; tenant filtering does not fully constrain the query; required outbox/lifecycle coverage, authorization tests, and schema-sensitive-field rejection are absent. |
| S03-06 | **Still open** | Exact-ID ordering and local metadata validators exist. | No deterministic full 37-operation evidence bundle generated from request/runtime execution supplies lifecycle, race, authorization, topology, redaction, local-only, source-revision, and mandatory checksum evidence. |

## Preserved controls

The reviewers confirmed continued recipient/outbox foundation, generation and recipient lease fencing, safe outbound URL controls, bounded timeouts/no redirects, short transaction intent, and non-PII/redaction intent. These must remain while completing the missing work. Payments settlement and Consent decision/audit ownership must remain outside Scheduler mutation authority.

## Final conclusion

**Partially aligned / remediation required.** The Stage 3 code adds substantial local foundations, but Item 03 may not advance: all six defects remain open under the agreed Stage 4 rule. The immediate internal priorities are the default durable-dispatch proof and legacy reconciliation, publisher race/crash protocol tests, authenticated database status isolation, a genuine test-only fake topology, and a full 37-operation Django runtime evidence pipeline.

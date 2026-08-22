# Item 03 — SCH-02.1 Scope Confirmation: Bounded Publisher Recovery

**Date:** 2026-08-21  
**Review method:** Two independent scope confirmations.  
**Scope verdict:** **Conceptually ready; real PostgreSQL transaction-test gate must be corrected before implementation can be accepted.**

## Confirmed implementation boundary

SCH-02.1 is correctly limited to the publisher recovery/state contract: explicit publisher vocabulary; finite retry/backoff and attempt budget; terminal exhaustion; guarded replay; existing token/generation fencing; additive migration safety; and focused database-backed publisher tests. It must not modify recipient semantics, publisher crash-window proof, acknowledgement/history, SCH-01 authority or lifecycle, projections, adapters, fakes, harnesses, staging, the official suite, or submission.

The increment must preserve the existing transactional outbox and the stable recipient idempotency/correlation identity. `LOCAL_FAILURE`, `UNKNOWN_HANDOFF`, and `PUBLISHED` must remain distinct. The intended guarantee is durable at-least-once recovery with correlation, not external exactly-once delivery.

## Required test-gate correction

Both reviews found that `config/settings/test.py` documents PostgreSQL but actually configures in-memory SQLite. That configuration cannot serve as the complete SCH-02.1 `TransactionTestCase` gate for row locking, lease expiry, competing publisher ownership, token/generation fencing, or migration behavior.

The repository already provides a PostgreSQL service definition in `docker-compose.yml`, and local Docker is available. SCH-02.1 therefore requires an explicit reproducible PostgreSQL test profile or equivalent committed execution contract before closure. The later change list must define the minimal test configuration and test command, without changing production publisher behavior or broadening into a harness/staging task.

| Requirement | Confirmation |
|---|---|
| Publisher-only bounded recovery scope | Confirmed |
| Recipient and SCH-01 exclusions | Confirmed |
| Migration safety | Required |
| Focused TransactionTestCase state-machine gate | Required |
| Current SQLite test profile sufficient for locking/concurrency closure | Not sufficient |
| Reproducible PostgreSQL test contract before acceptance | Required |

## Consequence for the next stage

Stage 2 must produce an exact publisher-only change list that includes the minimal PostgreSQL test-profile/execution correction required to run the complete focused publisher gate. It must explicitly keep broad test harness, staging, official-suite, and deployment work out of scope.

# Item 03 — SCH-02.1 Retry Scope and PostgreSQL Readiness Confirmation

**Date:** 2026-08-21  
**Decision:** Ready to proceed to the refreshed exact change-list stage.

## Scope confirmation

Two shared-context reviews independently confirm that the SCH-02.1 retry remains strictly bounded to **durable publisher recovery and its state contract**. The repaired Payments migration closes the former PostgreSQL prerequisite only; it is not part of the Scheduler implementation scope and does not establish SCH-02.1 closure evidence.

| In-scope requirement | Required boundary |
|---|---|
| Publisher vocabulary | Persist and distinguish `PENDING`, `CLAIMED`, `LOCAL_FAILURE`, `UNKNOWN_HANDOFF`, `PUBLISHED`, `EXHAUSTED`, and existing `CANCELLED`. |
| Bounded policy | Use a finite configured attempt budget, deterministic persisted backoff, and no default immediate hot loop. |
| Exhaustion and replay | Make exhaustion terminal for ordinary due/claim paths; allow deliberate guarded replay only for eligible exhausted rows. |
| Fencing | Preserve current publisher token/generation fencing for failure and publication finalization. |
| Additive schema | Use one backward-safe Scheduler migration preserving existing intent, correlation, attempts, availability, leases, owners, tokens, generation, errors, published rows, and cancelled rows. |
| Evidence | Run all ten named publisher `TransactionTestCase` methods against PostgreSQL with migrations enabled. |

`UNKNOWN_HANDOFF` remains a durable at-least-once ambiguity classification. It is neither a local failure nor evidence of external publication. The retry must not claim external exactly-once delivery.

## PostgreSQL readiness

The prerequisite is satisfied. The independently verified Payments repair now allows a fresh PostgreSQL full project migration graph to complete, reports no pending model changes, retains `payments_batchlease.id` as a non-null UUID primary key, and proceeds through Appointments/Scheduler migrations.

The retry must nevertheless generate its own publisher-specific PostgreSQL evidence. It must preserve the exact command, fresh migration outcome, dependency/environment identity, raw test output, and explicit results for every required test method. SQLite is not an acceptable substitute.

## Explicit exclusions

The retry excludes recipient behavior and recipient recovery; acknowledgement/history or recovery facts; SCH-01 authority/lifecycle changes; publisher crash-window proof reserved for SCH-02.2; projections/read models; adapters/transports; fakes; the 37-operation harness; staging; deployment; the official suite; submission; external exactly-once claims; and any new Payments behavior or data migration.

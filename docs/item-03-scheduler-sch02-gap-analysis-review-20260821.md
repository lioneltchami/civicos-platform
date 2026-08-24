# Item 03 — SCH-02 Independent Review: Publisher and Recipient Fault/Recovery Gaps

**Date:** 2026-08-21  
**Review method:** Two fresh independent reviews of the current codebase, SCH-02 analysis, and official Scheduler source notes.  
**Conclusion:** **Gap analysis confirmed with ordering corrections.**

## Verified conclusion

Both independent reviewers confirm that CivicOS has durable delivery and outbox rows, deterministic materialization, claim/lease tokens, recipient retry/dead-letter/replay/reaping primitives, publisher claim fencing, and post-claim I/O separation. These are meaningful foundations, but they do not yet form a complete publisher-and-recipient recovery protocol.

The official Scheduler sources require configured retry before communication failure recording, durable message/alert correlation, acknowledgement/status logging, and transaction/failure observability. They intentionally leave retry mechanics implementation-specific.[1] [2] Therefore, the required CivicOS target is a **bounded, observable, at-least-once recovery contract**, not a claim that the Scheduler can enforce external exactly-once delivery.

## Corrections to the Stage 1 ordering

| Topic | Stage 1 position | Independent correction |
|---|---|---|
| Publisher unknown handoff | Defined mainly in the publisher crash-window increment. | SCH-02.1 must already define the vocabulary/contract distinguishing local enqueue failure, unresolved handoff, and confirmed publication; SCH-02.2 then proves the crash/reclaim behavior. |
| Recipient outcome normalization | Originally proposed before the lease-expiry invariant. | Move it after intrinsic lease-expiry fencing. A worker whose lease is already invalid must not be able to decide an outcome policy. |
| Retry formula | Stage 1 identified backoff and bounds as necessary. | Retain the requirement for a configured and observable policy, but do not represent exponential backoff, a specific dead-letter schema, or a numeric attempt limit as a fixed official GovStack algorithm. |
| External duplicate delivery | Identified as a crash-window risk. | Preserve it explicitly as an at-least-once/unknown-handoff limitation. Local token/generation fences protect current database state; they cannot revoke an externally accepted enqueue or HTTP request. |

## Verified prioritized gaps

| Priority | Remaining internal gap |
|---:|---|
| P0-A | Publisher enqueue failure is immediately due and unbounded: no publisher classification, backoff, maximum attempt budget, terminal exhaustion, or guarded replay. |
| P0-B | Publisher broker-handoff ambiguity is not represented in a durable recovery contract. |
| P0-C | Recipient completion does not atomically reject its own expired lease; expiry becomes effective only after a competing recovery action replaces or clears the token. |
| P0-D | Recipient transport outcomes lack a normalized retryability policy for accepted/duplicate/transient/timeout/throttle/auth/schema/not-found conditions. |
| P1 | Publisher reclaim is implicit, and recovery facts/history are not durable and auditable. |
| P1 | Retry/replay accounting is not unified across normal failure, expiry, publisher recovery, and administrative replay. |
| P1 | Acknowledgement lacks alert-token/source/status/duplicate/late/invalid semantics. |

## Corrected strict increment order

| Order | Increment | Strict exit boundary |
|---:|---|---|
| 1 | **SCH-02.1 — Bounded publisher recovery and state contract** | Publisher retry classification, persisted backoff, attempt budget, terminal exhaustion, guarded replay, and a defined local-failure/unknown-handoff/published vocabulary; preserve token/generation fencing. |
| 2 | **SCH-02.2 — Publisher crash-window and reclaim proof** | Before-handoff and after-handoff/unknown windows, lease expiry, competing publishers, stale finalization, cancellation/generation changes, and explicit at-least-once semantics. |
| 3 | **SCH-02.3 — Intrinsic recipient lease-expiry fencing** | Atomically reject expired completion and prove completion/reap/reclaim/replay concurrency invariants. |
| 4 | **SCH-02.4 — Normalized recipient outcome contract** | Explicit retryability mapping and bounded transitions for accepted, duplicate, transient, timeout, throttle, authentication, schema/validation, not-found, and terminal results. |
| 5 | **SCH-02.5 — Recovery facts and acknowledgement correlation** | Minimum durable recovery facts plus token/source/time/outcome/duplicate acknowledgement inputs, without building projections. |
| 6 | **SCH-02.6 — Focused recovery gate** | Publisher/recipient crash, concurrency, retry, replay, expiry, generation-fence, acknowledgement, and history regression proof. |

## First increment direction

The first small increment remains **SCH-02.1 — Bounded Publisher Recovery and State Contract**. It is the narrowest change that closes the presently unbounded publisher hot-loop while setting the vocabulary that SCH-02.2 needs to test ambiguous broker handoff correctly. It must not alter recipient semantics, SCH-01 admission/lifecycle behavior, status projections, adapters, fakes, the 37-operation harness, staging, official-suite work, or submission.

## References

[1]: [GovStack Scheduler Specification — Description](https://specs.govstack.global/scheduler/2-description), §2.1.5.

[2]: [GovStack Scheduler Specification — Key Digital Functionalities](https://specs.govstack.global/scheduler/4-key-digital-functionalities), §§4.3, 4.9, and 4.12.

[3]: [GovStackWorkingGroup/bb-scheduler](https://github.com/GovStackWorkingGroup/bb-scheduler).

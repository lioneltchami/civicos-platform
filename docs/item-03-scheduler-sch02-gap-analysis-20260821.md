# Item 03 — SCH-02 Gap Analysis: Publisher and Recipient Fault/Recovery Protocol

**Date:** 2026-08-21  
**Scope:** SCH-02 only — publisher and recipient fault/recovery protocol  
**Current internal status:** **Partially aligned / remediation required**

## Official requirement baseline

The Scheduler specification requires durable alert tracking with unique tokens, acknowledgement/status logging, internal transaction and communication-failure monitoring, and configured retries before a communication failure is recorded. The retry algorithm is implementation-specific, but the resulting policy must be coherent, bounded, and observable.[1] The specification also describes an internal queue until handoff to Messaging and status logs that include time, source, and status information.[2]

> The implementation target for SCH-02 is therefore **durable at-least-once recovery with stable correlation and a configured bounded policy**. It is not Scheduler-enforced exactly-once external delivery.

## What is already solid

| Recovery concern | Current implementation evidence | Assessment |
|---|---|---|
| Durable recipient intent | `SchedulerRecipientDelivery` persists schedule, generation, recipient identity, idempotency/correlation context, state, attempt count, retry timing, lease token/expiry, error, acknowledgement, cancellation, and dead-letter timestamps in `apps/appointments/models.py`. | Strong foundation. |
| Durable publisher intent | `SchedulerOutbox` is a one-to-one child of delivery with publication, availability, publisher generation/token/owner/lease, cancellation, attempts, and error state. | Strong transactional-outbox foundation. |
| Idempotent materialization | `scheduler_runtime._materialize_locked()` derives deterministic identity and creates one recipient/outbox pair under the locked SCH-01 admission path. | Strong duplicate convergence. |
| Recipient claim and stale completion | `claim()`, `_with_valid_lease()`, `succeed()`, and `fail()` in `apps/appointments/services/scheduler_runtime.py` use a locked row and exact lease token. | Strong local mutual exclusion and stale-token guard. |
| Recipient retry, dead letter, replay and reaping | `fail()`, `replay()`, and `reap_expired()` provide bounded recipient retry timing, terminal dead-letter state, replay, and expired-lease recovery. | Real primitives, but policy is incomplete. |
| Publisher claim and stale completion | `claim_outbox()`, `mark_outbox_published()`, and `mark_outbox_failed()` use locked selection and publisher token/generation predicates. | Strong local publisher fencing. |
| Transport after durable claim | `apps/appointments/scheduler_tasks.py` performs broker/HTTP activity after durable claim, not inside the row-lock transaction. | Correct transaction/I-O separation. |
| Basic acknowledgement state | `acknowledge()` transitions delivered recipient work to acknowledged state and records a local time. | Useful local primitive, insufficient official correlation/history. |
| Existing focused tests | `test_scheduler_runtime.py` covers recipient/outbox convergence, claim exclusion, stale token rejection, acknowledgement, dead-letter/replay, and live durable dispatch. | Useful foundation, not a complete crash/concurrency gate. |

## Publisher recovery gaps

The publisher path durably claims an outbox row, performs broker handoff outside the transaction, then marks publication with the active publisher token and generation. Two competing publishers therefore have one durable claimant, and an old publisher cannot finalize a row reclaimed by a newer owner.

The principal blocker is that publisher failure recovery is **unbounded and immediate**. `mark_outbox_failed()` clears the lease and makes the row due immediately. There is no configured publisher backoff, publisher failure classification, maximum publication-attempt budget, terminal publisher dead-letter state, guarded replay, or recovery transition/audit. A persistent broker failure can hot-loop the same row and never reach an observable exhausted state. That is not a defensible configured retry-before-failure policy.[1]

The crash window after successful broker enqueue but before `mark_outbox_published()` remains inherently ambiguous. Reclaim is desirable after a crash, but it can enqueue a duplicate recipient task. The recipient idempotency key can support downstream deduplication, but CivicOS cannot guarantee external exactly-once delivery. SCH-02 must explicitly represent the resulting at-least-once/unknown-handoff contract and prove that stale publisher completion cannot mutate the current row.

| Publisher window | Current behavior | Required remediation evidence |
|---|---|---|
| Crash before broker handoff | Lease eventually becomes claimable via due/expired predicate. | Explicit reclaim transition and bounded retry policy. |
| Broker handoff fails | Lease is cleared and row is due immediately. | Backoff, retryability classification, bounded exhaustion, terminal state, replay. |
| Crash after broker accepts handoff | Later reclaim may enqueue again. | Explicit unknown-handoff semantics, stable idempotency context, stale completion rejection. |
| Old publisher finishes after reclaim | Token/generation predicate rejects its update. | Deterministic competing-publisher and stale-finalization proof. |

## Recipient recovery gaps

Recipient recovery is materially stronger, but not closed. The runtime provides a claim lease, retry/backoff, dead letter, replay, and expired-lease reaping. The worker sends the stable idempotency key and correlation context, but the external recipient is responsible for honoring it.

The primary gap is an **expiry-completion race**. `succeed()` and `fail()` validate state and token but do not intrinsically reject a completion solely because `lease_expires_at` has passed. A reaper or later claimant clears/replaces the token and then fences the older worker, but an expired worker can win if it finalizes first. SCH-02 needs one atomic expiry rule and a deterministic concurrent proof.

The transport worker also maps diverse outcomes to generic failure treatment. Timeout, connection failure, throttle, authentication, schema/validation, terminal-not-found, successful acceptance, and successful duplicate are not normalized into a durable retryability contract. Permanent errors can consume retries unnecessarily, while the rationale for any retry is not auditable. The official requirements permit implementation-specific rules but require configured retries and failure monitoring.[1]

Acknowledgement is only a local state flip keyed by internal idempotency key. The official requirement calls for correlation to alert tokens and logging sender/source/time/status information.[1] SCH-02 must at minimum make acknowledgement input idempotent, distinguish first/duplicate/late/invalid acknowledgement, and persist bounded sender/time/token/outcome facts. Broader read models and status projections remain explicitly out of scope.

## Prioritized internal recovery gaps

| Priority | Gap | Concrete evidence |
|---:|---|---|
| P0 | Bounded publisher retry, backoff, exhaustion, terminal failure, and replay are absent. | `mark_outbox_failed()` makes the row immediately due; no publisher terminal/replay policy. |
| P0 | Publisher broker-handoff crash semantics are unknown and unrepresented. | Enqueue can succeed before publication mark; later reclaim can enqueue again. |
| P0 | Recipient outcomes lack a normalized retryability contract. | Worker/runtime treat heterogeneous external failures as broadly equivalent. |
| P0 | Recipient completion is not intrinsically invalidated by lease expiry. | Completion checks token/state; expiry fencing depends on a separate recovery action winning first. |
| P1 | Publisher reclaim has no explicit recovery fact or deterministic reaper evidence. | Expired lease is implicitly claimable only. |
| P1 | Retry/replay accounting is not unified across normal failure, timeout, lease expiry, publisher failure, and administrative replay. | Existing primitives are individually useful but not one documented policy. |
| P1 | Acknowledgement lacks sender/token/status payload correlation and duplicate/late handling. | `acknowledge()` records only local terminal transition/time. |
| P1 | Recovery transaction facts are not durable history. | Current row state cannot reconstruct claim, reclaim, stale completion, handoff-unknown, retry, or replay events. |

## Recommended ordered strict sub-increments

| Increment | Narrow purpose | Completion evidence | Explicit exclusions |
|---|---|---|---|
| **SCH-02.1** | Freeze and implement **bounded publisher recovery**: classified broker failure, durable retry timing, maximum attempts, terminal publisher failure, replay, and token/generation fence. | Publisher failure/backoff/exhaustion/replay/expiry/stale-completion `TransactionTestCase` suite. | Recipient outcome normalization, acknowledgement history, projections, adapters, fakes. |
| **SCH-02.2** | Establish a **publisher crash-window and reclaim contract** for before-handoff, after-handoff/unknown, concurrent publisher, and stale completion states. | Two-publisher real-task tests proving single valid durable outcome and explicit at-least-once handoff semantics. | Recipient policy changes, external exactly-once claims. |
| **SCH-02.3** | Normalize **recipient transport outcomes** into accepted/duplicate/transient/terminal/timeout/auth/schema classes with configured retryability. | HTTP-result classification and bounded retry/dead-letter tests. | Publisher recovery changes, adapters, projections. |
| **SCH-02.4** | Close **recipient lease-expiry/reaper/replay accounting** and prove stale completion cannot win after intrinsic expiry. | Concurrent worker, lease-expiry, reaper, replay, and attempt-budget tests. | Acknowledgement history and projection work. |
| **SCH-02.5** | Add minimal **acknowledgement correlation and recovery facts**: token, sender/source, timestamp, outcome, duplicate/late handling, plus core recovery transition history. | Durable acknowledgement/recovery-fact tests. | Status read-model/projection design. |
| **SCH-02.6** | Run the focused SCH-02 end-to-end recovery gate. | Publisher and recipient concurrent/crash/retry/replay/generation-fence regression evidence. | 37-operation harness, staging, official suite, submission. |

## Recommended first increment

**SCH-02.1 — Bounded Publisher Recovery** is the smallest first increment. It closes the only path with immediate unbounded retry and no terminal publisher failure, while preserving all established SCH-01 admission and lifecycle behavior. It should add no recipient semantic change, no adapter, no projection, and no external test environment work.

## References

[1]: [GovStack Scheduler Specification — Description](https://specs.govstack.global/scheduler/2-description), §2.1.5.

[2]: [GovStack Scheduler Specification — Key Digital Functionalities](https://specs.govstack.global/scheduler/4-key-digital-functionalities), §§4.3, 4.9, and 4.12.

[3]: [GovStackWorkingGroup/bb-scheduler](https://github.com/GovStackWorkingGroup/bb-scheduler).

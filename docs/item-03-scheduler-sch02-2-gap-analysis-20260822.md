# Item 03 — SCH-02.2 Publisher Crash-Window Gap Analysis

After SCH-02.1, durable admission, bounded claims, token/generation fencing, finite retry, unknown-handoff classification, exhaustion, replay, and one-current-owner PostgreSQL proof are solid. The publisher claim commits before broker I/O, and stale database finalization is rejected after expiry/reclaim.

| Window | Current control | Remaining gap |
|---|---|---|
| Claim committed; before broker handoff | Expired `CLAIMED` row is reclaimable | No real process-crash test proves recovery after this boundary. |
| Broker handoff accepted; before durable finalization | Lease reclaim and stable delivery idempotency key | Reclaim may enqueue duplicate work; `PUBLISHED` means local enqueue returned, not broker receipt or endpoint delivery. |
| Handoff timeout/ambiguous response | `UNKNOWN_HANDOFF` with bounded retry | No broker receipt/reconciliation or durable handoff-attempt evidence. |
| Lease expiry during slow handoff | New claimant fences stale database write | No heartbeat/lease extension or proof for overlap of old handoff with new reclaimer. |
| Reclaim/stale finalization | Token and generation predicates | Database writes are fenced, but already-issued broker/HTTP side effects cannot be retracted. |

The official Scheduler requirements require configured retry/backoff, communication-failure handling, unique alert tokens, and status/transaction logging.[1] [2] SCH-02.1 meets a bounded database recovery subset but does not prove crash-phase evidence, broker confirmation, downstream idempotency, or append-only publisher attempt history.

## Prioritized gaps and order

1. **SCH-02.2-A — Publisher attempt evidence and deterministic crash injection.** Add a minimal durable publisher-attempt phase record or equivalent existing durable log contract, then prove process failure after claim and before handoff, and after handoff return before finalization.
2. **SCH-02.2-B — Handoff ambiguity and reclaim race.** Prove an accepted-but-unfinalized/unknown handoff and lease-expiry race with real PostgreSQL connections, including stale finalization rejection and explicit at-least-once duplicate-safe semantics.
3. **SCH-02.2-C — Lease-overlap closure.** Bound handoff duration below lease lifetime or renew the current lease; prove a slow handoff cannot silently overlap an unsafe reclaimer.
4. **SCH-02.2-D — Broker/downstream reconciliation contract.** Persist broker identity/receipt where available, or retain explicit unknown state and a safe reconciliation policy. Recipient recovery and acknowledgement history remain excluded.

[1]: https://specs.govstack.global/scheduler/2-description
[2]: https://specs.govstack.global/scheduler/4-key-digital-functionalities

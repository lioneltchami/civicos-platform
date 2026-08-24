# Item 03 — SCH-02.2 First Increment Plan

## SCH-02.2-A — Durable publisher attempt phases and crash-injection proof

**Scope.** Add the smallest durable publisher-attempt evidence record or equivalent append-only event contract needed to distinguish: claim committed, handoff about to start, handoff returned, unknown handoff, finalization, lease expiry/reclaim, and stale finalization rejection. Add deterministic test hooks only at the publisher boundary; do not change recipient behavior, broker adapter semantics, or SCH-02.1 state meanings.

| Acceptance criterion | Required proof |
|---|---|
| Durable phase evidence | Real PostgreSQL records keyed by outbox, generation, correlation, and publisher attempt identity. |
| Pre-handoff crash | Commit claim, inject termination before enqueue, expire/reclaim, and prove one current owner plus durable phase sequence. |
| Post-handoff/pre-finalization crash | Inject termination after a controlled handoff return and before finalization; prove reclaimed attempt, stale finalization rejection, and explicit at-least-once duplicate-safe classification. |
| Unknown handoff | Persist ambiguity without false publication and prove bounded reclaim behavior. |
| Observability | Record claim, handoff phase, reclaim, and stale-finalization rejection durably; no mutable-row-only assertion. |
| Regressions | Real PostgreSQL TransactionTestCase tests plus retained SCH-02.1 publisher and bounded SCH-01/runtime regression gates. |

**Explicit exclusions.** No recipient recovery, downstream endpoint implementation, acknowledgement history, broker redesign, exactly-once assertion, projections, adapters, fakes, harnesses, staging, or SCH-02.3/later work. Do not reinterpret current `PUBLISHED` as broker receipt in this increment.

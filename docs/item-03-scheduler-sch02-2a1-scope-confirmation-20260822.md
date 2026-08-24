# Item 03 — SCH-02.2-A1 Scope Confirmation

Two shared-context analyses confirm this is a **publisher audit-only** increment: one additive immutable attempt-event model/migration and append-only writes in existing claim, failure, finalization, and reclaim transactions. Existing outbox state, token/generation fences, attempts, leases, correlation, idempotency, retry, replay, and cancellation remain authoritative.

| Required event seam | Required invariant |
|---|---|
| Claim | A committed claim writes an immutable event in the same transaction. |
| Local failure / unknown handoff | Current fenced outcome writes a distinct immutable event without inferring publication. |
| Published finalization | Only current token/generation finalization writes publication evidence. |
| Reclaim | New attempt/generation ownership appends evidence; prior event is never mutated. |

No crash hooks, crash-window tests, recipient changes, broker adapter changes, projection/history work, or SCH-01 change is allowed. Focused evidence must be PostgreSQL-backed and retain SCH-02.1 plus SCH-01 regressions.

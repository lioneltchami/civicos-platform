# Item 03 — SCH-02.1 Retry 2 Scope and Readiness Confirmation

**Status: Ready for Stage 2 change-list work; not implementation or closure evidence.** Two shared-context analyses confirmed that SCH-02.1 can be retried as a clean, narrowly bounded publisher-recovery increment. The previous candidate implementation remains discarded, and neither completed SCH-01 work nor the separately verified PostgreSQL dispatch-lock correction will be reopened.

| Readiness condition | Confirmation |
|---|---|
| Payments PostgreSQL migration prerequisite | Resolved in the independently verified Payments repair; the fresh full graph is no longer blocked by the historical UUID-to-bigint alteration. |
| Live Scheduler dispatch PostgreSQL prerequisite | Resolved by the verified schedule-only scoped lock; the nullable-resource dispatch test and bounded Scheduler lifecycle/runtime regressions pass on PostgreSQL. |
| Current SCH-02.1 baseline | Does not contain publisher recovery implementation; explicit publisher state/classification, finite publisher backoff/exhaustion, guarded replay, and the dedicated PostgreSQL ten-method suite remain to be implemented. |
| Intended scope | Publisher outbox state contract, finite retry/replay policy, fenced claim/finalization, safe additive migration, task outcome classification, dedicated PostgreSQL profile, exact ten tests, and bounded Scheduler regression only. |
| Forbidden scope | Recipient recovery, crash-window proof, acknowledgement/history, projections, adapters/transports redesign, fakes, harnesses, staging, official validation/submission, SCH-01 behavior, and Payments changes. |

The future implementation must create the required seven-state durable publisher vocabulary—`PENDING`, `CLAIMED`, `LOCAL_FAILURE`, `UNKNOWN_HANDOFF`, `PUBLISHED`, `EXHAUSTED`, and `CANCELLED`—without conflating local failure, ambiguous broker handoff, and confirmed publication. It must persist finite deterministic backoff, exclude exhausted rows from ordinary due/claim selection, permit only guarded replay of eligible exhausted rows, and retain current token-and-generation fencing.

The retry remains subject to the all-or-discard rule. Retention requires a fresh additive migration, no pending model changes, all ten exact PostgreSQL `TransactionTestCase` methods, and a passing bounded Scheduler runtime/lifecycle regression. The resolved Payments and dispatch prerequisites do not substitute for this new evidence.

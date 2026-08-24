# Item 03 — SCH-02.2-A Implementation Rejection

**Status: Rejected under the all-or-discard rule.**

Two isolated implementation attempts did not produce a complete, runnable SCH-02.2-A change. One returned only default-disabled environment-variable crash hooks and explicitly reported that it could not validate the complete Django/PostgreSQL graph; the other rejected the incomplete packet. Neither delivered the required additive append-only attempt-event model, migration, durable runtime event seams, exact five PostgreSQL tests, or raw PostgreSQL evidence.

| Required criterion | Result |
|---|---|
| Append-only durable publisher attempt evidence | Missing |
| Publisher-boundary deterministic crash hooks | Partial/unvalidated candidate only; not applied |
| Pre-handoff PostgreSQL crash/reclaim proof | Missing |
| Post-handoff/pre-finalization crash/reclaim/stale fence proof | Missing |
| Unknown-handoff durable phase proof | Missing |
| Retained SCH-02.1 and SCH-01 PostgreSQL regressions | Not run for SCH-02.2-A |

No implementation candidate was applied to the repository. `git status --short` was clean immediately before this record, with `f5365c3` as the committed planning baseline. No Stage 4 verification began. SCH-02.2-A remains **Still open**; SCH-01 and SCH-02.1 remain closed and unchanged.

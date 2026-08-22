# Item 03 — SCH-02.2-A1 Implementation Rejection

**Status: Rejected under the all-or-discard rule.**

Two isolated candidates were reviewed but neither was complete or validated. Both explicitly reported that they could not run the canonical Django/PostgreSQL migration and test graph. The candidates also lacked the required complete focused PostgreSQL suite and did not establish the exact immutable, database-enforced append-only contract. No candidate was applied.

| Required deliverable | Result |
|---|---|
| Immutable append-only event model and additive migration | Partial drafts only; not applied |
| Transactional event writes at all required seams | Partial drafts only; not applied |
| Focused PostgreSQL event tests | Missing/incomplete and not run |
| SCH-02.1 / SCH-01 PostgreSQL regressions | Not run for SCH-02.2-A1 |

The worktree was clean immediately before this record; `4816c4b` remains the planning baseline. SCH-02.2-A1 is **Still open**. SCH-01 and SCH-02.1 remain closed and unchanged. No Stage 4 verification started.

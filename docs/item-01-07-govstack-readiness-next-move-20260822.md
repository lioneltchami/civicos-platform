# Recommended Next Move — CivicOS Items 01–07

## Recommendation

The two independent recommendations converge on one highest-leverage next work item: **prepare for a separately human-authorised, bounded staging validation run with a pre-registered evidence-capture plan and named governance owner.** This is an authorisation/readiness package only; it does **not** authorise, schedule, deploy, access secrets, run staging, execute official tests, or submit any candidate.

| Candidate | Why not first | Dependency relationship |
|---|---|---|
| **Authorised staging validation readiness** | Recommended: it is the clearest cross-cutting gate between existing internal work and trustworthy external evidence. | Can generate truthful evidence for advanced paths such as Payments and expose remaining gaps before submission preparation. |
| SCH-02.2 | Remains valuable but narrowly scoped and r| SCH-02.2 | Remains valuable but narrowly scoped and r| SCH-02.2 | Remains valuable but narrowly scoped and r| SCHon-blocked staging/testing path. |
| File Management runner | Environment-blocked. | Requires its own infrastructure resolution first. |
| Consent expansion / requirements documentation | Helpful but lower cross-portfolio leverage. | Does not remove the authorisation and evidence bottleneck. |

## SCH-02.2 position

**SCH-02.2 should remain deferred for now.** It remains a genuine Scheduler engineering gap, but it should not be reopened without a complete implementation/evidence plan and a reliable PostgreSQL execution path. Revisiting it after the staging-validation governance criteria are ready is preferable, unless the separately authorised validation design itself identifies a safe, specific route to collect the missing Scheduler evidence.

## Required boundary

A future staging step requires explicit human authorisation, a named owner, approved target and scope, access and deployment approval, rollback and observability criteria, redaction rules, immutable evidence capture, and a stop/exit rule. Results must be recorded as evidence, including failure; they cannot be presumed to pass or treated as a certification/submission basis without the corresponding official gates.

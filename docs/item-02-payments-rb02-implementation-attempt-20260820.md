# Item 02 — RB-02 Complete Implementation Attempt

**Date:** 2026-08-20  
**Stage:** Stage 3 all-or-discard implementation attempt  
**Disposition:** **Rejected and discarded.**

> Two independent full implementation attempts were performed against the frozen seam map and final change list. Neither delivered all seven acceptance points and all twelve mandatory live `TransactionTestCase` methods. Under the mandatory all-or-discard rule, no implementation patch was applied or committed.

## Attempt results

| Attempt | Result | Blocking deficiencies | Accepted patch |
|---|---|---|---|
| A | Rejected | The live worker rewrite was incomplete; task-local lease logic and mapper-count finalization remained. Bound-child finality materialization, live policy wiring, unique decision persistence, fenced audit/callback coupling, and the complete test module were absent. | None. |
| B | Rejected | The lease, binding, decision, and finality work remained exploratory/partial. Live policy integration, complete side-effect fencing, and all twelve required real-database tests were absent. | None. |

## Acceptance-point disposition

| Acceptance point | Status | Reason |
|---:|---|---|
| 1. Reusable owner-token/generation lease service | Not accepted | Partial exploratory work does not establish a complete service integrated through the live task. |
| 2. Lease-fence every live side effect | Not accepted | Child, finality, audit, decision, callback, projection, heartbeat, and release fences were not all implemented. |
| 3. Durable instruction-to-attempt binding | Not accepted | Partial model/migration work was not coupled to a complete live worker path. |
| 4. Authoritative provider observation/reconciliation finality | Not accepted | Complete bound-child finality materialization and aggregation were absent. |
| 5. Live `govstack_batch_policy.evaluate()` invocation | Not accepted | Complete live policy integration was absent. |
| 6. Durable idempotent pause/retry/review/return-funds decisions | Not accepted | Unique decision persistence and idempotent decision/audit/callback coupling were absent. |
| 7. Full competing-worker `TransactionTestCase` suite | Not accepted | The required live test module and all twelve named methods were absent. |

## Mandatory test disposition

None of the following required methods was delivered in a passing real-database `TransactionTestCase` module: `test_fresh_acquire_and_heartbeat_is_fenced`, `test_expiry_takeover_advances_generation_once`, `test_stale_owner_cannot_write_any_side_effect`, `test_authoritative_mixed_child_finality_is_nonterminal`, `test_threshold_pause_persists_one_idempotent_decision`, `test_retry_decision_is_durable_and_idempotent`, `test_review_decision_is_durable_and_idempotent`, `test_configured_return_funds_is_explicit_and_idempotent`, `test_empty_batch_has_one_durable_outcome`, `test_already_terminal_children_are_aggregated_not_reprocessed`, `test_duplicate_finalization_creates_one_decision_audit_and_callback`, and `test_two_live_workers_cross_expiry_and_stale_finalization`.

## Scope and gate

RB-02 is **still open**. Stage 4 independent closure verification was not started because there is no clean complete implementation commit to verify. RB-03, RB-04, staging, official-suite, and submission work remain out of scope. The frozen canonical seam mapping, pre-flight confirmation, and final change list remain valid prerequisites for a future complete implementation attempt.

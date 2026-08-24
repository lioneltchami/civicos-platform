# Item 03 — SCH-01.2c Independent Delete-and-Re-arm Verification

**Date:** 2026-08-21  
**Stage:** Two fresh independent source reviews, followed by two fresh complete-evidence re-reviews  
**Verdict:** **SCH-01.2c Closed.**

> The initial source reviews found no correctness defect but could not execute the archived source. A complete evidence package then supplied the source, focused real-database runtime log, exact changed-file list, and baseline diff. Both final reviewers confirmed closure with complete evidence.

## Verified controls

| Requirement | Verified result |
|---|---|
| Locked Delete | Runtime locks the parent schedule, fences non-terminal recipients and unpublished outbox rows, then performs existing hard delete/cascade. |
| Durable stale-work prevention | Parent absence after hard delete prevents a later admission invocation from recreating delivery or outbox rows. |
| Broker boundary | Broker revoke remains best-effort cleanup only; the database transaction and parent deletion are the correctness boundary. |
| Distinct Re-arm | Re-arm is a separate locked transition, not Modify behavior. |
| Re-arm transition | It fences old work, advances generation exactly once, restores `delivery_admittable=True`, and resets admission markers. |
| Re-arm idempotency | Repeated Re-arm returns a duplicate/no-op result without another generation advance. |
| New admission | Old generation is rejected rowlessly; one new-generation admission succeeds; repeated admission is duplicate/no-op. |

## Runtime and boundary evidence

| Evidence | Result |
|---|---|
| `test_delete_schedule_cannot_leave_recreatable_admission_work` | Passed against a real test database. |
| `test_rearm_advances_generation_and_admits_exactly_once` | Passed against a real test database. |
| Focused run | **2 tests passed** in 0.364 seconds. |
| Final independent complete-evidence reviews | Both **Closed** and evidence-complete. |
| Exact implementation diff | Limited to `scheduler_runtime.py`, `govstack_alert_schedule.py`, and `test_scheduler_lifecycle.py`. |

## Scope audit

No model or migration change, task integration, recovery, adapter, projection, fake, harness, staging, official-suite work, submission, Item 02, or later Scheduler work was added.

## Lifecycle conclusion

SCH-01.1, SCH-01.2a, SCH-01.2b, and SCH-01.2c are now closed as internal increments. Therefore the full **SCH-01.2 lifecycle fencing milestone is internally complete**. This is not a platform conformance, certification, official-suite, staging, testing-site, or submission claim.

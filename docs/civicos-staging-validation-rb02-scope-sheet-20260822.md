# Payments RB-02 Operational Scope Sheet — Draft Pending Human Approval

## Candidate and authority boundary

This is a versioned, code-grounded scope sheet for `Civicos-Payments-RB02-OptionB-ScopePure`, canonical SHA-256 `3fb5a4327e3ce5b0b9c076f3d2426d4e663ae178f7091ed6c19515c8c5b4ddca`, manifest SHA-256 `15b56e3bc3caf858522ce5196c70b61cc32efa476411455df367560a8d653ff9`, at `artifacts/payments-rb02-option-b-scope-pure/`.

> **DRAFTED / PENDING HUMAN APPROVAL.** This sheet creates no staging route, test window, deployment permission, secret access, provider access, or execution authority.

## Allowed code-level surface for any future approved review

| Surface | Code-grounded limit | Evidence |
|---|---|---|
| Admission | The mounted GovStack bulk-payment surface that creates the durable batch/instruction work leading to `process_bulk_payment_batch`. | `apps/payments/govstack_views.py`; `apps/payments/govstack_tasks.py`; `apps/payments/tests/test_govstack_tasks.py`. |
| Batch ownership | Atomic owner-token/generation lease acquisition, heartbeat, expiry takeover, current-owner assertion, release, and stale-owner rejection. | `apps/payments/govstack_batch_lease.py`; live tests `test_fresh_acquire_and_heartbeat_is_fenced`, `test_expiry_takeover_advances_generation_once`, and `test_stale_owner_cannot_write_any_side_effect`. |
| Finality/policy | Bound provider-observation/finality handling and the existing batch policy/decision surface for mixed finality, pause, retry, review, and configured return-funds decisions. | `apps/payments/govstack_batch_policy.py`; `apps/payments/govstack_tasks.py`; live tests listed below. |
| Finalisation | One durable logical decision, audit, and callback behavior for duplicate finalisation; empty-batch durable outcome. | `apps/payments/govstack_tasks.py`; live tests `test_empty_batch_has_one_durable_outcome` and `test_duplicate_finalization_creates_one_decision_audit_and_callback`. |
| Competing workers | Cross-expiry takeover with stale finalisation rejected on the actual live task path. | `apps/payments/tests/test_item02_rb02_batch_lease_live.py::test_two_live_workers_cross_expiry_and_stale_finalization`. |

The expected code-test scope is the twelve named methods in `apps/payments/tests/test_item02_rb02_batch_lease_live.py`: lease acquire/heartbeat, expiry takeover, stale-write rejection, mixed/terminal child handling, durable pause/retry/review/return-funds decisions, empty/duplicate finalisation, and the live two-worker expiry scenario. This is implementation evidence, not permission to run any remote test.

## Hard limits and dependencies

The candidate is limited to the committed RB-02 component artifact and its allowlisted Payments source/tests. It depends on the application’s Django persistence/migrations, the durable batch lease, policy service, task queue configuration, and provider-observation data model. Those dependencies are code-level facts only; no runtime target, worker topology, broker, database, provider account, callback host, human operator, or secret mechanism is evidenced here.

No kill switch, feature flag, maintenance switch, or named stop operator is established by this sheet. These controls remain **UNAVAILABLE / BLOCKED** and must be resolved through the operational-precondition pack before any future authorisation decision.

## Future success and stop criteria

A future, separately authorised, bounded review would require the immutable candidate continuity check to pass and the entire named RB-02 live test surface to remain green. It must stop or remain blocked if lease/fencing, finality, policy, audit/callback idempotency, empty/duplicate finalisation, or competing-worker assertions fail; if candidate digests differ; if a required dependency/control is unavailable; or if the proposed action requires an unapproved route, remote provider, secret, real money, or any excluded scope.

## Explicit exclusions

This scope excludes **all live money movement**, live provider credentials/accounts, real payment instruments, real refunds/disbursements/settlement, production/customer/payment data, external callback registration, staging/production environment creation, deployment, official tests, release, submission, worker-topology changes, and any remote execution. It also excludes SCH-02.2, all Scheduler work, File Management/Document Management, adapters, recipient recovery, acknowledgement/history, projections, and any scope outside the immutable RB-02 artifact. The mixed `49e69fb8a3051de6c1cf7adff8e16c928cf84412` anchor remains provenance-only.

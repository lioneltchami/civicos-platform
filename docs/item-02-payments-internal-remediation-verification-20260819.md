# Item 02 — Payments internal runtime-enforcement verification

**Date:** 2026-08-19
**Stage:** 4 of 4 — two independent final code reviews
**Scope:** Internal source, migrations, local tests, and committed remediation plan only. No staging, provider credentials, external provider, official suite, testing site, deployment, certification, or submission action was performed.

## Independent-verification method

Two totally new reviewers received only the final Payments codebase and `docs/item-02-payments-internal-remediation-plan-20260819.md`. They did not receive prior analyses, code-review transcripts, implementation patches, external systems, or credentials. Each applied the plan’s closure rule:

> A model, migration, helper, pure policy, or optional enqueue does not close a defect. Closure requires an enforced local mounted route → canonical admission → committed task → durable claim → configured adapter → observation/reconciliation → bounded status flow, plus migration, race, negative-security, and deterministic-adapter evidence.

Both reviewers reached the same overall conclusion: **no P02 defect is Closed**. The implementation adds useful fail-closed foundations but does not satisfy the required live-path enforcement condition.

## Defect-by-defect conclusion

| ID | Independent conclusion | Concrete final-code evidence | Why it is not closed |
|---|---|---|---|
| **P02-01** | **Partially implemented** | `ProviderRegistration` and migration `0034_item02_core_runtime.py`; `ProviderRuntime.resolve()` now requires an active durable registration. | Executable adapter materialization remains the local `_providers` map in `provider_runtime.py`. A separately initialized worker cannot resolve its adapter from durable registration alone; rotation/restart, malformed/inactive/ambiguous, and worker-factory evidence is absent. |
| **P02-02** | **Partially implemented** | `provider_admission.admit_provider_attempt()` rejects blank tenant scope; `PaymentAttempt.tenant_id` is nonblank at the model schema level. | No proof that trusted tenant identity propagates from mounted G2P request through bulk/batch/instruction/task/observation/reconciliation. Legacy, cross-tenant, forged-task, immutable-scope, and mounted negative evidence are incomplete. |
| **P02-03** | **Still open** | Generic admission exists, but no mounted prepayment flow invokes it. | Beneficiary/account validation remains distinct from a durable provider-attempt lifecycle. The required prepayment route → attempt → post-commit task → deterministic adapter → observation/reconciliation/status path and its failure/retry tests are absent. |
| **P02-04** | **Partially implemented** | Attempt token, expiry, generation, and stale-result checks are present in `provider_runtime.py`; adapter I/O remains outside transactions. | Submission intent, bounded recovery, expiry takeover, crash recovery, stale-owner races, and duplicate adapter-call prevention are not fully persisted or evidenced. |
| **P02-05** | **Partially implemented** | The runtime polls an uncertain attempt before submit and retains non-final handling. | It is not proven to be the sole claim-driven recovery entry point for G2P, prepayment, P2G, due attempts, retry, and replay; complete workflow reuse and outcome-conflict tests are absent. |
| **P02-06** | **Still open** | `IdempotencyLedger`, durable reservation/replay service, canonical fingerprint, and focused primitive tests remain present. | There is no complete mounted mutating-route inventory or universal route-level guard that proves exact replay, changed-payload conflict, in-progress behavior, rollback, concurrency, and tenant/method/route scoping before side effects. |
| **P02-07** | **Partially implemented** | `process_bulk_payment_batch` now acquires/fences an initial `BatchLease` owner and generation and rejects a competing live owner. | Renewal, per-child revalidation, pause/threshold/partial-retry policy, settled exclusion, crash/takeover, and stale-owner protection through all child transitions are neither fully enforced nor tested. |
| **P02-08** | **Still open** | Exact provider-observation binding and bounded reconciliation primitives remain intact. | No verified request-identity-to-tenant authorization boundary was added. Header conflict, wrong-tenant, permission, isolation, deterministic pagination, and sensitive-field absence evidence is missing. |
| **P02-09** | **Partially implemented** | `admit_provider_attempt()` validates registration/scope, creates an attempt atomically, and registers post-commit enqueue. | Mounted G2P, prepayment, and P2G routes are not proven to call canonical admission. Complete route → commit → task → sole adapter → observation/reconciliation/status tests, rollback/no-task, duplicate-delivery, task registration/import, and URL coverage are absent. |

## Controls preserved by the implementation

The reviewers confirmed that several important safeguards remain in place. `PaymentLifecycleService.record_provider_result()` remains the finality boundary; verified, exactly bound observations are distinct from unverified or uncertain outcomes. Provider absence records a non-final unavailable result rather than simulating settlement. The runtime retains poll-first behavior for uncertain attempts, keeps provider I/O outside the transaction, and rejects a stale claim before result persistence. Callback activity remains downstream and non-authoritative for financial finality.

Those controls are valuable **foundations**. They do not establish universal mounting, worker-process configuration, route-wide idempotency, live batch leasing, or request-level reconciliation authorization.

## Local validation evidence

The implementation snapshot passed the Payments migration-drift command with **No changes detected** and completed **203 focused Payments tests** successfully. The raw output is retained at `docs/govstack/testing/evidence/item-02-payments-internal-remediation-validation-20260819.log`.

The independent reviewers did not treat that local pass as proof of full closure, because the required mounted-route, worker-restart, race, migration-upgrade, and negative-security coverage for the nine defects was not present.

## Final conclusion

> **Partially aligned / remediation required.** The focused internal pass added fail-closed runtime foundations, but **P02-01 through P02-09 remain open under the agreed Stage 4 closure rule**. Item 02 is not ready to advance, and no external readiness, conformance, certification, staging, official-test, or submission claim is supported.

The minimum next internal engineering work is to finish the non-closed items in the table, starting with a durable worker-resolvable adapter factory, trusted G2P/prepayment/P2G tenant/admission wiring, and one claimed recovery state machine. Those prerequisites must then be joined with universal idempotency, complete batch leasing, and verified reconciliation authorization before an additional independent verification pass can reasonably seek closure.

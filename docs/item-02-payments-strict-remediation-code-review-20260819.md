# Item 02 — Payments strict runtime-enforcement code review

**Date:** 2026-08-19  
**Stage:** 2 of 4 — two fresh focused code reviews  
**Scope:** P02-01 through P02-09 only; internal code and deterministic local validation only.

## Review conclusion

The reviews confirm that all nine defects remain open. The only acceptable closure path is enforced end-to-end execution:

> **mounted mutation → trusted tenant/idempotency admission → transaction commit → registered task → durable generation-fenced claim → process-safe adapter resolution → exact observation/reconciliation → bounded authorised status.**

A helper, optional flag, process-local registry, model, policy primitive, or isolated unit test cannot close any P02 item. Existing finality, fail-closed provider absence, exact observation binding, conservative non-final outcomes, and provider-I/O-outside-transaction controls must remain unchanged.

## Prioritised production changes

| Priority | Defects | Mandatory change set | Primary files |
|---|---|---|---|
| 1 | P02-01, P02-04, P02-05 | Create a process-safe worker adapter resolver; make durable claim/submission intent/token/generation/expiry authoritative; route all due/retry/replay/recovery through one status-first worker command. | `provider_runtime.py`, provider task modules, `govstack_models.py`, new migrations, deterministic provider fixture/tests. |
| 2 | P02-02, P02-09 | Create one trusted-scope canonical admission command and make it mandatory for mounted G2P, prepayment, and P2G financial mutations. Validate scope, registration, amount/currency, idempotency, attempt creation, and post-commit task scheduling atomically. | `provider_admission.py`, `govstack_views.py`, `govstack_services.py`, `govstack_tasks.py`, `govstack_urls.py`, request-scope service. |
| 3 | P02-03 | Replace validation-only prepayment advancement with a named provider-attempt operation through the canonical admission and common worker lifecycle. | prepayment serializer/view/service/task modules, canonical admission, status/reconciliation tests. |
| 4 | P02-06 | Install a mandatory durable HTTP idempotency guard ahead of every mounted mutating Payments route and maintain a complete URL inventory. | idempotency module/service, all mutation views/URLs, ledger migration/tests. |
| 5 | P02-07 | Make batch lease acquisition, renewal, owner/generation revalidation, policy, settled exclusion, takeover, and accounting mandatory in the live bulk worker. | bulk task/service, `BatchLease` models/migrations, batch policy/tests. |
| 6 | P02-08 | Derive reconciliation tenant from verified request identity/trusted resource binding, enforce query-level isolation, permission matrix, deterministic pagination/order, and response allowlist/redaction. | reconciliation/report views, scope service, serializer/tests. |

## Required route and test inventory

The implementation must first enumerate every mounted mutating Payments URL—G2P, bulk, prepayment, P2G, fee, donation, refund, manual, webhook, and any related endpoint—and classify it as provider-executable or non-provider-mutating. Every provider-executable mutation must enter canonical admission. Every mutating route must enter the durable idempotency guard before a side effect.

| Defect | Mandatory evidence |
|---|---|
| P02-01 | Mounted route to independently initialized worker with cleared process-local state; rotation/restart, malformed/inactive/ambiguous registration, factory failure, tenant/operation isolation, migration proof. |
| P02-02 | Mounted missing/forged/conflicting/wrong tenant and forged-task tests; immutable propagation through observation/reconciliation; zero adapter calls on rejection. |
| P02-03 | Mounted prepayment success/rejection/validation-failure/callback-failure/uncertain/rollback/duplicate/retry flow with actual attempt, task, deterministic adapter, observation, reconciliation, and status. |
| P02-04 | Barrier-controlled two-worker database race, one `submit()`, crash before/after I/O, expiry/takeover, stale completion, duplicate delivery, and bounded retry. |
| P02-05 | All G2P/prepayment/P2G/due/retry/replay/reconciliation paths use the same command; known correlation polls before submit; terminal/review/finality states exclude resubmission. |
| P02-06 | Parameterised coverage for every inventory URL: replay, changed payload conflict, concurrent writer, in-progress, invalid key, scope separation, rollback, and exactly-once side effect. |
| P02-07 | Live bulk worker competing claim, renewal failure, expiry/takeover, stale owner, pause/threshold, partial retry, settled exclusion, crash/retry, mixed/empty/replay accounting. |
| P02-08 | Mounted unauthenticated/unauthorised/forged/conflicting/wrong-tenant/cross-object tests, denial before access, stable pages/order, and sensitive-field absence. |
| P02-09 | Each G2P/prepayment/P2G route proves one attempt/task, rollback/no-task, task registration/import, deterministic adapter, observation/reconciliation/status, disabled/malformed configuration, and direct-adapter bypass prevention. |

## Two implementation boundaries

| Agent scope | Owns | Depends on / must expose |
|---|---|---|
| **A — Runtime and lease protocol** | P02-01, P02-04, P02-05, P02-07: durable registration factory/resolver, worker command, submission claim/intent, status-first recovery, batch lease protocol, deterministic adapter/race tests. | A stable worker-command contract that accepts an admitted attempt ID and persists only finality-safe outcomes. No view, idempotency, reconciliation, or trusted-request-scope changes. |
| **B — Admission, routes, security, and prepayment** | P02-02, P02-03, P02-06, P02-08, P02-09: tenant identity boundary, route-wide idempotency, canonical admission, mounted flow integration, real prepayment operation, reconciliation request authorization/status, route inventory tests. | B consumes A’s worker contract. It must not reimplement adapter or claim internals, and must preserve the worker as the sole adapter caller. |

## Prohibited shortcuts

Do not retain an in-memory provider map as authority; trust body/header/task tenant fields; treat validation or callbacks as settlement; enqueue before commit; hold database transactions across provider I/O; use process-local locks; allow direct adapter calls in views/services; use an optional flag for acceptance-critical routing; authorize after broad tenant queries; serialize secrets/tokens/raw provider payloads; claim route-wide behavior from one representative test; or introduce synthetic/default provider success.

## Stage 3 claim rule

Each P02 defect may be marked **claimed closed** only with a named mounted/integration test and observed deterministic behavior that covers its required row above. Tests must run against the integrated code in a clean isolated database and must not contact an external provider. Any defect lacking such proof remains open for Stage 4.

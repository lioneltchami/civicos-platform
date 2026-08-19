# Item 02 — Payments internal runtime-enforcement code review

**Date:** 2026-08-19
**Stage:** 2 of 4 — two fresh focused code reviews
**Scope:** P02-01 through P02-09 in the Stage 1 plan. Internal code and local deterministic validation only; no staging, official suite, provider credential, deployment, testing-site, certification, or submission work.

## Consolidated review conclusion

Both independent reviewers confirm that all nine planned defects are necessary and remain must-fix-now. The controlling implementation principle is:

> **A durable model, helper, pure policy, local test, or optional enqueue does not close a defect. Closure requires an enforced mounted route → canonical admission → committed task → claimed worker → configured adapter → persisted observation/reconciliation → bounded status path.**

The P0 prerequisites are durable provider registration, trusted immutable tenant scope, explicit prepayment attempt semantics, and one canonical admission boundary. Claims, recovery, idempotency, batch leasing, and reconciliation authorization must attach to that boundary rather than create parallel optional paths.

## Required implementation order

| Order | Plan IDs | Required result |
|---:|---|---|
| 1 | P02-01, P02-02, P02-03 | Durable provider registration; trusted, immutable nonblank tenant scope; prepayment as a provider attempt, distinct from beneficiary validation. |
| 2 | P02-09 | One canonical admission command creates a valid attempt and post-commit enqueue for G2P, prepayment, and P2G. |
| 3 | P02-04, P02-05 | Durable attempt claim fencing and one poll-first recovery/orchestration worker used by all retry/replay paths. |
| 4 | P02-06, P02-07 | Route-wide durable idempotency and a mandatory generation-fenced batch lease in the live worker. |
| 5 | P02-08 | Verified request tenant derivation, authorization, query isolation, pagination, and redacted reconciliation reporting. |
| 6 | All | Migration-upgrade, mounted-route, task, deterministic-adapter, race, rollback, negative-security, redaction, and no-network-in-transaction evidence. |

## Plan-by-plan implementation directives

| ID | Precise implementation directive | Compatibility and safety conditions | Mandatory focused proof |
|---|---|---|---|
| P02-01 | Add an additive durable registration model in `apps/payments/govstack_models.py` and a migration after the current Payments migration tip. It must bind tenant, operation, provider identity, active state, and safe configuration/version reference, with an unambiguous active `(tenant, operation)` resolution. Change `apps/payments/provider_runtime.py` to query this durable source in both admission and worker processes. | Do not persist secrets or raw configuration. Do not retain `_providers` as an authoritative fallback. Missing, inactive, malformed, expired, or ambiguous registration must stay fail-closed. Existing process-local registration cannot silently become durable truth. | Migration upgrade; independently initialized resolver instances; tenant/operation isolation; inactive/malformed/duplicate/missing negative cases; registration change while work is queued; adapter selection/redaction. |
| P02-02 | Enforce normalized nonblank immutable tenant scope on every provider-executable `PaymentAttempt`, in the lifecycle service and all creation paths. Trace G2P request, bulk/batch/instruction, task, observation, and reconciliation propagation. | Backfill only known scopes; park unknown legacy work as non-executable/review-only. Do not trust arbitrary body, header, callback, or task fields. A database constraint supplements, but does not replace, service/task validation. | Mounted G2P valid/missing/blank/mismatch scope; cross-tenant bulk; forged task; immutable scope update; migration/backfill; no adapter call on scope failure. |
| P02-03 | Define a named prepayment provider operation and route prepayment through canonical admission. Persist attempt tenant, operation, request/idempotency, canonical payload/fingerprint, amount/currency, registration/version, and state; enqueue after commit only. | Keep `validate_prepayment_async` as beneficiary/account validation. Validation or callback success must not establish settlement. Preserve existing envelope where required but separate it from financial finality. | Mounted prepayment → attempt → task → deterministic adapter → normalized observation/finality; validation failure no side effect; uncertain poll-first; callback failure non-authoritative; duplicate/retry. |
| P02-04 | Add attempt claim owner token, lease expiry, monotonic generation, and bounded counters. Refactor submission into short transaction claim, external call outside transaction, and short token-plus-generation-fenced outcome write. | Persist submission intent before I/O. Terminal/review status must be rechecked before I/O and outcome write. Expiry takeover must poll first, never blind-resubmit. Do not hold DB transaction across network I/O. | Competing claimers; stale writer rejection; expiry/takeover generation; crash after claim/provider call; retry bounds; deterministic adapter call count; transaction boundary assertion. |
| P02-05 | Add one canonical claim-driven worker/service orchestration path and route G2P, prepayment, P2G, due-attempt, retry, replay, and recovery through it. It must poll uncertain/in-flight work first, record every observation through the existing finality/reconciliation boundary, and submit only explicit retryable work. | Retain `PaymentLifecycleService.record_provider_result()` as sole financial finality boundary. Do not use callback results as recovery/finality evidence. Unknown, malformed, conflicting, and unverified outcomes remain non-final. | Common workflow matrix; poll-before-submit; terminal/review exclusion; malformed/conflict; duplicate observations; stale claim; retryable-only resubmission; static/call-site coverage. |
| P02-06 | Create a durable route-level idempotency admission boundary around every required mutation, preferably coordinated with canonical admission. Scope key/fingerprint by trusted tenant, method, normalized route/operation, and payload. | Guard must run before all side effects. Exact completed response replays; changed fingerprint conflicts; in-progress behavior is stable; cross-tenant/method/operation keys do not replay. Preserve existing savepoint duplicate-key correction. | Complete URL inventory; exact replay body/status; changed payload; tenant/method/route separation; concurrent first writer; in-progress; rollback; no side effect before admission. |
| P02-07 | Integrate generation-fenced durable `BatchLease` into the actual bulk worker as the first action and revalidate before every child transition/provider attempt. Implement renewal, expiry/takeover, pause/threshold, partial retry, and settled exclusion. | Batch claims and attempt claims are separate; acquire in documented consistent order to avoid deadlock. Do not hold a transaction during provider I/O. Missing, expired, or lost lease must fail closed. | Competing workers; live holder; expiry/takeover; stale owner/generation; renewal failure; pause/threshold; settled exclusion; child after takeover; no provider call after lease loss. |
| P02-08 | Replace supplied header authority in reconciliation with verified request identity/permission or trusted resource binding. Apply tenant filtering in the query layer, object-level authorization, deterministic pagination/order, and an explicit non-sensitive serializer. | Header may be a consistency check only. Reject absent/malformed/conflicting scope before existence-sensitive query/mutation. Preserve exact observation payment/amount/currency binding and do not expose raw provider payloads, secrets, functional identifiers, or unbounded errors. | Missing/invalid auth; wrong tenant; header conflict; permission matrix; list and mutation isolation; deterministic pagination; bounded page; mismatch/empty; sensitive-field absence. |
| P02-09 | Implement the sole canonical admission command in `apps/payments/govstack_failure_services.py` or a dedicated module. It must validate trusted request proof, P02-06 idempotency, P02-02 tenant, P02-01 registration, operation, payload, amount/currency, and durable attempt creation in one short transaction, then schedule the sole worker by `transaction.on_commit()`. Route G2P, prepayment, and P2G views through it. | Encapsulate adapter use so only the canonical claimed worker can invoke it. No enqueue on rollback. A disabled/invalid registration may create a bounded non-executable/review outcome only if that behavior is deliberately explicit; it must never silently bypass lifecycle or invoke a default adapter. | For each mounted flow: request → commit → task import/registration → claim → deterministic adapter → observation/reconciliation → status. Rollback/no task; disabled/malformed registration; duplicate delivery; URL-to-command coverage; no direct terminal state assignment. |

## Migration and legacy-data rules

The migration sequence must be additive and preserve existing finality data. Existing rows with missing tenant or unknown registration must not receive invented values and must not become provider-executable by default. They should be held in a bounded non-executable/review state until explicitly reconciled. Existing in-flight work should require a fresh durable claim under the new generation-fenced protocol. Existing idempotency rows require defined incomplete-fingerprint handling; no legacy value should expand replay scope across tenant, method, or operation.

## Invariants that Stage 3 must preserve

| Invariant | Required guard |
|---|---|
| Fail-closed provider finality | Only stable, verified, exactly bound observations may establish accepted settlement/rejection through `PaymentLifecycleService.record_provider_result()`. |
| Non-final uncertainty | Timeout, network, unverified, unknown, malformed, and conflicting results remain uncertain/review/retry; recovery polls before resubmission. |
| Callback separation | Callback activity is downstream and cannot establish financial finality. |
| Tenant/observation binding | Tenant is trusted and immutable; observation finality binding remains exact on tenant/payment/amount/currency. |
| Redaction | Store and expose only bounded safe provider identity/version and operational fields; no secrets, raw provider payloads, payee identifiers, or unbounded errors. |
| Transaction discipline | Claims/admission/outcome writes use short transactions; adapter/network I/O occurs outside transactions and outcomes are fenced on re-entry. |

## Stage 3 acceptance rule

Stage 3 must update the Stage 1 plan with a defect-by-defect status and reference actual code/tests. The implementation is eligible for independent Stage 4 verification only when P02-01 through P02-09 are all demonstrably enforced in the mounted local flows—not merely represented by new helpers, migrations, or optional code paths.

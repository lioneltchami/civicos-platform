# Item 03 — Scheduler internal durable-wiring and runtime-evidence code review

**Date:** 2026-08-19
**Stage:** 2 of 4 — two fresh focused code reviews
**Scope:** S03-01 through S03-06 only. Internal source and local deterministic evidence; no external dependency, staging, official-suite, deployment, testing-site, certification, or submission activity.

## Consolidated review conclusion

Both reviewers found all six defects open and necessary. The highest-risk joint defect is the combination of an opt-in durable branch in `dispatch_alert_schedule()` and an unfenced outbox publisher. The implementation must establish one authoritative, migration-safe durable dispatch protocol before generating broad runtime evidence.

> **Critical invariant:** No schedule Boolean, in-memory helper, task flag, or best-effort broker fan-out may decide whether recipient work exists. Durable recipient/outbox rows, generation fencing, and short transaction boundaries remain authoritative.

## Required sequencing

| Order | Work | Reason |
|---:|---|---|
| 1 | Define migration-backed outbox publisher claim/version/lease protocol. | S03-01 materialization produces the rows S03-02 must safely publish. |
| 2 | Make durable materialization authoritative for every supported dispatch; add idempotent legacy reconciliation/backfill. | Default task behavior must not bypass durable work or transport before commit. |
| 3 | Add database-backed authorized operational status and test-only local fake boundary. | Status must represent real publisher states; topology must stay disabled outside tests. |
| 4 | Build exact 37-operation Django request/runtime harness and recursive allowlist-based redaction. | Route inventory must become executable local evidence, not documentation. |
| 5 | Generate deterministic evidence and run combined migration, race, crash, negative authorization, topology, and privacy tests. | S03-06 must consume the canonical trace harness instead of becoming a second, inconsistent system. |

## S03-ID implementation directions

| ID | Required implementation | Migration, concurrency, and security conditions | Mandatory proof |
|---|---|---|---|
| S03-01 | Create one authoritative materialization service, invoked by all supported request/ETA paths. It must lock/reload the schedule, reject superseded/cancelled generation, resolve opaque recipient references from a consistent snapshot, create durable recipient/outbox rows idempotently, commit, then enqueue publication via `on_commit`. Set `dispatched` only as a projection. | Add resumable, idempotent reconciliation for legacy `dispatched` schedules. Differentiate legacy true/no durable rows, partial durable rows, empty recipient sets, cancelled/superseded generation, and unreconcilable records. No recipient resolution, URL lookup, broker, or transport I/O may occur while a transaction is open. | Default settings creates durable rows; legacy true/false reconciliation; rerun/backfill idempotency; rollback/no row; concurrent dispatch; re-arm/cancel race; crash after commit/before publisher; no transport before durable commit; compatibility response. |
| S03-02 | Add distinct publisher claim fields to `SchedulerOutbox`: token, owner, expiry, generation/version, bounded attempt/error state. Implement short-transaction claim, publish outside transaction, token/generation-fenced publish/failure outcome, expiry reclaim, and ambiguity redrive. | Publisher lease is independent from recipient delivery lease. `published_at` is outcome/projection, not full protocol. Re-arm/cancel must fence publisher outcomes. Broker success followed by DB crash remains recoverable and can redrive safely because recipient delivery idempotency converges duplicate task messages. | Concurrent publishers; broker exception; crash before/after enqueue; expiry/redrive; stale publisher success/failure; duplicate delivery convergence; rollback; no lock around `.delay()`; no destination/payload/token leakage. |
| S03-03 | Build a test-only Django client/worker harness over exactly one checked-in 37-operation inventory. It must record safe request class, authorization result, response contract, synthetic correlation/idempotency, durable state/task/outbox delta, and redaction verdict. | Use actual URL/view/service/task boundaries, not direct helper calls. Fail on missing, duplicate, or extra operation IDs. Use schema allowlisting plus recursive redaction/rejection for nested data, URLs, headers, tokens, exceptions, and message content. | Valid and relevant negative request for each ID; malformed, authorization, owner/tenant, state/no-op, correlation, response/error schema, task/outbox, completeness, and redaction assertions. |
| S03-04 | Add a Scheduler-owned, test-only dependency boundary for disabled-by-default `LocalPaymentsFake` and `LocalConsentFake`. Allowlist operations, propagate synthetic correlation/idempotency, classify bounded outcomes, and retain authority labels. | Do not import fakes directly through production paths. Unknown authority/malformed result must fail closed. Fakes cannot mutate Payments settlement or Consent decision/audit records. Keep synthetic non-PII data only. | Mounted request → service/task → fake success, timeout, rejection, malformed, duplicate correlation, retry, disabled fake, unknown authority, redaction, and non-mutation tests. |
| S03-05 | Add Scheduler-owned query/projection service and read-only authorized route over delivery/outbox rows. Derive persisted owner/tenant scope server-side, filter before aggregation, use deterministic bounded pagination/retention, and serialize an explicit non-PII allowlist. | Do not trust caller-supplied owner, tenant, schedule, recipient, or correlation filters as authority. Do not expose recipient refs unless documented opaque/safe; never expose URL, payload, message content, names, contact data, credentials, auth, raw errors, or lease tokens. | Multi-owner/tenant; auth/wrong scope/conflict/object-not-found; pagination/retention; claim/retry/cancel/dead-letter/replay/ack/publication states; stable schema; sensitive-field rejection. |
| S03-06 | Build deterministic local-only evidence solely from S03-03 traces. Include source revision, exact IDs, schema version, stable synthetic correlation IDs, deterministic order, checksum, valid/negative/lifecycle/topology markers, and local-only marker. | Validator must fail on missing/extra/duplicate IDs, nondeterminism, absent required negative coverage, sensitive fields, or inconsistent state/task/outbox deltas. A JSONL/checksum file alone is not evidence. | Complete 37-ID bundle; deterministic rerun; lifecycle/race/unsafe URL/retry/dead-letter/replay/status/topology cases; exact completeness validator; trace redaction; synthetic/local-only assertion. |

## Cross-cutting invariants

Recipient-level delivery state and outbox rows remain the source of truth. All claim, success, failure, cancel, replay, and publication writes must be generation/token/version fenced. Existing HTTPS-only, public-address, no-redirect, bounded-timeout transport controls must remain unchanged. Outbound/broker I/O occurs outside database transactions. Legacy `dispatched` and task IDs can survive only as compatibility projections until replacement/backfill behavior is proven. Traces/status responses must use recursive allowlisting rather than shallow key-name filtering and must not contain PII, recipient destinations, payloads, credentials, authorization data, or lease tokens.

## Stage 3 acceptance rule

The implementation must update the Stage 1 plan with per-ID status and concrete code/test references. None of S03-01 through S03-06 is eligible for closure from a helper, migration, route declaration, fake class, or matrix alone. Stage 4 must independently confirm enforced behavior from final code and this plan only.

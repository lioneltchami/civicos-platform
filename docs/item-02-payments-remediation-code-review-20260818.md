# Item 02 — Payments Production-Wiring Remediation: Focused Code Review

**Date:** 2026-08-18
**Scope:** Internal code and deterministic tests only. Existing safe primitives and external evidence boundaries remain intact.

## Mandatory invariants

> **Only a verified provider/source observation bound to the exact tenant, payment/instruction, provider transaction/event, amount and currency may establish settlement or rejection.** Local accepted state, callback receipt, task success, deterministic adapter result, operator action or retry must never forge finality.

| Priority | Required change | Paths |
|---|---|---|
| P0 | Persist authoritative provider/source observations with immutable IDs, verification metadata, exact bindings, one accepted finality per payment, and conservative `uncertain`/`manual_review` outcomes. Centralize terminal transitions through the lifecycle service. | `govstack_models.py`; core `models.py`; `govstack_failure_services.py`; provider boundary; forward migrations |
| P0 | Wire G2P bulk and prepayment tasks to provider `submit`/`get_status` and persist transaction/request/outcome evidence. Status-before-retry and reconciliation-before-resubmission are mandatory; accepted/settled attempts are excluded. | `govstack_provider.py`; `providers/deterministic.py`; `govstack_tasks.py`; `govstack_failure_services.py` |
| P0 | Implement durable webhook received/verified/applied/rejected/retryable/dead-letter state; mark processed only after a terminal audited decision. | core models; webhook views/tasks; migrations; webhook tests |
| P0 | Gate P2G bill and batch completion exclusively on exact accepted finality child records under transaction locks. Local mark-paid/fallback becomes audited review, never provider settlement. | `govstack_models.py`; `govstack_services.py`; `govstack_tasks.py`; P2G tests |
| P0 | Adopt database-backed canonical HTTP idempotency on every G2P, prepayment and P2G POST entry point: atomic reservation, tenant/method/normalized-path scope, stored response replay, payload conflict and crash recovery. | `govstack_http_idempotency.py`; `govstack_views.py`; `govstack_urls.py`; `urls.py`; migrations; route tests |
| P0 | Add tenant-scoped provider/source observation ingestion, reconciliation, mismatch resolution and status/report APIs. Mismatch stays review, never locally selected success. | `govstack_reconciliation.py`; `govstack_status_views.py`; `govstack_urls.py`; models/migrations/tests |
| P1 | Integrate batch threshold/pause/kick-back policy, leases, typed per-item outcomes and settled-item exclusion from partial retry. | `govstack_batch_policy.py`; `govstack_tasks.py`; `govstack_models.py`; bulk/recovery tests |
| P1 | Make retry, reconciliation, callback redelivery/dead-letter and batch partial retry tasks idempotent, leased, bounded and audited. | `govstack_tasks.py`; core `tasks.py`; failure services; tests |
| P1 | Ensure finality, audit row and outbox/retry obligation commit atomically. Record redacted provider/status/callback/reconciliation/review/conflict/no-op events. | models; signals; tasks; failure services; migrations/tests |
| P1 | Add exact route, tenant, proxy, method/slash, concurrency, forged/mismatched observation, timeout-after-acceptance, batch threshold and recovery regressions. | Item 02 provider, failure lifecycle/recovery, bulk, task and HTTP route test modules |

## Explicitly excluded from this pass

Do not contact a real provider, use credentials, deploy to staging, run an official suite, change external configuration, open a portal, or make a submission. Deterministic provider adapters remain local test doubles and must retain their non-finality evidence label.

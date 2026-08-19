# Item 02 — Payments Failure Remediation: Focused Code Review

**Date:** 2026-08-18
**Scope:** Only gaps in `item-02-payments-failure-remediation-remediation-plan-20260818.md`; preserve the lifecycle/outbox/audit foundations already aligned.

## Repository-closeable implementation plan

| Priority | Change | Exact paths |
|---|---|---|
| P0 | Define an injectable provider contract (`submit`, `get_status`, optional `compensate`) with deterministic outcome taxonomy and no credential persistence. Only provider/source outcomes may produce settlement finality. | `apps/payments/govstack_provider.py`; `apps/payments/providers/deterministic.py`; `apps/payments/govstack_tasks.py`; `apps/payments/govstack_failure_services.py` |
| P0 | Add status-before-retry/reconciliation-before-resubmission logic, duplicate-settlement guards, owned unresolved review/dead-letter transitions, and deterministic timeout/network/duplicate tests. | `apps/payments/govstack_failure_services.py`; `apps/payments/tests/test_govstack_provider_adapter.py`; `apps/payments/tests/test_item02_failure_recovery.py` |
| P0 | Add tenant-scoped reconciliation/status reporting services and exact route contracts, using non-PII canonical statuses and auditable mismatch resolution. | `apps/payments/govstack_reconciliation.py`; `apps/payments/govstack_status_views.py`; `apps/payments/govstack_urls.py`; tests |
| P0 | Apply canonical HTTP idempotency to all relevant G2P/prepayment/P2G entry points; prove replay, conflict, concurrent first-writer, slash/method and tenant behavior. | `apps/payments/govstack_http_idempotency.py`; `apps/payments/govstack_views.py`; relevant route modules/tests |
| P1 | Add batch threshold/pause/kick-back policy, typed per-item outcomes, lease/lock selection and settled-item exclusion from partial resubmission. | `apps/payments/govstack_batch_policy.py`; `apps/payments/govstack_tasks.py`; bulk/recovery tests |
| P1 | Extend redacted provider/status/compensation/operator audit events and create owned review/replay/defer operational controls, queue age signals and runbook. | `apps/payments/govstack_failure_services.py`; `apps/payments/govstack_operations.py`; `docs/payments/govstack-failure-operations.md` |
| P1 | Add exact route/proxy/tenant/malformed-input regression tests plus local evidence manifest validator. | `apps/payments/tests/test_item02_http_routes.py`; `apps/payments/tests/test_item02_route_proxy_tenant.py`; `scripts/validate_item02_payments_evidence.py` |

## External blockers

Real provider semantics, non-production provider credentials, staging candidate deployment, full official harness execution, proxy observability, tenant-equivalent deployment evidence, and any external submission or production replay/compensation approval remain outside repository-only closure. Local deterministic adapters/tests must remain labelled as such and cannot be represented as provider, staging, official, certified, or submitted evidence.

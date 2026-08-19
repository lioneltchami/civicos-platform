# Item 02 — Payments Production-Wiring Remediation Plan

**Date:** 2026-08-18
**Scope:** Internal repository engineering only. This plan excludes staging, official-suite, portal, credentials, and external submission work.

## Do not touch

| Existing control | Status |
|---|---|
| Provider-neutral deterministic primitives, lifecycle vocabulary, evidence validator and conservative status model | **Do not touch** |
| Callback outbox safety, bounded local retry/review triage and no-blind-retry boundary | **Do not weaken** |
| Explicit external-evidence and non-certification boundaries | **Do not change** |

## Remaining internal gaps

| Classification | Exact gap | Paths | Concrete closure work |
|---|---|---|---|
| **Must-wire-now** | G2P bulk/prepayment tasks do not submit to a provider or persist provider finality. | `govstack_provider.py`; `providers/deterministic.py`; `govstack_tasks.py`; `govstack_failure_services.py` | Define/invoke `submit`, `get_status`, and safe optional compensation; persist provider request/transaction/outcome references; make only provider/source outcomes authoritative for settlement/rejection. |
| **Must-wire-now** | Timeout/uncertain retry is not status-before-retry and reconciliation-before-resubmission. | `govstack_tasks.py`; `govstack_failure_services.py`; provider tests | Poll status before any retry; reconcile before resubmission; exclude accepted/settled attempts; route unresolved work to owned review/dead-letter. |
| **Must-wire-now** | Reconciliation and tenant-scoped status/mismatch reporting are not connected to actual internal provider/source feeds. | `govstack_reconciliation.py`; `govstack_status_views.py`; `govstack_urls.py` | Persist comparisons, mismatch resolution and redacted audit events; expose tenant-scoped status/reconciliation routes with exact service/route tests. |
| **Must-wire-now** | Canonical idempotency is not adopted by every G2P, prepayment and P2G HTTP entry point. | `govstack_http_idempotency.py`; `govstack_views.py`; `govstack_urls.py`; `urls.py` | Return stored canonical replay results; reject changed-payload conflicts; enforce tenant/method/slash/concurrency semantics at all production entry points. |
| **Must-wire-now** | Batch failure threshold, pause/kick-back and settled-item-safe partial retry are absent. | `govstack_batch_policy.py`; `govstack_tasks.py`; bulk/recovery tests | Add typed per-item results, transactional lease/lock selection, configurable threshold policy and settled-item exclusion. |
| **Must-wire-now** | P2G local mark-paid/notification transitions can be mistaken for settlement. | `govstack_services.py`; `govstack_views.py`; `govstack_status_views.py`; `govstack_urls.py` | Gate automatic completion on provider/source finality; route manual fallback through explicit audited review/reconciliation states. |
| **Must-wire-now** | Provider/status/callback/reconciliation/review audit mapping and route-level regression evidence are incomplete. | `govstack_failure_services.py`; provider/recovery/route test modules | Emit typed redacted audit events across every internal outcome; test exact route, tenant, error taxonomy, callback, reconciliation, replay/conflict and recovery behavior. |
| **Deferred** | Operator SLA/escalation and expanded manual replay console. | `govstack_operations.py`; `docs/payments/` | Follow after the internal production wiring above; do not block this focused wiring pass. |

## Acceptance checklist

| Internal acceptance item | Current status |
|---|---|
| Existing primitives retained and authoritative boundaries preserved | **Aligned — do not touch** |
| Provider-backed settlement/finality wired into G2P and prepayment flows | **Open** |
| Status-before-retry and reconciliation-before-resubmission | **Open** |
| Tenant-scoped reconciliation/status/mismatch lifecycle | **Open** |
| Canonical HTTP idempotency across G2P/prepayment/P2G | **Open** |
| Batch pause/kick-back and safe partial retry | **Open** |
| P2G provider/source finality gate | **Open** |
| Failure/audit and full internal route regression coverage | **Open** |

> A deterministic provider adapter is an internal integration seam, not a claim of real-provider, staging, official-suite, certification or submission evidence.

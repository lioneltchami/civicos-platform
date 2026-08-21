# Item 02 — RB-02.4 Scope Confirmation

**Date:** 2026-08-20  
**Stage:** Scope confirmation before the RB-02.4 change-list review

> **Scope: confirmed with one minimal deterministic test seam.** RB-02.4 adds one real-database two-live-worker proof to the existing focused module. It does not alter the closed RB-02.1 lease service, RB-02.2 materializer, or RB-02.3 policy/decision behavior.

## Included work

| Area | RB-02.4 boundary |
|---|---|
| Required method | Add exactly `test_two_live_workers_cross_expiry_and_stale_finalization` to `apps/payments/tests/test_item02_rb02_batch_lease_live.py`. |
| Live-worker interleaving | Worker A must enter the real `process_bulk_payment_batch()` path and acquire the lease. A narrow test-only timing seam pauses it immediately before its first child/decision/projection/audit/callback side effect. The test expires A’s durable lease, runs Worker B through the real task to acquire the next generation and complete, then resumes A. |
| Required proof | The test must prove B owns the sole final decision and lease generation. After A resumes, it must produce zero additional decision, decision audit, callback delivery, child/instruction mutation, or batch projection writes. |
| Permitted implementation adjustment | The task may expose a narrowly scoped in-process test barrier/hook at the pause point. The hook is inactive in normal execution and cannot add a production persistence path, policy behavior, provider-runtime behavior, or business outcome. Existing owner-token/generation assertions remain the actual fence. |
| Regression gate | The complete focused module must pass: 3 RB-02.1 methods, 2 RB-02.2 methods, 6 RB-02.3 methods, and this one RB-02.4 method, for **12 tests total**. |

## Explicit exclusions

RB-02.4 does not add a new persistence model, rework prior increments, add additional race tests, redesign callback/provider runtime behavior, touch RB-03/RB-04, staging, official-suite work, submission, or Stage 4. The timing seam is solely a deterministic way to exercise the already live lease fence across expiry and stale finalization.

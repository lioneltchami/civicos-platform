# CivicOS PR #1 — Payments Test Remediation

**Date:** 2026-08-24
**Scope:** PR #1 post-public CI remediation, functional Payments Increment 2 only
**Status:** Local verification complete; pending focused commit, normal push, and CI confirmation

## Remediated Test Contracts

The increment restores the duplicate bulk-payment HTTP contract while preserving canonical admission. A repeated canonical command now returns the established G2P error envelope only when the referenced `BatchID` is already persisted; a replay without a persisted batch remains on the existing replay path.

The production-mode registered-caller test fixture now supplies the currently required explicit platform-tenant authorization. The task test suite was also aligned with the existing RB-02 contracts: verified and reconciled provider finality for terminal decisions, durable decision-audit records, queued callback deliveries, and non-final ID-mapper outcomes. No production finality, authorization, policy, lease, audit, callback-delivery, CI, workflow, or dependency behavior was changed.

## Local Evidence

| Verification | Result |
|---|---|
| Focused duplicate-batch regression | Passed |
| Complete bulk-payment module | 87 tests passed |
| Complete GovStack task module | 27 tests passed |
| CI-equivalent parallel Django suite | 6,457 tests passed; 18 skipped |
| `ruff check .` with Ruff 0.8.4 | Passed |
| `ruff format --check .` with Ruff 0.8.4 | Passed |
| `git diff --check` | Passed |

The full-suite execution produced ephemeral test-only credential values in process output. They were not copied into repository evidence, documentation, commits, or user-facing materials.

## Boundaries

This record does not authorize merge, deployment, staging, production operations, secret access, release, submission, provider or payment-rail activation, or any GovStack certification or conformance claim. Security dependency remediation remains a separate later increment.

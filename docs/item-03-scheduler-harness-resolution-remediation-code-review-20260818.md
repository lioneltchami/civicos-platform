# Item 03 — Scheduler Harness Resolution: Focused Code Review

**Date:** 2026-08-18
**Scope:** Only open items in `item-03-scheduler-harness-resolution-remediation-plan-20260818.md`. Preserve existing route inventory, local-only safety, documentation boundary, and focused regression foundations.

## Repository-closeable changes

| Priority | Change | Exact paths |
|---|---|---|
| P0 | Add durable recipient-scoped delivery/attempt records, unique correlation/idempotency keys, terminal-state fencing and indexes. | `apps/appointments/models.py`; `apps/appointments/migrations/` |
| P0 | Refactor alert dispatch into transactional claim → attempt → bounded retry/dead-letter → terminal/cancelled/acknowledged flow; no PII logging and no false delivered state. | `apps/appointments/tasks.py`; `apps/appointments/services/govstack_alert_schedule.py`; `apps/appointments/services/govstack_message.py` |
| P0 | Add fault-injection tests for timeout, 4xx/5xx, duplicate, partial recipient failure, cancellation race, worker loss, late acknowledgement and replay. | `apps/appointments/tests/test_govstack_alert_schedule.py`; `apps/appointments/tests/test_govstack_message.py` |
| P0 | Bind validated runtime configuration to web/worker execution and provide pinned, loopback-only local topology with worker, broker, recipient fake and health checks. | `examples/civicos-scheduler/config.schema.json`; `docker-compose.yml`; `test_entrypoint.sh`; `scripts/validate_scheduler_harness.py` |
| P0 | Execute all 37 matrix rows over the local HTTP boundary, write redacted JSONL result rows, manifest and checksums. | `examples/civicos-scheduler/operation-matrix.json`; `scripts/validate_scheduler_harness.py`; focused harness tests |
| P1 | Add disabled-by-default local Payments/Consent fakes with authority-preserving correlation/idempotency and deterministic dependency failures. | `apps/appointments/services/`; `examples/civicos-scheduler/`; corresponding tests |
| P1 | Add non-PII delivery/job status, queue/latency/retry/dead-letter metrics, ownership/escalation/retention and authorized operational query paths. | `apps/appointments/services/govstack_log.py`; models/migrations; tests |
| P1 | Expand event/entity/resource/subscriber/message route-level lifecycle contract tests for auth, ownership, duplicate, malformed, failure and cleanup. | existing `apps/appointments/tests/test_govstack_*.py` modules |
| P1 | Document reproducible local execution, artifact integrity/redaction, triage and exact local/staging/official evidence labels. | Scheduler README and GovStack testing documentation |

## External blockers

A maintainer-supported pinned official Scheduler harness dependency, authorised staging topology, recipient integration semantics, current official-suite output, and any conformance/submission decision are **not locally closeable**. The existing blocked official manifest must remain historical evidence and must not be reclassified as a pass.

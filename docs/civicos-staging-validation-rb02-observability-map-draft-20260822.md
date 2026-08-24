# Payments RB-02 Observability and Stop Map — Draft Pending Human Approval

## Status and source boundary

This is a non-operational observability map for the immutable RB-02 candidate. `docs/DEPLOY_NOTES.md` documents production health endpoints, JSON logs, and Sentry PII filtering. These are **production-only patterns**, not evidence that an authorised target, signal, dashboard, alert route, or stop person exists for RB-02.

| Signal | Code/documentation pattern | Future RB-02 interpretation | Required evidence | Current state |
|---|---|---|---|---|
| Health | `/health/live/` and `/health/ready/` are documented production health patterns | Compare approved target availability/dependency status before/after a future bounded action | Redacted response, target identity, UTC timestamp and approved interpretation | **BLOCKED** — target/access/threshold unknown. |
| Application logs | JSON-formatted application logs are documented | Assess RB-02 task/lease/finality/policy exceptions and correlation fields without raw payment data | Query definition, time range, aggregate/redacted output, provenance | **BLOCKED** — log sink/access/schema/threshold unknown. |
| Error monitoring | Sentry PII filtering is documented | Detect candidate-linked exception changes only after filtering is independently verified | Redacted event/group reference, UTC time, filtering confirmation | **BLOCKED** — project/access/routing/threshold unknown. |
| RB-02 integrity | Live lease/finality/policy/decision test surface is retained in the candidate | Treat any failure of approved acceptance criteria as a stop condition | Approved test/check reference and non-sensitive result | **BLOCKED** — authorised target/data/check procedure unknown. |
| Candidate identity | Canonical/manifest digests and artifact path are retained | Stop on identity mismatch | Recomputed digest record before a future decision | **DRAFTED** — re-hash required later. |

No named stop authority, backup, alert destination, acknowledgement route, numeric threshold, dashboard, on-call rotation, or target signal is known. Each remains **UNAVAILABLE / BLOCKED**. Until separately evidenced, no alert is presumed delivered and no stop control is presumed effective.

## Stop conditions

Any candidate mismatch, unavailable required signal, unredactable sensitive content, loss of lease/fencing/finality/policy/idempotency control, unapproved provider/live-money implication, or scope expansion to SCH-02.2/File Management must keep the activity blocked and require a fresh human decision.

# Controlled Staging Validation — Defer Resume Conditions

## Status and boundary

This is a documentation-only reconsideration path following the human-owner **Defer** decision. It creates **no schedule to run staging** and no execution authority. The attached candidate remains deferred, attached, and not rejected.

## Required sequence before reconsideration

| Gate | Required record | Pass condition | If not passed |
|---|---|---|---|
| Candidate continuity | Same immutable candidate identity, exact digests/path, scope, owner, and exclusions. | No candidate substitution or scope drift. | Retain Defer. |
| Operational-precondition evidence pack | Evidence for environment, operators/access, approved secret mechanism without values, data/retention, bounded window, rollback/recovery, observability, stop authority, and evidence/redaction/failure rules. | Every applicable condition is current, attributable, internally consistent, and explicitly evidenced. | Retain Defer or Reject. |
| Exclusion/provenance integrity | Explicit SCH-02.2/File Management exclusions and `49e…` provenance-only treatment. | No excluded scope appears cleared, in-scope, or executable; `49e…` is not used as readiness evidence. | Correct pack; retain Defer. |
| Independent review | Review by a person independent from evidence preparation and operational ownership, with documented gate-by-gate findings. | No material unexplained gap, stale evidence, contradiction, or scope mismatch. | Retain Defer or Reject. |
| Fresh human-owner decision | New explicit **Authorize / Defer / Reject** decision referencing the reviewed evidence pack. | Only a separately recorded Authorize may replace this Defer for a specifically bounded future activity. | No resumption. |

The evidence pack must preserve candidate identity and contain the exact intended scope, proposed environment, accountable owner, timestamps, methods, results, residual risks, assumptions, unresolved findings, compensating controls, rollback/recovery readiness, evidence index, retention/redaction treatment, and source pointers. No secret values may appear.

## Guardrails

The evidence pack, independent review, and this brief are not staging authorisation. They do not permit deployment, secret access, data processing, official tests, release, submission, production use, external distribution, or operational side effects. Any later scope, environment, configuration, risk, or prerequisite change invalidates the pack and requires a new evidence pack, independent review, and fresh human-owner decision.

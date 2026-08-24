# CivicOS Product / Production Path Brief

> **Status: non-authorising planning material.** This brief does not approve deployment, release, staging, secret handling, payment/provider activation, GovStack conformance, certification, official-suite execution, or submission.

## Purpose and separation

This brief concerns CivicOS as an application platform and is deliberately separate from the parked GovStack/staging/submission campaign. A later product deployment decision must not be represented as GovStack Building Block conformance, certification, official validation, official-suite completion, or submission evidence.

The existing `docs/DEPLOY_NOTES.md` records useful non-secret deployment patterns. Those patterns must be freshly validated by the accountable owner; they are not proof of current infrastructure, control effectiveness, release approval, or successful test execution.

## Recommended cautious baseline

A future human decision may consider a narrow application deployment in which a public website and public API are separately bounded, using the documented candidate pattern of a website deployment and an API host behind a controlled network ingress. The documented container pattern—reverse proxy to application web process, private PostgreSQL/Redis, worker/beat, and one-shot migration service—may be reviewed as a candidate baseline only.

The safe default is that **all money, payment, external-provider, settlement, payout, webhook, and provider-triggered rails remain disabled and unreachable**. The Payments RB-02 component artifact is not a release artifact, production evidence, readiness claim, or authorization. Its documented local identity does not alter this product-path boundary.

## Health, rollback, and observability baseline

The documented patterns identify `/health/live/` for process liveness and `/health/ready/` for dependency readiness. Any later approved target must freshly demonstrate the relevant checks; their documentation is not a passing result. The corresponding review should confirm private data-store exposure, structured logging, appropriate log retention/aggregation, PII-filtered error monitoring, staff MFA, and named alert/on-call ownership.

A product decision should require a backup and demonstrated restore test, an explicit migration compatibility plan, and a named authority able to stop traffic or workers. A prior imA product decision should require a backup and demonstrated restore test, an explicit migration compatibility plan, and a named authority able to stop traffic or workers. A prior imA product decision should require a backup and demonstrated restore test, an explicit migration compatibility plan, and a named authority able to stop traffic or work now |
|---|---|---|
| Product boundary | Written statement that application deployment is separate from GovStack BB claims. | Pending fresh decision |
| Target and ownership | Exact approved target, domain/network boundary, service owner, operator, escalation, and stop authority. | Pending fresh validation |
| Non-secret configuration method | Approved injection, rotation, revocation, and access procedure without revealing values. | Pending review |
| Disabled rails | Evidence that money/provider/webhook rails remain disabled unless separately approved. | Required; no activation approved |
| Data and access controls | Data classification, retention/deletion, private data-store access, MFA, least privilege, and incident handling. | Pending approval |
| Migration and recovery | Compatibility plan, backup evidence, restore test, failure procedure, and decision thresholds. | Required before a deploy decision |
| Health and observability | Fresh liveness/readiness, logs, alert routes, error monitoring, PII filtering, and on-call evidence. | Pending fresh validation |
| Scope smoke tests | Dated outcomes for applicable health, schemas/docs, admin MFA, CMS/static assets, media, email, and periodic-task checks. | Templates only; no pass claim |
| Final human gate | Explicit scope, target, enabled capabilities, evidence reviewed, duration, risks, and approve/reject decision. | No approval granted |

## Hard non-claims

- This brief does not authorise deployment, release, staging, official tests, secret access, payment/provider activation, or any live financial action.
- It does not change the GovStack campaign closeout, the external-claim prohibition, or the deferred staging state.
- It does not treat the RB-02 artifact as a production release or as evidence of provider, regulatory, or conformance approval.
- It does not assert that any deployment-note pattern, health check, rollback procedure, backup, restore, log, alert, or smoke test is presently effective or has passed.
- Any actual product deployment requires a **new, explicit, separately recorded human action** outside this documentation cycle.

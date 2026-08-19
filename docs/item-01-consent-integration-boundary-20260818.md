# Item 01 — Identity and Information Mediator Boundary

External Identity and Information Mediator endpoints and credentials were not supplied. CivicOS therefore uses an explicit **fail-closed bounded stub**, not a fabricated integration.

| Concern | Contract |
|---|---|
| Subject mapping | Caller supplies a non-empty opaque `subject_id`; no local identity is promoted to external proof |
| Configuration ownership | `CIVICOS_CONSENT_IDENTITY_ENDPOINT` and `CIVICOS_CONSENT_IM_ENDPOINT` are deployment-owned; both are required |
| Message shape | JSON object with `event_type`, `subject_id`, `resource_id`, `idempotency_key`, and `payload` |
| Retry / idempotency | No network retry in stub; caller owns retry and must preserve idempotency key |
| Replay protection | Empty or duplicate idempotency keys are rejected |
| Failure mode | `ConsentIntegrationUnavailable` is raised before any outbound request when configuration is absent |

This boundary is tested locally only. It does not prove external interoperability.

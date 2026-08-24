# Item 01 — Consent Audit Event Inventory

| Mutation | Event | Required provenance | Current status |
|---|---|---|---|
| Policy create/update/delete | `consent.policy.mutated` | actor, policy, revision, before/after, outcome | partial |
| Agreement/data-agreement mutation | `consent.agreement.mutated` | actor, subject, agreement, revision, outcome | partial |
| Record grant/amend/withdraw/remove | `consent.record.mutated` | actor, subject, record, revision, rationale, signature result | partial |
| Signature/verification | `consent.signature.verified` | actor, record, signature, verification result | partial |
| Webhook delivery | `consent.webhook.delivery` | webhook, event, attempt, outcome, idempotency key | partial |
| Audit retrieval | `consent.audit.read` | actor, filters, sort, result count, outcome | partial |

Existing hash-chain and Consent history infrastructure is retained. Complete mutation emission and official Audit list/detail parity remain evidence gates.


## Evidence guard

Every inventory row remains `partial` until mutation provenance, lifecycle transition, revision/signature linkage, and retrieval behavior are demonstrated by bounded executable evidence. Local model/history tests establish only that the local structures exist; they do not promote audit parity or official equivalence. Any export or package index must carry an evidence identity and remain `NOT_READY` for staging, official, submitted, or certified claims.

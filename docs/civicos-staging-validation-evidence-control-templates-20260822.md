# Payments RB-02 Evidence, Redaction, and Failure-Control Templates

## Status

**DRAFTED / PENDING HUMAN APPROVAL.** These templates create no remote evidence sink, retention rule, custodian, access grant, or execution authority. Those fields remain **UNAVAILABLE / BLOCKED** until supplied by the appropriate human owner.

## Evidence index

| Evidence ID | Control/check | Candidate digest | Environment | UTC time range | Source/type | Custodian | Redaction state | Integrity reference | Disposition |
|---|---|---|---|---|---|---|---|---|---|
| `RB02-E-###` | `[fill]` | `3fb5a432…` | `[BLOCKED if unknown]` | `[fill]` | `[health/log/error/test/decision]` | `[BLOCKED if unknown]` | `pending/reviewed/rejected` | `[approved hash/reference]` | `open/accepted/rejected` |

## Redaction checklist

- [ ] No secret values, tokens, keys, connection strings, cookies, authorization headers, or credentials.
- [ ] No card, bank, customer, payee, beneficiary, or other unnecessary payment/personal identifiers.
- [ ] Request/response bodies, URLs, query strings, stack traces, screenshots, and logs are limited to approved non-sensitive fields.
- [ ] Any error-monitoring extract demonstrates the documented PII-filtering posture before export.
- [ ] UTC time range, source provenance, redactor, reviewer, access classification, and retention disposition are recorded.
- [ ] Redacted copy is linked to an approved source reference without copying sensitive source content.

## Failure register

| Failure ID | UTC detection | Phase | Signal/symptom | Expected control | Immediate stop decision | Owner/route | Evidence IDs | Status | Follow-up |
|---|---|---|---|---|---|---|---|---|---|
| `RB02-F-###` | `[fill]` | `baseline/review/rollback/verify` | `[non-sensitive description]` | `[fill]` | `[BLOCKED if authority unknown]` | `[BLOCKED if unknown]` | `[RB02-E-###]` | `open/blocked/closed` | `[fill]` |

Every missing gate, contradictory result, redaction defect, candidate mismatch, or integrity anomaly must receive a register entry. A health response alone never closes a failure; closure requires approved recovery checks and independent evidence review.

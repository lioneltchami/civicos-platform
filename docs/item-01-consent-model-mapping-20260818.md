# Item 01 — Consent Resource and Lifecycle Mapping

This evidence-backed mapping records the local boundary without claiming unverified equivalence.

| Official concept | CivicOS evidence | Status / gap |
|---|---|---|
| Policy / Data Policy | `ConsentPolicy` in `apps/consent/models.py` and serializers | Partial; field-level contract tests remain required |
| Purpose / category | `ConsentCategory` purpose labels and attributes | Constrained adapter; not assumed to be a first-class official Purpose |
| Agreement / Data Agreement | category/policy and GovStack views | Partial; official schema/cardinality mapping remains open |
| Consent Record | `ConsentRecord`, current-record constraint, service actions | Partial; published response/error parity remains open |
| Revision / history | `ConsentRevision` and migrations 0011, 0019 | Present locally; full official linkage evidence remains open |
| Signature / verification | signature models/services and GovStack views | Partial; external cryptographic interoperability remains unproven |
| Withdrawal / revocation | local grant/withdraw services | Present locally; official transition matrix remains open |
| Webhook provenance | webhook models/tasks/migrations | Partial; delivery outcome contract remains open |

## Lifecycle invariant test targets

Identifiers, cardinality, revision linkage, immutable serialized hashes, valid grant/withdraw transitions, signature verification, and Agreement–Purpose/Data Agreement relationships require focused contract tests before any row is green.

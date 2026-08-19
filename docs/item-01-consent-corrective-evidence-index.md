# Item 01 Consent Corrective Evidence Index

This index records only locally verifiable evidence. It does not assert external Identity or Information Mediator connectivity, staging execution, testing-site submission, certification, or official wire conformance.

| Claim | Local evidence | Test link | SHA-256 | Limitation / owner |
|---|---|---|---|---|
| The 42 published-v23Q4 operations are represented with official paths, methods, status codes, security, parameters, and version disposition. | `docs/item-01-consent-v23q4-operation-matrix.json` and pinned OpenAPI | `tests/govstack/test_item01_consent_matrix.py` | See `docs/item-01-consent-claim-hashes.json` | Schema-level wire validation remains an external gate owned by release/submission owner. |
| CivicOS route/view surfaces are recorded where current code contains the route. | `apps/consent/govstack_urls.py`, `apps/consent/govstack_views.py` | matrix validator | See claim hashes | Route presence is not interoperability proof. |
| Consent model lifecycle, revision/hash linkage, signature boundary, and audit inventory remain executable local evidence. | `apps/consent/models.py`, `apps/consent/services.py`, existing consent tests | `tests/govstack/test_item01_consent_matrix.py` plus `apps/consent/tests/` | See claim hashes | External identity, mediator, and official-suite gates remain open. |
| Authority pin is machine-verifiable and main delta is explicitly tracked. | `docs/govstack/authority-manifest.json`, `docs/item-01-consent-authority-20260818.md` | matrix validator | See claim hashes | Official source is preserved read-only; current-main review is descriptive only. |

## External gate ownership

Release/submission owner must obtain authorised testing-site access, configure real Identity and Information Mediator dependencies, run the official v23Q4 suite against the deployed candidate, preserve the result report, and make the human-approved submission/certification decision. This patch intentionally does not claim any of those outcomes.

# Item 01 — Consent internal operation-parity remediation plan

**Date:** 2026-08-19
**Stage:** 1 of 4 — two shared-context gap analyses
**Scope:** Internal mounted-route parity only. No staging, official suite, Identity/Information Mediator integration, credentials, deployment, certification, or submission work.

## Selected supported surface

The two reviewers considered a read-only current-record operation and a grant/signature mutation. This plan selects the **narrower, lower-risk read-only surface**:

> **`serviceIndividualConsentRecordRead` — `GET /service/individual/record/data-agreement/{dataAgreementId}/`**

The selection has an existing route, authenticated individual scope, category/data-agreement relationship lookup, `is_current=True` invariant, dedicated response serializer, and bounded local not-found behavior. The grant/signature path remains deferred because revision, signature verification, state transition, audit provenance, duplicate, and failure semantics would broaden this pass beyond a narrow operation-parity increment.

The pinned official authority remains [GovStackWorkingGroup Consent Building Block][1]. This is local/auditable evidence only and does not change the distinction from observable runtime evidence described by the official requirements model.[2]

## In-scope parity gaps

| ID | Internal gap | Current source | Required local change and proof | Status |
|---|---|---|---|---|
| C01-01 | Mounted GET contract has no complete request/serializer/status/auth/error regression coverage. | `apps/consent/govstack_views.py::ServiceIndividualDataAgreementConsentRecordView.get`; `apps/consent/serializers.py::ConsentRecordGovStackSerializer`; Consent URL registration. | Add mounted local tests for success envelope, authenticated citizen scope, malformed identifier, unknown category, no current record, cross-citizen isolation, and stable error/status behavior. | Must fix now |
| C01-02 | Current-record relationship and history selection are not explicitly proven at operation level. | `ConsentRecord.objects.get(citizen=request.user, category=category, is_current=True)` and Consent record models. | Seed historical and current rows and prove exactly the current row is returned for the caller/category, another citizen cannot be returned, and no revision history leaks. | Must fix now |
| C01-03 | Response field/provenance behavior is not contract-tested. | `ConsentRecordGovStackSerializer`, record/revision/signature fields. | Assert only actually exposed identifiers, category/data-agreement relation, current/lifecycle/withdrawal representation, revision/signature metadata when present, timestamps, and stored provenance fields. Do not invent absent official fields. | Must fix now |
| C01-04 | Read-only operation has no explicit non-mutation/fail-closed boundary regression. | Consent services, audit models, webhook/task paths, `integration_boundary.py`. | Prove GET creates no record/revision/signature/audit/webhook change and makes no external call. Preserve missing configuration failure and configured `NotImplementedError`; no fallback, retry, synthetic success, or network transport. | Must fix now |

## Explicitly deferred operations

All other 41 operations remain **partial**. In particular, do not implement or promote grant, signature create/update, withdrawal/revocation, revision mutation, verification views, audit APIs, config/data-agreement/policy CRUD, webhook delivery, Identity subject mapping, or Information Mediator interaction. The operation matrix must retain all 42 dispositions as `partial`; this pass may add local-evidence notes only.

## Do-not-touch controls

Keep the Identity/Information Mediator boundary fail-closed. Preserve candidate `NOT_READY` guards, operation-matrix all-partial tests, existing authentication/permission behavior, audit/hash foundations, signature algorithms, migration history, and Item 02–07 work. No external endpoint, secret, queueing, retry, credential, or subject-mapping behavior may be introduced.

## Stage 3 acceptance rule

The selected GET operation is locally complete only if C01-01 through C01-04 have mounted regression evidence and the all-partial guard remains unchanged. It is not eligible for `match`, readiness, conformance, certification, staging, or submission interpretation. Stage 4 must independently confirm the route coverage and fail-closed boundary from final code and this plan only.

## References

[1]: [GovStackWorkingGroup Consent Building Block](https://github.com/GovStackWorkingGroup/bb-consent)

[2]: [GovStack requirements model](https://specs.govstack.global/architecture/5-specification-framework/5.3-requirements-model)

## Stage 3 implementation status — 2026-08-19

The selected read-only `serviceIndividualConsentRecordRead` pass added mounted route regression tests without changing production route, serializer, authentication, operation-matrix disposition, or external integration behavior.

| ID | Status | Evidence |
|---|---|---|
| C01-01 | **Implemented, pending independent verification** | New mounted regression coverage exercises authenticated success, no authentication, malformed identifier, unknown category, and no-current-record cases on the selected data-agreement route. |
| C01-02 | **Implemented, pending independent verification** | Fixtures prove citizen/category scope and current-row selection over an explicit historical row; response contains no history envelope. |
| C01-03 | **Implemented, pending independent verification** | Tests assert the exact existing eight-field `ConsentRecordGovStackSerializer` allowlist and scalar/null behavior without exposing additional model or audit fields. |
| C01-04 | **Implemented, pending independent verification** | Repeated GET snapshots Consent record/revision/signature/audit state and patches the external publish boundary to fail if invoked; no production external-boundary behavior changed. |

### Local validation

The isolated snapshot reported **No changes detected in app `consent`** and passed **177 focused Consent tests**. Raw output is retained in `docs/govstack/testing/evidence/item-01-consent-internal-parity-validation-20260819.log`.

> This evidence supports only the selected local read operation. All 42 operation rows remain **partial**; no unsupported operation was promoted and the Identity/Information Mediator boundary remains fail-closed.

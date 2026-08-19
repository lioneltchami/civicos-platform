# Item 01 — Consent internal operation-parity code review

**Date:** 2026-08-19
**Stage:** 2 of 4 — two fresh focused code reviews
**Selected operation:** `serviceIndividualConsentRecordRead` — authenticated current-record GET by `dataAgreementId`.

## Consolidated review conclusion

The selected read endpoint is an appropriate narrow local scope. Both reviews recommend a **test-first** pass: existing implementation already contains the core local predicates, route, serializer envelope, and fail-closed non-integration behavior. Production code must change only if mounted tests expose a concrete defect. The other 41 operations remain partial and deferred.

| ID | Required direction | File and proof |
|---|---|---|
| C01-01 | Preserve the mounted data-agreement GET route, authentication, integer parser, category lookup, 200 envelope, bounded 400/404 behavior, and trailing slash. Add exact mounted route/status tests. | `apps/consent/govstack_urls.py`; `ServiceIndividualDataAgreementConsentRecordView.get`; focused API tests. |
| C01-02 | Prove authenticated citizen + category + `is_current=True` selection with historical/current, cross-citizen, and cross-category fixtures. Do not allow a client-supplied individual override. | `apps/consent/govstack_views.py`; `ConsentRecord` tests. |
| C01-03 | Contract-test the current `ConsentRecordGovStackSerializer` allowlist only: `id`, `dataAgreement`, `dataAgreementRevision`, `dataAgreementRevisionHash`, `individual`, `optIn`, `state`, and `signature`. Do not invent model fields or expose audit/revision snapshots. | `apps/consent/serializers.py::ConsentRecordGovStackSerializer`. |
| C01-04 | Prove repeated GET causes no record/revision/signature/audit/webhook mutation and no external-boundary call. Preserve missing-configuration failure and configured `NotImplementedError`. | Consent model counts/value snapshot plus `integration_boundary` no-call test. |

## Required mounted test matrix

| Case | Expected local outcome |
|---|---|
| Authenticated owner/current record | 200 and exactly `{"consentRecord": ...}` with serializer allowlist. |
| No credentials | Existing project 401 behavior and no record data. |
| Non-integer `dataAgreementId` | Existing bounded 400 behavior. |
| Unknown category | Existing bounded 404 behavior. |
| Known category/no caller current row | Existing bounded 404 behavior. |
| Foreign citizen or caller category mismatch | Same non-disclosing 404 behavior; no existence leak. |
| Historical plus current row | Return only the current row; no history array. |
| Repeated GET/boundary patched to fail | Identical read response; no mutable DB/audit/webhook change and no boundary call. |

## Security and deferral constraints

The response must contain only already-authorized serializer fields. Never expose audit data, actor/IP/source material, revision snapshots, raw signature material, credentials, endpoint configuration, or integration errors. No endpoint, credential, retry, subject mapping, queue, or synthetic success behavior may be added. Keep all matrix rows partial and retain candidate `NOT_READY`/fail-closed boundary guards.

## Stage 3 acceptance rule

C01-01 through C01-04 may be marked locally complete only if the test matrix passes on the mounted route and the external boundary remains uncalled and fail-closed. This has no effect on official conformance, testing-site readiness, certification, staging, or submission status.

"""
GovStack Consent BB v1.3.0 — URL configuration.

All paths mirror the GovStack OpenAPI spec exactly so the test harness at
testing.govstack.global can call them without path remapping.

Mounted at /api/v1/consent/ by apps.api.urls alongside the legacy CivicOS paths.

GovStack namespaces:
  /config/    — org-authenticated CRUD (Policy, DataAgreement, Individual, Webhook)
  /service/   — individual-authenticated consent management + verification
  /audit/     — auditor read-only (ConsentRecord, DataAgreement)

Note on ID types:
  - ConsentPolicy      → uuid  (extends UUIDModel)
  - ConsentCategory    → int   (DataAgreement; plain Django model)
  - ConsentRecord      → uuid  (extends UUIDModel)
  - ConsentRevision    → uuid  (extends UUIDModel)
  - ConsentWebhook     → uuid  (extends UUIDModel)
  - User (Individual)  → uuid  (auth_extension User extends UUIDModel)
"""
from django.urls import path

from . import govstack_views as v

urlpatterns = [
    # -----------------------------------------------------------------------
    # CONFIG — Policy
    # -----------------------------------------------------------------------
    path("config/policies/",          v.ConfigPolicyCollectionView.as_view(), name="gs-policy-list"),
    path("config/policy/",            v.ConfigPolicyCreateView.as_view(),     name="gs-policy-create"),
    path("config/policy/<str:policy_id>/",
                                      v.ConfigPolicyDetailView.as_view(),    name="gs-policy-detail"),
    path("config/policy/<str:policy_id>/revisions/",
                                      v.ConfigPolicyRevisionsView.as_view(), name="gs-policy-revisions"),

    # -----------------------------------------------------------------------
    # CONFIG — DataAgreement (ConsentCategory uses integer PK)
    # -----------------------------------------------------------------------
    path("config/data-agreements/",   v.ConfigDataAgreementCollectionView.as_view(), name="gs-da-list"),
    path("config/data-agreement/",    v.ConfigDataAgreementCreateView.as_view(),     name="gs-da-create"),
    path("config/data-agreement/<str:data_agreement_id>/",
                                      v.ConfigDataAgreementDetailView.as_view(), name="gs-da-detail"),

    # -----------------------------------------------------------------------
    # CONFIG — Individual (User UUID)
    # -----------------------------------------------------------------------
    path("config/individuals/",       v.ConfigIndividualCollectionView.as_view(), name="gs-config-individual-list"),
    path("config/individual/",        v.ConfigIndividualCreateView.as_view(),     name="gs-config-individual-create"),
    path("config/individual/<str:individual_id>/",
                                      v.ConfigIndividualDetailView.as_view(), name="gs-config-individual-detail"),

    # -----------------------------------------------------------------------
    # CONFIG — Webhook (uuid)
    # -----------------------------------------------------------------------
    path("config/webhooks/",          v.ConfigWebhookCollectionView.as_view(), name="gs-webhook-list"),
    path("config/webhook/",           v.ConfigWebhookCreateView.as_view(),     name="gs-webhook-create"),
    # NOTE: /payload/ must come before the bare {id}/ path to avoid routing ambiguity
    path("config/webhook/<str:webhook_id>/payload/",
                                      v.ConfigWebhookPayloadView.as_view(), name="gs-webhook-payload"),
    path("config/webhook/<str:webhook_id>/",
                                      v.ConfigWebhookDetailView.as_view(),  name="gs-webhook-detail"),

    # -----------------------------------------------------------------------
    # SERVICE — Individual self-service (User UUID)
    # -----------------------------------------------------------------------
    path("service/individuals/",      v.ServiceIndividualCollectionView.as_view(), name="gs-service-individual-list"),
    path("service/individual/",       v.ServiceIndividualCreateView.as_view(),     name="gs-service-individual-create"),
    # NOTE: Right to Be Forgotten (below) MUST come before the
    # <str:individual_id> detail route. With the <uuid:>/<int:> converters
    # previously used here, the literal segment "record" could never match
    # <uuid:individual_id> so ordering didn't matter — but now that malformed-
    # ID validation has moved into the view (Fix 2: govstack_views.py's
    # _parse_uuid_param), this route uses <str:>, which matches ANY
    # single path segment including the literal "record". Without this
    # ordering, "service/individual/record/" would be incorrectly captured
    # by ServiceIndividualDetailView with individual_id="record" instead of
    # reaching ServiceIndividualRightToBeForgottenView.
    path("service/individual/record/",
                                      v.ServiceIndividualRightToBeForgottenView.as_view(),
                                      name="gs-rtbf"),
    path("service/individual/<str:individual_id>/",
                                      v.ServiceIndividualDetailView.as_view(), name="gs-service-individual-detail"),

    # SERVICE — DataAgreement (read-only for individuals; int PK)
    path("service/data-agreement/<str:data_agreement_id>/",
                                      v.ServiceDataAgreementDetailView.as_view(), name="gs-service-da-detail"),

    # SERVICE — Policy (read-only for individuals; uuid)
    path("service/policy/<str:policy_id>/",
                                      v.ServicePolicyDetailView.as_view(),   name="gs-service-policy-detail"),

    # SERVICE — Verification (data consumer endpoints)
    path("service/verification/data-agreements/",
                                      v.ServiceVerificationDataAgreementsView.as_view(),
                                      name="gs-verification-da-list"),
    path("service/verification/consent-records/",
                                      v.ServiceVerificationConsentRecordsView.as_view(),
                                      name="gs-verification-cr-list"),
    path("service/verification/consent-record/<str:consent_record_id>/",
                                      v.ServiceVerificationConsentRecordDetailView.as_view(),
                                      name="gs-verification-cr-detail"),

    # SERVICE — Individual ConsentRecord CRUD
    # NOTE: The draft endpoint MUST come before the list/create endpoint so
    # Django matches "draft/" as a literal path, not a UUID.
    path("service/individual/record/consent-record/draft/",
                                      v.ServiceIndividualConsentRecordDraftView.as_view(),
                                      name="gs-cr-draft"),
    path("service/individual/record/consent-record/",
                                      v.ServiceIndividualConsentRecordListView.as_view(),
                                      name="gs-cr-list-create"),
    path("service/individual/record/consent-record/<str:consent_record_id>/",
                                      v.ServiceIndividualConsentRecordDetailView.as_view(),
                                      name="gs-cr-detail"),

    # SERVICE — DataAgreement-scoped consent records (int PK for DataAgreement)
    # NOTE: /all/ path MUST come before the bare DA path to avoid routing ambiguity.
    path("service/individual/record/data-agreement/<str:data_agreement_id>/all/",
                                      v.ServiceIndividualDataAgreementAllConsentRecordsView.as_view(),
                                      name="gs-cr-by-da-all"),
    path("service/individual/record/data-agreement/<str:data_agreement_id>/",
                                      v.ServiceIndividualDataAgreementConsentRecordView.as_view(),
                                      name="gs-cr-by-da"),

    # SERVICE — ConsentRecord Signature (POST create, PUT update)
    path("service/individual/record/consent-record/<str:consent_record_id>/signature/",
                                      v.ServiceConsentRecordSignatureView.as_view(),
                                      name="gs-cr-signature"),

    # -----------------------------------------------------------------------
    # AUDIT
    # -----------------------------------------------------------------------
    path("audit/consent-records/",    v.AuditConsentRecordListView.as_view(),    name="gs-audit-cr-list"),
    path("audit/consent-record/<str:consent_record_id>/",
                                      v.AuditConsentRecordDetailView.as_view(),  name="gs-audit-cr-detail"),
    path("audit/data-agreements/",    v.AuditDataAgreementListView.as_view(),    name="gs-audit-da-list"),
    path("audit/data-agreement/<str:data_agreement_id>/",
                                      v.AuditDataAgreementDetailView.as_view(),  name="gs-audit-da-detail"),
    # CivicOS extension: full consent audit log with timestamps
    path("audit/consent-log/",        v.AuditConsentLogView.as_view(),           name="gs-audit-log"),
]

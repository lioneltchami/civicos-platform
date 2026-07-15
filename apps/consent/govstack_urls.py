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
    path("config/policy/<uuid:policy_id>/",
                                      v.ConfigPolicyDetailView.as_view(),    name="gs-policy-detail"),
    path("config/policy/<uuid:policy_id>/revisions/",
                                      v.ConfigPolicyRevisionsView.as_view(), name="gs-policy-revisions"),

    # -----------------------------------------------------------------------
    # CONFIG — DataAgreement (ConsentCategory uses integer PK)
    # -----------------------------------------------------------------------
    path("config/data-agreements/",   v.ConfigDataAgreementCollectionView.as_view(), name="gs-da-list"),
    path("config/data-agreement/",    v.ConfigDataAgreementCreateView.as_view(),     name="gs-da-create"),
    path("config/data-agreement/<int:data_agreement_id>/",
                                      v.ConfigDataAgreementDetailView.as_view(), name="gs-da-detail"),

    # -----------------------------------------------------------------------
    # CONFIG — Individual (User UUID)
    # -----------------------------------------------------------------------
    path("config/individuals/",       v.ConfigIndividualCollectionView.as_view(), name="gs-config-individual-list"),
    path("config/individual/",        v.ConfigIndividualCreateView.as_view(),     name="gs-config-individual-create"),
    path("config/individual/<uuid:individual_id>/",
                                      v.ConfigIndividualDetailView.as_view(), name="gs-config-individual-detail"),

    # -----------------------------------------------------------------------
    # CONFIG — Webhook (uuid)
    # -----------------------------------------------------------------------
    path("config/webhooks/",          v.ConfigWebhookCollectionView.as_view(), name="gs-webhook-list"),
    path("config/webhook/",           v.ConfigWebhookCreateView.as_view(),     name="gs-webhook-create"),
    # NOTE: /payload/ must come before the bare {id}/ path to avoid routing ambiguity
    path("config/webhook/<uuid:webhook_id>/payload/",
                                      v.ConfigWebhookPayloadView.as_view(), name="gs-webhook-payload"),
    path("config/webhook/<uuid:webhook_id>/",
                                      v.ConfigWebhookDetailView.as_view(),  name="gs-webhook-detail"),

    # -----------------------------------------------------------------------
    # SERVICE — Individual self-service (User UUID)
    # -----------------------------------------------------------------------
    path("service/individuals/",      v.ServiceIndividualCollectionView.as_view(), name="gs-service-individual-list"),
    path("service/individual/",       v.ServiceIndividualCreateView.as_view(),     name="gs-service-individual-create"),
    path("service/individual/<uuid:individual_id>/",
                                      v.ServiceIndividualDetailView.as_view(), name="gs-service-individual-detail"),

    # SERVICE — DataAgreement (read-only for individuals; int PK)
    path("service/data-agreement/<int:data_agreement_id>/",
                                      v.ServiceDataAgreementDetailView.as_view(), name="gs-service-da-detail"),

    # SERVICE — Policy (read-only for individuals; uuid)
    path("service/policy/<uuid:policy_id>/",
                                      v.ServicePolicyDetailView.as_view(),   name="gs-service-policy-detail"),

    # SERVICE — Verification (data consumer endpoints)
    path("service/verification/data-agreements/",
                                      v.ServiceVerificationDataAgreementsView.as_view(),
                                      name="gs-verification-da-list"),
    path("service/verification/consent-records/",
                                      v.ServiceVerificationConsentRecordsView.as_view(),
                                      name="gs-verification-cr-list"),
    path("service/verification/consent-record/<uuid:consent_record_id>/",
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
    path("service/individual/record/consent-record/<uuid:consent_record_id>/",
                                      v.ServiceIndividualConsentRecordDetailView.as_view(),
                                      name="gs-cr-detail"),

    # SERVICE — DataAgreement-scoped consent records (int PK for DataAgreement)
    # NOTE: /all/ path MUST come before the bare DA path to avoid routing ambiguity.
    path("service/individual/record/data-agreement/<int:data_agreement_id>/all/",
                                      v.ServiceIndividualDataAgreementAllConsentRecordsView.as_view(),
                                      name="gs-cr-by-da-all"),
    path("service/individual/record/data-agreement/<int:data_agreement_id>/",
                                      v.ServiceIndividualDataAgreementConsentRecordView.as_view(),
                                      name="gs-cr-by-da"),

    # SERVICE — ConsentRecord Signature (POST create, PUT update)
    path("service/individual/record/consent-record/<uuid:consent_record_id>/signature/",
                                      v.ServiceConsentRecordSignatureView.as_view(),
                                      name="gs-cr-signature"),

    # SERVICE — Right to Be Forgotten (DELETE all forgettable records)
    path("service/individual/record/",
                                      v.ServiceIndividualRightToBeForgottenView.as_view(),
                                      name="gs-rtbf"),

    # -----------------------------------------------------------------------
    # AUDIT
    # -----------------------------------------------------------------------
    path("audit/consent-records/",    v.AuditConsentRecordListView.as_view(),    name="gs-audit-cr-list"),
    path("audit/consent-record/<uuid:consent_record_id>/",
                                      v.AuditConsentRecordDetailView.as_view(),  name="gs-audit-cr-detail"),
    path("audit/data-agreements/",    v.AuditDataAgreementListView.as_view(),    name="gs-audit-da-list"),
    path("audit/data-agreement/<int:data_agreement_id>/",
                                      v.AuditDataAgreementDetailView.as_view(),  name="gs-audit-da-detail"),
    # CivicOS extension: full consent audit log with timestamps
    path("audit/consent-log/",        v.AuditConsentLogView.as_view(),           name="gs-audit-log"),
]

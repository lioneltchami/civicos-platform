# Item 01 Consent v23Q4 Operation Matrix

Baseline: `published v23Q4`; official OpenAPI SHA-256: `5d35ed438e60293046522cb69229133ddb0bf23fccafec4d1e9fcf11e0b542cf`.
All rows remain **partial** because metadata alignment is not local wire-conformance or testing-site evidence.

| Group | Method | Path | Operation ID | Status | Security | Local evidence | Disposition |
|---|---|---|---|---|---|---|---|
| auditor | `GET` | `/audit/consent-record/{consentRecordId}/` | `auditConsentRecordRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| auditor | `GET` | `/audit/consent-records/` | `auditConsentRecordList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| auditor | `GET` | `/audit/data-agreement/{dataAgreementId}/` | `auditDataAgreementRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| auditor | `GET` | `/audit/data-agreements/` | `auditDataAgreementList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `POST` | `/config/data-agreement/` | `configDataAgreementCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `DELETE` | `/config/data-agreement/{dataAgreementId}/` | `configDataAgreementDelete` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/data-agreement/{dataAgreementId}/` | `configDataAgreementRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `PUT` | `/config/data-agreement/{dataAgreementId}/` | `configDataAgreementUpdate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/data-agreements/` | `configDataAgreementList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `POST` | `/config/individual/` | `configIndividualCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/individual/{individualId}/` | `configIndividualRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/individuals/` | `configIndividualList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/policies/` | `configPolicyList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `POST` | `/config/policy/` | `configPolicyCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `DELETE` | `/config/policy/{policyId}/` | `configPolicyDelete` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/policy/{policyId}/` | `configPolicyRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `PUT` | `/config/policy/{policyId}/` | `configPolicyUpdate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/policy/{policyId}/revisions/` | `configPolicyRevisionsList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `POST` | `/config/webhook/` | `configWebhookCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `DELETE` | `/config/webhook/{webhookId}/` | `configWebhookDelete` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/webhook/{webhookId}/` | `configWebhookRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `PUT` | `/config/webhook/{webhookId}/` | `configWebhookUpdate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| config | `GET` | `/config/webhooks/` | `configWebhookList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/data-agreement/{dataAgreementId}/` | `serviceDataAgreementRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `POST` | `/service/individual/` | `serviceIndividualCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `DELETE` | `/service/individual/record/` | `serviceIndividualConsentRecordDeleteAll` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/individual/record/consent-record/` | `serviceIndividualConsentRecordList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `POST` | `/service/individual/record/consent-record/` | `serviceIndividualConsentRecordSignatureCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `POST` | `/service/individual/record/consent-record/draft/` | `serviceIndividualConsentRecordDraftCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `PUT` | `/service/individual/record/consent-record/{consentRecordId}/` | `serviceIndividualConsentRecordUpdate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `POST` | `/service/individual/record/consent-record/{consentRecordId}/signature/` | `serviceIndividualSignatureCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `PUT` | `/service/individual/record/consent-record/{consentRecordId}/signature/` | `serviceIndividualSignatureUpdate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/individual/record/data-agreement/{dataAgreementId}/` | `serviceIndividualConsentRecordRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `POST` | `/service/individual/record/data-agreement/{dataAgreementId}/` | `serviceIndividualConsentRecordCreate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/individual/record/data-agreement/{dataAgreementId}/all/` | `serviceIndividualDataAgreementConsentRecordList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/individual/{individualId}/` | `serviceIndividualRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `PUT` | `/service/individual/{individualId}/` | `serviceIndividualUpdate` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/individuals/` | `serviceIndividualList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/policy/{policyId}/` | `servicePolicyRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/verification/consent-record/{consentRecordId}/` | `serviceVerificationConsentRecordRead` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/verification/consent-records/` | `serviceVerificationConsentRecordList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |
| service | `GET` | `/service/verification/data-agreements/` | `serviceVerificationDataAgreementList` | 200, 400 | no operation-level security requirement | documented; wire-unverified | **partial** |

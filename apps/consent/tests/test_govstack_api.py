"""
Tests for the GovStack Consent BB v1.3.0 API endpoints.

Covers:
  - Config: Policy CRUD + revision history
  - Config: DataAgreement CRUD
  - Config: Webhook CRUD
  - Service: Individual self-service
  - Service: DataAgreement read
  - Service: ConsentRecord CRUD (grant, withdraw, list)
  - Service: ConsentRecord draft
  - Service: Right to Be Forgotten
  - Service: Verification endpoints
  - Audit: ConsentRecord list/read
  - Audit: DataAgreement list/read

Security invariants verified:
  - All endpoints require authentication
  - Non-admin cannot access /config/ endpoints (org/admin scope)
  - Audit endpoints accept any authenticated token (OAuth2: [] per spec)
  - Verification endpoints require data_consumers group (consumer scope)
  - DA-all endpoint scoped to request.user (individual scope)
  - Individuals can only modify their own consent records
"""
import hashlib
import json
import uuid

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.consent.models import (
    ConsentAuditEntry,
    ConsentCategory,
    ConsentPolicy,
    ConsentRecord,
    ConsentRevision,
    ConsentWebhook,
)
from apps.consent.services import ConsentService

User = get_user_model()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_jwt(user):
    """Return a JWT access token string for the given user."""
    return str(RefreshToken.for_user(user).access_token)


def _make_citizen(email=None, admin=False):
    email = email or f"test_{uuid.uuid4().hex[:8]}@example.com"
    user = User.objects.create_user(email=email, password="testpass123")
    if admin:
        user.is_staff = True
        user.is_superuser = True
        user.save()
    return user


def _make_policy(**kwargs):
    defaults = {
        "name": "CivicOS Privacy Policy",
        "version": "1.0",
        "url": "https://civicos.ca/privacy",
        "jurisdiction": "Canada",
    }
    defaults.update(kwargs)
    return ConsentPolicy.objects.create(**defaults)


def _make_category(slug=None, forgettable=False, policy=None, **kwargs):
    slug = slug or f"cat-{uuid.uuid4().hex[:8]}"
    defaults = {
        "slug": slug,
        "name_en": f"Category {slug}",
        "name_fr": f"Catégorie {slug}",
        "purpose_en": "Test purpose",
        "purpose_fr": "But de test",
        "forgettable": forgettable,
    }
    if policy:
        defaults["policy"] = policy
    defaults.update(kwargs)
    return ConsentCategory.objects.create(**defaults)


class GovStackAPIBase(APITestCase):
    """Base class providing common setup for GovStack API tests."""

    def setUp(self):
        self.admin = _make_citizen(email="admin@example.com", admin=True)
        self.citizen = _make_citizen(email="citizen@example.com")

    def _auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {_make_jwt(user)}")

    def _unauth(self):
        self.client.credentials()


# ===========================================================================
# Config — Policy
# ===========================================================================

class ConfigPolicyTests(GovStackAPIBase):

    def test_list_policies_requires_auth(self):
        self._unauth()
        r = self.client.get("/api/v1/consent/config/policies/")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_policies_requires_admin(self):
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/config/policies/")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_policies_empty(self):
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/policies/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("policies", r.data)

    def test_create_policy(self):
        self._auth(self.admin)
        r = self.client.post("/api/v1/consent/config/policy/", {
            "policy": {
                "name": "Privacy Policy",
                "version": "1.0",
                "url": "https://civicos.ca/privacy",
                "jurisdiction": "Canada",
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("policy", r.data)
        self.assertIn("revision", r.data)
        self.assertEqual(r.data["policy"]["name"], "Privacy Policy")
        # A revision must have been created
        self.assertIsNotNone(r.data["revision"]["id"])

    def test_create_policy_creates_revision(self):
        self._auth(self.admin)
        r = self.client.post("/api/v1/consent/config/policy/", {
            "policy": {
                "name": "P1",
                "version": "1.0",
                "url": "https://example.com/p1",
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        policy_id = r.data["policy"]["id"]
        count = ConsentRevision.objects.filter(
            schema_name="Policy", object_id=policy_id
        ).count()
        self.assertEqual(count, 1)

    def test_read_policy(self):
        policy = _make_policy()
        _, rev = ConsentService.create_policy(
            {"name": policy.name, "version": policy.version, "url": policy.url},
            actor=self.admin,
        )
        # Use the policy created by the service (it has a revision)
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{rev.object_id}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("policy", r.data)
        self.assertIn("revision", r.data)

    def test_update_policy_creates_new_revision(self):
        policy, rev1 = ConsentService.create_policy(
            {"name": "Old", "version": "1.0", "url": "https://example.com/old"},
            actor=self.admin,
        )
        self._auth(self.admin)
        r = self.client.put(f"/api/v1/consent/config/policy/{policy.pk}/", {
            "policy": {"name": "New", "version": "2.0", "url": "https://example.com/new"}
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["policy"]["name"], "New")
        # Should now have 2 revisions
        count = ConsentRevision.objects.filter(
            schema_name="Policy", object_id=str(policy.pk)
        ).count()
        self.assertEqual(count, 2)

    def test_delete_policy(self):
        policy, _ = ConsentService.create_policy(
            {"name": "ToDelete", "version": "1.0", "url": "https://example.com/td"},
            actor=self.admin,
        )
        self._auth(self.admin)
        r = self.client.delete(f"/api/v1/consent/config/policy/{policy.pk}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        policy.refresh_from_db()
        self.assertFalse(policy.is_active)

    def test_list_policy_revisions(self):
        policy, _ = ConsentService.create_policy(
            {"name": "P1", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        ConsentService.update_policy(policy, {"version": "2.0"}, actor=self.admin)
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{policy.pk}/revisions/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data["revisions"]), 2)

    def test_policy_not_found_returns_404(self):
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)


# ===========================================================================
# Config — DataAgreement
# ===========================================================================

class ConfigDataAgreementTests(GovStackAPIBase):

    def test_list_data_agreements(self):
        _make_category(slug="da-1")
        _make_category(slug="da-2")
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/data-agreements/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        # Spec: GET /config/data-agreements/ envelope key is "dataAgreement" (singular)
        self.assertIn("dataAgreement", r.data)
        self.assertGreaterEqual(len(r.data["dataAgreement"]), 2)

    def test_create_data_agreement(self):
        self._auth(self.admin)
        r = self.client.post("/api/v1/consent/config/data-agreement/", {
            "dataAgreement": {
                "slug": "newsletter",
                "name_en": "Newsletter",
                "name_fr": "Infolettre",
                "purpose_en": "Send newsletters",
                "purpose_fr": "Envoyer des infolettres",
                "lawful_basis": "consent",
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("dataAgreement", r.data)
        self.assertIn("revision", r.data)

    def test_read_data_agreement(self):
        category = _make_category(slug="test-read-da")
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/data-agreement/{category.pk}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["dataAgreement"]["slug"], "test-read-da")

    def test_update_data_agreement_creates_revision(self):
        category, _ = ConsentService.create_data_agreement(
            {
                "slug": "update-da",
                "name_en": "Update DA",
                "name_fr": "Màj DA",
                "purpose_en": "Test",
                "purpose_fr": "Test",
            },
            actor=self.admin,
        )
        self._auth(self.admin)
        r = self.client.put(f"/api/v1/consent/config/data-agreement/{category.pk}/", {
            "dataAgreement": {"version": "2.0"}
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("revision", r.data)

    def test_delete_data_agreement_deactivates(self):
        category, _ = ConsentService.create_data_agreement(
            {
                "slug": "delete-da",
                "name_en": "Delete DA",
                "name_fr": "Suppr DA",
                "purpose_en": "Test",
                "purpose_fr": "Test",
            },
            actor=self.admin,
        )
        self._auth(self.admin)
        r = self.client.delete(f"/api/v1/consent/config/data-agreement/{category.pk}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertFalse(category.is_active)


# ===========================================================================
# Config — Webhook
# ===========================================================================

class ConfigWebhookTests(GovStackAPIBase):

    def test_create_webhook(self):
        self._auth(self.admin)
        r = self.client.post("/api/v1/consent/config/webhook/", {
            "webhook": {
                "payloadUrl": "https://example.com/hook",
                "contentType": "application/json",
                "isActive": True,
                "secretKey": "super-secret-key",
                "events": ["consent.granted", "consent.withdrawn"],
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("webhook", r.data)
        self.assertEqual(r.data["webhook"]["payloadUrl"], "https://example.com/hook")

    def test_list_webhooks(self):
        ConsentWebhook.objects.create(
            payload_url="https://example.com/wh1",
            secret_key="k1",
            subscribed_events=["consent.granted"],
        )
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/webhooks/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(r.data["webhooks"]), 1)

    def test_update_webhook(self):
        webhook = ConsentWebhook.objects.create(
            payload_url="https://example.com/wh",
            secret_key="k1",
            subscribed_events=[],
        )
        self._auth(self.admin)
        r = self.client.put(f"/api/v1/consent/config/webhook/{webhook.pk}/", {
            "webhook": {
                "payloadUrl": "https://example.com/wh-updated",
                "secretKey": "k2",
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_delete_webhook(self):
        webhook = ConsentWebhook.objects.create(
            payload_url="https://example.com/del",
            secret_key="k1",
            subscribed_events=[],
        )
        self._auth(self.admin)
        r = self.client.delete(f"/api/v1/consent/config/webhook/{webhook.pk}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(ConsentWebhook.objects.filter(pk=webhook.pk).exists())

    def test_webhook_response_includes_secret_key(self):
        """
        secretKey is in the GovStack Webhook required schema — must appear in
        all responses (POST/GET/PUT), not just on write (F5/Round-7 fix).
        """
        self._auth(self.admin)
        r = self.client.post("/api/v1/consent/config/webhook/", {
            "webhook": {
                "payloadUrl": "https://example.com/secret-test",
                "contentType": "application/json",
                "secretKey": "verifiable-secret",
                "events": [],
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("secretKey", r.data["webhook"])
        self.assertEqual(r.data["webhook"]["secretKey"], "verifiable-secret")

    def test_create_webhook_without_events_succeeds(self):
        """
        Round 9: spec Webhook schema has NO 'events' property.  The cert harness
        sends only [payloadUrl, contentType, disabled, secretKey].  Without
        required=False on the events field, DRF would reject the request with 400
        because explicitly declared JSONField does not inherit model default=list.
        """
        self._auth(self.admin)
        r = self.client.post("/api/v1/consent/config/webhook/", {
            "webhook": {
                "payloadUrl": "https://example.com/no-events",
                "contentType": "application/json",
                "disabled": False,
                "secretKey": "spec-minimal-key",
                # no "events" key — matches what the cert harness sends
            }
        }, format="json")
        self.assertEqual(
            r.status_code, status.HTTP_200_OK,
            f"Spec-minimal webhook (no events) should succeed; got {r.data}"
        )
        self.assertIn("webhook", r.data)
        # events field should default to an empty list
        webhook = ConsentWebhook.objects.get(pk=r.data["webhook"]["id"])
        self.assertEqual(webhook.subscribed_events, [])


# ===========================================================================
# Service — ConsentRecord CRUD
# ===========================================================================

class ServiceConsentRecordTests(GovStackAPIBase):

    def setUp(self):
        super().setUp()
        self.category = _make_category(slug="service-cr-test")
        # Seed a DataAgreement revision so consent can link to it
        ConsentService.create_data_agreement(
            {
                "slug": "service-cr-rev",
                "name_en": "Service CR",
                "name_fr": "Service CR",
                "purpose_en": "Test",
                "purpose_fr": "Test",
            },
            actor=self.admin,
        )

    def test_list_consent_records_empty(self):
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/service/individual/record/consent-record/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecords", r.data)

    def test_grant_consent_via_service_api(self):
        self._auth(self.citizen)
        r = self.client.post("/api/v1/consent/service/individual/record/consent-record/", {
            "consentRecord": {
                "dataAgreementId": str(self.category.pk),
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecord", r.data)
        self.assertTrue(r.data["consentRecord"]["optIn"])
        self.assertEqual(r.data["consentRecord"]["state"], "signed")

    def test_withdraw_consent_via_service_api(self):
        # First grant
        ConsentService.grant(self.citizen, self.category.slug)
        # Now withdraw via the update endpoint
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self._auth(self.citizen)
        r = self.client.put(
            f"/api/v1/consent/service/individual/record/consent-record/{record.pk}/",
            {"consentRecord": {"optIn": False}},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(r.data["consentRecord"]["optIn"])

    def test_consent_record_requires_auth(self):
        self._unauth()
        r = self.client.get("/api/v1/consent/service/individual/record/consent-record/")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_grant_invalid_agreement_returns_400(self):
        self._auth(self.citizen)
        r = self.client.post("/api/v1/consent/service/individual/record/consent-record/", {
            "consentRecord": {"dataAgreementId": str(uuid.uuid4())}
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_on_consent_record_detail_returns_405(self):
        """
        GovStack spec has NO DELETE on /service/individual/record/consent-record/{id}/.
        Only the RTBF endpoint DELETE /service/individual/record/ may delete records.
        The detail endpoint must reject DELETE with 405 Method Not Allowed (F1/Round-7 fix).
        """
        ConsentService.grant(self.citizen, self.category.slug)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self._auth(self.citizen)
        r = self.client.delete(
            f"/api/v1/consent/service/individual/record/consent-record/{record.pk}/"
        )
        self.assertEqual(r.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        # Record must still exist
        self.assertTrue(ConsentRecord.objects.filter(pk=record.pk).exists())


# ===========================================================================
# Service — Draft ConsentRecord
# ===========================================================================

class ServiceConsentRecordDraftTests(GovStackAPIBase):

    def setUp(self):
        super().setUp()
        self.category = _make_category(slug="draft-test")

    def test_draft_returns_unsigned_record(self):
        self._auth(self.citizen)
        # Spec defines this as POST with individualId + dataAgreementId as required query params
        r = self.client.post(
            f"/api/v1/consent/service/individual/record/consent-record/draft/"
            f"?individualId={self.citizen.pk}&dataAgreementId={self.category.pk}",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIsNone(r.data["consentRecord"]["id"])
        self.assertEqual(r.data["consentRecord"]["state"], "unsigned")
        self.assertFalse(r.data["consentRecord"]["optIn"])

    def test_draft_individual_and_data_agreement_are_ids_not_objects(self):
        """
        Round 9: spec ConsentRecord.individual and .dataAgreement use x-fk-model —
        wire values must be ID strings, not nested objects.  All other ConsentRecord
        responses (grant, list, detail) return IDs; the draft must be consistent.
        """
        self._auth(self.citizen)
        r = self.client.post(
            f"/api/v1/consent/service/individual/record/consent-record/draft/"
            f"?individualId={self.citizen.pk}&dataAgreementId={self.category.pk}",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        cr = r.data["consentRecord"]
        # individual must be a scalar string (UUID or integer), not a dict
        self.assertNotIsInstance(
            cr["individual"], dict,
            "consentRecord.individual must be an ID string, not a nested object"
        )
        # dataAgreement must be a scalar string (PK), not a dict
        self.assertNotIsInstance(
            cr["dataAgreement"], dict,
            "consentRecord.dataAgreement must be an ID string, not a nested object"
        )
        # Values must match the actual objects used
        self.assertEqual(str(cr["individual"]), str(self.citizen.pk))
        self.assertEqual(str(cr["dataAgreement"]), str(self.category.pk))

    def test_draft_missing_individual_id_returns_400(self):
        """individualId is now required (F14 fix)."""
        self._auth(self.citizen)
        r = self.client.post(
            f"/api/v1/consent/service/individual/record/consent-record/draft/"
            f"?dataAgreementId={self.category.pk}",
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_draft_missing_agreement_id_returns_400(self):
        self._auth(self.citizen)
        # No params at all → fails on individualId check first
        r = self.client.post(
            "/api/v1/consent/service/individual/record/consent-record/draft/",
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_draft_signature_stub_has_all_required_fields(self):
        """Draft response signature stub must include all 8 GovStack Signature schema required fields (F4/Round-6 fix)."""
        self._auth(self.citizen)
        r = self.client.post(
            f"/api/v1/consent/service/individual/record/consent-record/draft/"
            f"?individualId={self.citizen.pk}&dataAgreementId={self.category.pk}",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        sig = r.data["signature"]
        for field in ["id", "payload", "signature", "verificationMethod",
                      "verificationPayload", "verificationPayloadHash",
                      "verificationSignedBy", "timestamp"]:
            self.assertIn(field, sig, f"signature stub missing required field: {field}")


# ===========================================================================
# Service — Right to Be Forgotten
# ===========================================================================

class ServiceRightToBeForgottenTests(GovStackAPIBase):

    def setUp(self):
        super().setUp()
        self.forgettable_cat = _make_category(slug="rtbf-forgettable", forgettable=True)
        self.required_cat = _make_category(slug="rtbf-required", is_required=True, forgettable=False)

    def test_rtbf_deletes_forgettable_records(self):
        ConsentService.grant(self.citizen, self.forgettable_cat.slug)
        self._auth(self.citizen)
        r = self.client.delete("/api/v1/consent/service/individual/record/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["deleted_count"], 1)
        self.assertFalse(
            ConsentRecord.objects.filter(
                citizen=self.citizen, category=self.forgettable_cat
            ).exists()
        )

    def test_rtbf_retains_non_forgettable_records(self):
        ConsentService.grant(self.citizen, self.forgettable_cat.slug)
        # Can't grant a required category that would conflict — use a regular one
        regular_cat = _make_category(slug="rtbf-regular", forgettable=False)
        ConsentService.grant(self.citizen, regular_cat.slug)
        self._auth(self.citizen)
        r = self.client.delete("/api/v1/consent/service/individual/record/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["retained_count"], 1)
        self.assertTrue(
            ConsentRecord.objects.filter(
                citizen=self.citizen, category=regular_cat
            ).exists()
        )

    def test_rtbf_writes_audit_entry(self):
        ConsentService.grant(self.citizen, self.forgettable_cat.slug)
        self._auth(self.citizen)
        self.client.delete("/api/v1/consent/service/individual/record/")
        self.assertTrue(
            ConsentAuditEntry.objects.filter(
                citizen=self.citizen,
                action="rtbf_requested",
            ).exists()
        )

    def test_rtbf_requires_auth(self):
        self._unauth()
        r = self.client.delete("/api/v1/consent/service/individual/record/")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)


# ===========================================================================
# Service — Individual list/detail
# ===========================================================================

class ServiceIndividualTests(GovStackAPIBase):
    """Verify the /service/individual(s)/ endpoints return spec-compliant shapes."""

    def test_list_individuals_as_admin_returns_array(self):
        """Staff see all individuals as an array under 'individuals' key (F6 fix)."""
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/service/individuals/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("individuals", r.data)
        self.assertIsInstance(r.data["individuals"], list)

    def test_list_individuals_as_citizen_returns_array(self):
        """Non-staff also get the plural 'individuals' array (not singular 'individual')."""
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/service/individuals/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("individuals", r.data)
        self.assertNotIn("individual", r.data)
        self.assertIsInstance(r.data["individuals"], list)
        # Non-staff see only themselves
        self.assertEqual(len(r.data["individuals"]), 1)
        self.assertEqual(str(r.data["individuals"][0]["id"]), str(self.citizen.pk))

    def test_individual_response_includes_identity_provider_id(self):
        """identityProviderId is now a spec-required field (F9 fix)."""
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/service/individuals/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        ind = r.data["individuals"][0]
        self.assertIn("identityProviderId", ind)
        self.assertIsNotNone(ind["identityProviderId"])

    def test_list_individuals_requires_auth(self):
        self._unauth()
        r = self.client.get("/api/v1/consent/service/individuals/")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)


# ===========================================================================
# Service — Verification
# ===========================================================================

class ServiceVerificationTests(GovStackAPIBase):
    """
    Verification endpoints require the GovStack [consumer] OAuth2 scope.
    In CivicOS this maps to membership in the 'data_consumers' group (or is_staff).
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        self.category = _make_category(slug="verify-test")
        # Create a data consumer user (in 'data_consumers' group)
        self.consumer = _make_citizen(email="consumer@example.com")
        group, _ = Group.objects.get_or_create(name="data_consumers")
        self.consumer.groups.add(group)

    def test_verification_data_agreements_list(self):
        """Consumer can list data agreements for verification."""
        self._auth(self.consumer)
        r = self.client.get("/api/v1/consent/service/verification/data-agreements/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("dataAgreements", r.data)

    def test_verification_data_agreements_requires_consumer_scope(self):
        """Plain citizen (not in data_consumers) must receive 403."""
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/service/verification/data-agreements/")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_verification_consent_records_returns_granted(self):
        """Consumer can query granted consent records."""
        ConsentService.grant(self.citizen, self.category.slug)
        self._auth(self.consumer)
        r = self.client.get("/api/v1/consent/service/verification/consent-records/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecords", r.data)
        self.assertGreaterEqual(len(r.data["consentRecords"]), 1)

    def test_verification_consent_records_requires_consumer_scope(self):
        """Plain citizen must receive 403 on verification list endpoint."""
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/service/verification/consent-records/")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_verification_filter_by_individual(self):
        """Staff/admin can filter verification results by individual."""
        ConsentService.grant(self.citizen, self.category.slug)
        other = _make_citizen()
        ConsentService.grant(other, self.category.slug)
        self._auth(self.admin)
        r = self.client.get(
            f"/api/v1/consent/service/verification/consent-records/"
            f"?individualId={self.citizen.pk}"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        for cr in r.data["consentRecords"]:
            self.assertEqual(str(cr["individual"]), str(self.citizen.pk))

    def test_verification_consent_record_detail(self):
        """Consumer can read a single consent record for verification."""
        ConsentService.grant(self.citizen, self.category.slug)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self._auth(self.consumer)
        r = self.client.get(
            f"/api/v1/consent/service/verification/consent-record/{record.pk}/"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecord", r.data)

    def test_verification_consent_record_detail_requires_consumer_scope(self):
        """Plain citizen must receive 403 on verification detail endpoint."""
        ConsentService.grant(self.citizen, self.category.slug)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self._auth(self.citizen)
        r = self.client.get(
            f"/api/v1/consent/service/verification/consent-record/{record.pk}/"
        )
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)


# ===========================================================================
# Audit API
# ===========================================================================

class AuditAPITests(GovStackAPIBase):

    def setUp(self):
        super().setUp()
        self.category = _make_category(slug="audit-test")
        ConsentService.grant(self.citizen, self.category.slug)

    def test_audit_consent_records_list(self):
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/audit/consent-records/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecords", r.data)
        self.assertGreaterEqual(r.data["total"], 1)

    def test_audit_consent_record_detail(self):
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/audit/consent-record/{record.pk}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecord", r.data)

    def test_audit_data_agreements_list(self):
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/audit/data-agreements/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("dataAgreements", r.data)

    def test_audit_data_agreement_detail(self):
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/audit/data-agreement/{self.category.pk}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("dataAgreement", r.data)

    def test_audit_consent_records_requires_auditor_role(self):
        # C-01 fix: citizen tokens must be rejected on audit endpoints (PIPEDA).
        # Only staff or consent_auditors group members may enumerate all consent records.
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/audit/consent-records/")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_audit_consent_records_requires_authentication(self):
        # Unauthenticated requests must still be rejected.
        self._unauth()
        r = self.client.get("/api/v1/consent/audit/consent-records/")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_audit_consent_log(self):
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/audit/consent-log/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentLog", r.data)


# ===========================================================================
# ConsentRevision — integrity
# ===========================================================================

class ConsentRevisionTests(GovStackAPIBase):

    def test_policy_revision_is_append_only(self):
        policy, rev = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        with self.assertRaises(ValueError):
            rev.serialized_hash = "tampered"
            rev.save()

    def test_revision_hash_is_deterministic(self):
        policy, rev = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        expected = rev._compute_hash()
        self.assertEqual(rev.serialized_hash, expected)

    def test_revision_chain_links_predecessor(self):
        policy, rev1 = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        _, rev2 = ConsentService.update_policy(policy, {"version": "2.0"}, actor=self.admin)
        self.assertEqual(rev2.predecessor_hash, rev1.serialized_hash)

    def test_previous_revision_successor_points_to_new(self):
        policy, rev1 = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        _, rev2 = ConsentService.update_policy(policy, {"version": "2.0"}, actor=self.admin)
        rev1.refresh_from_db()
        self.assertEqual(rev1.successor_id, rev2.pk)

    def test_revision_serializer_admin_actor_in_authorized_by_other(self):
        """
        F8 fix: admin/org actors go in authorizedByOther, not authorizedByIndividual.

        authorizedByIndividual is reserved for the citizen/data-subject who
        authorized the consent action.  For Policy and DataAgreement revisions
        (created by admins), the actor must appear in authorizedByOther so the
        GovStack spec field semantics are correct.
        """
        policy, rev = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{policy.pk}/revisions/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        rev_data = r.data["revisions"][0]
        # authorizedByIndividual must be null for admin-initiated revisions
        self.assertIn("authorizedByIndividual", rev_data)
        self.assertIsNone(rev_data["authorizedByIndividual"])
        # authorizedByOther must hold the admin's PK
        self.assertIn("authorizedByOther", rev_data)
        self.assertEqual(str(rev_data["authorizedByOther"]), str(self.admin.pk))

    def test_revision_serializer_includes_successor(self):
        """RevisionSerializer must expose successor (F2a fix)."""
        policy, rev1 = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        _, rev2 = ConsentService.update_policy(policy, {"version": "2.0"}, actor=self.admin)
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{policy.pk}/revisions/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        # First revision (oldest) should have successor pointing to rev2
        revs = sorted(r.data["revisions"], key=lambda x: x["serializedHash"])
        # Find rev1 in the list and check its successor
        rev1_data = next(rv for rv in r.data["revisions"] if rv["id"] == str(rev1.pk))
        self.assertEqual(str(rev1_data["successor"]), str(rev2.pk))

    def test_snapshot_does_not_contain_predecessor_hash_key(self):
        """serializedSnapshot must be a clean object snapshot — no _predecessor_hash (F2b fix)."""
        policy, rev = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        self.assertNotIn("_predecessor_hash", rev.serialized_snapshot)

    def test_revision_serialized_snapshot_is_a_string(self):
        """
        GovStack spec: Revision.serializedSnapshot is type: string, not a
        nested JSON object. Also verifies a client can independently
        recompute serializedHash from serializedSnapshot and get a matching
        value — the whole point of publishing both fields.
        """
        policy, rev = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{policy.pk}/revisions/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        rev_data = next(rv for rv in r.data["revisions"] if rv["id"] == str(rev.pk))

        snapshot_str = rev_data["serializedSnapshot"]
        self.assertIsInstance(snapshot_str, str)

        # Round-trip: json.loads() must reproduce the exact model snapshot.
        self.assertEqual(json.loads(snapshot_str), rev.serialized_snapshot)

        # Independently recompute serializedHash from serializedSnapshot —
        # must match the persisted serializedHash exactly (same hashing
        # scheme as ConsentRevision._compute_hash()).
        recomputed_hash = hashlib.sha256(snapshot_str.encode()).hexdigest()
        self.assertEqual(recomputed_hash, rev_data["serializedHash"])
        self.assertEqual(recomputed_hash, rev.serialized_hash)

    def test_policy_list_includes_pagination(self):
        """ConfigPolicyListView must support offset/limit (F7 fix)."""
        ConsentService.create_policy(
            {"name": "P1", "version": "1.0", "url": "https://example.com/p1"}, actor=self.admin
        )
        ConsentService.create_policy(
            {"name": "P2", "version": "1.0", "url": "https://example.com/p2"}, actor=self.admin
        )
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/policies/?offset=0&limit=1")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data["policies"]), 1)
        self.assertIn("total", r.data)
        self.assertGreaterEqual(r.data["total"], 2)

    def test_revision_serializer_includes_signed_without_object_id(self):
        """RevisionSerializer must expose signedWithoutObjectId (F7/Round-6 fix)."""
        policy, _ = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{policy.pk}/revisions/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        rev_data = r.data["revisions"][0]
        self.assertIn("signedWithoutObjectId", rev_data)

    def test_revision_serializer_includes_predecessor_signature(self):
        """RevisionSerializer must expose predecessorSignature (F7/Round-6 fix)."""
        policy, _ = ConsentService.create_policy(
            {"name": "P", "version": "1.0", "url": "https://example.com"},
            actor=self.admin,
        )
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{policy.pk}/revisions/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        rev_data = r.data["revisions"][0]
        self.assertIn("predecessorSignature", rev_data)


# ===========================================================================
# Service — DataAgreement-scoped consent record
# ===========================================================================

class ServiceDataAgreementConsentRecordTests(GovStackAPIBase):

    def setUp(self):
        super().setUp()
        self.category = _make_category(slug="da-scoped-test")

    def test_create_consent_record_by_da_id(self):
        self._auth(self.citizen)
        r = self.client.post(
            f"/api/v1/consent/service/individual/record/data-agreement/{self.category.pk}/"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data["consentRecord"]["optIn"])

    def test_read_consent_record_by_da_id(self):
        ConsentService.grant(self.citizen, self.category.slug)
        self._auth(self.citizen)
        r = self.client.get(
            f"/api/v1/consent/service/individual/record/data-agreement/{self.category.pk}/"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecord", r.data)

    def test_read_nonexistent_consent_record_returns_404(self):
        self._auth(self.citizen)
        r = self.client.get(
            f"/api/v1/consent/service/individual/record/data-agreement/{self.category.pk}/"
        )
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)


# ===========================================================================
# Service — ConsentRecord Signature (POST create / PUT update)
# GovStack paths: /service/individual/record/consent-record/{id}/signature/
# ===========================================================================

class ConsentRecordSignatureTests(GovStackAPIBase):
    """Tests for POST/PUT /service/individual/record/consent-record/{id}/signature/"""

    def setUp(self):
        super().setUp()
        self.category = _make_category(slug="sig-test-cat")
        ConsentService.grant(self.citizen, self.category.slug)
        self.record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        # Round 9: grant() now auto-creates a ConsentSignature. Delete it so that
        # the explicit POST /signature/ tests can exercise the create path cleanly.
        # The duplicate-prevention test re-creates it by calling POST first.
        from apps.consent.models import ConsentSignature
        ConsentSignature.objects.filter(consent_record=self.record).delete()

    def _sig_url(self):
        return f"/api/v1/consent/service/individual/record/consent-record/{self.record.pk}/signature/"

    def _valid_payload(self):
        return {
            "signature": {
                "payload": '{"consentRecordId": "' + str(self.record.pk) + '"}',
                "signature": "sha256-fakesig",
                "verificationMethod": "string",
                "verificationPayload": '{"id": "' + str(self.record.pk) + '"}',
                "verificationPayloadHash": "abc123",
                "verificationSignedBy": str(self.citizen.pk),
                "timestamp": "2026-07-07T00:00:00Z",
            }
        }

    # --- Auth / permission ---
    def test_signature_requires_auth(self):
        self._unauth()
        r = self.client.post(self._sig_url(), self._valid_payload(), format="json")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cannot_sign_another_users_record(self):
        other = _make_citizen(email="other_sig@example.com")
        self._auth(other)
        r = self.client.post(self._sig_url(), self._valid_payload(), format="json")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    # --- POST create ---
    def test_create_signature(self):
        self._auth(self.citizen)
        r = self.client.post(self._sig_url(), self._valid_payload(), format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("signature", r.data)
        self.assertIsNotNone(r.data["signature"]["id"])
        self.assertEqual(r.data["signature"]["verificationMethod"], "string")

    def test_create_signature_sets_record_state_signed(self):
        self._auth(self.citizen)
        self.client.post(self._sig_url(), self._valid_payload(), format="json")
        self.record.refresh_from_db()
        self.assertEqual(self.record.state, ConsentRecord.STATE_SIGNED)

    def test_create_signature_duplicate_returns_400(self):
        self._auth(self.citizen)
        self.client.post(self._sig_url(), self._valid_payload(), format="json")
        # Second POST on same record should fail
        r = self.client.post(self._sig_url(), self._valid_payload(), format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_signature_missing_required_field_returns_400(self):
        self._auth(self.citizen)
        payload = self._valid_payload()
        del payload["signature"]["verificationPayloadHash"]  # required field
        r = self.client.post(self._sig_url(), payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    # --- PUT update ---
    def test_update_signature(self):
        self._auth(self.citizen)
        # Create first
        self.client.post(self._sig_url(), self._valid_payload(), format="json")
        # Then update
        update_payload = {"signature": {"verificationMethod": "rs256"}}
        r = self.client.put(self._sig_url(), update_payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["signature"]["verificationMethod"], "rs256")

    def test_update_signature_when_none_exists_returns_404(self):
        self._auth(self.citizen)
        r = self.client.put(self._sig_url(), {"signature": {"verificationType": "string"}}, format="json")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    # --- Response envelope conformance ---
    def test_post_consent_record_includes_signature_key(self):
        """POST /service/individual/record/consent-record/ must return {consentRecord, revision, signature}."""
        category2 = _make_category(slug="sig-envelope-test")
        self._auth(self.citizen)
        r = self.client.post(
            "/api/v1/consent/service/individual/record/consent-record/",
            {"consentRecord": {"dataAgreementId": category2.pk}},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecord", r.data)
        self.assertIn("revision", r.data)
        self.assertIn("signature", r.data)
        # Round 9: grant() auto-creates a ConsentSignature, so signature is now non-null
        self.assertIsNotNone(
            r.data["signature"],
            "POST consent-record must return a non-null signature (Round 9 F9 fix)"
        )


# ===========================================================================
# Service — All ConsentRecords for a DataAgreement (/all/ endpoint)
# GovStack path: /service/individual/record/data-agreement/{id}/all/
# ===========================================================================

class DataAgreementAllConsentRecordsTests(GovStackAPIBase):
    """Tests for GET /service/individual/record/data-agreement/{id}/all/"""

    def setUp(self):
        super().setUp()
        self.category = _make_category(slug="all-cr-test")
        self.citizen2 = _make_citizen(email="citizen2_all@example.com")
        # Both citizens grant consent
        ConsentService.grant(self.citizen, self.category.slug)
        ConsentService.grant(self.citizen2, self.category.slug)

    def _all_url(self):
        return f"/api/v1/consent/service/individual/record/data-agreement/{self.category.pk}/all/"

    def test_all_requires_auth(self):
        self._unauth()
        r = self.client.get(self._all_url())
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_all_accessible_by_any_authenticated_individual(self):
        # GovStack spec: security: [{OAuth2: ['individual']}] — citizen tokens are valid.
        # The endpoint returns only the authenticated user's own records.
        self._auth(self.citizen)
        r = self.client.get(self._all_url())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("consentRecords", r.data)
        # citizen has 1 record for this DA; citizen2's record is NOT returned
        self.assertEqual(len(r.data["consentRecords"]), 1)
        self.assertEqual(str(r.data["consentRecords"][0]["individual"]), str(self.citizen.pk))

    def test_all_returns_only_own_records(self):
        # citizen2 has their own record — must not appear in citizen's response
        self._auth(self.citizen)
        r = self.client.get(self._all_url())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        for cr in r.data["consentRecords"]:
            self.assertEqual(str(cr["individual"]), str(self.citizen.pk))

    def test_all_citizen2_sees_only_own_record(self):
        self._auth(self.citizen2)
        r = self.client.get(self._all_url())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data["consentRecords"]), 1)
        self.assertEqual(str(r.data["consentRecords"][0]["individual"]), str(self.citizen2.pk))

    def test_all_nonexistent_da_returns_404(self):
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/service/individual/record/data-agreement/999999/all/")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_all_pagination(self):
        self._auth(self.citizen)
        r = self.client.get(self._all_url(), {"limit": 1, "offset": 0})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data["consentRecords"]), 1)

    def test_all_response_includes_total(self):
        """
        Pagination envelope standardization: this endpoint must include
        "total" = the full unfiltered count, not the page count.
        """
        self._auth(self.citizen)
        r = self.client.get(self._all_url(), {"limit": 1, "offset": 0})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("total", r.data)
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(len(r.data["consentRecords"]), 1)


# ===========================================================================
# Round 9+ fixes — new regression tests
# ===========================================================================

class Round9WebhookDisabledFieldTests(GovStackAPIBase):
    """
    F8: Webhook create must honour the spec 'disabled' field, not legacy 'isActive'.
    The cert harness sends {"disabled": true} and expects the webhook to be disabled.
    """

    def test_create_webhook_with_disabled_true_creates_disabled_webhook(self):
        """Spec-minimal payload with disabled=true must create an is_disabled=True webhook."""
        self._auth(self.admin)
        r = self.client.post("/api/v1/consent/config/webhook/", {
            "webhook": {
                "payloadUrl": "https://example.com/disabled-hook",
                "contentType": "application/json",
                "disabled": True,
                "secretKey": "test-disabled-key",
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webhook = ConsentWebhook.objects.get(pk=r.data["webhook"]["id"])
        self.assertTrue(webhook.is_disabled, "webhook.is_disabled should be True when disabled=true is sent")
        # Response should reflect the disabled state
        self.assertTrue(r.data["webhook"]["disabled"])
        # F15 fix: isActive removed from serializer (was CivicOS extension, not in GovStack spec)
        self.assertNotIn("isActive", r.data["webhook"])

    def test_create_webhook_with_disabled_false_creates_active_webhook(self):
        """disabled=false must create an active webhook."""
        self._auth(self.admin)
        r = self.client.post("/api/v1/consent/config/webhook/", {
            "webhook": {
                "payloadUrl": "https://example.com/active-hook",
                "contentType": "application/json",
                "disabled": False,
                "secretKey": "test-active-key",
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webhook = ConsentWebhook.objects.get(pk=r.data["webhook"]["id"])
        self.assertFalse(webhook.is_disabled)

    def test_update_webhook_with_disabled_field(self):
        """PUT with disabled=true must disable the webhook."""
        webhook = ConsentWebhook.objects.create(
            payload_url="https://example.com/put-test",
            secret_key="k1",
            is_disabled=False,
        )
        self._auth(self.admin)
        r = self.client.put(f"/api/v1/consent/config/webhook/{webhook.pk}/", {
            "webhook": {
                "payloadUrl": "https://example.com/put-test",
                "secretKey": "k1",
                "disabled": True,
            }
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        webhook.refresh_from_db()
        self.assertTrue(webhook.is_disabled)


class Round9GrantAutoSignatureTests(GovStackAPIBase):
    """
    F9: POST /service/individual/record/consent-record/ must return a non-null
    signature. ConsentService.grant() must auto-create a ConsentSignature.
    """

    def setUp(self):
        super().setUp()
        self.category = _make_category(slug="grant-sig-test")

    def test_grant_via_service_api_returns_non_null_signature(self):
        """POST consent-record must return signature != null (Round 9 / F9 fix)."""
        self._auth(self.citizen)
        r = self.client.post(
            "/api/v1/consent/service/individual/record/consent-record/",
            {"consentRecord": {"dataAgreementId": self.category.pk}},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("signature", r.data)
        self.assertIsNotNone(
            r.data["signature"],
            "POST consent-record must return a non-null signature object"
        )

    def test_grant_service_creates_consent_signature_record(self):
        """ConsentService.grant() must persist a ConsentSignature row."""
        from apps.consent.models import ConsentSignature
        ConsentService.grant(self.citizen, self.category.slug)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        self.assertTrue(
            ConsentSignature.objects.filter(consent_record=record).exists(),
            "ConsentService.grant() must auto-create a ConsentSignature"
        )

    def test_grant_idempotent_updates_signature(self):
        """Calling grant() twice on the same record must not raise an error."""
        from apps.consent.models import ConsentSignature
        ConsentService.grant(self.citizen, self.category.slug)
        ConsentService.grant(self.citizen, self.category.slug)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        # Exactly one signature should exist (update_or_create is idempotent)
        self.assertEqual(
            ConsentSignature.objects.filter(consent_record=record).count(), 1
        )

    def test_grant_signature_has_string_verification_type(self):
        """Auto-created signature must use verificationMethod='string'."""
        from apps.consent.models import ConsentSignature
        ConsentService.grant(self.citizen, self.category.slug)
        record = ConsentRecord.objects.get(citizen=self.citizen, category=self.category)
        sig = ConsentSignature.objects.get(consent_record=record)
        self.assertEqual(sig.verification_type, "string")


class Round9RevisionIdParamTests(GovStackAPIBase):
    """
    F3: revisionId query param on policy detail and draft endpoints must be honoured.
    """

    def setUp(self):
        super().setUp()
        self.policy, self.rev1 = ConsentService.create_policy(
            {"name": "RevId Test Policy", "version": "1.0", "url": "https://example.com/p"},
            actor=self.admin,
        )
        _, self.rev2 = ConsentService.update_policy(
            self.policy, {"version": "2.0"}, actor=self.admin
        )

    def test_policy_detail_without_revision_id_returns_latest(self):
        """No ?revisionId → latest revision (successor=None)."""
        self._auth(self.admin)
        r = self.client.get(f"/api/v1/consent/config/policy/{self.policy.pk}/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["revision"]["id"], str(self.rev2.pk))

    def test_policy_detail_with_revision_id_returns_specific_revision(self):
        """?revisionId=<rev1_id> → returns rev1, not the latest."""
        self._auth(self.admin)
        r = self.client.get(
            f"/api/v1/consent/config/policy/{self.policy.pk}/?revisionId={self.rev1.pk}"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["revision"]["id"], str(self.rev1.pk))

    def test_policy_detail_with_invalid_revision_id_returns_404(self):
        """Non-existent revisionId → 404."""
        self._auth(self.admin)
        r = self.client.get(
            f"/api/v1/consent/config/policy/{self.policy.pk}/?revisionId={uuid.uuid4()}"
        )
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)


class Round9RTBFRequiredGuardTests(GovStackAPIBase):
    """
    F12: RTBF must not delete records for required categories even if they are
    also marked forgettable (contradictory but possible DB state).
    """

    def test_rtbf_does_not_delete_required_forgettable_records(self):
        """A category with both is_required=True and forgettable=True must NOT be deleted by RTBF."""
        required_and_forgettable = _make_category(
            slug="rtbf-req-forget",
            is_required=True,
            forgettable=True,
        )
        # Grant consent bypassing the is_required withdrawal check (grant always succeeds)
        ConsentService.grant(self.citizen, required_and_forgettable.slug)
        self._auth(self.citizen)
        r = self.client.delete("/api/v1/consent/service/individual/record/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        # The required record must still exist
        self.assertTrue(
            ConsentRecord.objects.filter(
                citizen=self.citizen, category=required_and_forgettable
            ).exists(),
            "RTBF must not delete records for required categories even if forgettable=True"
        )
        self.assertEqual(r.data["deleted_count"], 0)


class AuditEndpointAuthorizationTests(APITestCase):
    """
    C-01 fix verification: /audit/ endpoints must reject citizen tokens.
    Only staff users and consent_auditors group members may read audit data.
    """

    def setUp(self):
        self.citizen = User.objects.create_user(
            email=f"au-citizen-{uuid.uuid4().hex[:6]}@example.gov",
            password="SecureTest123!",
        )
        self.auditor = User.objects.create_user(
            email=f"au-auditor-{uuid.uuid4().hex[:6]}@example.gov",
            password="SecureTest123!",
            is_staff=False,
        )
        from django.contrib.auth.models import Group
        grp, _ = Group.objects.get_or_create(name="consent_auditors")
        self.auditor.groups.add(grp)
        self.staff = User.objects.create_user(
            email=f"au-staff-{uuid.uuid4().hex[:6]}@example.gov",
            password="SecureTest123!",
            is_staff=True,
        )

    def test_citizen_cannot_list_audit_consent_records(self):
        self.client.force_authenticate(user=self.citizen)
        r = self.client.get("/api/v1/consent/audit/consent-records/")
        self.assertEqual(r.status_code, 403)

    def test_citizen_cannot_read_audit_data_agreements(self):
        self.client.force_authenticate(user=self.citizen)
        r = self.client.get("/api/v1/consent/audit/data-agreements/")
        self.assertEqual(r.status_code, 403)

    def test_auditor_group_can_list_consent_records(self):
        self.client.force_authenticate(user=self.auditor)
        r = self.client.get("/api/v1/consent/audit/consent-records/")
        self.assertEqual(r.status_code, 200)

    def test_auditor_group_can_list_data_agreements(self):
        self.client.force_authenticate(user=self.auditor)
        r = self.client.get("/api/v1/consent/audit/data-agreements/")
        self.assertEqual(r.status_code, 200)

    def test_staff_can_access_all_audit_endpoints(self):
        self.client.force_authenticate(user=self.staff)
        r = self.client.get("/api/v1/consent/audit/consent-records/")
        self.assertEqual(r.status_code, 200)

    def test_unauthenticated_gets_401(self):
        r = self.client.get("/api/v1/consent/audit/consent-records/")
        self.assertEqual(r.status_code, 401)


# ===========================================================================
# Malformed path-ID routing (fix for the 400-vs-raw-404 regression)
#
# The live upstream Gherkin harness (bb-consent/test/gherkin/features/
# data_agreement.feature) expects HTTP 400 with a JSON error body for a
# malformed path ID. Since govstack_urls.py now routes ALL of these segments
# with <str:> converters (validation moved into the view via _parse_uuid_param
# / _parse_int_param), a bad ID must produce a DRF ValidationError → JSON 400,
# not Django's raw, un-routed, HTML 404.
# ===========================================================================

class MalformedPathIdRoutingTests(GovStackAPIBase):

    def test_config_policy_detail_malformed_uuid_returns_400(self):
        """UUID-typed route, config namespace."""
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/policy/not-a-uuid/")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsInstance(r.data, (dict, list))

    def test_service_data_agreement_detail_malformed_int_returns_400(self):
        """Integer-typed route, service namespace."""
        self._auth(self.citizen)
        r = self.client.get("/api/v1/consent/service/data-agreement/abc/")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsInstance(r.data, (dict, list))

    def test_audit_consent_record_detail_malformed_uuid_returns_400(self):
        """UUID-typed route, audit namespace."""
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/audit/consent-record/not-a-uuid/")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsInstance(r.data, (dict, list))

    def test_audit_data_agreement_detail_malformed_int_returns_400(self):
        """Integer-typed route, audit namespace."""
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/audit/data-agreement/xyz/")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_config_webhook_detail_malformed_uuid_returns_400(self):
        """UUID-typed route, config namespace (webhook)."""
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/webhook/not-a-uuid/")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_service_individual_record_consent_record_malformed_uuid_returns_400(self):
        """UUID-typed route, service namespace (ConsentRecord)."""
        self._auth(self.citizen)
        r = self.client.get(
            "/api/v1/consent/service/individual/record/consent-record/not-a-uuid/"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_config_data_agreement_detail_valid_but_nonexistent_id_still_404s(self):
        """
        Sanity check: a well-formed but nonexistent int ID must still 404
        (NOT 400) — only malformed IDs get the 400 treatment.
        """
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/data-agreement/999999/")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)


# ===========================================================================
# Pagination envelope standardization — "total" must be present on every
# list endpoint (the full unfiltered count, not the page count).
# ===========================================================================

class PaginationTotalFieldTests(GovStackAPIBase):

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        self.category = _make_category(slug="total-field-test")
        self.consumer = _make_citizen(email="total-consumer@example.com")
        group, _ = Group.objects.get_or_create(name="data_consumers")
        self.consumer.groups.add(group)

    def test_config_individuals_list_includes_total(self):
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/individuals/", {"limit": 1})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("total", r.data)
        self.assertGreaterEqual(r.data["total"], 2)  # at least admin + citizen
        self.assertEqual(len(r.data["individuals"]), 1)

    def test_config_webhooks_list_includes_total(self):
        ConsentWebhook.objects.create(
            payload_url="https://example.com/hook1",
            secret_key="s1",
        )
        ConsentWebhook.objects.create(
            payload_url="https://example.com/hook2",
            secret_key="s2",
        )
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/config/webhooks/", {"limit": 1})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("total", r.data)
        self.assertEqual(r.data["total"], 2)
        self.assertEqual(len(r.data["webhooks"]), 1)

    def test_service_individuals_list_includes_total(self):
        self._auth(self.admin)
        r = self.client.get("/api/v1/consent/service/individuals/", {"limit": 1})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("total", r.data)
        self.assertGreaterEqual(r.data["total"], 2)
        self.assertEqual(len(r.data["individuals"]), 1)

    def test_verification_data_agreements_list_includes_total(self):
        _make_category(slug="total-field-test-2")
        self._auth(self.consumer)
        r = self.client.get(
            "/api/v1/consent/service/verification/data-agreements/", {"limit": 1}
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("total", r.data)
        self.assertGreaterEqual(r.data["total"], 2)
        self.assertEqual(len(r.data["dataAgreements"]), 1)

    def test_verification_consent_records_list_includes_total(self):
        ConsentService.grant(self.citizen, self.category.slug)
        other = _make_citizen()
        other_category = _make_category(slug="total-field-test-cr2")
        ConsentService.grant(other, other_category.slug)
        self._auth(self.consumer)
        r = self.client.get(
            "/api/v1/consent/service/verification/consent-records/", {"limit": 1}
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("total", r.data)
        self.assertGreaterEqual(r.data["total"], 2)
        self.assertEqual(len(r.data["consentRecords"]), 1)

    def test_service_individual_consent_record_list_includes_total(self):
        cat2 = _make_category(slug="total-field-test-cr3")
        ConsentService.grant(self.citizen, self.category.slug)
        ConsentService.grant(self.citizen, cat2.slug)
        self._auth(self.citizen)
        r = self.client.get(
            "/api/v1/consent/service/individual/record/consent-record/", {"limit": 1}
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("total", r.data)
        self.assertEqual(r.data["total"], 2)
        self.assertEqual(len(r.data["consentRecords"]), 1)

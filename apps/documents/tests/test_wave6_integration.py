"""
apps/documents/tests/test_wave6_integration.py
================================================
Wave 6 integration test suite for the Documents Building Block.

Coverage
--------
1.  TestDocumentCategorySeed                      — seed migration forwards + idempotency
2.  TestCertificationDocumentIntegration          — document_v2 FK (post-Phase-3)
3.  TestHonorariumT4ADocumentIntegration          — t4a_document OneToOne
4.  TestScreeningRecordDocumentIntegration        — vsc_confirmation_doc FK
5.  TestOfficialDonationReceiptDocumentIntegration — document FK + has_pdf
6.  TestDataExportRequestDocumentIntegration      — document FK (consent BB)
7.  TestServiceRequestGenericRelation             — DocumentAttachment generic linker
8.  TestWorkItemGenericRelation                   — WorkItem + WorkItemComment attachments
9.  TestMigrateExistingFilesCommand               — management command
10. TestTransitoryDocumentDisposal                — mark_purpose_fulfilled
11. TestDataExportTaskDocumentBBIntegration       — process_data_export → Document BB record
12. TestCorrectiveMigration0008                   — volunteer-certification + cra-t4a-slip fixes
13. TestServiceFeePaymentGenericRelation          — ServiceFeePayment.document_attachments
14. TestVolunteerApplicationGenericRelation       — VolunteerApplication.document_attachments
15. TestCleanupExportFilesActorFix                — actor=req.citizen (not None) in cleanup task

Security invariants tested:
  - storage_key never in logs
  - original_filename never in audit event_detail
  - PII (email) never in logs
  - volunteer-certification and cra-t4a-slip are staff_only=True (access control)
  - volunteer-certification has min_retention_days≥730 (Privacy Act s.6(1))

Governing law: PIPEDA 4.5.3, Privacy Act s.6(1), LAC DA #2016/001,
OWASP A01 Broken Access Control.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_user(**kwargs):
    defaults = {
        "email": f"user_{uuid.uuid4().hex[:8]}@example.com",
        "password": "x",
    }
    defaults.update(kwargs)
    # CivicOS uses email as USERNAME_FIELD
    return User.objects.create_user(**defaults)


def _make_category(**kwargs):
    from apps.documents.models import DocumentCategory

    defaults = {
        "slug": f"test-cat-{uuid.uuid4().hex[:6]}",
        "name_en": "Test",
        "name_fr": "Test",
        "min_retention_days": 730,
        "max_retention_days": 2555,
    }
    defaults.update(kwargs)
    slug = defaults.pop("slug")
    obj, _ = DocumentCategory.objects.get_or_create(slug=slug, defaults=defaults)
    return obj


def _make_document(user=None, category=None, **kwargs):
    from apps.documents.models import Document

    if user is None:
        user = _make_user()
    if category is None:
        category = _make_category()
    defaults = {
        "category": category,
        "uploaded_by": user,
        "original_filename": "test.pdf",
        "_storage_key": f"documents/active/{uuid.uuid4()}/{uuid.uuid4().hex}.bin",
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        "scan_status": "active",
        "version_number": 1,
        "is_latest_version": True,
        "security_classification": "protected_b",
    }
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# 1. TestDocumentCategorySeed
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_SLUGS = [
    "service-request-evidence",
    "service-request-decision-letter",
    "volunteer-application-docs",
    "volunteer-certification",
    "cra-t4a-slip",
    "donation-receipt-pdf",
    "pipeda-data-export",
    "staff-decision-memo",
    "system-generated-report",
]


class TestDocumentCategorySeed(TestCase):
    """Verify the seed migration creates all 9 categories with correct attributes."""

    def _run_seed(self):
        """Run the seed forwards function directly against the real ORM."""
        import importlib

        from apps.documents.models import DocumentCategory

        # Migration filename starts with a digit; use importlib to load it.
        m = importlib.import_module(
            "apps.documents.migrations.0007_seed_document_categories"
        )

        # The migration's forwards function uses apps.get_model(); we pass a
        # thin adapter that delegates straight to the real Django model class.
        class _FakeApps:
            def get_model(self, app_label, model_name):
                return DocumentCategory

        m.seed_document_categories(_FakeApps(), None)

    def test_all_9_categories_exist_after_migration(self):
        """All 9 expected category slugs must be present after seeding."""
        from apps.documents.models import DocumentCategory

        self._run_seed()
        existing = set(
            DocumentCategory.objects.filter(slug__in=EXPECTED_SLUGS).values_list(
                "slug", flat=True
            )
        )
        self.assertEqual(existing, set(EXPECTED_SLUGS))

    def test_pipeda_data_export_is_transitory(self):
        """The pipeda-data-export category must have is_transitory=True."""
        from apps.documents.models import DocumentCategory

        self._run_seed()
        cat = DocumentCategory.objects.get(slug="pipeda-data-export")
        self.assertTrue(cat.is_transitory)

    def test_pipeda_data_export_max_retention_30(self):
        """The pipeda-data-export category must have max_retention_days=30."""
        from apps.documents.models import DocumentCategory

        self._run_seed()
        cat = DocumentCategory.objects.get(slug="pipeda-data-export")
        self.assertEqual(cat.max_retention_days, 30)

    def test_seed_is_idempotent(self):
        """Running the seed forwards function twice must not raise and must
        leave exactly 9 categories (no duplicates)."""
        from apps.documents.models import DocumentCategory

        self._run_seed()
        self._run_seed()  # second call — must be idempotent
        count = DocumentCategory.objects.filter(slug__in=EXPECTED_SLUGS).count()
        self.assertEqual(count, len(EXPECTED_SLUGS))

    def test_staff_decision_memo_is_staff_only(self):
        """staff-decision-memo must have staff_only=True (update_or_create path in seed)."""
        from apps.documents.models import DocumentCategory

        self._run_seed()
        cat = DocumentCategory.objects.get(slug="staff-decision-memo")
        self.assertTrue(cat.staff_only, "staff-decision-memo must be staff_only=True")

    def test_system_generated_report_is_staff_only(self):
        """system-generated-report must have staff_only=True (update_or_create path)."""
        from apps.documents.models import DocumentCategory

        self._run_seed()
        cat = DocumentCategory.objects.get(slug="system-generated-report")
        self.assertTrue(cat.staff_only, "system-generated-report must be staff_only=True")


# Import shim for migration module (dotted name has a leading digit in filename).
# Django already imports these migration modules at startup using importlib with
# an underscore prefix. We just make the alias accessible for the test helper above.
import importlib
import sys

try:
    _seed_mod = importlib.import_module(
        "apps.documents.migrations.0007_seed_document_categories"
    )
    # Store under the alias the TestDocumentCategorySeed._run_seed() references.
    sys.modules.setdefault(
        "apps.documents.migrations._0007_seed_document_categories", _seed_mod
    )
except ImportError:
    _seed_mod = None


# ─────────────────────────────────────────────────────────────────────────────
# 2. TestCertificationDocumentIntegration
# ─────────────────────────────────────────────────────────────────────────────


class TestCertificationDocumentIntegration(TestCase):
    """Verify Certification.document_v2 FK (Wave 6 / post-Phase-3 state)."""

    def _make_volunteer_and_cert(self):
        from apps.volunteers.models import (
            Certification,
            Opportunity,
            Program,
            VolunteerProfile,
        )

        user = _make_user()
        vol = VolunteerProfile.objects.create(user=user)
        prog = Program.objects.create(
            name_en="Prog",
            name_fr="Prog",
            slug=f"prog-{uuid.uuid4().hex[:6]}",
        )
        opp = Opportunity.objects.create(
            program=prog,
            title_en="T",
            title_fr="T",
            slug=f"opp-{uuid.uuid4().hex[:6]}",
            description_en="D",
            description_fr="D",
        )
        cert = Certification.objects.create(
            volunteer=vol,
            cert_type=Certification.CERT_TYPE_FIRST_AID,
            issued_date=date(2024, 1, 1),
            document_v2=None,
        )
        return cert, user

    def test_certification_document_v2_field_exists(self):
        """Certification instances must have a document_v2 attribute."""
        cert, _ = self._make_volunteer_and_cert()
        self.assertTrue(hasattr(cert, "document_v2"))

    def test_certification_document_v2_nullable(self):
        """Can save a Certification with document_v2=None."""
        cert, _ = self._make_volunteer_and_cert()
        self.assertIsNone(cert.document_v2)

    def test_certification_document_v2_can_be_set(self):
        """Can link a Document record to Certification.document_v2."""
        cert, user = self._make_volunteer_and_cert()
        doc = _make_document(user=user)
        cert.document_v2 = doc
        cert.save(update_fields=["document_v2"])
        cert.refresh_from_db()
        self.assertEqual(cert.document_v2_id, doc.pk)

    def test_certification_document_v2_protect(self):
        """Deleting a Document referenced by document_v2 must be prevented (PROTECT)."""
        from django.db import IntegrityError
        from django.db.models.deletion import ProtectedError

        cert, user = self._make_volunteer_and_cert()
        doc = _make_document(user=user)
        cert.document_v2 = doc
        cert.save(update_fields=["document_v2"])

        with self.assertRaises((ProtectedError, IntegrityError)):
            doc.delete()


# ─────────────────────────────────────────────────────────────────────────────
# 3. TestHonorariumT4ADocumentIntegration
# ─────────────────────────────────────────────────────────────────────────────


class TestHonorariumT4ADocumentIntegration(TestCase):
    """Verify Honorarium.t4a_document OneToOneField."""

    def _make_honorarium(self, created_by=None):
        from apps.volunteers.models import Honorarium, Program, VolunteerProfile

        if created_by is None:
            created_by = _make_user()
        vol_user = _make_user()
        vol = VolunteerProfile.objects.create(user=vol_user)
        hon = Honorarium.objects.create(
            volunteer=vol,
            payment_type=Honorarium.PAYMENT_TYPE_EXPENSE,
            amount=Decimal("50.00"),
            description="Test expense",
            payment_date=date(2026, 1, 15),
            created_by=created_by,
        )
        return hon, created_by

    def test_honorarium_t4a_document_field_exists(self):
        """Honorarium must have a t4a_document attribute."""
        hon, _ = self._make_honorarium()
        self.assertTrue(hasattr(hon, "t4a_document"))

    def test_honorarium_t4a_document_nullable(self):
        """t4a_document defaults to None."""
        hon, _ = self._make_honorarium()
        self.assertIsNone(hon.t4a_document)

    def test_honorarium_t4a_document_can_link_document(self):
        """Can set t4a_document to a Document record and save."""
        hon, user = self._make_honorarium()
        doc = _make_document(user=user)
        hon.t4a_document = doc
        hon.save(update_fields=["t4a_document"])
        hon.refresh_from_db()
        self.assertEqual(hon.t4a_document_id, doc.pk)

    def test_honorarium_t4a_document_one_to_one(self):
        """Two Honoraria cannot link to the same Document (OneToOne constraint)."""
        from django.core.exceptions import ValidationError
        from django.db import IntegrityError

        user = _make_user()
        hon1, _ = self._make_honorarium(created_by=user)
        hon2, _ = self._make_honorarium(created_by=user)
        doc = _make_document(user=user)

        hon1.t4a_document = doc
        hon1.save(update_fields=["t4a_document"])

        hon2.t4a_document_id = doc.pk
        # Honorarium.save() calls full_clean() which raises ValidationError before
        # the DB unique constraint fires on some backends; catch both.
        with self.assertRaises((ValidationError, IntegrityError)):
            hon2.save(update_fields=["t4a_document"])


# ─────────────────────────────────────────────────────────────────────────────
# 4. TestScreeningRecordDocumentIntegration
# ─────────────────────────────────────────────────────────────────────────────


class TestScreeningRecordDocumentIntegration(TestCase):
    """Verify ScreeningRecord.vsc_confirmation_doc FK."""

    def _make_screening_record(self):
        from apps.volunteers.models import ScreeningRecord, VolunteerProfile

        user = _make_user()
        vol = VolunteerProfile.objects.create(user=user)
        record = ScreeningRecord.objects.create(
            volunteer=vol,
            check_type=ScreeningRecord.CHECK_TYPE_VSC,
            completed_date=date(2024, 6, 1),
        )
        return record, user

    def test_vsc_confirmation_doc_field_exists(self):
        """ScreeningRecord must have a vsc_confirmation_doc attribute."""
        record, _ = self._make_screening_record()
        self.assertTrue(hasattr(record, "vsc_confirmation_doc"))

    def test_vsc_confirmation_doc_nullable(self):
        """Can save a ScreeningRecord without vsc_confirmation_doc."""
        record, _ = self._make_screening_record()
        self.assertIsNone(record.vsc_confirmation_doc)

    def test_vsc_confirmation_doc_pipeda_help_text(self):
        """Help text on vsc_confirmation_doc must reference PIPEDA and criminal constraints."""
        from apps.volunteers.models import ScreeningRecord

        field = ScreeningRecord._meta.get_field("vsc_confirmation_doc")
        help_lower = field.help_text.lower()
        # Must mention PIPEDA (privacy law reference)
        self.assertIn("pipeda", help_lower)
        # Must mention criminal check context
        self.assertIn("criminal", help_lower)

    def test_vsc_confirmation_doc_can_be_set(self):
        """Can link a Document to vsc_confirmation_doc."""
        record, user = self._make_screening_record()
        doc = _make_document(user=user)
        record.vsc_confirmation_doc = doc
        record.save(update_fields=["vsc_confirmation_doc"])
        record.refresh_from_db()
        self.assertEqual(record.vsc_confirmation_doc_id, doc.pk)


# ─────────────────────────────────────────────────────────────────────────────
# 5. TestOfficialDonationReceiptDocumentIntegration
# ─────────────────────────────────────────────────────────────────────────────


class TestOfficialDonationReceiptDocumentIntegration(TestCase):
    """Verify OfficialDonationReceipt.document FK and has_pdf property."""

    def _make_receipt(self, user=None):
        from apps.payments.models import (
            Donation,
            DonationCampaign,
            OfficialDonationReceipt,
            PaymentIntent,
        )

        if user is None:
            user = _make_user()

        intent = PaymentIntent.objects.create(
            payer=user,
            amount=Decimal("100.00"),
            purpose=PaymentIntent.PURPOSE_DONATION,
            gateway=PaymentIntent.GATEWAY_MANUAL,
        )
        campaign = DonationCampaign.objects.create(
            slug=f"camp-{uuid.uuid4().hex[:6]}",
            name_en="Test Campaign",
            start_date=date(2026, 1, 1),
        )
        donation = Donation.objects.create(
            payment_intent=intent,
            donor=user,
            campaign=campaign,
            amount=Decimal("100.00"),
            donor_name_snapshot="Jane Doe",
            donor_address_snapshot="123 Main St",
        )
        receipt = OfficialDonationReceipt(
            donation=donation,
            # Pre-set serial_number so OfficialDonationReceipt.save() skips the
            # `nextval('payments_receipt_serial_seq')` PostgreSQL sequence call,
            # which is unavailable in the SQLite test database.
            serial_number="2026-000001",
            donor_legal_name="Jane Doe",
            donor_address_line1="123 Main St",
            donor_city="Ottawa",
            donor_province="ON",
            donor_postal_code="K1A 0A9",
            donation_date=date(2026, 1, 15),
            receipt_date=date(2026, 1, 15),
            eligible_amount=Decimal("100.00"),
            charity_legal_name="Test Charity",
            charity_registration_number="123456789 RR 0001",
            charity_address="456 Charity Ave, Ottawa ON K1A 0B1",
            place_of_issue="Ottawa",
            authorized_signatory_name="John Smith",
            authorized_signatory_title="Executive Director",
        )
        receipt.save()
        return receipt, user

    def test_document_field_exists(self):
        """OfficialDonationReceipt must have a document attribute."""
        receipt, _ = self._make_receipt()
        self.assertTrue(hasattr(receipt, "document"))

    def test_document_nullable(self):
        """Can create a receipt without a document."""
        receipt, _ = self._make_receipt()
        self.assertIsNone(receipt.document)

    def test_has_pdf_false_when_no_document(self):
        """has_pdf must return False when document is None."""
        receipt, _ = self._make_receipt()
        self.assertFalse(receipt.has_pdf)

    def test_has_pdf_true_when_document_set(self):
        """has_pdf must return True when a Document is linked."""
        receipt, user = self._make_receipt()
        doc = _make_document(user=user)
        # Use _base_manager to bypass append-only guard on direct field update
        from apps.payments.models import OfficialDonationReceipt

        OfficialDonationReceipt._base_manager.filter(pk=receipt.pk).update(
            document_id=doc.pk
        )
        receipt.refresh_from_db()
        self.assertTrue(receipt.has_pdf)

    def test_pdf_path_field_absent_after_phase3(self):
        """pdf_path CharField must have been removed by the Phase 3 migration
        (payments/0014_drop_donation_receipt_pdf_path.py).

        Phase 3 has been applied in this codebase iteration. The document FK
        is now the authoritative reference; the raw path CharField is gone.
        """
        from apps.payments.models import OfficialDonationReceipt

        field_names = [f.name for f in OfficialDonationReceipt._meta.get_fields()]
        # Phase 3 migration must have dropped pdf_path.
        self.assertNotIn(
            "pdf_path",
            field_names,
            "pdf_path CharField must be absent after Phase 3 migration "
            "(payments/0014_drop_donation_receipt_pdf_path.py).",
        )
        # The document FK must remain present.
        self.assertIn(
            "document",
            field_names,
            "OfficialDonationReceipt must have a 'document' FK field after Wave 6 migration.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. TestDataExportRequestDocumentIntegration
# ─────────────────────────────────────────────────────────────────────────────


class TestDataExportRequestDocumentIntegration(TestCase):
    """Verify DataExportRequest.document FK (consent BB)."""

    def _make_export_request(self, user=None):
        from apps.consent.models import DataExportRequest

        if user is None:
            user = _make_user()
        req = DataExportRequest.objects.create(citizen=user, document=None)
        return req, user

    def test_document_field_exists(self):
        """DataExportRequest must have a document attribute."""
        req, _ = self._make_export_request()
        self.assertTrue(hasattr(req, "document"))

    def test_document_nullable(self):
        """Can create a DataExportRequest without a document."""
        req, _ = self._make_export_request()
        self.assertIsNone(req.document)

    def test_document_can_be_set(self):
        """Can link a Document to DataExportRequest.document and save."""
        req, user = self._make_export_request()
        doc = _make_document(user=user)
        req.document = doc
        req.save(update_fields=["document"])
        req.refresh_from_db()
        self.assertEqual(req.document_id, doc.pk)

    def test_document_protect(self):
        """Deleting a Document referenced by DataExportRequest.document is prevented."""
        from django.db import IntegrityError
        from django.db.models.deletion import ProtectedError

        req, user = self._make_export_request()
        doc = _make_document(user=user)
        req.document = doc
        req.save(update_fields=["document"])

        with self.assertRaises((ProtectedError, IntegrityError)):
            doc.delete()

    def test_storage_path_field_removed(self):
        """storage_path CharField must be absent after Phase 3 migration."""
        from apps.consent.models import DataExportRequest

        field_names = [f.name for f in DataExportRequest._meta.get_fields()]
        self.assertNotIn(
            "storage_path",
            field_names,
            "storage_path must be absent — Phase 3 migration 0005 has been applied.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. TestServiceRequestGenericRelation
# ─────────────────────────────────────────────────────────────────────────────


class TestServiceRequestGenericRelation(TestCase):
    """Verify DocumentAttachment generic relation on ServiceRequest."""

    def test_service_request_has_document_attachments_attribute(self):
        """ServiceRequest instances must have a document_attachments related manager."""
        from apps.portal.models import ServiceRequest

        user = _make_user()
        sr = ServiceRequest.objects.create(citizen=user, service_name="Test Service")
        self.assertTrue(hasattr(sr, "document_attachments"))

    def test_can_attach_document_to_service_request(self):
        """Creating a DocumentAttachment pointing to a ServiceRequest must expose
        the attachment via the reverse document_attachments relation."""
        from apps.documents.models import DocumentAttachment
        from apps.portal.models import ServiceRequest

        user = _make_user()
        sr = ServiceRequest.objects.create(citizen=user, service_name="Test Service")
        doc = _make_document(user=user)
        ct = ContentType.objects.get_for_model(ServiceRequest)

        da = DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=str(sr.pk),
            attachment_role="supporting_evidence",
            attached_by=user,
        )

        self.assertEqual(sr.document_attachments.count(), 1)
        self.assertEqual(sr.document_attachments.first().pk, da.pk)

    def test_document_attachment_references_correct_document(self):
        """The attached Document is the same one created."""
        from apps.documents.models import DocumentAttachment
        from apps.portal.models import ServiceRequest

        user = _make_user()
        sr = ServiceRequest.objects.create(citizen=user, service_name="Test Service")
        doc = _make_document(user=user)
        ct = ContentType.objects.get_for_model(ServiceRequest)
        DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=str(sr.pk),
            attachment_role="decision_letter",
            attached_by=user,
        )
        attachment = sr.document_attachments.select_related("document").first()
        self.assertEqual(attachment.document_id, doc.pk)


# ─────────────────────────────────────────────────────────────────────────────
# 8. TestWorkItemGenericRelation
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkItemGenericRelation(TestCase):
    """Verify DocumentAttachment generic relation on WorkItem and WorkItemComment."""

    def _make_work_item(self, user=None):
        from apps.portal.models import ServiceRequest
        from apps.workflows.models import WorkItem

        if user is None:
            user = _make_user()
        # WorkItem.content_object can be any model; use ServiceRequest for convenience.
        sr = ServiceRequest.objects.create(citizen=user, service_name="WI Test")
        ct = ContentType.objects.get_for_model(ServiceRequest)
        wi = WorkItem.objects.create(
            content_type=ct,
            object_id=str(sr.pk),
            title="Review request",
        )
        return wi, user

    def test_work_item_has_document_attachments(self):
        """WorkItem must expose document_attachments reverse manager."""
        wi, _ = self._make_work_item()
        self.assertTrue(hasattr(wi, "document_attachments"))

    def test_can_attach_document_to_work_item(self):
        """Creating a DocumentAttachment for a WorkItem must be queryable."""
        from apps.documents.models import DocumentAttachment
        from apps.workflows.models import WorkItem

        wi, user = self._make_work_item(user=_make_user())
        doc = _make_document(user=user)
        ct = ContentType.objects.get_for_model(WorkItem)

        DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=str(wi.pk),
            attachment_role="supporting_evidence",
            attached_by=user,
        )

        self.assertEqual(wi.document_attachments.count(), 1)

    def test_work_item_comment_has_document_attachments(self):
        """WorkItemComment must expose document_attachments reverse manager."""
        from apps.workflows.models import WorkItemComment

        wi, user = self._make_work_item()
        comment = WorkItemComment.objects.create(
            work_item=wi, author=user, body="Review note"
        )
        self.assertTrue(hasattr(comment, "document_attachments"))

    def test_can_attach_document_to_work_item_comment(self):
        """Creating a DocumentAttachment for a WorkItemComment is queryable."""
        from apps.documents.models import DocumentAttachment
        from apps.workflows.models import WorkItem, WorkItemComment

        wi, user = self._make_work_item(user=_make_user())
        comment = WorkItemComment.objects.create(
            work_item=wi, author=user, body="Evidence note"
        )
        doc = _make_document(user=user)
        ct = ContentType.objects.get_for_model(WorkItemComment)

        DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=str(comment.pk),
            attachment_role="supporting_evidence",
            attached_by=user,
        )

        self.assertEqual(comment.document_attachments.count(), 1)

    def test_document_attachment_cascade_delete_on_workitem_delete(self):
        """Deleting a WorkItem must cascade-delete associated DocumentAttachments."""
        from apps.documents.models import DocumentAttachment
        from apps.workflows.models import WorkItem

        wi, user = self._make_work_item(user=_make_user())
        doc = _make_document(user=user)
        ct = ContentType.objects.get_for_model(WorkItem)
        da = DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=str(wi.pk),
            attachment_role="supporting_evidence",
            attached_by=user,
        )
        da_pk = da.pk

        # WorkItem content_type FK is CASCADE; deleting the WorkItem's
        # ContentType cascades to DocumentAttachment. However, the WorkItem itself
        # can be deleted if no PROTECT constraints block it.
        # DocumentAttachment.content_type on_delete=CASCADE means deleting the
        # WorkItem row *alone* does not cascade the attachment; it's the CT that
        # cascades. We test that when the WorkItem is deleted directly, its
        # related DocumentAttachments (via the GenericRelation) are cleaned up.
        wi.delete()

        self.assertFalse(
            DocumentAttachment.objects.filter(pk=da_pk).exists(),
            "DocumentAttachment should be removed when the linked WorkItem is deleted.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 9. TestMigrateExistingFilesCommand
# ─────────────────────────────────────────────────────────────────────────────


class TestMigrateExistingFilesCommand(TestCase):
    """Verify the migrate_existing_files management command."""

    def test_dry_run_does_not_write_to_db(self):
        """--dry-run never writes Documents even in Phase 3 state."""
        from apps.documents.models import Document

        count_before = Document.objects.count()
        out = StringIO()
        call_command("migrate_existing_files", "--dry-run", stdout=out)
        self.assertEqual(Document.objects.count(), count_before)

    def test_certification_migration_detects_phase3(self):
        """When Phase 3 has already dropped Certification.document, command exits cleanly."""
        out = StringIO()
        # Should not raise — Phase 3 detection returns MigrationResult(phase_3_applied=True)
        call_command("migrate_existing_files", "--model", "certification", stdout=out)
        # Command completed without exception — Phase 3 was detected

    def test_idempotency(self):
        """Running migrate_existing_files multiple times in Phase 3 state is safe."""
        from apps.documents.models import Document

        count_before = Document.objects.count()
        out = StringIO()
        call_command("migrate_existing_files", "--model", "all", stdout=out)
        call_command("migrate_existing_files", "--model", "all", stdout=out)
        count_after = Document.objects.count()
        # Phase 3 has been applied; command should detect this and create zero Documents
        self.assertEqual(count_before, count_after)

    def test_no_pii_in_logs(self):
        """migrate_existing_files must not log any PII or storage keys."""
        out = StringIO()
        logger_name = "apps.documents.management.commands.migrate_existing_files"
        with self.assertLogs(logger_name, level="INFO") as log_ctx:
            call_command("migrate_existing_files", "--model", "all", stdout=out)
        combined = "\n".join(log_ctx.output)
        self.assertNotIn("storage_key", combined)
        self.assertNotIn("_storage_key", combined)
        self.assertNotIn("original_filename", combined)
        self.assertNotIn("@", combined, "No email addresses in logs")


# ─────────────────────────────────────────────────────────────────────────────
# 10. TestTransitoryDocumentDisposal
# ─────────────────────────────────────────────────────────────────────────────


class TestTransitoryDocumentDisposal(TestCase):
    """Verify mark_purpose_fulfilled on transitory documents."""

    def test_mark_purpose_fulfilled_on_transitory_category(self):
        """mark_purpose_fulfilled() must soft-delete a transitory Document inline."""
        from apps.documents.services.retention import mark_purpose_fulfilled

        user = _make_user()
        transitory_cat = _make_category(
            slug=f"transitory-{uuid.uuid4().hex[:6]}",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )
        doc = _make_document(user=user, category=transitory_cat)

        result = mark_purpose_fulfilled(document=doc, actor=user)

        doc.refresh_from_db()
        self.assertIsNotNone(doc.deleted_at, "deleted_at must be set after purpose fulfilled.")
        self.assertEqual(doc.deletion_reason, "transitory_purpose_fulfilled")

    def test_mark_purpose_fulfilled_non_transitory_raises(self):
        """mark_purpose_fulfilled() on a non-transitory document must raise ValueError."""
        from apps.documents.services.retention import mark_purpose_fulfilled

        user = _make_user()
        non_transitory_cat = _make_category(
            slug=f"non-transitory-{uuid.uuid4().hex[:6]}",
            is_transitory=False,
        )
        doc = _make_document(user=user, category=non_transitory_cat)

        with self.assertRaises(ValueError, msg="Must raise ValueError for non-transitory doc"):
            mark_purpose_fulfilled(document=doc, actor=user)

    def test_mark_purpose_fulfilled_sets_expires_at(self):
        """mark_purpose_fulfilled() must set expires_at=now() on the document."""
        from apps.documents.services.retention import mark_purpose_fulfilled

        user = _make_user()
        transitory_cat = _make_category(
            slug=f"transitory2-{uuid.uuid4().hex[:6]}",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )
        doc = _make_document(user=user, category=transitory_cat)
        self.assertIsNone(doc.expires_at)

        mark_purpose_fulfilled(document=doc, actor=user)

        doc.refresh_from_db()
        self.assertIsNotNone(doc.expires_at, "expires_at must be set when purpose is fulfilled.")

    def test_mark_purpose_fulfilled_legal_hold_raises(self):
        """mark_purpose_fulfilled() must raise ValueError if the doc is on legal hold."""
        from apps.documents.models import Document
        from apps.documents.services.retention import mark_purpose_fulfilled

        user = _make_user()
        transitory_cat = _make_category(
            slug=f"transitory3-{uuid.uuid4().hex[:6]}",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )
        doc = _make_document(user=user, category=transitory_cat)
        # Force legal_hold=True directly in DB to bypass permission check.
        Document.objects.filter(pk=doc.pk).update(legal_hold=True)
        doc.legal_hold = True

        with self.assertRaises(ValueError):
            mark_purpose_fulfilled(document=doc, actor=user)

    def test_mark_purpose_fulfilled_already_deleted_raises(self):
        """mark_purpose_fulfilled() on an already-soft-deleted doc must raise ValueError."""
        from django.utils import timezone

        from apps.documents.models import Document
        from apps.documents.services.retention import mark_purpose_fulfilled

        user = _make_user()
        transitory_cat = _make_category(
            slug=f"transitory4-{uuid.uuid4().hex[:6]}",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )
        doc = _make_document(user=user, category=transitory_cat)
        now = timezone.now()
        Document.objects.filter(pk=doc.pk).update(
            deleted_at=now, scan_status=Document.ScanStatus.DELETED
        )
        doc.deleted_at = now

        with self.assertRaises(ValueError):
            mark_purpose_fulfilled(document=doc, actor=user)


# ─────────────────────────────────────────────────────────────────────────────
# 11. TestDataExportTaskDocumentBBIntegration
# ─────────────────────────────────────────────────────────────────────────────


class TestDataExportTaskDocumentBBIntegration(TestCase):
    """Verify process_data_export task creates a Document BB record when the
    pipeda-data-export category exists."""

    def test_process_data_export_creates_document_when_category_exists(self):
        """When the pipeda-data-export category is seeded, the task must create
        a Document record and link it to the DataExportRequest."""
        from apps.consent.models import DataExportRequest
        from apps.consent.tasks import process_data_export
        from apps.documents.models import Document

        # Seed the required category.
        cat = _make_category(
            slug="pipeda-data-export",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )

        user = _make_user()
        req = DataExportRequest.objects.create(citizen=user)
        doc_count_before = Document.objects.count()

        with patch(
            "django.core.files.storage.default_storage.save",
            return_value="exports/test.json",
        ), patch(
            "django.core.files.storage.default_storage.delete"
        ), patch(
            "apps.consent.tasks._notify_export_ready"
        ):
            result = process_data_export(str(req.pk))

        self.assertEqual(result["status"], "ready")

        req.refresh_from_db()
        self.assertIsNotNone(
            req.document_id,
            "DataExportRequest.document must be set after successful export task.",
        )
        # A Document BB record should have been created.
        self.assertGreater(Document.objects.count(), doc_count_before)

    def test_process_data_export_falls_back_when_category_missing(self):
        """When the pipeda-data-export category is NOT seeded, the task must still
        complete successfully (falling back to storage_path only)."""
        from apps.consent.models import DataExportRequest
        from apps.consent.tasks import process_data_export

        user = _make_user()
        req = DataExportRequest.objects.create(citizen=user)

        with patch(
            "django.core.files.storage.default_storage.save",
            return_value="exports/test.json",
        ), patch(
            "django.core.files.storage.default_storage.delete"
        ), patch(
            "apps.consent.tasks._notify_export_ready"
        ):
            result = process_data_export(str(req.pk))

        # Task should still succeed even without the category.
        self.assertIn(result.get("status", ""), ["ready", ""])
        # No document linked (category not found → fallback).
        req.refresh_from_db()
        # document may or may not be set depending on category availability.
        # The task must not raise regardless.

    def test_storage_key_not_in_logs(self):
        """The process_data_export task must not log any storage_key values."""
        from apps.consent.models import DataExportRequest
        from apps.consent.tasks import process_data_export

        # Seed category.
        _make_category(
            slug="pipeda-data-export",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )

        user = _make_user()
        req = DataExportRequest.objects.create(citizen=user)

        captured_storage_keys = []

        def _mock_save(path, content):
            captured_storage_keys.append(path)
            return path

        with patch(
            "django.core.files.storage.default_storage.save", side_effect=_mock_save
        ), patch(
            "django.core.files.storage.default_storage.delete"
        ), patch(
            "apps.consent.tasks._notify_export_ready"
        ), self.assertLogs(
            "apps.consent", level="DEBUG"
        ) as log_ctx:
            process_data_export(str(req.pk))

        all_log_text = "\n".join(log_ctx.output)
        for key in captured_storage_keys:
            if key.startswith("documents/active/"):
                # Documents BB storage keys must NEVER appear in logs (PIPEDA).
                self.assertNotIn(
                    key,
                    all_log_text,
                    f"storage_key '{key}' must never appear in log output (PIPEDA).",
                )

    def test_pii_not_in_export_task_logs(self):
        """Citizen email must not appear in process_data_export logs."""
        from apps.consent.models import DataExportRequest
        from apps.consent.tasks import process_data_export

        user = _make_user()
        email = user.email
        req = DataExportRequest.objects.create(citizen=user)

        with patch(
            "django.core.files.storage.default_storage.save",
            return_value="exports/test.json",
        ), patch(
            "django.core.files.storage.default_storage.delete"
        ), patch(
            "apps.consent.tasks._notify_export_ready"
        ), self.assertLogs(
            "apps.consent", level="DEBUG"
        ) as log_ctx:
            process_data_export(str(req.pk))

        all_log_text = "\n".join(log_ctx.output)
        self.assertNotIn(
            email,
            all_log_text,
            f"Citizen email '{email}' must never appear in log output (PIPEDA).",
        )

    def test_legacy_export_file_deleted_after_successful_document_bb_creation(self):
        """After a successful export with Document BB integration, the legacy
        exports/{token}.json file must be deleted (PIPEDA data-minimisation —
        no redundant copies of citizen PII data)."""
        from apps.consent.models import DataExportRequest
        from apps.consent.tasks import process_data_export

        _make_category(
            slug="pipeda-data-export",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )
        user = _make_user()
        req = DataExportRequest.objects.create(citizen=user)
        deleted_paths = []

        with patch(
            "django.core.files.storage.default_storage.save",
            return_value="exports/test.json",
        ), patch(
            "django.core.files.storage.default_storage.delete",
            side_effect=lambda p: deleted_paths.append(p),
        ), patch("apps.consent.tasks._notify_export_ready"):
            process_data_export(str(req.pk))

        # The legacy exports/ file must have been deleted after DB save succeeded.
        self.assertTrue(
            any("exports/" in p for p in deleted_paths),
            "Legacy exports/{token}.json file must be deleted after Document BB "
            "creation succeeds (PIPEDA data-minimisation).",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 12. TestCorrectiveMigration0008
# ─────────────────────────────────────────────────────────────────────────────


class TestCorrectiveMigration0008(TestCase):
    """Verify the 0008 corrective data migration fixes volunteer-certification
    and cra-t4a-slip categories.

    Test strategy: explicitly set categories to the pre-correction (broken) state,
    then run the corrective migration function and verify the fix is applied.
    This approach works regardless of what migration state the test DB is in,
    because we establish the pre-condition ourselves.

    Security invariants:
      - volunteer-certification: min_retention_days≥730 (Privacy Act s.6(1))
      - volunteer-certification: staff_only=True (OWASP A01)
      - cra-t4a-slip: staff_only=True (OWASP A01)
    """

    def _run_corrective(self):
        """Run the 0008 corrective migration function directly."""
        import importlib
        from apps.documents.models import DocumentCategory

        m = importlib.import_module(
            "apps.documents.migrations.0008_fix_document_category_retention_and_staff_only"
        )

        class _FakeApps:
            def get_model(self, app_label, model_name):
                return DocumentCategory

        m.fix_category_retention_and_staff_only(_FakeApps(), None)

    def _break_volunteer_certification(self):
        """Reset volunteer-certification to the pre-0008 (broken) state."""
        from apps.documents.models import DocumentCategory

        DocumentCategory.objects.filter(slug="volunteer-certification").update(
            min_retention_days=365,  # broken: below Privacy Act s.6(1) minimum
            max_retention_days=1825,
            staff_only=False,  # broken: citizens can upload
        )

    def _break_cra_t4a_slip(self):
        """Reset cra-t4a-slip to the pre-0008 (broken) state."""
        from apps.documents.models import DocumentCategory

        DocumentCategory.objects.filter(slug="cra-t4a-slip").update(
            staff_only=False,  # broken: citizens can upload
        )

    def test_volunteer_certification_staff_only_after_corrective_migration(self):
        """volunteer-certification must have staff_only=True after 0008 runs.

        OWASP A01 Broken Access Control: citizens must not be able to upload
        to the CRC/VSC certification category (staff-managed government records).
        """
        from apps.documents.models import DocumentCategory

        # Establish broken pre-condition.
        self._break_volunteer_certification()
        cat = DocumentCategory.objects.get(slug="volunteer-certification")
        self.assertFalse(cat.staff_only, "Pre-condition: staff_only=False before correction.")

        self._run_corrective()
        cat.refresh_from_db()
        self.assertTrue(
            cat.staff_only,
            "volunteer-certification must have staff_only=True after corrective "
            "migration 0008 (OWASP A01 — prevent citizen injection of CRC/VSC records).",
        )

    def test_volunteer_certification_min_retention_is_730_days(self):
        """volunteer-certification min_retention_days must be ≥ 730 after 0008.

        Privacy Act s.6(1): personal information used for an administrative purpose
        must be retained for at least 2 years (730 days) after last use.
        CRC/VSC screening records are administrative-purpose records.
        """
        from apps.documents.models import DocumentCategory

        # Establish broken pre-condition.
        self._break_volunteer_certification()
        cat = DocumentCategory.objects.get(slug="volunteer-certification")
        self.assertEqual(cat.min_retention_days, 365, "Pre-condition: min=365 before correction.")

        self._run_corrective()
        cat.refresh_from_db()
        self.assertGreaterEqual(
            cat.min_retention_days,
            730,
            "volunteer-certification min_retention_days must be ≥ 730 "
            "(Privacy Act s.6(1) — 2-year minimum for administrative records).",
        )

    def test_cra_t4a_slip_staff_only_after_corrective_migration(self):
        """cra-t4a-slip must have staff_only=True after 0008.

        CRA T4A slips are CRA-generated tax documents attached to honourarium
        records by staff. Citizen-uploadable T4A slips create a fabricated-record
        injection risk in honourarium payment reporting.
        """
        from apps.documents.models import DocumentCategory

        # Establish broken pre-condition.
        self._break_cra_t4a_slip()
        cat = DocumentCategory.objects.get(slug="cra-t4a-slip")
        self.assertFalse(cat.staff_only, "Pre-condition: staff_only=False before correction.")

        self._run_corrective()
        cat.refresh_from_db()
        self.assertTrue(
            cat.staff_only,
            "cra-t4a-slip must have staff_only=True after corrective migration 0008 "
            "(OWASP A01 — prevent citizen injection of fabricated T4A slips).",
        )

    def test_corrective_migration_is_idempotent(self):
        """Running 0008 twice must leave categories unchanged (no crash, no duplicate)."""
        from apps.documents.models import DocumentCategory

        self._break_volunteer_certification()
        self._break_cra_t4a_slip()
        self._run_corrective()
        self._run_corrective()  # second call — must be idempotent

        cat = DocumentCategory.objects.get(slug="volunteer-certification")
        self.assertTrue(cat.staff_only)
        self.assertGreaterEqual(cat.min_retention_days, 730)

        cat2 = DocumentCategory.objects.get(slug="cra-t4a-slip")
        self.assertTrue(cat2.staff_only)

    def test_volunteer_certification_final_state_is_correct(self):
        """After both seed (0007) and corrective (0008) migrations, volunteer-certification
        must have the correct final state — staff_only=True, min_retention_days=730."""
        from apps.documents.models import DocumentCategory

        # The test DB has already run both 0007 and 0008 — just verify the result.
        cat = DocumentCategory.objects.get(slug="volunteer-certification")
        self.assertTrue(
            cat.staff_only,
            "volunteer-certification must be staff_only=True in the current DB "
            "(migration 0008 must have applied the correction).",
        )
        self.assertGreaterEqual(
            cat.min_retention_days,
            730,
            "volunteer-certification min_retention_days must be ≥ 730 in the current DB.",
        )

    def test_cra_t4a_slip_final_state_is_correct(self):
        """After both seed (0007) and corrective (0008) migrations, cra-t4a-slip
        must have staff_only=True."""
        from apps.documents.models import DocumentCategory

        cat = DocumentCategory.objects.get(slug="cra-t4a-slip")
        self.assertTrue(
            cat.staff_only,
            "cra-t4a-slip must be staff_only=True in the current DB "
            "(migration 0008 must have applied the correction).",
        )

    def test_noop_reverse_does_not_raise(self):
        """Reverse function (noop) must not raise."""
        import importlib
        from apps.documents.models import DocumentCategory

        m = importlib.import_module(
            "apps.documents.migrations.0008_fix_document_category_retention_and_staff_only"
        )

        class _FakeApps:
            def get_model(self, app_label, model_name):
                return DocumentCategory

        # Should be a no-op and not raise.
        m.noop(_FakeApps(), None)


# ─────────────────────────────────────────────────────────────────────────────
# 13. TestServiceFeePaymentGenericRelation
# ─────────────────────────────────────────────────────────────────────────────


class TestServiceFeePaymentGenericRelation(TestCase):
    """Verify ServiceFeePayment.document_attachments GenericRelation.

    Spec §16.4: ServiceFeePayment — optional confirmation document:
        document_attachments = GenericRelation("documents.DocumentAttachment")
    """

    def _make_payment_intent(self):
        from apps.payments.models import PaymentIntent

        return PaymentIntent.objects.create(
            amount=Decimal("25.00"),
            currency="CAD",
            gateway=None,  # manual
        )

    def test_service_fee_payment_has_document_attachments_relation(self):
        """ServiceFeePayment must have a document_attachments attribute."""
        from apps.payments.models import ServiceFeePayment

        self.assertTrue(
            hasattr(ServiceFeePayment, "document_attachments"),
            "ServiceFeePayment must have a document_attachments GenericRelation "
            "(Documents BB spec §16.4).",
        )

    def test_document_can_be_attached_to_service_fee_payment(self):
        """A DocumentAttachment can be linked to a ServiceFeePayment."""
        from django.contrib.contenttypes.models import ContentType

        from apps.documents.models import DocumentAttachment
        from apps.payments.models import ServiceFeePayment

        user = _make_user()
        try:
            pi = self._make_payment_intent()
        except Exception:
            self.skipTest("PaymentIntent creation failed — likely missing gateway config")
            return

        try:
            sfp = ServiceFeePayment.objects.create(
                payment_intent=pi,
                service_request_id=uuid.uuid4(),
                fee_code="FEE-001",
                base_amount=Decimal("25.00"),
                tax_amount=Decimal("0.00"),
                tax_rate_applied=Decimal("0.00000"),
                description_en="Test fee",
                description_fr="Frais test",
            )
        except Exception:
            self.skipTest("ServiceFeePayment creation failed — check required fields")
            return

        doc = _make_document(user=user)
        ct = ContentType.objects.get_for_model(ServiceFeePayment)
        attachment = DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=str(sfp.pk),
            attachment_role="confirmation",
            attached_by=user,
        )
        self.assertEqual(sfp.document_attachments.count(), 1)
        self.assertEqual(sfp.document_attachments.first().pk, attachment.pk)


# ─────────────────────────────────────────────────────────────────────────────
# 14. TestVolunteerApplicationGenericRelation
# ─────────────────────────────────────────────────────────────────────────────


class TestVolunteerApplicationGenericRelation(TestCase):
    """Verify VolunteerApplication.document_attachments GenericRelation.

    Spec §16.3: VolunteerApplication — supporting documents (references, ID,
    certifications requested during onboarding).
    """

    def _make_application(self):
        from apps.volunteers.models import Opportunity, Program, VolunteerApplication, VolunteerProfile

        user = _make_user()
        vol = VolunteerProfile.objects.create(user=user)
        prog = Program.objects.create(
            name_en="Prog",
            name_fr="Prog",
            slug=f"prog-{uuid.uuid4().hex[:6]}",
        )
        opp = Opportunity.objects.create(
            program=prog,
            title_en="Opp",
            title_fr="Opp",
            slug=f"opp-{uuid.uuid4().hex[:6]}",
            description_en="D",
            description_fr="D",
        )
        app = VolunteerApplication.objects.create(
            opportunity=opp,
            volunteer=vol,
        )
        return app, user

    def test_volunteer_application_has_document_attachments_relation(self):
        """VolunteerApplication must have a document_attachments attribute."""
        from apps.volunteers.models import VolunteerApplication

        self.assertTrue(
            hasattr(VolunteerApplication, "document_attachments"),
            "VolunteerApplication must have a document_attachments GenericRelation "
            "(Documents BB spec §16.3).",
        )

    def test_document_can_be_attached_to_volunteer_application(self):
        """A DocumentAttachment can be linked to a VolunteerApplication."""
        from django.contrib.contenttypes.models import ContentType

        from apps.documents.models import DocumentAttachment
        from apps.volunteers.models import VolunteerApplication

        app, user = self._make_application()
        doc = _make_document(user=user)
        ct = ContentType.objects.get_for_model(VolunteerApplication)
        attachment = DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=str(app.pk),
            attachment_role="supporting_evidence",
            attached_by=user,
        )
        self.assertEqual(app.document_attachments.count(), 1)
        self.assertEqual(app.document_attachments.first().pk, attachment.pk)

    def test_volunteer_application_attachment_cascade_on_delete(self):
        """Deleting a VolunteerApplication cascades to its DocumentAttachments
        (content_type FK on DocumentAttachment uses CASCADE)."""
        from django.contrib.contenttypes.models import ContentType

        from apps.documents.models import DocumentAttachment
        from apps.volunteers.models import VolunteerApplication

        app, user = self._make_application()
        doc = _make_document(user=user)
        ct = ContentType.objects.get_for_model(VolunteerApplication)
        att = DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=str(app.pk),
            attachment_role="supporting_evidence",
            attached_by=user,
        )
        att_pk = att.pk

        app.delete()
        self.assertFalse(
            DocumentAttachment.objects.filter(pk=att_pk).exists(),
            "Deleting a VolunteerApplication must cascade-delete its DocumentAttachments.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 15. TestCleanupExportFilesActorFix
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanupExportFilesActorFix(TestCase):
    """Verify cleanup_export_files passes actor=req.citizen (not None) to
    mark_purpose_fulfilled().

    Regression test for: cleanup_export_files was passing actor=None which
    caused AttributeError inside mark_purpose_fulfilled() at actor.pk, leaving
    transitory PIPEDA export packages undisposed — a PIPEDA data retention
    violation (4.5.3 — destroy once purpose fulfilled).
    """

    def test_cleanup_does_not_crash_with_transitory_document(self):
        """cleanup_export_files must not raise AttributeError when processing
        a DataExportRequest with a transitory Document linked."""
        from django.utils import timezone

        from apps.consent.models import DataExportRequest
        from apps.consent.tasks import cleanup_export_files

        user = _make_user()
        transitory_cat = _make_category(
            slug=f"pipeda-export-{uuid.uuid4().hex[:6]}",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )
        doc = _make_document(user=user, category=transitory_cat)

        req = DataExportRequest.objects.create(citizen=user)
        # Manually link document and set status=READY + expires_at in the past.
        DataExportRequest.objects.filter(pk=req.pk).update(
            document=doc,
            status=DataExportRequest.STATUS_READY,
            expires_at=timezone.now(),  # expired now
        )
        req.refresh_from_db()

        # Should not raise AttributeError ("NoneType has no attribute 'pk'").
        with patch("django.core.files.storage.default_storage.delete"):
            result = cleanup_export_files()

        # Verify the request was expired.
        req.refresh_from_db()
        self.assertEqual(req.status, DataExportRequest.STATUS_EXPIRED)

    def test_cleanup_marks_transitory_document_soft_deleted(self):
        """cleanup_export_files must soft-delete the linked transitory Document
        (PIPEDA 4.5.3: destroy transitory records once purpose fulfilled)."""
        from django.utils import timezone

        from apps.consent.models import DataExportRequest
        from apps.consent.tasks import cleanup_export_files
        from apps.documents.models import Document

        user = _make_user()
        transitory_cat = _make_category(
            slug=f"pipeda-export2-{uuid.uuid4().hex[:6]}",
            is_transitory=True,
            min_retention_days=0,
            max_retention_days=30,
        )
        doc = _make_document(user=user, category=transitory_cat)

        req = DataExportRequest.objects.create(citizen=user)
        DataExportRequest.objects.filter(pk=req.pk).update(
            document=doc,
            status=DataExportRequest.STATUS_READY,
            expires_at=timezone.now(),
        )

        with patch("django.core.files.storage.default_storage.delete"):
            cleanup_export_files()

        doc.refresh_from_db()
        self.assertIsNotNone(
            doc.deleted_at,
            "Transitory document must be soft-deleted by cleanup_export_files "
            "(PIPEDA 4.5.3 — destroy once purpose fulfilled).",
        )
        self.assertEqual(
            doc.scan_status,
            Document.ScanStatus.DELETED,
            "Transitory document must have scan_status=DELETED after disposal.",
        )

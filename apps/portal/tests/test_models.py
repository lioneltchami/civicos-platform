"""Tests for portal models: ServiceRequest, StatusUpdate."""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.portal.models import ServiceRequest, ServiceRequestStatus, StatusUpdate

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_user(email="citizen@example.com"):
    return User.objects.create_user(email=email, password=VALID_PASSWORD)


def make_request(citizen=None, status=ServiceRequestStatus.SUBMITTED, **kwargs):
    if citizen is None:
        citizen = make_user(f"u{uuid.uuid4().hex[:6]}@example.com")
    return ServiceRequest.objects.create(
        citizen=citizen,
        service_name=kwargs.get("service_name", "Pothole Repair"),
        submission_data=kwargs.get("submission_data", {"description": "Large pothole on Main St"}),
        status=status,
    )


class ReferenceNumberTest(TestCase):
    def test_reference_number_auto_generated_on_save(self):
        sr = make_request()
        self.assertIsNotNone(sr.reference_number)
        self.assertTrue(sr.reference_number.startswith("GS-"))

    def test_reference_number_format_gs_year_suffix(self):
        sr = make_request()
        year = str(timezone.now().year)
        parts = sr.reference_number.split("-")
        self.assertEqual(parts[0], "GS")
        self.assertEqual(parts[1], year)
        self.assertEqual(len(parts[2]), 6)

    def test_reference_number_is_unique(self):
        sr1 = make_request()
        sr2 = make_request()
        self.assertNotEqual(sr1.reference_number, sr2.reference_number)

    def test_reference_number_not_overwritten_on_resave(self):
        sr = make_request()
        original_ref = sr.reference_number
        sr.status = ServiceRequestStatus.IN_REVIEW
        sr.save(update_fields=["status"])
        sr.refresh_from_db()
        self.assertEqual(sr.reference_number, original_ref)

    def test_str_returns_reference_and_service_name(self):
        sr = make_request()
        self.assertIn(sr.reference_number, str(sr))
        self.assertIn("Pothole Repair", str(sr))


class ServiceRequestStatusTest(TestCase):
    def test_can_be_cancelled_when_submitted(self):
        sr = make_request(status=ServiceRequestStatus.SUBMITTED)
        self.assertTrue(sr.can_be_cancelled())

    def test_can_be_cancelled_when_in_review(self):
        sr = make_request(status=ServiceRequestStatus.IN_REVIEW)
        self.assertTrue(sr.can_be_cancelled())

    def test_cannot_be_cancelled_when_approved(self):
        sr = make_request(status=ServiceRequestStatus.APPROVED)
        self.assertFalse(sr.can_be_cancelled())

    def test_cannot_be_cancelled_when_rejected(self):
        sr = make_request(status=ServiceRequestStatus.REJECTED)
        self.assertFalse(sr.can_be_cancelled())

    def test_is_terminal_approved(self):
        sr = make_request(status=ServiceRequestStatus.APPROVED)
        self.assertTrue(sr.is_terminal())

    def test_is_terminal_rejected(self):
        sr = make_request(status=ServiceRequestStatus.REJECTED)
        self.assertTrue(sr.is_terminal())

    def test_is_terminal_closed(self):
        sr = make_request(status=ServiceRequestStatus.CLOSED)
        self.assertTrue(sr.is_terminal())

    def test_is_not_terminal_submitted(self):
        sr = make_request(status=ServiceRequestStatus.SUBMITTED)
        self.assertFalse(sr.is_terminal())

    def test_is_not_terminal_in_review(self):
        sr = make_request(status=ServiceRequestStatus.IN_REVIEW)
        self.assertFalse(sr.is_terminal())

    def test_status_display_class_returns_string(self):
        sr = make_request(status=ServiceRequestStatus.APPROVED)
        self.assertIsInstance(sr.status_display_class, str)
        self.assertIn("green", sr.status_display_class)

    def test_status_display_class_submitted_is_blue(self):
        sr = make_request(status=ServiceRequestStatus.SUBMITTED)
        self.assertIn("blue", sr.status_display_class)

    def test_status_display_class_rejected_is_red(self):
        sr = make_request(status=ServiceRequestStatus.REJECTED)
        self.assertIn("red", sr.status_display_class)

    def test_can_be_cancelled_when_awaiting_info(self):
        sr = make_request(status=ServiceRequestStatus.AWAITING_INFO)
        self.assertTrue(sr.can_be_cancelled())

    def test_cannot_be_cancelled_when_closed(self):
        sr = make_request(status=ServiceRequestStatus.CLOSED)
        self.assertFalse(sr.can_be_cancelled())


class StatusUpdateImmutabilityTest(TestCase):
    def test_status_update_cannot_be_modified(self):
        citizen = make_user()
        sr = make_request(citizen=citizen)
        update = StatusUpdate.objects.create(
            service_request=sr,
            old_status="",
            new_status=ServiceRequestStatus.SUBMITTED,
            changed_by=citizen,
            public_note="Created",
        )
        update.public_note = "Tampered"
        with self.assertRaises(ValueError):
            update.save()

    def test_status_update_str_contains_reference_number(self):
        citizen = make_user()
        sr = make_request(citizen=citizen)
        update = StatusUpdate.objects.create(
            service_request=sr,
            old_status="",
            new_status=ServiceRequestStatus.SUBMITTED,
            changed_by=citizen,
            public_note="Initial",
        )
        self.assertIn(sr.reference_number, str(update))

    def test_status_update_can_be_created(self):
        citizen = make_user()
        sr = make_request(citizen=citizen)
        update = StatusUpdate.objects.create(
            service_request=sr,
            old_status=ServiceRequestStatus.SUBMITTED,
            new_status=ServiceRequestStatus.IN_REVIEW,
            changed_by=citizen,
            public_note="Under review",
        )
        self.assertIsNotNone(update.pk)

    def test_status_update_linked_to_service_request(self):
        citizen = make_user()
        sr = make_request(citizen=citizen)
        StatusUpdate.objects.create(
            service_request=sr,
            old_status="",
            new_status=ServiceRequestStatus.SUBMITTED,
            changed_by=citizen,
            public_note="",
        )
        self.assertEqual(sr.status_updates.count(), 1)

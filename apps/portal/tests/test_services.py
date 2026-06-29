"""Tests for the portal service layer."""
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.portal.models import ServiceRequest, ServiceRequestStatus, StatusUpdate
from apps.portal.services import (
    cancel_service_request,
    create_service_request,
    get_citizen_requests,
    update_request_status,
)

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_user(email=None):
    email = email or f"u{uuid.uuid4().hex[:6]}@example.com"
    return User.objects.create_user(email=email, password=VALID_PASSWORD)


def make_service_request(citizen=None, **kwargs):
    """Create a ServiceRequest via the service layer with side-effects suppressed."""
    if citizen is None:
        citizen = make_user()
    with patch("apps.portal.services._fire_notification"), \
         patch("apps.portal.services._write_audit"):
        return create_service_request(
            citizen,
            kwargs.get("service_name", "Pothole Repair"),
            kwargs.get("submission_data", {"description": "Big hole"}),
        )


class CreateServiceRequestTest(TestCase):
    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_creates_service_request(self, mock_audit, mock_notify):
        citizen = make_user()
        sr = create_service_request(citizen, "Pothole Repair", {"description": "Big hole"})
        self.assertIsNotNone(sr.pk)
        self.assertEqual(sr.citizen, citizen)
        self.assertEqual(sr.service_name, "Pothole Repair")
        self.assertEqual(sr.status, ServiceRequestStatus.SUBMITTED)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_generates_reference_number(self, mock_audit, mock_notify):
        citizen = make_user()
        sr = create_service_request(citizen, "Test Service", {})
        self.assertTrue(sr.reference_number.startswith("GS-"))

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_creates_initial_status_update(self, mock_audit, mock_notify):
        citizen = make_user()
        sr = create_service_request(citizen, "Test", {})
        self.assertEqual(sr.status_updates.count(), 1)
        update = sr.status_updates.first()
        self.assertEqual(update.new_status, ServiceRequestStatus.SUBMITTED)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_initial_status_update_has_empty_old_status(self, mock_audit, mock_notify):
        citizen = make_user()
        sr = create_service_request(citizen, "Test", {})
        update = sr.status_updates.first()
        self.assertEqual(update.old_status, "")

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_sets_expiry_date(self, mock_audit, mock_notify):
        citizen = make_user()
        sr = create_service_request(citizen, "Test", {})
        self.assertIsNotNone(sr.expires_at)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_stores_submission_data(self, mock_audit, mock_notify):
        citizen = make_user()
        data = {"description": "My issue", "contact_email": "c@example.com"}
        sr = create_service_request(citizen, "Test", data)
        self.assertEqual(sr.submission_data, data)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_fires_notification(self, mock_audit, mock_notify):
        citizen = make_user()
        with self.captureOnCommitCallbacks(execute=True):
            create_service_request(citizen, "Test", {})
        mock_notify.assert_called_once()

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_writes_audit_log(self, mock_audit, mock_notify):
        citizen = make_user()
        with self.captureOnCommitCallbacks(execute=True):
            create_service_request(citizen, "Test", {})
        mock_audit.assert_called_once()

    def test_raises_for_anonymous_user(self):
        class AnonUser:
            pk = None

        with self.assertRaises(ValueError):
            create_service_request(AnonUser(), "Test", {})

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_stores_service_page_id_when_provided(self, mock_audit, mock_notify):
        citizen = make_user()
        sr = create_service_request(citizen, "Test", {}, service_page_id=42)
        self.assertEqual(sr.service_page_id, 42)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_service_page_id_defaults_to_none(self, mock_audit, mock_notify):
        citizen = make_user()
        sr = create_service_request(citizen, "Test", {})
        self.assertIsNone(sr.service_page_id)


class UpdateRequestStatusTest(TestCase):
    def setUp(self):
        self.staff = make_user("staff@gov.ca")
        self.staff.is_staff = True
        self.staff.save()
        self.citizen = make_user()
        self.sr = make_service_request(citizen=self.citizen)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_updates_status(self, mock_audit, mock_notify):
        update_request_status(self.sr, ServiceRequestStatus.IN_REVIEW, self.staff, "Under review")
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status, ServiceRequestStatus.IN_REVIEW)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_creates_status_update_record(self, mock_audit, mock_notify):
        before_count = self.sr.status_updates.count()
        update_request_status(self.sr, ServiceRequestStatus.IN_REVIEW, self.staff)
        self.assertEqual(self.sr.status_updates.count(), before_count + 1)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_status_update_records_old_status(self, mock_audit, mock_notify):
        old_status = self.sr.status
        result = update_request_status(self.sr, ServiceRequestStatus.IN_REVIEW, self.staff)
        self.assertEqual(result.old_status, old_status)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_status_update_records_new_status(self, mock_audit, mock_notify):
        result = update_request_status(self.sr, ServiceRequestStatus.IN_REVIEW, self.staff)
        self.assertEqual(result.new_status, ServiceRequestStatus.IN_REVIEW)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_status_update_records_public_note(self, mock_audit, mock_notify):
        result = update_request_status(
            self.sr, ServiceRequestStatus.IN_REVIEW, self.staff, "We are looking into it."
        )
        self.assertEqual(result.public_note, "We are looking into it.")

    def test_raises_for_invalid_status(self):
        with self.assertRaises(ValueError):
            update_request_status(self.sr, "invalid_status", self.staff)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_cannot_transition_from_terminal_status(self, mock_audit, mock_notify):
        update_request_status(self.sr, ServiceRequestStatus.APPROVED, self.staff)
        self.sr.refresh_from_db()
        with self.assertRaises(ValueError):
            update_request_status(self.sr, ServiceRequestStatus.IN_REVIEW, self.staff)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_fires_notification_on_status_change(self, mock_audit, mock_notify):
        with self.captureOnCommitCallbacks(execute=True):
            update_request_status(self.sr, ServiceRequestStatus.IN_REVIEW, self.staff)
        mock_notify.assert_called_once()

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_writes_audit_on_status_change(self, mock_audit, mock_notify):
        with self.captureOnCommitCallbacks(execute=True):
            update_request_status(self.sr, ServiceRequestStatus.IN_REVIEW, self.staff)
        mock_audit.assert_called_once()


class CancelServiceRequestTest(TestCase):
    def setUp(self):
        self.citizen = make_user()
        self.sr = make_service_request(citizen=self.citizen)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_citizen_can_cancel_own_request(self, mock_audit, mock_notify):
        cancel_service_request(self.sr, self.citizen)
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status, ServiceRequestStatus.CLOSED)

    def test_cannot_cancel_another_citizens_request(self):
        other_citizen = make_user("other@example.com")
        with self.assertRaises(PermissionError):
            cancel_service_request(self.sr, other_citizen)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_cannot_cancel_approved_request(self, mock_audit, mock_notify):
        update_request_status(self.sr, ServiceRequestStatus.APPROVED, self.citizen)
        self.sr.refresh_from_db()
        with self.assertRaises(ValueError):
            cancel_service_request(self.sr, self.citizen)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_cannot_cancel_rejected_request(self, mock_audit, mock_notify):
        update_request_status(self.sr, ServiceRequestStatus.REJECTED, self.citizen)
        self.sr.refresh_from_db()
        with self.assertRaises(ValueError):
            cancel_service_request(self.sr, self.citizen)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_cancellation_includes_reason_in_note(self, mock_audit, mock_notify):
        result = cancel_service_request(self.sr, self.citizen, reason="Changed my mind")
        self.assertIn("Changed my mind", result.public_note)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_cancellation_creates_status_update(self, mock_audit, mock_notify):
        before_count = self.sr.status_updates.count()
        cancel_service_request(self.sr, self.citizen)
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status_updates.count(), before_count + 1)


class GetCitizenRequestsTest(TestCase):
    def setUp(self):
        self.citizen = make_user("citizen@example.com")
        self.other = make_user("other@example.com")

    def test_returns_only_citizens_requests(self):
        make_service_request(citizen=self.citizen)
        make_service_request(citizen=self.other)
        qs = get_citizen_requests(self.citizen)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().citizen, self.citizen)

    def test_returns_empty_queryset_when_no_requests(self):
        qs = get_citizen_requests(self.citizen)
        self.assertEqual(qs.count(), 0)

    def test_status_filter_includes_matching_status(self):
        make_service_request(citizen=self.citizen)
        qs = get_citizen_requests(self.citizen, status_filter=ServiceRequestStatus.SUBMITTED)
        self.assertEqual(qs.count(), 1)

    def test_status_filter_excludes_non_matching_status(self):
        make_service_request(citizen=self.citizen)
        qs = get_citizen_requests(self.citizen, status_filter=ServiceRequestStatus.APPROVED)
        self.assertEqual(qs.count(), 0)

    def test_invalid_status_filter_returns_all(self):
        make_service_request(citizen=self.citizen)
        qs = get_citizen_requests(self.citizen, status_filter="not_a_status")
        self.assertEqual(qs.count(), 1)

    def test_returns_multiple_requests(self):
        make_service_request(citizen=self.citizen)
        make_service_request(citizen=self.citizen)
        qs = get_citizen_requests(self.citizen)
        self.assertEqual(qs.count(), 2)

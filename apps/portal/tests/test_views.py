"""Integration tests for portal views — IDOR protection, auth, flows."""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.portal.models import ServiceRequest, ServiceRequestStatus
from apps.portal.services import create_service_request

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_user(email=None, is_staff=False):
    email = email or f"u{uuid.uuid4().hex[:6]}@example.com"
    user = User.objects.create_user(email=email, password=VALID_PASSWORD)
    if is_staff:
        user.is_staff = True
        user.save()
    return user


def make_request_for(citizen, **kwargs):
    """Create a ServiceRequest for a citizen via the service layer, suppressing side-effects."""
    with (
        patch("apps.portal.services._fire_notification"),
        patch("apps.portal.services._write_audit"),
    ):
        return create_service_request(
            citizen,
            kwargs.get("service_name", "Test Service"),
            kwargs.get("submission_data", {}),
        )


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class DashboardViewTest(TestCase):
    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)

    def test_dashboard_loads(self):
        response = self.client.get("/portal/")
        self.assertEqual(response.status_code, 200)

    def test_dashboard_requires_login(self):
        self.client.logout()
        response = self.client.get("/portal/")
        self.assertEqual(response.status_code, 302)

    def test_dashboard_shows_recent_requests(self):
        make_request_for(self.citizen)
        response = self.client.get("/portal/")
        self.assertIn("recent_requests", response.context)
        self.assertEqual(len(response.context["recent_requests"]), 1)

    def test_dashboard_only_shows_own_requests(self):
        other_citizen = make_user()
        make_request_for(other_citizen)
        response = self.client.get("/portal/")
        self.assertEqual(len(response.context["recent_requests"]), 0)

    def test_dashboard_includes_total_count(self):
        make_request_for(self.citizen)
        make_request_for(self.citizen)
        response = self.client.get("/portal/")
        # Context key is total_requests (matches template variable name)
        self.assertEqual(response.context["total_requests"], 2)

    def test_dashboard_includes_active_count(self):
        make_request_for(self.citizen)
        response = self.client.get("/portal/")
        # Context key is active_requests (matches template variable name)
        self.assertIn("active_requests", response.context)

    def test_dashboard_includes_completed_count(self):
        make_request_for(self.citizen)
        response = self.client.get("/portal/")
        self.assertIn("completed_requests", response.context)

    def test_dashboard_caps_recent_requests_at_five(self):
        for _ in range(7):
            make_request_for(self.citizen)
        response = self.client.get("/portal/")
        self.assertLessEqual(len(response.context["recent_requests"]), 5)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ServiceRequestListViewTest(TestCase):
    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)

    def test_list_view_loads(self):
        response = self.client.get("/portal/requests/")
        self.assertEqual(response.status_code, 200)

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get("/portal/requests/")
        self.assertEqual(response.status_code, 302)

    def test_list_only_shows_own_requests(self):
        other = make_user()
        make_request_for(other)
        response = self.client.get("/portal/requests/")
        self.assertEqual(response.context["requests"].count(), 0)

    def test_list_shows_citizen_requests(self):
        make_request_for(self.citizen)
        make_request_for(self.citizen)
        response = self.client.get("/portal/requests/")
        self.assertEqual(response.context["requests"].count(), 2)

    def test_status_filter_works(self):
        make_request_for(self.citizen)
        response = self.client.get("/portal/requests/?status=submitted")
        self.assertEqual(response.context["requests"].count(), 1)

    def test_status_filter_excludes_other_statuses(self):
        make_request_for(self.citizen)
        response = self.client.get("/portal/requests/?status=approved")
        self.assertEqual(response.context["requests"].count(), 0)

    def test_context_includes_status_choices(self):
        response = self.client.get("/portal/requests/")
        self.assertIn("status_choices", response.context)

    def test_context_includes_active_status(self):
        response = self.client.get("/portal/requests/?status=submitted")
        self.assertEqual(response.context["current_status"], "submitted")


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ServiceRequestDetailViewTest(TestCase):
    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)
        self.sr = make_request_for(self.citizen)

    def test_detail_view_loads_own_request(self):
        response = self.client.get(f"/portal/requests/{self.sr.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_cannot_view_another_citizens_request(self):
        """IDOR prevention: citizen cannot view another citizen's request."""
        other = make_user()
        other_sr = make_request_for(other)
        response = self.client.get(f"/portal/requests/{other_sr.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_detail_view_requires_login(self):
        self.client.logout()
        response = self.client.get(f"/portal/requests/{self.sr.pk}/")
        self.assertEqual(response.status_code, 302)

    def test_detail_shows_status_timeline(self):
        response = self.client.get(f"/portal/requests/{self.sr.pk}/")
        self.assertIn("status_updates", response.context)

    def test_nonexistent_request_returns_404(self):
        fake_pk = uuid.uuid4()
        response = self.client.get(f"/portal/requests/{fake_pk}/")
        self.assertEqual(response.status_code, 404)

    def test_detail_context_includes_can_cancel(self):
        response = self.client.get(f"/portal/requests/{self.sr.pk}/")
        self.assertIn("can_cancel", response.context)

    def test_detail_context_includes_cancel_form(self):
        response = self.client.get(f"/portal/requests/{self.sr.pk}/")
        self.assertIn("cancel_form", response.context)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class SubmitRequestViewTest(TestCase):
    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)

    def test_submit_form_loads(self):
        response = self.client.get("/portal/submit/")
        self.assertEqual(response.status_code, 200)

    def test_submit_requires_login(self):
        self.client.logout()
        response = self.client.get("/portal/submit/")
        self.assertEqual(response.status_code, 302)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_submit_valid_request_creates_record(self, mock_audit, mock_notify):
        self.client.post(
            "/portal/submit/",
            {
                "service_name": "Pothole Repair",
                "description": "Large pothole on Main St",
                "contact_email": self.citizen.email,
                "contact_phone": "",
                "consent_given": True,
            },
            follow=True,
        )
        self.assertEqual(ServiceRequest.objects.filter(citizen=self.citizen).count(), 1)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_submit_redirects_to_detail_on_success(self, mock_audit, mock_notify):
        response = self.client.post(
            "/portal/submit/",
            {
                "service_name": "Pothole Repair",
                "description": "Large pothole on Main St",
                "contact_email": self.citizen.email,
                "contact_phone": "",
                "consent_given": True,
            },
        )
        sr = ServiceRequest.objects.filter(citizen=self.citizen).first()
        self.assertRedirects(response, f"/portal/requests/{sr.pk}/")

    def test_submit_without_consent_fails(self):
        response = self.client.post(
            "/portal/submit/",
            {
                "service_name": "Test",
                "description": "Test desc",
                "contact_email": self.citizen.email,
                "consent_given": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ServiceRequest.objects.count(), 0)

    def test_submit_missing_service_name_fails(self):
        response = self.client.post(
            "/portal/submit/",
            {
                "service_name": "",
                "description": "Some description",
                "contact_email": self.citizen.email,
                "consent_given": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ServiceRequest.objects.count(), 0)

    def test_submit_missing_description_fails(self):
        response = self.client.post(
            "/portal/submit/",
            {
                "service_name": "Test",
                "description": "",
                "contact_email": self.citizen.email,
                "consent_given": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ServiceRequest.objects.count(), 0)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class CancelRequestViewTest(TestCase):
    def setUp(self):
        self.citizen = make_user()
        self.client.force_login(self.citizen)
        self.sr = make_request_for(self.citizen)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_citizen_can_cancel_own_request(self, mock_audit, mock_notify):
        self.client.post(
            f"/portal/requests/{self.sr.pk}/cancel/",
            {
                "confirm": True,
                "reason": "Changed my mind",
            },
        )
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status, ServiceRequestStatus.CLOSED)

    def test_cannot_cancel_another_citizens_request(self):
        other = make_user()
        other_sr = make_request_for(other)
        response = self.client.post(
            f"/portal/requests/{other_sr.pk}/cancel/",
            {
                "confirm": True,
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_cancel_requires_confirmation(self):
        self.client.post(
            f"/portal/requests/{self.sr.pk}/cancel/",
            {
                "confirm": False,
            },
        )
        self.sr.refresh_from_db()
        self.assertNotEqual(self.sr.status, ServiceRequestStatus.CLOSED)

    def test_cancel_requires_post(self):
        response = self.client.get(f"/portal/requests/{self.sr.pk}/cancel/")
        self.assertEqual(response.status_code, 405)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_cancel_redirects_to_request_list(self, mock_audit, mock_notify):
        response = self.client.post(
            f"/portal/requests/{self.sr.pk}/cancel/",
            {
                "confirm": True,
                "reason": "",
            },
        )
        self.assertRedirects(response, "/portal/requests/")

    def test_cancel_nonexistent_request_returns_404(self):
        fake_pk = uuid.uuid4()
        response = self.client.post(
            f"/portal/requests/{fake_pk}/cancel/",
            {
                "confirm": True,
            },
        )
        self.assertEqual(response.status_code, 404)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class StaffQueueViewTest(TestCase):
    def setUp(self):
        self.staff = make_user("staff@gov.ca", is_staff=True)
        self.citizen = make_user()
        self.client.force_login(self.staff)

    def test_staff_queue_loads(self):
        response = self.client.get("/portal/staff/queue/")
        self.assertEqual(response.status_code, 200)

    def test_citizen_cannot_access_staff_queue(self):
        self.client.force_login(self.citizen)
        response = self.client.get("/portal/staff/queue/")
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_access_staff_queue(self):
        self.client.logout()
        response = self.client.get("/portal/staff/queue/")
        self.assertEqual(response.status_code, 302)

    def test_staff_can_see_all_citizens_requests(self):
        make_request_for(self.citizen)
        response = self.client.get("/portal/staff/queue/")
        self.assertEqual(response.status_code, 200)

    def test_staff_queue_context_includes_status_choices(self):
        response = self.client.get("/portal/staff/queue/")
        self.assertIn("status_choices", response.context)

    def test_staff_queue_context_includes_counts(self):
        response = self.client.get("/portal/staff/queue/")
        self.assertIn("counts", response.context)

    def test_staff_queue_status_filter_works(self):
        make_request_for(self.citizen)
        response = self.client.get("/portal/staff/queue/?status=submitted")
        self.assertEqual(response.status_code, 200)
        for req in response.context["requests"]:
            self.assertEqual(req.status, ServiceRequestStatus.SUBMITTED)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class StaffStatusUpdateTest(TestCase):
    def setUp(self):
        self.staff = make_user("staff@gov.ca", is_staff=True)
        self.citizen = make_user()
        self.client.force_login(self.staff)
        self.sr = make_request_for(self.citizen)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_staff_can_update_status(self, mock_audit, mock_notify):
        self.client.post(
            f"/portal/staff/requests/{self.sr.pk}/update-status/",
            {
                "new_status": "in_review",
                "public_note": "We are reviewing your request.",
            },
        )
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status, ServiceRequestStatus.IN_REVIEW)

    def test_citizen_cannot_update_status(self):
        self.client.force_login(self.citizen)
        response = self.client.post(
            f"/portal/staff/requests/{self.sr.pk}/update-status/",
            {
                "new_status": "approved",
                "public_note": "Self-approved!",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.sr.refresh_from_db()
        self.assertNotEqual(self.sr.status, ServiceRequestStatus.APPROVED)

    def test_anonymous_cannot_update_status(self):
        self.client.logout()
        response = self.client.post(
            f"/portal/staff/requests/{self.sr.pk}/update-status/",
            {
                "new_status": "approved",
                "public_note": "",
            },
        )
        self.assertEqual(response.status_code, 302)

    @patch("apps.portal.services._fire_notification")
    @patch("apps.portal.services._write_audit")
    def test_staff_update_redirects_to_staff_detail(self, mock_audit, mock_notify):
        response = self.client.post(
            f"/portal/staff/requests/{self.sr.pk}/update-status/",
            {
                "new_status": "in_review",
                "public_note": "",
            },
        )
        self.assertRedirects(response, f"/portal/staff/requests/{self.sr.pk}/")

    def test_invalid_status_does_not_update_request(self):
        response = self.client.post(
            f"/portal/staff/requests/{self.sr.pk}/update-status/",
            {
                "new_status": "not_a_real_status",
                "public_note": "",
            },
        )
        # Invalid form → redirects back to staff detail (302), request untouched
        self.assertEqual(response.status_code, 302)
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status, ServiceRequestStatus.SUBMITTED)

    def test_update_nonexistent_request_returns_404(self):
        fake_pk = uuid.uuid4()
        response = self.client.post(
            f"/portal/staff/requests/{fake_pk}/update-status/",
            {
                "new_status": "in_review",
                "public_note": "",
            },
        )
        self.assertEqual(response.status_code, 404)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class StaffRequestDetailViewTest(TestCase):
    """Tests for the staff-only request detail view."""

    def setUp(self):
        self.staff = make_user("staff2@gov.ca", is_staff=True)
        self.citizen = make_user()
        self.client.force_login(self.staff)
        self.sr = make_request_for(self.citizen)

    def test_staff_detail_loads(self):
        response = self.client.get(f"/portal/staff/requests/{self.sr.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_citizen_cannot_access_staff_detail(self):
        self.client.force_login(self.citizen)
        response = self.client.get(f"/portal/staff/requests/{self.sr.pk}/")
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_access_staff_detail(self):
        self.client.logout()
        response = self.client.get(f"/portal/staff/requests/{self.sr.pk}/")
        self.assertEqual(response.status_code, 302)

    def test_staff_detail_context_includes_status_form(self):
        response = self.client.get(f"/portal/staff/requests/{self.sr.pk}/")
        self.assertIn("status_form", response.context)

    def test_staff_detail_context_includes_status_updates(self):
        response = self.client.get(f"/portal/staff/requests/{self.sr.pk}/")
        self.assertIn("status_updates", response.context)

    def test_staff_detail_nonexistent_request_returns_404(self):
        fake_pk = uuid.uuid4()
        response = self.client.get(f"/portal/staff/requests/{fake_pk}/")
        self.assertEqual(response.status_code, 404)

    def test_staff_detail_shows_any_citizens_request(self):
        """Staff can view requests belonging to any citizen (no IDOR restriction at staff level)."""
        other_citizen = make_user()
        other_sr = make_request_for(other_citizen)
        response = self.client.get(f"/portal/staff/requests/{other_sr.pk}/")
        self.assertEqual(response.status_code, 200)

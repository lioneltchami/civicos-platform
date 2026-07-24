"""
Test suite for the Portal API building block.

Covers ServiceRequestListCreateView, ServiceRequestDetailView, and
CancelServiceRequestView — including IDOR prevention, ownership isolation,
response envelope shape, and validation error handling.

Security invariants:
- Citizens only ever see/cancel their own service requests (IDOR guard).
- Non-owner lookups return 404, not 403, to prevent reference-number enumeration.
- Unauthenticated requests return 401.
- Error responses always use the civicos error envelope:
  {"error": {"code": ..., "detail": ..., "status": ...}}
"""

import itertools
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.portal.models import ServiceRequest, ServiceRequestStatus

User = get_user_model()

VALID_PASSWORD = "SecureTestPass123!"
LIST_URL = "/api/v1/portal/requests/"

# Patch targets — suppress Celery tasks and audit writes that fire via on_commit.
_PATCH_NOTIFICATION = "apps.portal.services._fire_notification"
_PATCH_AUDIT = "apps.portal.services._write_audit"
_PATCH_SIGNAL = "apps.core.signals.service_request_submitted.send_robust"

# Counter to generate unique emails per test without relying on random values.
_counter = itertools.count(1)


def _email(prefix="user"):
    return f"{prefix}+{next(_counter)}@example.gov"


def _make_citizen(email=None):
    """Create and return a non-staff (citizen) user."""
    return User.objects.create_user(
        email=email or _email("citizen"),
        password=VALID_PASSWORD,
        is_staff=False,
    )


def _make_staff(email=None):
    """Create and return a staff user."""
    return User.objects.create_user(
        email=email or _email("staff"),
        password=VALID_PASSWORD,
        is_staff=True,
    )


def _bearer(client, user):
    """Obtain a JWT access token for a user and return an auth header dict."""
    resp = client.post(
        "/api/v1/auth/token/",
        {"email": user.email, "password": VALID_PASSWORD},
        format="json",
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token fetch failed for {user.email}: "
            f"status={resp.status_code} data={resp.data}"
        )
    return {"HTTP_AUTHORIZATION": f"Bearer {resp.data['access']}"}


def _detail_url(reference_number):
    return f"/api/v1/portal/requests/{reference_number}/"


def _cancel_url(reference_number):
    return f"/api/v1/portal/requests/{reference_number}/cancel/"


class ServiceRequestListTests(TestCase):
    """Tests for GET /api/v1/portal/requests/ — listing the citizen's own requests."""

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()

    def test_authenticated_citizen_gets_200_empty_list(self):
        # A fresh citizen with no requests should receive an empty paginated list.
        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.data)
        self.assertEqual(resp.data["count"], 0)
        self.assertEqual(resp.data["results"], [])

    def test_unauthenticated_request_returns_401(self):
        # No auth header — must return 401, not 403 (RFC 7235 semantics).
        resp = self.client.get(LIST_URL)

        self.assertEqual(resp.status_code, 401)

    def test_list_shows_own_requests(self):
        # A citizen's list must include requests they own.
        sr = ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="Passport",
            submission_data={"applicant": "Test"},
        )

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["reference_number"], sr.reference_number)

    def test_idor_citizen_a_cannot_see_citizen_b_requests(self):
        # IDOR guard: citizen A's list must not contain citizen B's requests.
        citizen_b = _make_citizen()
        ServiceRequest.objects.create(
            citizen=citizen_b,
            service_name="Birth Certificate",
            submission_data={"field": "value"},
        )

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data["count"],
            0,
            "Citizen A must never see Citizen B's service requests.",
        )

    def test_list_response_does_not_include_submission_data(self):
        # Sensitive form data must be omitted from list responses (detail endpoint only).
        ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="Driving Licence",
            submission_data={"sin": "123-456-789"},
        )

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        item = resp.data["results"][0]
        self.assertNotIn("submission_data", item)

    def test_list_response_includes_expected_fields(self):
        # The list serializer must expose the documented public fields.
        ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="Health Card",
            submission_data={},
        )

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        item = resp.data["results"][0]
        for field in ("reference_number", "service_name", "status", "status_display", "created_at"):
            self.assertIn(field, item, f"Expected field '{field}' missing from list response")

    def test_status_filter_returns_only_matching_requests(self):
        # ?status=submitted must return only SUBMITTED requests; IN_REVIEW requests excluded.
        ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="Submitted Service",
            submission_data={},
            status=ServiceRequestStatus.SUBMITTED,
        )
        ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="In Review Service",
            submission_data={},
            status=ServiceRequestStatus.IN_REVIEW,
        )

        resp = self.client.get(
            LIST_URL + "?status=submitted", **_bearer(self.client, self.citizen)
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["status"], ServiceRequestStatus.SUBMITTED)

    def test_invalid_status_filter_returns_unfiltered_list(self):
        # An invalid ?status= value must be ignored (not a 400), returning all requests.
        ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="My Service",
            submission_data={},
            status=ServiceRequestStatus.SUBMITTED,
        )

        resp = self.client.get(
            LIST_URL + "?status=garbage", **_bearer(self.client, self.citizen)
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)


class ServiceRequestCreateTests(TestCase):
    """Tests for POST /api/v1/portal/requests/ — submitting a new service request."""

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()

    @patch(_PATCH_SIGNAL)
    @patch(_PATCH_AUDIT)
    @patch(_PATCH_NOTIFICATION)
    def test_valid_payload_creates_request_and_returns_201(self, mock_notif, mock_audit, mock_sig):
        # A complete, valid payload must create the record and return HTTP 201
        # with the full detail representation (including submission_data).
        # captureOnCommitCallbacks is required so that on_commit() hooks execute
        # within the Django TestCase transaction rollback context.
        payload = {
            "service_name": "Health Card Application",
            "submission_data": {"dob": "1990-01-01", "province": "ON"},
        }

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                LIST_URL, payload, format="json", **_bearer(self.client, self.citizen)
            )

        self.assertEqual(resp.status_code, 201)
        self.assertIn("reference_number", resp.data)
        self.assertIn("submission_data", resp.data)
        self.assertTrue(
            ServiceRequest.objects.filter(citizen=self.citizen).exists(),
            "ServiceRequest must be persisted in the database.",
        )
        # Verify the on_commit hooks actually fired (notification, audit, signal)
        mock_notif.assert_called_once()
        mock_audit.assert_called_once()
        mock_sig.assert_called_once()

    def test_missing_service_name_returns_400(self):
        # Omitting required service_name must trigger a 400 validation error.
        resp = self.client.post(
            LIST_URL,
            {"submission_data": {"key": "value"}},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 400)

    def test_missing_submission_data_returns_400(self):
        # Omitting required submission_data must trigger a 400 validation error.
        resp = self.client.post(
            LIST_URL,
            {"service_name": "Passport"},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 400)

    def test_blank_service_name_returns_400(self):
        # A whitespace-only service_name must be rejected by the serializer.
        resp = self.client.post(
            LIST_URL,
            {"service_name": "   ", "submission_data": {}},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 400)

    def test_submission_data_as_list_returns_400(self):
        # submission_data must be a JSON object (dict), not a list.
        resp = self.client.post(
            LIST_URL,
            {"service_name": "Passport", "submission_data": ["item1", "item2"]},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_create_returns_401(self):
        # POST without authentication must be rejected with 401.
        resp = self.client.post(
            LIST_URL,
            {"service_name": "Passport", "submission_data": {}},
            format="json",
        )

        self.assertEqual(resp.status_code, 401)

    @patch(_PATCH_SIGNAL)
    @patch(_PATCH_AUDIT)
    @patch(_PATCH_NOTIFICATION)
    def test_created_request_belongs_to_authenticated_citizen(self, mock_notif, mock_audit, mock_sig):
        # The citizen FK on the created record must always be the requesting user,
        # never a value supplied in the payload.
        payload = {
            "service_name": "Driving Licence",
            "submission_data": {"class": "G"},
        }

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                LIST_URL, payload, format="json", **_bearer(self.client, self.citizen)
            )

        sr = ServiceRequest.objects.get(citizen=self.citizen)
        self.assertEqual(sr.citizen_id, self.citizen.pk)


class ServiceRequestDetailTests(TestCase):
    """Tests for GET /api/v1/portal/requests/<reference_number>/."""

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()
        self.other_citizen = _make_citizen()
        # Create a request belonging to self.citizen directly (no service layer needed)
        self.sr = ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="Passport Renewal",
            submission_data={"passport_no": "AB123456"},
        )

    def test_owner_can_retrieve_own_request(self):
        # The owning citizen must be able to retrieve the full detail.
        resp = self.client.get(
            _detail_url(self.sr.reference_number),
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["reference_number"], self.sr.reference_number)
        # Detail endpoint must include submission_data (not redacted like the list)
        self.assertIn("submission_data", resp.data)
        # The submission_data value must match what was stored — key presence alone is insufficient
        self.assertEqual(resp.data["submission_data"], {"passport_no": "AB123456"})

    def test_non_owner_gets_404_not_403(self):
        # IDOR guard: a non-owner must receive 404 to avoid reference-number enumeration.
        # Returning 403 would confirm the reference exists, which is a disclosure risk.
        resp = self.client.get(
            _detail_url(self.sr.reference_number),
            **_bearer(self.client, self.other_citizen),
        )

        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_detail_returns_401(self):
        # No auth must return 401 (not 403) on the detail endpoint.
        resp = self.client.get(_detail_url(self.sr.reference_number))

        self.assertEqual(resp.status_code, 401)

    def test_nonexistent_reference_returns_404(self):
        # A completely made-up reference must return 404.
        resp = self.client.get(
            _detail_url("GS-9999-ZZZZZZ"),
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 404)


class CancelServiceRequestTests(TestCase):
    """Tests for POST /api/v1/portal/requests/<reference_number>/cancel/."""

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()
        self.other_citizen = _make_citizen()
        self.sr = ServiceRequest.objects.create(
            citizen=self.citizen,
            service_name="Health Card",
            submission_data={},
            status=ServiceRequestStatus.SUBMITTED,
        )

    @patch(_PATCH_AUDIT)
    @patch(_PATCH_NOTIFICATION)
    def test_owner_can_cancel_submitted_request(self, mock_notif, mock_audit):
        # A citizen must be able to cancel their own non-terminal request.
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                _cancel_url(self.sr.reference_number),
                {},
                format="json",
                **_bearer(self.client, self.citizen),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("detail", resp.data)
        self.sr.refresh_from_db()
        self.assertEqual(self.sr.status, ServiceRequestStatus.CLOSED)
        mock_notif.assert_called_once()
        mock_audit.assert_called_once()

    def test_non_owner_cancel_returns_404(self):
        # IDOR guard: cancelling another citizen's request must return 404, not 403.
        resp = self.client.post(
            _cancel_url(self.sr.reference_number),
            {},
            format="json",
            **_bearer(self.client, self.other_citizen),
        )

        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_cancel_returns_401(self):
        # No auth must return 401 on the cancel endpoint.
        resp = self.client.post(
            _cancel_url(self.sr.reference_number),
            {},
            format="json",
        )

        self.assertEqual(resp.status_code, 401)

    @patch(_PATCH_NOTIFICATION)
    @patch(_PATCH_AUDIT)
    def test_cancel_with_optional_reason_succeeds(self, mock_audit, mock_notif):
        # The optional reason field must be accepted without validation errors.
        # on_commit hooks (notification + audit) are verified via captureOnCommitCallbacks.
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                _cancel_url(self.sr.reference_number),
                {"reason": "I no longer need this service."},
                format="json",
                **_bearer(self.client, self.citizen),
            )

        self.assertEqual(resp.status_code, 200)
        mock_notif.assert_called_once()
        mock_audit.assert_called_once()

    def test_cancel_already_approved_request_returns_400_with_envelope(self):
        # A terminal (approved) request cannot be cancelled.
        # The error response must use the civicos error envelope.
        self.sr.status = ServiceRequestStatus.APPROVED
        self.sr.save(update_fields=["status"])

        resp = self.client.post(
            _cancel_url(self.sr.reference_number),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.data)
        error = resp.data["error"]
        self.assertIn("code", error)
        self.assertIn("message", error)

    def test_cancel_rejected_request_returns_400(self):
        # A rejected (terminal) request cannot be cancelled.
        self.sr.status = ServiceRequestStatus.REJECTED
        self.sr.save(update_fields=["status"])

        resp = self.client.post(
            _cancel_url(self.sr.reference_number),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 400)

    def test_cancel_closed_request_returns_400(self):
        # A closed (terminal) request cannot be cancelled again.
        self.sr.status = ServiceRequestStatus.CLOSED
        self.sr.save(update_fields=["status"])

        resp = self.client.post(
            _cancel_url(self.sr.reference_number),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 400)

    @patch(_PATCH_AUDIT)
    @patch(_PATCH_NOTIFICATION)
    def test_cancel_in_review_request_succeeds(self, mock_notif, mock_audit):
        # IN_REVIEW is a non-terminal status; cancellation must be allowed.
        self.sr.status = ServiceRequestStatus.IN_REVIEW
        self.sr.save(update_fields=["status"])

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                _cancel_url(self.sr.reference_number),
                {},
                format="json",
                **_bearer(self.client, self.citizen),
            )

        self.assertEqual(resp.status_code, 200)

    def test_cancel_nonexistent_reference_returns_404(self):
        # A reference number that doesn't exist at all must return 404.
        resp = self.client.post(
            _cancel_url("GS-9999-ZZZZZZ"),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 404)


class ErrorEnvelopeTests(TestCase):
    """
    Verifies the civicos error envelope shape on representative error responses.

    All error responses from the API must follow:
    {"error": {"code": str, "message": str, "details": dict}}
    (civicos_exception_handler — no "detail" or "status" keys)
    """

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()

    def test_401_response_has_error_envelope(self):
        # Unauthenticated access must use the civicos error envelope format.
        resp = self.client.get(LIST_URL)

        self.assertEqual(resp.status_code, 401)
        self.assertIn("error", resp.data)
        error = resp.data["error"]
        self.assertIn("code", error)
        self.assertIn("message", error)
        self.assertEqual(error["code"], "unauthorized")

    def test_400_validation_error_has_error_envelope(self):
        # Validation errors on POST must use the civicos envelope.
        resp = self.client.post(
            LIST_URL,
            {"submission_data": {}},  # missing service_name
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.data)
        error = resp.data["error"]
        self.assertIn("code", error)
        self.assertIn("message", error)

    def test_404_response_has_error_envelope(self):
        # A missing reference number must return the civicos error envelope.
        resp = self.client.get(
            _detail_url("GS-0000-XXXXXX"),
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 404)
        self.assertIn("error", resp.data)
        error = resp.data["error"]
        self.assertEqual(error["code"], "not_found")
        self.assertIn("message", error)

"""
Test suite for the Notification inbox API.

Covers NotificationListView, NotificationDetailView, and
MarkNotificationReadView — including IDOR prevention, ownership isolation,
read-state management, and access-control enforcement.

Key model notes (verified from apps/notifications/models.py and serializers.py):
- Notification.recipient  → FK to the owning user (not 'citizen')
- Notification.read_at    → DateTimeField (nullable); is_read is derived
- Notification.channel    → must be IN_APP for the inbox; email/sms are excluded
- Notification.body       → required text field
- Notification.subject    → optional subject line

Security invariants:
- Citizens only ever see notifications addressed to them (IDOR guard).
- Non-recipient lookups return 404, not 403, to prevent ID enumeration.
- Unauthenticated requests return 401.
- Throttling is disabled in test settings — no rate-limit interference.
"""

import itertools
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.notifications.models import Notification, NotificationChannel, NotificationStatus

User = get_user_model()

VALID_PASSWORD = "SecureTestPass123!"
LIST_URL = "/api/v1/notifications/"

_counter = itertools.count(1)


def _email(prefix="user"):
    return f"{prefix}+{next(_counter)}@example.gov"


def _make_citizen(email=None):
    """Create and return a non-staff citizen user."""
    return User.objects.create_user(
        email=email or _email("citizen"),
        password=VALID_PASSWORD,
        is_staff=False,
    )


def _bearer(client, user):
    """Obtain a JWT access token for a user and return an auth-header dict."""
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


def _make_in_app_notification(recipient, subject="Test subject", body="Test body", read_at=None):
    """
    Create an IN_APP Notification for a given recipient.
    Bypasses any service layer — tests construct state directly.
    """
    return Notification.objects.create(
        recipient=recipient,
        channel=NotificationChannel.IN_APP,
        subject=subject,
        body=body,
        status=NotificationStatus.DELIVERED,
        read_at=read_at,
    )


def _detail_url(pk):
    return f"/api/v1/notifications/{pk}/"


def _read_url(pk):
    return f"/api/v1/notifications/{pk}/read/"


class NotificationListTests(TestCase):
    """Tests for GET /api/v1/notifications/ — the citizen's in-app inbox."""

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()

    def test_authenticated_citizen_gets_200_empty_inbox(self):
        # A citizen with no in-app notifications must receive an empty paginated list.
        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.data)
        self.assertEqual(resp.data["count"], 0)
        self.assertEqual(resp.data["results"], [])

    def test_unauthenticated_request_returns_401(self):
        # No auth header must yield 401 — not 403.
        resp = self.client.get(LIST_URL)

        self.assertEqual(resp.status_code, 401)

    def test_own_in_app_notification_appears_in_list(self):
        # An in-app notification addressed to the citizen must appear in their inbox.
        notif = _make_in_app_notification(self.citizen, subject="Your request was received")

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(str(resp.data["results"][0]["id"]), str(notif.pk))

    def test_idor_citizen_a_cannot_see_citizen_b_notifications(self):
        # IDOR guard: citizen A's inbox must never contain citizen B's notifications.
        citizen_b = _make_citizen()
        _make_in_app_notification(citizen_b, subject="Only for B")

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data["count"],
            0,
            "Citizen A must never see Citizen B's notifications.",
        )

    def test_email_channel_notifications_excluded_from_inbox(self):
        # Email-channel records exist for audit but must NOT appear in the in-app inbox.
        Notification.objects.create(
            recipient=self.citizen,
            channel=NotificationChannel.EMAIL,
            body="An email notification",
            status=NotificationStatus.SENT,
        )

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data["count"],
            0,
            "Email-channel notifications must be excluded from the in-app inbox.",
        )

    def test_sms_channel_notifications_excluded_from_inbox(self):
        # SMS-channel records exist for audit but must NOT appear in the in-app inbox.
        Notification.objects.create(
            recipient=self.citizen,
            channel=NotificationChannel.SMS,
            body="An SMS notification",
            status=NotificationStatus.SENT,
        )

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)

    def test_unread_filter_returns_only_unread_notifications(self):
        # ?unread=1 must filter to only notifications where read_at is null.
        from django.utils import timezone

        _make_in_app_notification(self.citizen, subject="Unread")
        _make_in_app_notification(self.citizen, subject="Read", read_at=timezone.now())

        resp = self.client.get(LIST_URL + "?unread=1", **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["subject"], "Unread")

    def test_list_response_includes_is_read_derived_field(self):
        # is_read is a derived serializer field — must be present and correct.
        _make_in_app_notification(self.citizen)

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        item = resp.data["results"][0]
        self.assertIn("is_read", item)
        self.assertFalse(item["is_read"])

    def test_list_response_includes_expected_fields(self):
        # The serializer must expose all documented public fields.
        _make_in_app_notification(self.citizen)

        resp = self.client.get(LIST_URL, **_bearer(self.client, self.citizen))

        item = resp.data["results"][0]
        for field in ("id", "subject", "body", "channel", "status", "is_read", "read_at", "created_at"):
            self.assertIn(field, item, f"Expected field '{field}' missing from notification list response")


class NotificationDetailTests(TestCase):
    """Tests for GET /api/v1/notifications/<uuid:pk>/ — single notification retrieval."""

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()
        self.other_citizen = _make_citizen()
        self.notif = _make_in_app_notification(
            self.citizen,
            subject="Passport update",
            body="Your passport is ready.",
        )

    def test_recipient_can_retrieve_own_notification(self):
        # The owning recipient must be able to fetch the full notification detail.
        resp = self.client.get(
            _detail_url(self.notif.pk),
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(str(resp.data["id"]), str(self.notif.pk))
        self.assertEqual(resp.data["subject"], "Passport update")

    def test_non_recipient_gets_404_not_403(self):
        # IDOR guard: accessing another citizen's notification must yield 404.
        # 403 would confirm the notification ID exists (enumeration risk).
        resp = self.client.get(
            _detail_url(self.notif.pk),
            **_bearer(self.client, self.other_citizen),
        )

        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_detail_returns_401(self):
        # No auth must return 401 on the detail endpoint.
        resp = self.client.get(_detail_url(self.notif.pk))

        self.assertEqual(resp.status_code, 401)

    def test_nonexistent_pk_returns_404(self):
        # A completely made-up UUID must return 404.
        nonexistent = uuid.uuid4()
        resp = self.client.get(
            _detail_url(nonexistent),
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 404)


class NotificationPatchReadTests(TestCase):
    """Tests for PATCH /api/v1/notifications/<uuid:pk>/ — mark read/unread via partial update."""

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()
        self.notif = _make_in_app_notification(self.citizen)

    def test_patch_is_read_true_sets_read_at(self):
        # Sending is_read=true must set read_at to a non-null timestamp.
        resp = self.client.patch(
            _detail_url(self.notif.pk),
            {"is_read": True},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 200)
        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at, "read_at must be set after is_read=true")
        self.assertTrue(resp.data["is_read"])

    def test_patch_is_read_false_clears_read_at(self):
        # Sending is_read=false must clear read_at back to null.
        from django.utils import timezone

        self.notif.read_at = timezone.now()
        self.notif.save(update_fields=["read_at"])

        resp = self.client.patch(
            _detail_url(self.notif.pk),
            {"is_read": False},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 200)
        self.notif.refresh_from_db()
        self.assertIsNone(self.notif.read_at, "read_at must be cleared after is_read=false")

    def test_non_recipient_patch_returns_404(self):
        # A non-recipient must receive 404 on PATCH — IDOR guard.
        other = _make_citizen()
        resp = self.client.patch(
            _detail_url(self.notif.pk),
            {"is_read": True},
            format="json",
            **_bearer(self.client, other),
        )

        self.assertEqual(resp.status_code, 404)


class MarkNotificationReadTests(TestCase):
    """
    Tests for POST /api/v1/notifications/<uuid:pk>/read/ — convenience mark-read endpoint.

    This endpoint is idempotent: calling it on an already-read notification is fine.
    """

    def setUp(self):
        self.client = APIClient()
        self.citizen = _make_citizen()
        self.notif = _make_in_app_notification(self.citizen, subject="Action required")

    def test_post_read_marks_notification_as_read(self):
        # POST to /read/ must set read_at and return 200 with updated representation.
        resp = self.client.post(
            _read_url(self.notif.pk),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 200)
        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at, "read_at must be set after POST to /read/")
        self.assertTrue(resp.data["is_read"])

    def test_post_read_is_idempotent(self):
        # Calling /read/ twice must not raise an error — idempotent by design.
        from django.utils import timezone

        self.notif.read_at = timezone.now()
        self.notif.save(update_fields=["read_at"])
        first_read_at = self.notif.read_at

        resp = self.client.post(
            _read_url(self.notif.pk),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 200)
        # read_at must not be overwritten when already set
        self.notif.refresh_from_db()
        self.assertEqual(self.notif.read_at, first_read_at)

    def test_post_read_as_non_recipient_returns_404(self):
        # IDOR guard: marking another citizen's notification as read must return 404.
        other = _make_citizen()

        resp = self.client.post(
            _read_url(self.notif.pk),
            {},
            format="json",
            **_bearer(self.client, other),
        )

        self.assertEqual(resp.status_code, 404)

    def test_post_read_unauthenticated_returns_401(self):
        # No auth must return 401 on the /read/ endpoint.
        resp = self.client.post(
            _read_url(self.notif.pk),
            {},
            format="json",
        )

        self.assertEqual(resp.status_code, 401)

    def test_post_read_nonexistent_notification_returns_404(self):
        # A non-existent UUID on /read/ must return 404.
        resp = self.client.post(
            _read_url(uuid.uuid4()),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 404)

    def test_post_read_nonexistent_returns_civicos_error_envelope(self):
        # The 404 from /read/ must use the civicos error envelope (not a flat {"detail": "Not found."}).
        resp = self.client.post(
            _read_url(uuid.uuid4()),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 404)
        self.assertIn("error", resp.data, "404 must use civicos error envelope, not flat {'detail': ...}")
        error = resp.data["error"]
        self.assertEqual(error["code"], "not_found")
        self.assertIn("message", error)

"""Tests for backoffice audit log viewer."""

from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEventType, AuditLogEntry

User = get_user_model()

AUDIT_LIST_URL = reverse("backoffice:audit-list")


def _make_entry(**kwargs):
    """Create an AuditLogEntry with sensible defaults."""
    defaults = {
        "event_type": AuditEventType.LOGIN_SUCCESS,
        "outcome": "success",
        "actor_id": "0",
        "resource_id": "",
        "resource_type": "",
    }
    defaults.update(kwargs)
    return AuditLogEntry.objects.create(**defaults)


class AuditLogListTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            email="staff@example.com",
            password="testpass123",
            is_staff=True,
        )
        self.citizen_user = User.objects.create_user(
            email="citizen@example.com",
            password="testpass123",
            is_staff=False,
        )
        self.client = Client()

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    def test_requires_staff_unauthenticated(self):
        """Unauthenticated requests are redirected to the login page."""
        response = self.client.get(AUDIT_LIST_URL)
        self.assertRedirects(response, "/account/login/?next=/backoffice/audit/")

    def test_requires_staff_citizen_gets_403(self):
        """Authenticated non-staff users receive HTTP 403."""
        self.client.login(email="citizen@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL)
        self.assertEqual(response.status_code, 403)

    def test_requires_staff(self):
        """Staff users can access the audit log."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL)
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # Basic rendering
    # ------------------------------------------------------------------

    def test_staff_sees_audit_log(self):
        """Staff user gets 200 and entries appear in context."""
        _make_entry()
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("entries", response.context)
        self.assertGreaterEqual(len(response.context["entries"]), 1)

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def test_event_type_filter(self):
        """Filter by event_type returns only matching entries."""
        _make_entry(event_type=AuditEventType.LOGIN_SUCCESS)
        _make_entry(event_type=AuditEventType.LOGOUT)
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(
            AUDIT_LIST_URL, {"event_type": AuditEventType.LOGOUT}
        )
        self.assertEqual(response.status_code, 200)
        entries = response.context["entries"]
        for entry in entries:
            self.assertEqual(entry.event_type, AuditEventType.LOGOUT)

    def test_outcome_filter_success(self):
        """Filter outcome=success returns only successful entries."""
        _make_entry(outcome="success")
        _make_entry(outcome="failure")
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL, {"outcome": "success"})
        self.assertEqual(response.status_code, 200)
        entries = response.context["entries"]
        for entry in entries:
            self.assertEqual(entry.outcome, "success")

    def test_outcome_filter_failure(self):
        """Filter outcome=failure returns only failed entries."""
        _make_entry(outcome="success")
        _make_entry(outcome="failure")
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL, {"outcome": "failure"})
        self.assertEqual(response.status_code, 200)
        entries = response.context["entries"]
        for entry in entries:
            self.assertEqual(entry.outcome, "failure")

    def test_date_filter(self):
        """date_from / date_to filter by timestamp date."""
        # Create an entry and then filter for a date range that includes today.
        _make_entry()
        today_str = timezone.now().strftime("%Y-%m-%d")
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(
            AUDIT_LIST_URL,
            {"date_from": today_str, "date_to": today_str},
        )
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.context["entries"]), 1)

    def test_date_filter_excludes_old_entries(self):
        """date_from set to tomorrow excludes today's entries."""
        _make_entry()
        import datetime as dt_mod
        tomorrow = (timezone.now() + dt_mod.timedelta(days=1)).strftime("%Y-%m-%d")
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL, {"date_from": tomorrow})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["entries"]), 0)

    def test_bad_date_filter_ignored(self):
        """Malformed ?date_from= does not cause a 500 — it is silently ignored."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL, {"date_from": "notadate"})
        self.assertEqual(response.status_code, 200)

    def test_bad_date_to_filter_ignored(self):
        """Malformed ?date_to= does not cause a 500 — it is silently ignored."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL, {"date_to": "2026-99-99"})
        self.assertEqual(response.status_code, 200)

    def test_resource_type_filter(self):
        """resource_type filter uses case-insensitive contains match."""
        _make_entry(resource_type="portal.ServiceRequest")
        _make_entry(resource_type="workflows.WorkItem")
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL, {"resource_type": "portal"})
        self.assertEqual(response.status_code, 200)
        entries = response.context["entries"]
        for entry in entries:
            self.assertIn("portal", entry.resource_type.lower())

    def test_invalid_event_type_ignored(self):
        """An unrecognised event_type value is silently ignored (no filter applied)."""
        _make_entry()
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL, {"event_type": "invalid.unknown"})
        self.assertEqual(response.status_code, 200)
        # Should return all entries rather than crashing.
        self.assertGreaterEqual(len(response.context["entries"]), 1)

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def test_pagination_50_per_page(self):
        """With 51 entries, the paginator is active and only 50 are on page 1."""
        for _ in range(51):
            _make_entry()
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["entries"]), 50)

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    def test_event_type_choices_in_context(self):
        """event_type_choices list is present for the filter dropdown."""
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL)
        self.assertIn("event_type_choices", response.context)

    def test_total_count_in_context(self):
        """total_count represents the full unfiltered (or filtered) queryset size."""
        _make_entry()
        _make_entry()
        self.client.login(email="staff@example.com", password="testpass123")
        response = self.client.get(AUDIT_LIST_URL)
        self.assertIn("total_count", response.context)
        self.assertGreaterEqual(response.context["total_count"], 2)

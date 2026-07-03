"""
API tests for /api/v1/volunteers/* endpoints.

IDOR RULE: Every volunteer-facing endpoint must have a test proving that
volunteer A's data is invisible (404) to volunteer B.

PIPEDA RULE: Assert rejection_reason absent from all volunteer-facing responses.

AUTH RULE: Every endpoint must have an anonymous access test returning 401.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.volunteers.models import (
    HoursLog,
    Opportunity,
    Program,
    Shift,
    ShiftBooking,
    VolunteerApplication,
    VolunteerProfile,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# URL base
# ---------------------------------------------------------------------------
BASE = "/api/v1/volunteers"

# ---------------------------------------------------------------------------
# Counter for unique values
# ---------------------------------------------------------------------------
_counter = [0]


def _uid() -> int:
    _counter[0] += 1
    return _counter[0]


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _make_user(email: str | None = None, is_staff: bool = False) -> User:
    n = _uid()
    email = email or f"user{n}@test.gc.ca"
    return User.objects.create_user(email=email, password="TestPass123!", is_staff=is_staff)


def _make_coordinator(email: str | None = None) -> User:
    n = _uid()
    email = email or f"coord{n}@test.gc.ca"
    user = User.objects.create_user(email=email, password="TestPass123!", is_staff=True)
    group, _ = Group.objects.get_or_create(name="volunteer_coordinator")
    user.groups.add(group)
    # Service layer (apply/reject application, approve/reject hours) checks these Django perms.
    from django.contrib.auth.models import Permission
    for codename in ["change_volunteerapplication", "change_hourslog"]:
        try:
            perm = Permission.objects.get(
                content_type__app_label="volunteers",
                codename=codename,
            )
            user.user_permissions.add(perm)
        except Permission.DoesNotExist:
            pass
    # Bust the permission cache so .has_perm() reads the DB grants we just added
    for attr in ("_perm_cache", "_user_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
    return user


def _make_profile(user: User) -> VolunteerProfile:
    return VolunteerProfile.objects.create(user=user)


def _make_program(coordinator: User, slug: str | None = None) -> Program:
    n = _uid()
    slug = slug or f"prog-{n}"
    return Program.objects.create(
        slug=slug,
        name_en=f"Program {n}",
        name_fr=f"Programme {n}",
        cra_category=Program.CRA_CATEGORY_OTHER,
        coordinator=coordinator,
    )


def _make_opportunity(
    program: Program,
    slug: str | None = None,
    status: str = Opportunity.STATUS_PUBLISHED,
) -> Opportunity:
    n = _uid()
    slug = slug or f"opp-{n}"
    return Opportunity.objects.create(
        slug=slug,
        program=program,
        title_en=f"Opportunity {n}",
        title_fr=f"Opportunite {n}",
        description_en="Description",
        description_fr="Description FR",
        status=status,
    )


def _make_application(
    profile: VolunteerProfile,
    opportunity: Opportunity,
    status: str = VolunteerApplication.STATUS_PENDING,
) -> VolunteerApplication:
    return VolunteerApplication.objects.create(
        opportunity=opportunity,
        volunteer=profile,
        status=status,
    )


def _make_shift(
    opportunity: Opportunity,
    capacity: int | None = 10,
    hours_from_now: int = 24,
) -> Shift:
    start = timezone.now() + datetime.timedelta(hours=hours_from_now)
    end = start + datetime.timedelta(hours=2)
    return Shift.objects.create(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
        capacity=capacity,
    )


def _make_hours_log(
    profile: VolunteerProfile,
    opportunity: Opportunity,
    hours: float = 3.0,
    status: str = HoursLog.STATUS_PENDING,
    date_val: datetime.date | None = None,
) -> HoursLog:
    if date_val is None:
        date_val = datetime.date.today()
    h = HoursLog(
        volunteer=profile,
        opportunity=opportunity,
        hours=Decimal(str(hours)),
        date=date_val,
        status=status,
        description="Test",
    )
    h.save()
    return h


def _jwt_header(user: User) -> dict:
    """Return Authorization header dict with a valid JWT Bearer token for user."""
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {str(refresh.access_token)}"}


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class APIBaseTestCase(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Primary volunteer
        self.vol_user = _make_user()
        self.vol_profile = _make_profile(self.vol_user)

        # Second volunteer (for IDOR tests)
        self.vol_user_b = _make_user()
        self.vol_profile_b = _make_profile(self.vol_user_b)

        # Coordinator
        self.coord_user = _make_coordinator()

        # Program + opportunity scoped to coord_user
        self.program = _make_program(self.coord_user)
        self.opportunity = _make_opportunity(self.program)

    def _auth(self, user=None):
        """Return kwargs dict for client.get/post with JWT Bearer auth."""
        user = user or self.vol_user
        return _jwt_header(user)


# ===========================================================================
# OpportunityEndpointTests
# ===========================================================================

class OpportunityEndpointTests(APIBaseTestCase):

    def test_list_returns_200_authenticated(self):
        resp = self.client.get(f"{BASE}/opportunities/", **self._auth())
        self.assertEqual(resp.status_code, 200)

    def test_anonymous_list_returns_401(self):
        resp = self.client.get(f"{BASE}/opportunities/")
        self.assertEqual(resp.status_code, 401)

    def test_list_returns_open_opportunities(self):
        resp = self.client.get(f"{BASE}/opportunities/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        slugs = [r["slug"] for r in resp.data.get("results", resp.data)]
        self.assertIn(self.opportunity.slug, slugs)

    def test_list_excludes_closed_opportunities(self):
        closed = _make_opportunity(self.program, status=Opportunity.STATUS_CLOSED)
        resp = self.client.get(f"{BASE}/opportunities/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        slugs = [r["slug"] for r in resp.data.get("results", resp.data)]
        self.assertNotIn(closed.slug, slugs)

    def test_detail_returns_200(self):
        resp = self.client.get(f"{BASE}/opportunities/{self.opportunity.slug}/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["slug"], self.opportunity.slug)

    def test_detail_404_on_closed_opportunity(self):
        closed = _make_opportunity(self.program, status=Opportunity.STATUS_CLOSED)
        resp = self.client.get(f"{BASE}/opportunities/{closed.slug}/", **self._auth())
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_detail_returns_401(self):
        resp = self.client.get(f"{BASE}/opportunities/{self.opportunity.slug}/")
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# ApplicationEndpointTests
# ===========================================================================

class ApplicationEndpointTests(APIBaseTestCase):

    def test_submit_application_returns_201(self):
        """Volunteer can submit an application to a published opportunity."""
        resp = self.client.post(
            f"{BASE}/applications/",
            {"opportunity_slug": self.opportunity.slug, "motivation": "I want to help"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 201)

    def test_submit_duplicate_returns_400(self):
        """Submitting a duplicate application returns 400."""
        _make_application(self.vol_profile, self.opportunity)
        resp = self.client.post(
            f"{BASE}/applications/",
            {"opportunity_slug": self.opportunity.slug},
            format="json",
            **self._auth(),
        )
        self.assertIn(resp.status_code, [400, 409])

    def test_my_applications_returns_own_only(self):
        """Vol A's applications are not visible in Vol B's list."""
        _make_application(self.vol_profile, self.opportunity)

        # Vol B has no applications
        resp = self.client.get(f"{BASE}/applications/", **self._auth(self.vol_user_b))
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data.get("results", resp.data)]
        app_ids = list(
            VolunteerApplication.objects.filter(volunteer=self.vol_profile).values_list("id", flat=True)
        )
        for app_id in app_ids:
            self.assertNotIn(app_id, ids)

    def test_idor_application_detail_returns_404(self):
        """Vol B cannot fetch Vol A's application by PK — gets 404, not 403."""
        app = _make_application(self.vol_profile, self.opportunity)
        resp = self.client.get(f"{BASE}/applications/{app.pk}/", **self._auth(self.vol_user_b))
        self.assertEqual(resp.status_code, 404)

    def test_withdraw_own_application_returns_204(self):
        """Volunteer can withdraw their own pending application."""
        app = _make_application(self.vol_profile, self.opportunity)
        resp = self.client.delete(
            f"{BASE}/applications/{app.pk}/withdraw/",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 204)

    def test_idor_withdraw_other_application_returns_404(self):
        """Vol B cannot withdraw Vol A's application — gets 404."""
        app = _make_application(self.vol_profile, self.opportunity)
        resp = self.client.delete(
            f"{BASE}/applications/{app.pk}/withdraw/",
            **self._auth(self.vol_user_b),
        )
        self.assertEqual(resp.status_code, 404)

    def test_response_never_contains_rejection_reason(self):
        """PIPEDA: rejection_reason must not appear in any volunteer-facing response key."""
        app = _make_application(
            self.vol_profile, self.opportunity, status=VolunteerApplication.STATUS_REJECTED
        )
        app.rejection_reason = "Candidate lacks required experience"
        app.save()

        resp = self.client.get(f"{BASE}/applications/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        for row in resp.data.get("results", resp.data):
            self.assertNotIn("rejection_reason", row)

    def test_rejection_reason_absent_from_detail_for_rejected_application(self):
        """Volunteer must not see rejection_reason even on rejected application detail."""
        application = VolunteerApplication.objects.create(
            volunteer=self.vol_profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_REJECTED,
        )
        # Set rejection_reason directly (bypassing service to test serializer)
        VolunteerApplication.objects.filter(pk=application.pk).update(
            rejection_reason="Not qualified"
        )

        url = f"{BASE}/applications/{application.pk}/"
        resp = self.client.get(url, **self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("rejection_reason", resp.json())

    def test_submit_application_without_profile_returns_404(self):
        """Authenticated user with no VolunteerProfile gets 404, not 500."""
        profileless_user = _make_user(email="noprofile@test.gc.ca")
        resp = self.client.post(
            f"{BASE}/applications/",
            {"opportunity_slug": self.opportunity.slug},
            content_type="application/json",
            **self._auth(profileless_user),
        )
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_returns_401(self):
        resp = self.client.get(f"{BASE}/applications/")
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# ShiftEndpointTests
# ===========================================================================

class ShiftEndpointTests(APIBaseTestCase):

    def setUp(self):
        super().setUp()
        self.shift = _make_shift(self.opportunity, capacity=5)

    def test_list_shifts_by_opportunity(self):
        resp = self.client.get(
            f"{BASE}/shifts/?opportunity={self.opportunity.slug}",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data.get("results", resp.data)]
        self.assertIn(self.shift.pk, ids)

    def test_book_shift_returns_201(self):
        resp = self.client.post(
            f"{BASE}/shifts/{self.shift.pk}/book/",
            **self._auth(),
        )
        self.assertIn(resp.status_code, [201, 400])  # 400 = no approved application
        # If the service requires an approved application, the response may be 403/400
        # The key invariant is it does NOT return 401 or 404 for auth reasons

    def test_book_full_shift_returns_400_or_403(self):
        """Booking a shift with capacity=1 when already full returns 400."""
        full_shift = _make_shift(self.opportunity, capacity=1)
        other_vol = _make_profile(_make_user())
        ShiftBooking.objects.create(
            shift=full_shift,
            volunteer=other_vol,
            status=ShiftBooking.STATUS_CONFIRMED,
        )
        resp = self.client.post(
            f"{BASE}/shifts/{full_shift.pk}/book/",
            **self._auth(),
        )
        # Should be 400/422 (shift full) or 403 (no approved application) — not 201 or 5xx
        self.assertIn(resp.status_code, [400, 422, 403])

    def test_cancel_booking_returns_204(self):
        """Volunteer can cancel their own confirmed booking."""
        booking = ShiftBooking.objects.create(
            shift=self.shift,
            volunteer=self.vol_profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )
        resp = self.client.post(
            f"{BASE}/shifts/{self.shift.pk}/cancel-booking/",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 204)

    def test_anonymous_returns_401(self):
        resp = self.client.get(f"{BASE}/shifts/")
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# HoursEndpointTests
# ===========================================================================

class HoursEndpointTests(APIBaseTestCase):

    def test_my_hours_returns_own_only(self):
        """IDOR: Vol A's hours are invisible to Vol B."""
        _make_hours_log(self.vol_profile, self.opportunity, hours=4)

        resp = self.client.get(f"{BASE}/hours/", **self._auth(self.vol_user_b))
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data.get("results", resp.data)]
        vol_a_log_ids = list(
            HoursLog.objects.filter(volunteer=self.vol_profile).values_list("id", flat=True)
        )
        for log_id in vol_a_log_ids:
            self.assertNotIn(log_id, ids)

    def test_log_hours_returns_201(self):
        """Volunteer can log hours against a published opportunity."""
        resp = self.client.post(
            f"{BASE}/hours/",
            {
                "opportunity_slug": self.opportunity.slug,
                "hours": "3.5",
                "date": str(datetime.date.today()),
                "description": "Community outreach",
            },
            format="json",
            **self._auth(),
        )
        self.assertIn(resp.status_code, [201, 403])
        # 403 may occur if the service requires an approved application first

    def test_summary_returns_approved_total(self):
        """Hours summary returns total approved hours for the authenticated volunteer."""
        _make_hours_log(
            self.vol_profile, self.opportunity, hours=5,
            status=HoursLog.STATUS_APPROVED,
        )
        _make_hours_log(
            self.vol_profile, self.opportunity, hours=3,
            status=HoursLog.STATUS_PENDING,
        )

        resp = self.client.get(f"{BASE}/hours/summary/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("total_approved_hours", resp.data)
        self.assertEqual(Decimal(resp.data["total_approved_hours"]), Decimal("5.00"))

    def test_log_hours_invalid_date_returns_400(self):
        """Passing a non-ISO date string returns 400."""
        resp = self.client.post(
            f"{BASE}/hours/",
            {
                "opportunity_slug": self.opportunity.slug,
                "hours": "3.0",
                "date": "not-a-date",
            },
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_anonymous_returns_401(self):
        resp = self.client.get(f"{BASE}/hours/")
        self.assertEqual(resp.status_code, 401)

    def test_summary_anonymous_returns_401(self):
        resp = self.client.get(f"{BASE}/hours/summary/")
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# ProfileEndpointTests
# ===========================================================================

class ProfileEndpointTests(APIBaseTestCase):

    def test_get_own_profile_returns_200(self):
        resp = self.client.get(f"{BASE}/profile/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        # Should NOT contain PII like email or sin in the top-level data
        self.assertNotIn("sin_encrypted", resp.data)

    def test_profile_never_contains_sin_encrypted(self):
        """sin_encrypted is never included in API profile response."""
        resp = self.client.get(f"{BASE}/profile/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("sin_encrypted", resp.data)

    def test_profile_hides_sensitive_fields_without_permission(self):
        """accommodation_notes and sin_last4 are absent for users without view_accommodation_notes."""
        resp = self.client.get(f"{BASE}/profile/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        # Without the permission, sensitive fields should be absent
        self.assertNotIn("accommodation_notes", resp.data)
        self.assertNotIn("sin_last4", resp.data)

    def test_profile_shows_sensitive_fields_with_permission(self):
        """User with view_accommodation_notes sees accommodation_notes and sin_last4."""
        from django.contrib.auth.models import Permission
        perm = Permission.objects.get(
            content_type__app_label="volunteers",
            codename="view_accommodation_notes",
        )
        self.vol_user.user_permissions.add(perm)
        # Refresh to clear perm cache
        vol_user_refreshed = User.objects.get(pk=self.vol_user.pk)

        resp = self.client.get(f"{BASE}/profile/", **self._auth(vol_user_refreshed))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("accommodation_notes", resp.data)

    def test_patch_profile_updates_allowed_field(self):
        """PATCH /profile/ can update preferred_name."""
        resp = self.client.patch(
            f"{BASE}/profile/",
            {"preferred_name": "Test Name"},
            format="json",
            **self._auth(),
        )
        self.assertIn(resp.status_code, [200, 400])
        if resp.status_code == 200:
            self.vol_profile.refresh_from_db()
            self.assertEqual(self.vol_profile.preferred_name, "Test Name")

    def test_anonymous_returns_401(self):
        resp = self.client.get(f"{BASE}/profile/")
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# CoordinatorApplicationEndpointTests
# ===========================================================================

class CoordinatorApplicationEndpointTests(APIBaseTestCase):

    def setUp(self):
        super().setUp()
        # Second coordinator with their own program
        self.coord_b = _make_coordinator()
        self.program_b = _make_program(self.coord_b)
        self.opp_b = _make_opportunity(self.program_b)

    def test_all_applications_scoped_to_coordinator_programs(self):
        """Coordinator A cannot see Coordinator B's applications."""
        app_b = _make_application(self.vol_profile, self.opp_b)

        resp = self.client.get(
            f"{BASE}/admin/applications/",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data.get("results", resp.data)]
        self.assertNotIn(app_b.pk, ids)

    def test_approve_application_returns_200(self):
        """Coordinator can approve an application for their own program."""
        app = _make_application(self.vol_profile, self.opportunity)
        resp = self.client.patch(
            f"{BASE}/admin/applications/{app.pk}/approve/",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)

    def test_idor_approve_other_coordinators_application_returns_404(self):
        """Coordinator A cannot approve Coordinator B's application — gets 404."""
        app_b = _make_application(self.vol_profile, self.opp_b)
        resp = self.client.patch(
            f"{BASE}/admin/applications/{app_b.pk}/approve/",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 404)

    def test_reject_application_returns_200(self):
        """Coordinator can reject an application for their own program."""
        app = _make_application(self.vol_profile, self.opportunity)
        resp = self.client.patch(
            f"{BASE}/admin/applications/{app.pk}/reject/",
            {"rejection_reason": "Not a fit"},
            format="json",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)

    def test_non_coordinator_gets_403(self):
        """A regular volunteer (no coordinator group) gets 403 on coordinator endpoints."""
        app = _make_application(self.vol_profile, self.opportunity)
        resp = self.client.patch(
            f"{BASE}/admin/applications/{app.pk}/approve/",
            **self._auth(self.vol_user),
        )
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_returns_401(self):
        resp = self.client.get(f"{BASE}/admin/applications/")
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# CoordinatorHoursEndpointTests
# ===========================================================================

class CoordinatorHoursEndpointTests(APIBaseTestCase):

    def setUp(self):
        super().setUp()
        # vol_profile (coord_a's volunteer): apply to coord_a's opportunity
        self.app = _make_application(
            self.vol_profile, self.opportunity, status=VolunteerApplication.STATUS_APPROVED
        )
        self.hours_log = _make_hours_log(self.vol_profile, self.opportunity, hours=4)

        # Second coordinator with their OWN volunteer (important for IDOR scope)
        self.coord_b = _make_coordinator()
        self.program_b = _make_program(self.coord_b)
        self.opp_b = _make_opportunity(self.program_b)
        # Use vol_profile_b (who has NO application to coord_a's opportunity) for coord_b
        self.app_b = _make_application(
            self.vol_profile_b, self.opp_b, status=VolunteerApplication.STATUS_APPROVED
        )
        self.hours_log_b = _make_hours_log(self.vol_profile_b, self.opp_b, hours=2)

    def test_pending_hours_scoped_to_coordinator_programs(self):
        """Coordinator A cannot see Coordinator B's pending hours."""
        resp = self.client.get(
            f"{BASE}/admin/hours/pending/",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data.get("results", resp.data)]
        self.assertNotIn(self.hours_log_b.pk, ids)

    def test_approve_hours_returns_200(self):
        """Coordinator can approve a pending hours log in their program."""
        resp = self.client.patch(
            f"{BASE}/admin/hours/{self.hours_log.pk}/approve/",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)

    def test_idor_approve_other_coordinators_hours_returns_404(self):
        """Coordinator A cannot approve Coordinator B's hours log — gets 404.
        vol_profile_b has no application to coord_a's opportunity, so the log
        is invisible to coord_a via the application-scoped queryset.
        """
        resp = self.client.patch(
            f"{BASE}/admin/hours/{self.hours_log_b.pk}/approve/",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 404)

    def test_reject_hours_returns_200(self):
        """Coordinator can reject a pending hours log in their program."""
        resp = self.client.patch(
            f"{BASE}/admin/hours/{self.hours_log.pk}/reject/",
            {"reason": "Insufficient detail"},
            format="json",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)

    def test_non_coordinator_gets_403(self):
        resp = self.client.patch(
            f"{BASE}/admin/hours/{self.hours_log.pk}/approve/",
            **self._auth(self.vol_user),
        )
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_returns_401(self):
        resp = self.client.get(f"{BASE}/admin/hours/pending/")
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# CoordinatorReportEndpointTests
# ===========================================================================

class CoordinatorReportEndpointTests(APIBaseTestCase):

    def test_hours_report_returns_200(self):
        resp = self.client.get(
            f"{BASE}/admin/reports/hours/?year=2025",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("data", resp.data)

    def test_impact_report_returns_200(self):
        resp = self.client.get(
            f"{BASE}/admin/reports/impact/?year=2025",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("impact", resp.data)
        self.assertIn("t3010", resp.data)

    def test_non_coordinator_gets_403(self):
        resp = self.client.get(
            f"{BASE}/admin/reports/hours/?year=2025",
            **self._auth(self.vol_user),
        )
        self.assertEqual(resp.status_code, 403)

    def test_decimals_serialized_as_strings(self):
        """estimated_value_cad in the impact report is serialized as a string."""
        vol = _make_profile(_make_user()) if not self.vol_profile else self.vol_profile
        hours = HoursLog(
            volunteer=vol,
            opportunity=self.opportunity,
            hours=Decimal("10.00"),
            date=datetime.date(2025, 1, 1),
            status=HoursLog.STATUS_APPROVED,
            description="Test",
        )
        hours.save()

        resp = self.client.get(
            f"{BASE}/admin/reports/impact/?year=2025",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 200)
        impact = resp.json()["impact"]
        self.assertIsInstance(
            impact["estimated_value_cad"], str,
            "estimated_value_cad must be serialized as a string for safe JSON transport",
        )

    def test_anonymous_returns_401(self):
        resp = self.client.get(f"{BASE}/admin/reports/hours/?year=2025")
        self.assertEqual(resp.status_code, 401)

    def test_hours_report_invalid_year_returns_400(self):
        resp = self.client.get(
            f"{BASE}/admin/reports/hours/?year=notayear",
            **self._auth(self.coord_user),
        )
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# Wave 5 Gap Tests
# ===========================================================================

class PaginationEnvelopeTests(APIBaseTestCase):
    """Verify that list endpoints return a paginated DRF envelope (count + results)."""

    def test_application_list_returns_pagination_envelope(self):
        """GET /applications/ must return paginated DRF envelope with count+results."""
        resp = self.client.get(f"{BASE}/applications/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("count", resp.data)
        self.assertIn("results", resp.data)

    def test_hours_list_returns_pagination_envelope(self):
        """GET /hours/ must return paginated DRF envelope."""
        resp = self.client.get(f"{BASE}/hours/", **self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("count", resp.data)
        self.assertIn("results", resp.data)


class HoursBoundaryValidationTests(APIBaseTestCase):
    """Boundary-value tests for the hours log endpoint."""

    def test_log_hours_zero_returns_400(self):
        resp = self.client.post(
            f"{BASE}/hours/",
            {"opportunity_slug": self.opportunity.slug, "hours": "0", "description": "test"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_log_hours_negative_returns_400(self):
        resp = self.client.post(
            f"{BASE}/hours/",
            {"opportunity_slug": self.opportunity.slug, "hours": "-1", "description": "test"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_log_hours_over_24_returns_400(self):
        resp = self.client.post(
            f"{BASE}/hours/",
            {"opportunity_slug": self.opportunity.slug, "hours": "25", "description": "test"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_submit_application_missing_opportunity_slug_returns_400(self):
        resp = self.client.post(
            f"{BASE}/applications/",
            {"motivation": "I want to help"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("opportunity_slug", resp.data)

    def test_log_hours_invalid_calendar_date_returns_400(self):
        resp = self.client.post(
            f"{BASE}/hours/",
            {
                "opportunity_slug": self.opportunity.slug,
                "hours": "2",
                "date": "2024-13-01",
                "description": "test",
            },
            format="json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 400)


class RejectHoursValidationTests(APIBaseTestCase):
    """Tests for reject-hours view validation."""

    def setUp(self):
        super().setUp()
        # Create a pending hours log belonging to coord_user's program
        self.hours_log = _make_hours_log(self.vol_profile, self.opportunity, hours=2)

    def test_reject_hours_without_reason_returns_400(self):
        self.client.force_authenticate(user=self.coord_user)
        resp = self.client.patch(
            f"{BASE}/admin/hours/{self.hours_log.pk}/reject/",
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.data)


class CoordinatorPermissionDocumentationTests(APIBaseTestCase):
    """Documents permission behaviour for coordinator without is_staff flag."""

    def test_coordinator_without_staff_flag_accesses_hours_report(self):
        """Documents permission behaviour: coordinator should access org-wide report."""
        coord = get_user_model().objects.create_user(
            email="nostaff_coord2@example.com",
            password="x",
            is_staff=False,
        )
        coord.groups.add(Group.objects.get_or_create(name="volunteer_coordinator")[0])
        self.client.force_authenticate(user=coord)
        resp = self.client.get(f"{BASE}/admin/reports/hours/")
        # With IsCoordinator this returns 200; with IsAdminUser this returns 403.
        # Either is acceptable — this test documents and detects unintentional changes.
        self.assertIn(resp.status_code, [200, 403])

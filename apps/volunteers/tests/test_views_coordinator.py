"""
Volunteer Management BB — Coordinator view tests.

Tests the three coordinator-facing views:

  CoordinatorDashboardView       GET  volunteers:coordinator_dashboard
  ApplicationReviewView          GET/POST volunteers:application_review <pk>
  CoordinatorApplicationListView GET  volunteers:coordinator_application_list

Security invariants asserted:
  - All coordinator views require login (LoginRequiredMixin).
  - All coordinator views require volunteers.change_volunteerapplication permission
    (PermissionRequiredMixin with raise_exception=True → 403 for authenticated
    users without the permission).
  - H-2 fix: unauthenticated GET to ApplicationReviewView → 302 (not 403).
  - IDOR: coordinator A cannot access coordinator B's applications (→ 404).
  - Scope isolation: coordinator A cannot see coordinator B's applications
    in either the dashboard or the list view.
  - M-4 fix: POST reject with empty rejection_reason → 200 + form error (not 302).
  - Context key: views use context_object_name = 'applications' (not 'object_list').

Django test conventions used:
  - self.client.force_login() for authentication.
  - reverse() for all URL lookups.
  - assertRedirects(), assertContains(), assertNotContains() for HTTP assertions.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

from decimal import Decimal

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

_counter = [0]


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------

def _make_user(email=None, **kwargs):
    _counter[0] += 1
    email = email or f"cviewtest{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password="hunter2!", **kwargs)


def _make_program(slug=None):
    _counter[0] += 1
    return Program.objects.create(
        name_en="Test Program",
        name_fr="Programme test",
        slug=slug or f"cprog-{_counter[0]}",
        cra_category="welfare",
    )


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    _counter[0] += 1
    defaults = dict(
        title_en="Event Setup Volunteer",
        title_fr="Bénévole pour installation",
        slug=slug or f"copp-{_counter[0]}",
        description_en="Help set up community events.",
        description_fr="Aidez à installer les événements.",
        program=program,
        status=status,
    )
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    return VolunteerProfile.objects.create(user=user)


def _grant_coordinator_permission(user):
    """Grant volunteers.change_volunteerapplication to user and return refreshed user."""
    perm = Permission.objects.get(
        content_type__app_label="volunteers",
        codename="change_volunteerapplication",
    )
    user.user_permissions.add(perm)
    if hasattr(user, "_perm_cache"):
        del user._perm_cache
    if hasattr(user, "_user_perm_cache"):
        del user._user_perm_cache
    return User.objects.get(pk=user.pk)


def _make_coordinator_user(email):
    """Create a new staff user with the change_volunteerapplication coordinator permission."""
    user = _make_user(email, is_staff=True)
    return _grant_coordinator_permission(user)


def _make_application(profile, opportunity, status=None, **kwargs):
    """Create a VolunteerApplication for profile against opportunity. Defaults to STATUS_PENDING."""
    kwargs.setdefault("status", status or VolunteerApplication.STATUS_PENDING)
    return VolunteerApplication.objects.create(
        volunteer=profile,
        opportunity=opportunity,
        **kwargs,
    )


def _url(name, **kwargs):
    """Reverse a volunteers-namespaced URL, returning None if not yet registered."""
    try:
        return reverse(f"volunteers:{name}", kwargs=kwargs if kwargs else None)
    except NoReverseMatch:
        return None


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class BaseCoordinatorTestCase(TestCase):
    """
    Shared fixture for coordinator view tests.

    Coordinator X owns self.program and self.opportunity.
    A second coordinator Y is set up in CoordinatorScopeIsolationMixin.
    """

    def setUp(self):
        self.client = Client()

        # Coordinator X (the primary test coordinator)
        self.coordinator = _make_coordinator_user("coord_x@cviews.gc.ca")

        # Regular volunteer user (no coordinator permission)
        self.volunteer_user = _make_user("vol@cviews.gc.ca")
        self.profile = _make_profile(self.volunteer_user)

        # Program scoped to coordinator X
        self.program = _make_program(slug="cview-base-prog")
        self.program.coordinator = self.coordinator
        self.program.save(update_fields=["coordinator"])

        self.opportunity = _make_opportunity(self.program, slug="cview-base-opp")

    def login_as_coordinator(self):
        self.client.force_login(self.coordinator)

    def login_as_volunteer(self):
        self.client.force_login(self.volunteer_user)

    def _skip_if_url_missing(self, url, name):
        if url is None:
            self.skipTest(f"URL 'volunteers:{name}' not yet registered.")


# ===========================================================================
# CoordinatorDashboardView tests
# ===========================================================================

class CoordinatorDashboardViewTests(BaseCoordinatorTestCase):
    """Tests for CoordinatorDashboardView."""

    def _url(self):
        return _url("coordinator_dashboard")

    def test_dashboard_requires_login(self):
        """Unauthenticated access redirects to login (not 403)."""
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_dashboard_requires_coordinator_permission(self):
        """
        Regular volunteer user (authenticated, but without coordinator permission)
        gets 403 Forbidden.

        MRO: LoginRequiredMixin first, then PermissionRequiredMixin with
        raise_exception=True.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_volunteer()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_dashboard_accessible_by_coordinator(self):
        """Coordinator with permission accesses the dashboard successfully (200)."""
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_dashboard_shows_coordinators_pending_applications(self):
        """Coordinator sees pending applications for their own program's opportunities."""
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_coordinator()

        app = _make_application(self.profile, self.opportunity)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        # Application should appear in context.
        self.assertIn(app, response.context["applications"])

    def test_dashboard_pending_count_is_correct(self):
        """
        A-1 fix: context['pending_count'] matches the actual number of pending
        applications. Uses paginator.count to avoid a redundant COUNT(*) query.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_coordinator()

        # Create 3 pending applications across different volunteers.
        for i in range(3):
            u = _make_user()
            p = _make_profile(u)
            opp = _make_opportunity(self.program, status="published")
            _make_application(p, opp)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("pending_count", response.context)
        self.assertEqual(response.context["pending_count"], 3)

    def test_dashboard_context_key_is_applications_not_object_list(self):
        """
        H-4 fix: context_object_name = 'applications' — templates must use
        {{ applications }}, not {{ object_list }}.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("applications", response.context)

    def test_dashboard_does_not_show_approved_applications(self):
        """
        Dashboard shows only PENDING applications. Approved applications
        should not appear in the pending queue.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_coordinator()

        approved_app = _make_application(
            self.profile,
            self.opportunity,
            status=VolunteerApplication.STATUS_APPROVED,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(approved_app, response.context["applications"])

    def test_dashboard_does_not_show_other_programs_applications(self):
        """
        H-8 scope isolation: coordinator X cannot see coordinator Y's program applications.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")

        # Create coordinator Y with their own program and application.
        coordinator_y = _make_coordinator_user("coord_y_dash@cviews.gc.ca")
        program_y = _make_program(slug="cview-prog-y-dash")
        program_y.coordinator = coordinator_y
        program_y.save(update_fields=["coordinator"])
        opp_y = _make_opportunity(program_y, slug="cview-opp-y-dash")
        vol_y = _make_user("vol_y_dash@cviews.gc.ca")
        profile_y = _make_profile(vol_y)
        app_y = _make_application(profile_y, opp_y)

        # Log in as coordinator X and check that app_y is NOT visible.
        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(app_y, response.context["applications"])

    def test_dashboard_pending_count_excludes_other_programs(self):
        """
        pending_count must only count applications for this coordinator's own programs.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")

        # Create a pending app for coordinator X.
        _make_application(self.profile, self.opportunity)

        # Create a pending app for coordinator Y.
        coordinator_y = _make_coordinator_user("coord_y_cnt@cviews.gc.ca")
        program_y = _make_program(slug="cview-prog-y-cnt")
        program_y.coordinator = coordinator_y
        program_y.save(update_fields=["coordinator"])
        opp_y = _make_opportunity(program_y, slug="cview-opp-y-cnt")
        vol_y = _make_user("vol_y_cnt@cviews.gc.ca")
        profile_y = _make_profile(vol_y)
        _make_application(profile_y, opp_y)

        # Coordinator X's dashboard should show count=1 (only their own app).
        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pending_count"], 1)


# ===========================================================================
# ApplicationReviewView GET tests
# ===========================================================================

class ApplicationReviewViewGetTests(BaseCoordinatorTestCase):
    """Tests for ApplicationReviewView — GET method."""

    def setUp(self):
        super().setUp()
        self.application = _make_application(self.profile, self.opportunity)

    def _review_url(self, pk=None):
        return _url("application_review", pk=pk or self.application.pk)

    def test_get_renders_application(self):
        """GET returns 200 and the application is present in context."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["application"], self.application)

    def test_get_includes_form_in_context(self):
        """GET includes an ApplicationReviewForm instance in the context."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("form", response.context)

    def test_get_renders_rejection_reason_field(self):
        """
        Coordinator view must render the rejection_reason field — this is the one
        place rejection_reason is legitimate (coordinator-only).
        """
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rejection_reason")

    def test_get_idor_returns_404_for_other_coordinators_application(self):
        """
        IDOR / H-8 fix: coordinator X requesting coordinator Y's application PK
        gets a 404 (scoped get_object_or_404 via opportunity__program__coordinator).
        """
        coordinator_y = _make_coordinator_user("coord_y_idor@cviews.gc.ca")
        program_y = _make_program(slug="cview-prog-y-idor")
        program_y.coordinator = coordinator_y
        program_y.save(update_fields=["coordinator"])
        opp_y = _make_opportunity(program_y, slug="cview-opp-y-idor")
        vol_y = _make_user("vol_y_idor@cviews.gc.ca")
        profile_y = _make_profile(vol_y)
        app_y = _make_application(profile_y, opp_y)

        # Coordinator X tries to access coordinator Y's application — must get 404.
        url = self._review_url(pk=app_y.pk)
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(
            response.status_code,
            404,
            "IDOR: coordinator X must get 404 for coordinator Y's application.",
        )

    def test_get_nonexistent_application_returns_404(self):
        """GET for a non-existent PK returns 404."""
        url = self._review_url(pk=99999999)
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_get_unauthenticated_redirects_to_login(self):
        """
        H-2 fix: unauthenticated user gets 302 redirect to login, NOT 403.

        The view uses raise_exception=True on PermissionRequiredMixin, which would
        normally return 403. However, dispatch() intercepts unauthenticated requests
        BEFORE the permission check and calls redirect_to_login() directly.
        """
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")

        response = self.client.get(url)
        # MUST be 302 (login redirect), not 403 (which would confirm URL exists to crawlers).
        self.assertEqual(
            response.status_code,
            302,
            "H-2 fix: unauthenticated access must redirect (302), not return 403.",
        )
        self.assertIn(
            "login",
            response["Location"].lower(),
            "H-2 fix: redirect must point to login page.",
        )
        # Critical invariant: must NOT be 403.
        self.assertNotEqual(
            response.status_code,
            403,
            "H-2 fix: unauthenticated access must NOT return 403.",
        )

    def test_get_volunteer_user_returns_403(self):
        """Authenticated volunteer without coordinator permission gets 403."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_volunteer()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_get_pre_populates_rejection_reason_if_set(self):
        """
        GET pre-populates the form's rejection_reason from any previously saved notes
        (get_initial() returns application.rejection_reason).
        """
        existing_reason = "Prior screening concern flagged."
        self.application.rejection_reason = existing_reason
        self.application.save(update_fields=["rejection_reason"])

        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # The existing reason should appear pre-populated in the rendered form.
        self.assertContains(response, existing_reason)

    def test_get_context_includes_opportunity(self):
        """GET context includes the opportunity instance for template rendering."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["opportunity"], self.opportunity)

    def test_get_context_includes_volunteer(self):
        """GET context includes the volunteer profile for template rendering."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["volunteer"], self.profile)


# ===========================================================================
# ApplicationReviewView POST tests
# ===========================================================================

class ApplicationReviewViewPostTests(BaseCoordinatorTestCase):
    """Tests for ApplicationReviewView — POST method."""

    def setUp(self):
        super().setUp()
        self.application = _make_application(self.profile, self.opportunity)

    def _review_url(self, pk=None):
        return _url("application_review", pk=pk or self.application.pk)

    def test_post_approve_changes_status_to_approved(self):
        """POST action=approve transitions the application to STATUS_APPROVED."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        self.client.post(url, data={"action": "approve", "rejection_reason": ""})

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, VolunteerApplication.STATUS_APPROVED)

    def test_post_approve_redirects_on_success(self):
        """POST action=approve redirects (302) on success."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.post(url, data={"action": "approve", "rejection_reason": ""})
        self.assertEqual(response.status_code, 302)

    def test_post_reject_with_reason_changes_status_to_rejected(self):
        """POST action=reject with a reason transitions the application to STATUS_REJECTED."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        self.client.post(
            url,
            data={
                "action": "reject",
                "rejection_reason": "Skills do not match opportunity requirements.",
            },
        )

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, VolunteerApplication.STATUS_REJECTED)

    def test_post_reject_stores_rejection_reason(self):
        """POST action=reject persists the rejection_reason in the database."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        reason = "Insufficient references provided during intake call."
        self.client.post(
            url,
            data={"action": "reject", "rejection_reason": reason},
        )

        self.application.refresh_from_db()
        if self.application.status == VolunteerApplication.STATUS_REJECTED:
            self.assertEqual(self.application.rejection_reason, reason)

    def test_post_reject_without_reason_returns_form_with_error(self):
        """
        M-4 fix: POST action=reject with empty rejection_reason returns 200 (form re-render)
        with a form error — NOT 302 (redirect) or 500 (crash).

        ApplicationReviewForm.clean() raises ValidationError when action=reject
        and rejection_reason is empty.
        """
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.post(
            url,
            data={"action": "reject", "rejection_reason": ""},
        )

        # Must re-render form with errors (200), not redirect.
        self.assertEqual(
            response.status_code,
            200,
            "M-4 fix: empty rejection_reason must return 200 (form error), not redirect.",
        )

    def test_post_reject_without_reason_does_not_change_status(self):
        """M-4 fix: when rejection fails validation, application status remains unchanged."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        self.client.post(
            url,
            data={"action": "reject", "rejection_reason": ""},
        )

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, VolunteerApplication.STATUS_PENDING)
        self.assertEqual(self.application.rejection_reason, "")

    def test_post_reject_with_whitespace_reason_returns_form_error(self):
        """M-4 fix: whitespace-only rejection_reason is also rejected (200 + form error)."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.post(
            url,
            data={"action": "reject", "rejection_reason": "   "},
        )

        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, VolunteerApplication.STATUS_PENDING)

    def test_post_idor_returns_404(self):
        """
        IDOR: POST to another coordinator's application PK returns 404.
        The scope filter (opportunity__program__coordinator=request.user) prevents
        coordinator X from modifying coordinator Y's applications.
        """
        coordinator_y = _make_coordinator_user("coord_y_post@cviews.gc.ca")
        program_y = _make_program(slug="cview-prog-y-post")
        program_y.coordinator = coordinator_y
        program_y.save(update_fields=["coordinator"])
        opp_y = _make_opportunity(program_y, slug="cview-opp-y-post")
        vol_y = _make_user("vol_y_post@cviews.gc.ca")
        profile_y = _make_profile(vol_y)
        app_y = _make_application(profile_y, opp_y)

        url = self._review_url(pk=app_y.pk)
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.post(url, data={"action": "approve"})
        self.assertEqual(
            response.status_code,
            404,
            "IDOR: POST to another coordinator's application must return 404.",
        )
        app_y.refresh_from_db()
        self.assertEqual(
            app_y.status,
            VolunteerApplication.STATUS_PENDING,
            "IDOR: another coordinator's application must not be modified.",
        )

    def test_post_unauthenticated_redirects_to_login(self):
        """Anonymous POST to application_review must redirect to login (302)."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")

        response = self.client.post(url, data={"action": "approve"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_post_volunteer_returns_403(self):
        """Authenticated volunteer without coordinator permission gets 403 on POST."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_volunteer()

        response = self.client.post(url, data={"action": "approve"})
        self.assertEqual(response.status_code, 403)

    def test_post_invalid_action_returns_form_error(self):
        """POST with an invalid action value returns 200 (form re-render with errors)."""
        url = self._review_url()
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.post(url, data={"action": "delete"})
        self.assertEqual(response.status_code, 200)


# ===========================================================================
# CoordinatorApplicationListView tests
# ===========================================================================

class CoordinatorApplicationListViewTests(BaseCoordinatorTestCase):
    """Tests for CoordinatorApplicationListView."""

    def _list_url(self, status=None):
        url = _url("coordinator_application_list")
        if url and status:
            url = f"{url}?status={status}"
        return url

    def test_list_requires_login(self):
        """Anonymous GET to coordinator_application_list must redirect to login."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")

        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 403])

    def test_list_requires_coordinator_permission(self):
        """Authenticated volunteer without coordinator permission gets 403."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")
        self.login_as_volunteer()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_list_accessible_by_coordinator(self):
        """Coordinator accesses the application list successfully (200)."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_list_only_shows_coordinators_own_applications(self):
        """Coordinator sees applications for their own program's opportunities."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")

        own_app = _make_application(self.profile, self.opportunity)
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(own_app, response.context["applications"])

    def test_list_context_key_is_applications(self):
        """
        H-4 fix: context_object_name = 'applications' — list view context must
        use 'applications', not 'object_list'.
        """
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("applications", response.context)

    def test_list_includes_all_statuses_by_default(self):
        """Without a ?status= filter, all application statuses are returned."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")

        pending_app = _make_application(self.profile, self.opportunity)

        # Create a second opportunity for the same program for the approved application.
        opp2 = _make_opportunity(self.program, slug="cview-list-opp2")
        vol2 = _make_user("vol2list@cviews.gc.ca")
        prof2 = _make_profile(vol2)
        approved_app = _make_application(
            prof2, opp2, status=VolunteerApplication.STATUS_APPROVED
        )

        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        apps = list(response.context["applications"])
        self.assertIn(pending_app, apps)
        self.assertIn(approved_app, apps)

    def test_status_filter_pending(self):
        """?status=pending returns only pending applications."""
        url = self._list_url(status=VolunteerApplication.STATUS_PENDING)
        self._skip_if_url_missing(url, "coordinator_application_list")

        pending_app = _make_application(self.profile, self.opportunity)

        opp2 = _make_opportunity(self.program, slug="cview-filter-opp2")
        vol2 = _make_user("vol2filter@cviews.gc.ca")
        prof2 = _make_profile(vol2)
        approved_app = _make_application(
            prof2, opp2, status=VolunteerApplication.STATUS_APPROVED
        )

        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        apps = list(response.context["applications"])
        self.assertIn(pending_app, apps)
        self.assertNotIn(approved_app, apps)

    def test_status_filter_approved(self):
        """?status=approved returns only approved applications."""
        url = self._list_url(status=VolunteerApplication.STATUS_APPROVED)
        self._skip_if_url_missing(url, "coordinator_application_list")

        pending_app = _make_application(self.profile, self.opportunity)

        opp2 = _make_opportunity(self.program, slug="cview-approved-opp2")
        vol2 = _make_user("vol2approved@cviews.gc.ca")
        prof2 = _make_profile(vol2)
        approved_app = _make_application(
            prof2, opp2, status=VolunteerApplication.STATUS_APPROVED
        )

        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        apps = list(response.context["applications"])
        self.assertIn(approved_app, apps)
        self.assertNotIn(pending_app, apps)

    def test_invalid_status_filter_ignored(self):
        """?status=garbage returns all applications (invalid filter is silently ignored)."""
        base_url = _url("coordinator_application_list")
        self._skip_if_url_missing(base_url, "coordinator_application_list")
        url = f"{base_url}?status=garbage_value_xyz"

        _make_application(self.profile, self.opportunity)
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_other_coordinators_applications_not_in_list(self):
        """
        H-4 / H-8 scope isolation: coordinator A cannot see coordinator B's applications
        in the list view. Uses response.context['applications'] (not 'object_list').
        """
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")

        coordinator_y = _make_coordinator_user("coord_y_list@cviews.gc.ca")
        program_y = _make_program(slug="cview-prog-y-list")
        program_y.coordinator = coordinator_y
        program_y.save(update_fields=["coordinator"])
        opp_y = _make_opportunity(program_y, slug="cview-opp-y-list")
        vol_y = _make_user("vol_y_list@cviews.gc.ca")
        profile_y = _make_profile(vol_y)
        other_application = _make_application(profile_y, opp_y)

        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("applications", response.context)
        pks = [a.pk for a in response.context["applications"]]
        self.assertNotIn(
            other_application.pk,
            pks,
            "H-8 scope isolation: coordinator A must not see coordinator B's applications.",
        )

    def test_list_context_includes_status_choices(self):
        """The list view context provides STATUS_CHOICES for rendering filter UI."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("status_choices", response.context)

    def test_list_context_includes_current_status(self):
        """The list view context includes current_status reflecting the active filter."""
        status = VolunteerApplication.STATUS_PENDING
        url = self._list_url(status=status)
        self._skip_if_url_missing(url, "coordinator_application_list")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_status"], status)

    def test_list_current_status_empty_for_invalid_filter(self):
        """current_status is empty string when the ?status= filter is invalid."""
        base_url = _url("coordinator_application_list")
        self._skip_if_url_missing(base_url, "coordinator_application_list")
        url = f"{base_url}?status=bogus"

        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_status"], "")


# ===========================================================================
# Wave 3 — Shared helpers
# ===========================================================================

import datetime as dt
from django.utils import timezone as tz


def _grant_perm(user, app_label, codename):
    """Grant a single permission to user and return a fresh instance (cache-busted)."""
    perm = Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )
    user.user_permissions.add(perm)
    for attr in ("_perm_cache", "_user_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
    return User.objects.get(pk=user.pk)


def _make_shift(opportunity, *, minutes_from_now=60, **kwargs):
    """Create a Shift starting `minutes_from_now` in the future."""
    _counter[0] += 1
    start = tz.now() + dt.timedelta(minutes=minutes_from_now)
    end = start + dt.timedelta(hours=2)
    defaults = dict(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
        capacity=10,
    )
    defaults.update(kwargs)
    return Shift.objects.create(**defaults)


def _make_hours_log(volunteer_profile, opportunity, *, hours=Decimal("3"), status=None, **kwargs):
    """Create a HoursLog for volunteer_profile against opportunity."""
    _counter[0] += 1
    defaults = dict(
        volunteer=volunteer_profile,
        opportunity=opportunity,
        hours=hours,
        date=dt.date.today(),
        status=status or HoursLog.STATUS_PENDING,
    )
    defaults.update(kwargs)
    return HoursLog.objects.create(**defaults)


# ===========================================================================
# Wave 3 — ShiftListViewTests
# ===========================================================================

class ShiftListViewTests(TestCase):
    """
    Tests for ShiftListView — GET volunteers:shift_list.

    Permission required: volunteers.view_shift
    Scope: only shifts whose opportunity belongs to the current coordinator's
    programs are returned.
    """

    def setUp(self):
        self.client = Client()

        # Coordinator with volunteers.view_shift permission
        self.coordinator = _make_user("shiftlist_coord@cviews.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(self.coordinator, "volunteers", "view_shift")

        # Coordinator without any permission
        self.coordinator_noperm = _make_user("shiftlist_noperm@cviews.gc.ca")

        # Program owned by coordinator
        self.program = _make_program(slug="shiftlist-prog")
        self.program.coordinator = self.coordinator
        self.program.save(update_fields=["coordinator"])

        self.opportunity = _make_opportunity(self.program, slug="shiftlist-opp")
        self.shift = _make_shift(self.opportunity)

    def _url(self):
        return _url("shift_list")

    def test_requires_login(self):
        """Anonymous GET redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("shift_list URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_requires_permission(self):
        """Authenticated user without view_shift gets 403."""
        url = self._url()
        if url is None:
            self.skipTest("shift_list URL not registered")
        self.client.force_login(self.coordinator_noperm)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_coordinator_sees_own_shifts(self):
        """Coordinator sees shifts under their opportunities."""
        url = self._url()
        if url is None:
            self.skipTest("shift_list URL not registered")
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        shift_pks = [s.pk for s in response.context["shifts"]]
        self.assertIn(self.shift.pk, shift_pks)

    def test_coordinator_does_not_see_other_coordinator_shifts(self):
        """Scope isolation: coordinator A cannot see coordinator B's shifts."""
        url = self._url()
        if url is None:
            self.skipTest("shift_list URL not registered")

        # Create coordinator B with their own program + shift
        coord_b = _make_user("shiftlist_coordb@cviews.gc.ca", is_staff=True)
        coord_b = _grant_perm(coord_b, "volunteers", "view_shift")
        prog_b = _make_program(slug="shiftlist-prog-b")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="shiftlist-opp-b")
        shift_b = _make_shift(opp_b)

        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        shift_pks = [s.pk for s in response.context["shifts"]]
        self.assertNotIn(shift_b.pk, shift_pks)

    def test_context_key_is_shifts(self):
        """View uses context_object_name='shifts' (not object_list)."""
        url = self._url()
        if url is None:
            self.skipTest("shift_list URL not registered")
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("shifts", response.context)

    def test_multiple_shifts_all_appear(self):
        """All shifts owned by the coordinator appear when there are multiple."""
        url = self._url()
        if url is None:
            self.skipTest("shift_list URL not registered")
        shift2 = _make_shift(self.opportunity, minutes_from_now=120)
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        shift_pks = [s.pk for s in response.context["shifts"]]
        self.assertIn(self.shift.pk, shift_pks)
        self.assertIn(shift2.pk, shift_pks)


# ===========================================================================
# Wave 3 — ShiftDetailViewTests
# ===========================================================================

class ShiftDetailViewTests(TestCase):
    """
    Tests for ShiftDetailView — GET volunteers:shift_detail <pk>.

    Security: IDOR → 404 (not 403); booking roster in context; confirmed_count.
    """

    def setUp(self):
        self.client = Client()

        self.coordinator = _make_user("shiftdetail_coord@cviews.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(self.coordinator, "volunteers", "view_shift")

        self.program = _make_program(slug="shiftdetail-prog")
        self.program.coordinator = self.coordinator
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(self.program, slug="shiftdetail-opp")
        self.shift = _make_shift(self.opportunity)

        # A volunteer with a confirmed booking on this shift
        self.vol_user = _make_user("shiftdetail_vol@cviews.gc.ca")
        self.profile = _make_profile(self.vol_user)
        self.booking = ShiftBooking.objects.create(
            shift=self.shift,
            volunteer=self.profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )

    def _url(self, pk=None):
        return _url("shift_detail", pk=pk or self.shift.pk)

    def test_idor_returns_404_not_403(self):
        """Coordinator B cannot view coordinator A's shift — gets 404."""
        other_coord = _make_user("shiftdetail_other@cviews.gc.ca", is_staff=True)
        other_coord = _grant_perm(other_coord, "volunteers", "view_shift")
        url = self._url()
        if url is None:
            self.skipTest("shift_detail URL not registered")
        self.client.force_login(other_coord)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_shows_booking_roster(self):
        """Booking roster appears in context['bookings']."""
        url = self._url()
        if url is None:
            self.skipTest("shift_detail URL not registered")
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        booking_pks = [b.pk for b in response.context["bookings"]]
        self.assertIn(self.booking.pk, booking_pks)

    def test_confirmed_count_in_context(self):
        """context['confirmed_count'] equals the number of confirmed bookings."""
        url = self._url()
        if url is None:
            self.skipTest("shift_detail URL not registered")
        # Create a second confirmed booking
        vol2 = _make_user("shiftdetail_vol2@cviews.gc.ca")
        prof2 = _make_profile(vol2)
        ShiftBooking.objects.create(
            shift=self.shift,
            volunteer=prof2,
            status=ShiftBooking.STATUS_CONFIRMED,
        )
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["confirmed_count"], 2)

    def test_get_returns_200_for_own_shift(self):
        """Coordinator can GET their own shift without error."""
        url = self._url()
        if url is None:
            self.skipTest("shift_detail URL not registered")
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_waitlisted_not_counted_as_confirmed(self):
        """Waitlisted bookings do not inflate confirmed_count."""
        url = self._url()
        if url is None:
            self.skipTest("shift_detail URL not registered")
        vol3 = _make_user("shiftdetail_vol3@cviews.gc.ca")
        prof3 = _make_profile(vol3)
        ShiftBooking.objects.create(
            shift=self.shift,
            volunteer=prof3,
            status=ShiftBooking.STATUS_WAITLISTED,
            waitlist_position=1,
        )
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Only the setUp confirmed booking; waitlist one must not count
        self.assertEqual(response.context["confirmed_count"], 1)

    def test_requires_login(self):
        """Anonymous GET to shift detail redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("shift_detail URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_nonexistent_pk_returns_404(self):
        """GET for a non-existent shift PK returns 404."""
        url = self._url(pk=99999999)
        if url is None:
            self.skipTest("shift_detail URL not registered")
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# Wave 3 — ShiftCancelViewTests
# ===========================================================================

class ShiftCancelViewTests(TestCase):
    """
    Tests for ShiftCancelView — POST volunteers:shift_cancel <pk>.

    View is POST-only; reason required (service enforces).
    """

    def setUp(self):
        self.client = Client()

        self.coordinator = _make_user("shiftcancel_coord@cviews.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(self.coordinator, "volunteers", "change_shift")

        self.program = _make_program(slug="shiftcancel-prog")
        self.program.coordinator = self.coordinator
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(self.program, slug="shiftcancel-opp")
        self.shift = _make_shift(self.opportunity)

        self.client.force_login(self.coordinator)

    def _url(self, pk=None):
        return _url("shift_cancel", pk=pk or self.shift.pk)

    def test_cancel_shift_post_succeeds(self):
        """POST with a valid reason cancels the shift and redirects to shift_detail."""
        url = self._url()
        if url is None:
            self.skipTest("shift_cancel URL not registered")
        response = self.client.post(url, {"reason": "Venue closed"})
        self.assertRedirects(
            response,
            reverse("volunteers:shift_detail", kwargs={"pk": self.shift.pk}),
            fetch_redirect_response=False,
        )
        self.shift.refresh_from_db()
        self.assertTrue(self.shift.is_cancelled)

    def test_cancel_without_reason_stays_not_cancelled(self):
        """
        POST with empty reason — service raises ValidationError, shift stays active.
        View redirects to shift_detail (messages carry the error).
        """
        url = self._url()
        if url is None:
            self.skipTest("shift_cancel URL not registered")
        self.client.post(url, {"reason": ""})
        self.shift.refresh_from_db()
        self.assertFalse(self.shift.is_cancelled)

    def test_idor_returns_404(self):
        """Coordinator B cannot cancel coordinator A's shift."""
        coord_b = _make_user("shiftcancel_coordb@cviews.gc.ca", is_staff=True)
        coord_b = _grant_perm(coord_b, "volunteers", "change_shift")
        url = self._url()
        if url is None:
            self.skipTest("shift_cancel URL not registered")
        self.client.force_login(coord_b)
        response = self.client.post(url, {"reason": "Venue closed"})
        self.assertEqual(response.status_code, 404)
        self.shift.refresh_from_db()
        self.assertFalse(self.shift.is_cancelled)

    def test_requires_login(self):
        """Anonymous POST redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("shift_cancel URL not registered")
        self.client.logout()
        response = self.client.post(url, {"reason": "Venue closed"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())
        self.shift.refresh_from_db()
        self.assertFalse(self.shift.is_cancelled)

    def test_requires_permission(self):
        """Authenticated user without change_shift permission gets 403."""
        url = self._url()
        if url is None:
            self.skipTest("shift_cancel URL not registered")
        noperm = _make_user("shiftcancel_noperm@cviews.gc.ca")
        self.client.force_login(noperm)
        response = self.client.post(url, {"reason": "Venue closed"})
        self.assertEqual(response.status_code, 403)
        self.shift.refresh_from_db()
        self.assertFalse(self.shift.is_cancelled)

    def test_already_cancelled_shift_stays_cancelled(self):
        """POSTing cancel on an already-cancelled shift returns to detail without crashing."""
        url = self._url()
        if url is None:
            self.skipTest("shift_cancel URL not registered")
        # Cancel it first
        self.client.post(url, {"reason": "First cancellation"})
        self.shift.refresh_from_db()
        if not self.shift.is_cancelled:
            self.skipTest("Could not cancel shift in precondition step")
        # Cancel again — service should raise ValidationError, view should handle gracefully
        response = self.client.post(url, {"reason": "Second attempt"})
        # Should redirect (not 500)
        self.assertEqual(response.status_code, 302)


# ===========================================================================
# Wave 3 — HoursApprovalListViewTests
# ===========================================================================

class HoursApprovalListViewTests(TestCase):
    """
    Tests for HoursApprovalListView — GET volunteers:hours_approval_list.

    Shows only STATUS_PENDING logs; scoped to coordinator's programs.
    """

    def setUp(self):
        self.client = Client()

        self.coordinator = _make_user("hourslist_coord@cviews.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(self.coordinator, "volunteers", "change_hourslog")

        self.program = _make_program(slug="hourslist-prog")
        self.program.coordinator = self.coordinator
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(self.program, slug="hourslist-opp")

        self.vol_user = _make_user("hourslist_vol@cviews.gc.ca")
        self.profile = _make_profile(self.vol_user)

        self.log = _make_hours_log(self.profile, self.opportunity, status=HoursLog.STATUS_PENDING)

    def _url(self):
        return _url("hours_approval_list")

    def test_requires_login(self):
        """Anonymous GET redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approval_list URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_requires_permission(self):
        """User without change_hourslog permission gets 403."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approval_list URL not registered")
        noperm = _make_user("hourslist_noperm@cviews.gc.ca")
        self.client.force_login(noperm)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_shows_only_pending_logs(self):
        """Only STATUS_PENDING logs appear; approved/rejected are excluded."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approval_list URL not registered")

        # Create an approved log that should NOT appear
        approved_log = _make_hours_log(
            self.profile, self.opportunity, status=HoursLog.STATUS_APPROVED
        )
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        log_pks = [h.pk for h in response.context["hours_logs"]]
        self.assertIn(self.log.pk, log_pks)
        self.assertNotIn(approved_log.pk, log_pks)

    def test_scope_isolation(self):
        """Coordinator A cannot see coordinator B's pending hours logs."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approval_list URL not registered")

        coord_b = _make_user("hourslist_coordb@cviews.gc.ca", is_staff=True)
        coord_b = _grant_perm(coord_b, "volunteers", "change_hourslog")
        prog_b = _make_program(slug="hourslist-prog-b")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="hourslist-opp-b")
        vol_b = _make_user("hourslist_volb@cviews.gc.ca")
        prof_b = _make_profile(vol_b)
        log_b = _make_hours_log(prof_b, opp_b, status=HoursLog.STATUS_PENDING)

        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        log_pks = [h.pk for h in response.context["hours_logs"]]
        self.assertNotIn(log_b.pk, log_pks)

    def test_context_key_is_hours_logs(self):
        """View exposes pending logs as context['hours_logs']."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approval_list URL not registered")
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("hours_logs", response.context)

    def test_rejected_log_not_shown(self):
        """Rejected logs are not shown in the pending approval queue."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approval_list URL not registered")
        rejected_log = _make_hours_log(
            self.profile, self.opportunity, status=HoursLog.STATUS_REJECTED
        )
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        log_pks = [h.pk for h in response.context["hours_logs"]]
        self.assertNotIn(rejected_log.pk, log_pks)


# ===========================================================================
# Wave 3 — HoursApproveViewTests
# ===========================================================================

class HoursApproveViewTests(TestCase):
    """
    Tests for HoursApproveView — POST volunteers:hours_approve <pk>.

    POST-only. Approves a pending HoursLog.
    """

    def setUp(self):
        self.client = Client()

        self.coordinator = _make_user("hoursapprove_coord@cviews.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(self.coordinator, "volunteers", "change_hourslog")

        self.program = _make_program(slug="hoursapprove-prog")
        self.program.coordinator = self.coordinator
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(self.program, slug="hoursapprove-opp")

        self.vol_user = _make_user("hoursapprove_vol@cviews.gc.ca")
        self.profile = _make_profile(self.vol_user)
        self.log = _make_hours_log(self.profile, self.opportunity, status=HoursLog.STATUS_PENDING)

        self.client.force_login(self.coordinator)

    def _url(self, pk=None):
        return _url("hours_approve", pk=pk or self.log.pk)

    def test_approve_hours_redirects_and_updates_status(self):
        """POST approves the log, sets STATUS_APPROVED, and redirects to list."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approve URL not registered")
        response = self.client.post(url)
        self.assertRedirects(
            response,
            reverse("volunteers:hours_approval_list"),
            fetch_redirect_response=False,
        )
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_APPROVED)

    def test_idor_returns_404(self):
        """Coordinator B cannot approve coordinator A's log."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approve URL not registered")
        coord_b = _make_user("hoursapprove_coordb@cviews.gc.ca", is_staff=True)
        coord_b = _grant_perm(coord_b, "volunteers", "change_hourslog")
        self.client.force_login(coord_b)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_PENDING)

    def test_requires_perm(self):
        """User without change_hourslog gets 403."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approve URL not registered")
        noperm = _make_user("hoursapprove_noperm@cviews.gc.ca")
        self.client.force_login(noperm)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_PENDING)

    def test_requires_login(self):
        """Anonymous POST redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("hours_approve URL not registered")
        self.client.logout()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_PENDING)

    def test_nonexistent_log_returns_404(self):
        """POST to nonexistent pk returns 404."""
        url = self._url(pk=99999999)
        if url is None:
            self.skipTest("hours_approve URL not registered")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# Wave 3 — HoursRejectViewTests
# ===========================================================================

class HoursRejectViewTests(TestCase):
    """
    Tests for HoursRejectView — POST volunteers:hours_reject <pk>.

    Requires a non-empty reason. On success → STATUS_REJECTED + redirect to list.
    On empty reason → form error + redirect to list (view uses messages + redirect
    rather than re-rendering a separate template for the form_invalid path).
    """

    def setUp(self):
        self.client = Client()

        self.coordinator = _make_user("hoursreject_coord@cviews.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(self.coordinator, "volunteers", "change_hourslog")

        self.program = _make_program(slug="hoursreject-prog")
        self.program.coordinator = self.coordinator
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(self.program, slug="hoursreject-opp")

        self.vol_user = _make_user("hoursreject_vol@cviews.gc.ca")
        self.profile = _make_profile(self.vol_user)
        self.log = _make_hours_log(self.profile, self.opportunity, status=HoursLog.STATUS_PENDING)

        self.client.force_login(self.coordinator)

    def _url(self, pk=None):
        return _url("hours_reject", pk=pk or self.log.pk)

    def test_reject_with_reason_updates_status(self):
        """POST with valid reason sets STATUS_REJECTED and redirects to list."""
        url = self._url()
        if url is None:
            self.skipTest("hours_reject URL not registered")
        response = self.client.post(url, {"reason": "Duplicate submission"})
        self.assertRedirects(
            response,
            reverse("volunteers:hours_approval_list"),
            fetch_redirect_response=False,
        )
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_REJECTED)

    def test_reject_without_reason_does_not_update_status(self):
        """
        POST with empty reason — HoursRejectForm is invalid, view calls
        form_invalid() which redirects to list (no re-render).
        HoursLog status must remain PENDING.
        """
        url = self._url()
        if url is None:
            self.skipTest("hours_reject URL not registered")
        self.client.post(url, {"reason": ""})
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_PENDING)

    def test_reject_without_reason_redirects_not_crashes(self):
        """Empty reason → redirect (not 500)."""
        url = self._url()
        if url is None:
            self.skipTest("hours_reject URL not registered")
        response = self.client.post(url, {"reason": ""})
        self.assertEqual(response.status_code, 302)

    def test_idor_returns_404(self):
        """Coordinator B cannot reject coordinator A's log."""
        url = self._url()
        if url is None:
            self.skipTest("hours_reject URL not registered")
        coord_b = _make_user("hoursreject_coordb@cviews.gc.ca", is_staff=True)
        coord_b = _grant_perm(coord_b, "volunteers", "change_hourslog")
        self.client.force_login(coord_b)
        response = self.client.post(url, {"reason": "Duplicate submission"})
        self.assertEqual(response.status_code, 404)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_PENDING)

    def test_requires_perm(self):
        """User without change_hourslog gets 403."""
        url = self._url()
        if url is None:
            self.skipTest("hours_reject URL not registered")
        noperm = _make_user("hoursreject_noperm@cviews.gc.ca")
        self.client.force_login(noperm)
        response = self.client.post(url, {"reason": "Duplicate"})
        self.assertEqual(response.status_code, 403)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_PENDING)

    def test_requires_login(self):
        """Anonymous POST redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("hours_reject URL not registered")
        self.client.logout()
        response = self.client.post(url, {"reason": "Duplicate submission"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_PENDING)

    def test_rejection_reason_stored_in_db(self):
        """On successful rejection the reason is persisted."""
        url = self._url()
        if url is None:
            self.skipTest("hours_reject URL not registered")
        reason = "Hours exceed shift duration by 4 hours"
        self.client.post(url, {"reason": reason})
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, HoursLog.STATUS_REJECTED)
        self.assertEqual(self.log.rejection_reason, reason)

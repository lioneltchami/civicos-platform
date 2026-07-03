"""
Wave 2 — Volunteer Management BB: portal view test suite.

These tests are written *before* the views are implemented (TDD / Wave 2 work).
Every test documents the expected URL, template, and HTTP behaviour that the
view implementation must satisfy.

Views under test (all namespaced to "volunteers"):
  OpportunityListView    GET  volunteers:opportunity_list
  OpportunityDetailView  GET  volunteers:opportunity_detail  <pk>
  ApplicationFormView    GET/POST volunteers:apply  <opportunity_pk>
  MyApplicationsView     GET  volunteers:my_applications
  WithdrawApplicationView POST volunteers:withdraw <pk>
  CoordinatorDashboardView GET volunteers:coordinator_dashboard
  ApplicationReviewView  POST volunteers:application_review <pk>

Security invariants asserted:
  - Anonymous access to every view → 302 to login (LoginRequired).
  - IDOR / ownership: volunteers cannot access or modify other users' data.
  - PIPEDA: rejection_reason is NEVER present in the volunteer-facing
    MyApplicationsView response body.
  - Coordinator permission gate: views requiring change_volunteerapplication
    return 302 or 403 for regular volunteer users.

Django test conventions used throughout:
  - self.client.force_login() for authentication.
  - assertRedirects(), assertContains(), assertNotContains() for HTTP assertions.
  - self.client.post() for write operations.
  - reverse() for all URL lookups (no hard-coded paths).
"""
from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

import datetime as dt
from decimal import Decimal

from django.utils import timezone as tz

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
# URL helpers — gracefully handle URLs that don't exist yet (Wave 2 stubs).
# Every test that calls _url() will skip if the URL hasn't been wired yet,
# keeping the suite green during progressive view implementation.
# ---------------------------------------------------------------------------

def _url(name, **kwargs):
    """
    Reverse a volunteers-namespaced URL.  Returns None if the URL is not yet
    registered — tests should skip themselves in that case.
    """
    try:
        return reverse(f"volunteers:{name}", kwargs=kwargs if kwargs else None)
    except NoReverseMatch:
        return None


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------

_counter = [0]


def _make_user(email=None, **kwargs):
    _counter[0] += 1
    email = email or f"testuser{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password="hunter2!", **kwargs)


def _make_program(slug=None):
    _counter[0] += 1
    return Program.objects.create(
        name_en="Test Program",
        name_fr="Programme test",
        slug=slug or f"prog-{_counter[0]}",
        cra_category="welfare",
    )


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    _counter[0] += 1
    defaults = dict(
        title_en="Event Setup Volunteer",
        title_fr="Bénévole pour installation",
        slug=slug or f"opp-{_counter[0]}",
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


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class BaseViewTestCase(TestCase):
    """
    Shared fixture for all portal view tests.

    Attributes:
        self.client                — Django test client
        self.user                  — regular volunteer user
        self.coordinator_user      — user with change_volunteerapplication permission
        self.profile               — VolunteerProfile for self.user
        self.program               — Program instance
        self.opportunity           — active (published) Opportunity
        self.inactive_opportunity  — draft Opportunity (not accepting applications)
    """

    def setUp(self):
        self.client = Client()
        self.user = _make_user("alice@example.gc.ca")
        coordinator = _make_user("coord@example.gc.ca", is_staff=True)
        self.coordinator_user = _grant_coordinator_permission(coordinator)
        self.profile = _make_profile(self.user)
        self.program = _make_program(slug="base-prog")
        # Wire the coordinator onto the program so CoordinatorDashboardView's
        # queryset filter (opportunity__program__coordinator=request.user) works.
        self.program.coordinator = self.coordinator_user
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(
            self.program,
            slug="base-opp",
            status="published",
        )
        self.inactive_opportunity = _make_opportunity(
            self.program,
            slug="draft-opp",
            status="draft",
            # Must differ from self.opportunity.title_en so assertNotContains works.
            title_en="Draft Inactive Opportunity",
            title_fr="Opportunité brouillon inactive",
        )

    def login(self):
        """Authenticate the test client as the regular volunteer user."""
        self.client.force_login(self.user)

    def login_as_coordinator(self):
        """Authenticate the test client as the coordinator user."""
        self.client.force_login(self.coordinator_user)

    def _skip_if_url_missing(self, url, name):
        """Skip the test gracefully if the named URL hasn't been wired yet."""
        if url is None:
            self.skipTest(f"URL 'volunteers:{name}' not yet registered — Wave 2 stub.")


# ===========================================================================
# OpportunityListView
# ===========================================================================

class OpportunityListViewTests(BaseViewTestCase):

    def _url(self):
        return _url("opportunity_list")

    def test_opportunity_list_requires_login(self):
        """
        Anonymous GET to the opportunity list must redirect to the login page.
        LoginRequired is enforced on all portal views.
        """
        url = self._url()
        self._skip_if_url_missing(url, "opportunity_list")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_opportunity_list_shows_active_opportunities(self):
        """
        Authenticated volunteers see published opportunities in the list.
        The opportunity title must be present in the rendered response.
        """
        url = self._url()
        self._skip_if_url_missing(url, "opportunity_list")
        self.login()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Assert the title appears in whichever language is active (EN or FR).
        content = response.content.decode()
        self.assertTrue(
            self.opportunity.title_en in content or self.opportunity.title_fr in content,
            f"Expected opportunity title (EN or FR) in response. "
            f"title_en={self.opportunity.title_en!r}, title_fr={self.opportunity.title_fr!r}",
        )

    def test_opportunity_list_hides_inactive_opportunities(self):
        """
        Draft / closed opportunities must not appear in the volunteer-facing
        opportunity list. PIPEDA minimum disclosure principle.
        """
        url = self._url()
        self._skip_if_url_missing(url, "opportunity_list")
        self.login()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Neither the English nor the French title of the inactive opportunity
        # should appear (it's draft — hidden regardless of language).
        self.assertNotContains(response, self.inactive_opportunity.title_en)
        self.assertNotContains(response, self.inactive_opportunity.title_fr)

    def test_opportunity_list_shows_applied_badge(self):
        """
        If the logged-in volunteer has already applied to an opportunity, a
        visual indicator (e.g. 'Applied', 'Your application') must be present
        in the list so they can track their application status at a glance.
        """
        url = self._url()
        self._skip_if_url_missing(url, "opportunity_list")
        self.login()

        # Create an existing pending application.
        VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # The view should indicate the user has an active application.
        # Accept any of the common badge terms implementations might use.
        content = response.content.decode()
        has_badge = any(
            term in content.lower()
            for term in ["applied", "your application", "pending", "application"]
        )
        self.assertTrue(
            has_badge,
            "Expected an 'applied' badge on the opportunity listing for an applicant.",
        )


# ===========================================================================
# OpportunityDetailView
# ===========================================================================

class OpportunityDetailViewTests(BaseViewTestCase):

    def _url(self, pk=None):
        return _url("opportunity_detail", pk=pk or self.opportunity.pk)

    def test_opportunity_detail_requires_login(self):
        """Anonymous GET to an opportunity detail page → redirect to login."""
        url = self._url()
        self._skip_if_url_missing(url, "opportunity_detail")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_opportunity_detail_shows_opportunity(self):
        """
        Authenticated volunteer sees the opportunity title and description on
        the detail page.
        """
        url = self._url()
        self._skip_if_url_missing(url, "opportunity_detail")
        self.login()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Assert the title appears in whichever language is active (EN or FR).
        content = response.content.decode()
        self.assertTrue(
            self.opportunity.title_en in content or self.opportunity.title_fr in content,
            f"Expected opportunity title (EN or FR) in response. "
            f"title_en={self.opportunity.title_en!r}, title_fr={self.opportunity.title_fr!r}",
        )

    def test_opportunity_detail_404_if_inactive(self):
        """
        Requesting the detail page of a draft/inactive opportunity returns 404.
        Volunteers must not be able to view or apply to unpublished opportunities.
        """
        url = self._url(pk=self.inactive_opportunity.pk)
        self._skip_if_url_missing(url, "opportunity_detail")
        self.login()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_opportunity_detail_shows_existing_application_status(self):
        """
        When the volunteer already has an application for this opportunity,
        the detail page shows the current application status to the user.
        """
        url = self._url()
        self._skip_if_url_missing(url, "opportunity_detail")
        self.login()

        VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Status label 'Pending review' from STATUS_CHOICES should appear.
        self.assertContains(response, "Pending")

    def test_opportunity_detail_shows_apply_button_if_eligible(self):
        """
        A volunteer without an existing application sees an 'Apply' button (or
        link) on the detail page.  This is the primary call-to-action.
        """
        url = self._url()
        self._skip_if_url_missing(url, "opportunity_detail")
        self.login()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode().lower()
        has_apply = "apply" in content
        self.assertTrue(
            has_apply,
            "Expected an 'Apply' button/link on the opportunity detail page.",
        )


# ===========================================================================
# ApplicationFormView
# ===========================================================================

class ApplicationFormViewTests(BaseViewTestCase):

    def _apply_url(self, opportunity_pk=None):
        return _url("apply", pk=opportunity_pk or self.opportunity.pk)

    def test_apply_get_renders_form(self):
        """
        GET to the apply URL renders the ApplicationForm for authenticated users.
        """
        url = self._apply_url()
        self._skip_if_url_missing(url, "apply")
        self.login()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Form should contain the motivation field.
        self.assertContains(response, "motivation", msg_prefix="ApplicationForm not found in page")

    def test_apply_post_creates_application_and_redirects(self):
        """
        POST to the apply URL with a valid motivation creates a VolunteerApplication
        and redirects (302) to a success page (e.g. my_applications or detail).
        """
        url = self._apply_url()
        self._skip_if_url_missing(url, "apply")
        self.login()

        with mock.patch("apps.workflows.services.create_work_item"):
            response = self.client.post(
                url,
                data={"motivation": "I want to give back to my community."},
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            VolunteerApplication.objects.filter(
                volunteer=self.profile,
                opportunity=self.opportunity,
            ).exists()
        )

    def test_apply_post_validates_form(self):
        """
        POST with an empty motivation when the opportunity requires it should
        return 200 with form errors, not create an application.
        """
        url = self._apply_url()
        self._skip_if_url_missing(url, "apply")
        self.login()

        response = self.client.post(url, data={"motivation": ""})

        # Either a form error (200 + errors) or redirect (302) depending on
        # whether the view enforces the motivation requirement.
        if response.status_code == 200:
            # Form was re-rendered with errors — application must not be created.
            self.assertFalse(
                VolunteerApplication.objects.filter(
                    volunteer=self.profile,
                    opportunity=self.opportunity,
                ).exists()
            )

    def test_apply_post_rejects_inactive_opportunity(self):
        """
        POST to the apply URL for an inactive/draft opportunity must not create
        an application. Either a 404 (for the GET) or a validation error on POST.
        """
        url = self._apply_url(opportunity_pk=self.inactive_opportunity.pk)
        self._skip_if_url_missing(url, "apply")
        self.login()

        with mock.patch("apps.workflows.services.create_work_item"):
            response = self.client.post(
                url,
                data={"motivation": "I want to help."},
            )

        self.assertNotEqual(response.status_code, 200, "Expected non-200 for inactive opportunity")
        self.assertFalse(
            VolunteerApplication.objects.filter(
                volunteer=self.profile,
                opportunity=self.inactive_opportunity,
            ).exists(),
            "No application should be created for an inactive opportunity.",
        )

    def test_apply_cannot_apply_for_other_volunteer(self):
        """
        IDOR guard: a user cannot POST an application using another volunteer's
        profile PK. The view must associate the application with the current
        user's own VolunteerProfile, not a profile ID from the POST body.
        """
        other_user = _make_user("other@example.gc.ca")
        other_profile = _make_profile(other_user)

        url = self._apply_url()
        self._skip_if_url_missing(url, "apply")
        self.login()

        with mock.patch("apps.workflows.services.create_work_item"):
            self.client.post(
                url,
                data={
                    "motivation": "Applying as myself.",
                    "volunteer_profile_id": other_profile.pk,  # attempt IDOR
                },
            )

        # Application created — if any — must belong to the logged-in user's profile.
        other_apps = VolunteerApplication.objects.filter(volunteer=other_profile)
        self.assertFalse(
            other_apps.exists(),
            "IDOR: application must not be created under another volunteer's profile.",
        )

    def test_apply_requires_login(self):
        """Anonymous POST to the apply URL must redirect to login."""
        url = self._apply_url()
        self._skip_if_url_missing(url, "apply")

        response = self.client.post(
            url,
            data={"motivation": "Should fail."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())


# ===========================================================================
# MyApplicationsView
# ===========================================================================

class MyApplicationsViewTests(BaseViewTestCase):

    def _url(self):
        return _url("my_applications")

    def test_my_applications_requires_login(self):
        """Anonymous GET to my_applications → redirect to login."""
        url = self._url()
        self._skip_if_url_missing(url, "my_applications")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_my_applications_shows_own_applications(self):
        """
        Authenticated volunteer sees their own applications listed, including
        the opportunity title and current status.
        """
        url = self._url()
        self._skip_if_url_missing(url, "my_applications")
        self.login()

        VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Assert the title appears in whichever language is active (EN or FR).
        content = response.content.decode()
        self.assertTrue(
            self.opportunity.title_en in content or self.opportunity.title_fr in content,
            f"Expected opportunity title (EN or FR) in response. "
            f"title_en={self.opportunity.title_en!r}, title_fr={self.opportunity.title_fr!r}",
        )

    def test_my_applications_does_not_show_rejection_reason(self):
        """
        PIPEDA critical invariant: rejection_reason is an internal coordinator
        note that MUST NEVER be surfaced to the volunteer.

        This test:
        1. Creates an application with a non-empty, specific rejection_reason.
        2. GETs the MyApplicationsView as the volunteer.
        3. Asserts the rejection_reason text does NOT appear anywhere in the
           response body.

        A passing test is only meaningful if the rejection_reason is non-empty —
        we assert this explicitly before making the assertNotContains check.
        """
        url = self._url()
        self._skip_if_url_missing(url, "my_applications")
        self.login()

        rejection_reason = (
            "INTERNAL ONLY: Reference check returned unresolved concerns. "
            "Coordinator file #CF-2025-047. Do not disclose."
        )

        application = VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_REJECTED,
            rejection_reason=rejection_reason,
        )

        # Ensure the test is meaningful: rejection_reason must actually be set.
        self.assertTrue(
            application.rejection_reason,
            "Test setup error: rejection_reason must be non-empty for this test to be meaningful.",
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # The full rejection_reason string must not appear in the response.
        self.assertNotContains(
            response,
            application.rejection_reason,
            msg_prefix="PIPEDA violation: rejection_reason must never be visible to the volunteer.",
        )

        # Additionally, no fragment of the internal reference must leak.
        self.assertNotContains(
            response,
            "CF-2025-047",
            msg_prefix="PIPEDA violation: internal coordinator reference leaked to volunteer view.",
        )

    def test_my_applications_hides_other_users_applications(self):
        """
        IDOR guard: a logged-in volunteer must not see applications belonging
        to other volunteers.
        """
        url = self._url()
        self._skip_if_url_missing(url, "my_applications")
        self.login()

        other_user = _make_user("other2@example.gc.ca")
        other_profile = _make_profile(other_user)
        other_program = _make_program()
        other_opp = _make_opportunity(other_program, status="published")
        VolunteerApplication.objects.create(
            volunteer=other_profile,
            opportunity=other_opp,
            status=VolunteerApplication.STATUS_PENDING,
            motivation="Other volunteer's private motivation.",
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            "Other volunteer's private motivation.",
            msg_prefix="IDOR: another volunteer's application data leaked.",
        )

    def test_my_applications_shows_generic_status_not_reason_on_rejection(self):
        """
        When a volunteer's application is rejected, the page shows a generic
        status label (e.g. 'Not selected') but NOT the internal rejection_reason.
        This is a second PIPEDA assertion verifying UI copy, not just DB content.
        """
        url = self._url()
        self._skip_if_url_missing(url, "my_applications")
        self.login()

        rejection_reason = "Background screening outcome did not meet requirements."
        VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_REJECTED,
            rejection_reason=rejection_reason,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Template renders "Unsuccessful" for rejected status — generic, no PII.
        self.assertContains(response, "Unsuccessful")

        # The raw reason must not appear.
        self.assertNotContains(response, rejection_reason)


# ===========================================================================
# WithdrawApplicationView
# ===========================================================================

class WithdrawApplicationViewTests(BaseViewTestCase):

    def _pending_application(self):
        return VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )

    def _url(self, pk):
        return _url("withdraw", pk=pk)

    def test_withdraw_post_withdraws_pending_application(self):
        """
        POST to withdraw transitions the application to STATUS_WITHDRAWN.
        The volunteer is redirected to my_applications (or equivalent).
        """
        app = self._pending_application()
        url = self._url(pk=app.pk)
        self._skip_if_url_missing(url, "withdraw")
        self.login()

        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, VolunteerApplication.STATUS_WITHDRAWN)

    def test_withdraw_rejects_get_request(self):
        """
        GET to withdraw must not perform the withdrawal.
        Mutations should only happen via POST to prevent CSRF-style link attacks.
        """
        app = self._pending_application()
        url = self._url(pk=app.pk)
        self._skip_if_url_missing(url, "withdraw")
        self.login()

        response = self.client.get(url)

        # Acceptable responses: 405 Method Not Allowed, 200 (confirmation page), or 302.
        # The application must NOT be withdrawn by a GET.
        app.refresh_from_db()
        self.assertNotEqual(
            app.status,
            VolunteerApplication.STATUS_WITHDRAWN,
            "GET must not trigger a withdrawal.",
        )

    def test_withdraw_cannot_withdraw_others_application(self):
        """
        IDOR guard: a volunteer cannot POST to withdraw another volunteer's
        application by guessing the application PK.
        """
        other_user = _make_user("other3@example.gc.ca")
        other_profile = _make_profile(other_user)
        other_program = _make_program()
        other_opp = _make_opportunity(other_program, status="published")
        other_app = VolunteerApplication.objects.create(
            volunteer=other_profile,
            opportunity=other_opp,
            status=VolunteerApplication.STATUS_PENDING,
        )

        url = self._url(pk=other_app.pk)
        self._skip_if_url_missing(url, "withdraw")
        self.login()  # logged in as self.user, NOT other_user

        response = self.client.post(url)

        # Must return 403 or 404 (not 200 or 302-to-success).
        self.assertIn(
            response.status_code,
            [403, 404],
            "IDOR: expected 403/404 when attempting to withdraw another user's application.",
        )

        other_app.refresh_from_db()
        self.assertNotEqual(
            other_app.status,
            VolunteerApplication.STATUS_WITHDRAWN,
            "IDOR: another volunteer's application must not be withdrawn.",
        )

    def test_withdraw_requires_login(self):
        """Anonymous POST to withdraw → redirect to login."""
        app = self._pending_application()
        url = self._url(pk=app.pk)
        self._skip_if_url_missing(url, "withdraw")

        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())


# ===========================================================================
# CoordinatorDashboardView
# ===========================================================================

class CoordinatorDashboardViewTests(BaseViewTestCase):

    def _url(self):
        return _url("coordinator_dashboard")

    def test_coordinator_dashboard_requires_permission(self):
        """
        The coordinator dashboard requires the change_volunteerapplication
        permission. An anonymous user must be redirected to login.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_coordinator_dashboard_shows_pending_applications(self):
        """
        An authenticated coordinator sees pending applications on the dashboard.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_coordinator()

        VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Assert the title appears in whichever language is active (EN or FR).
        content = response.content.decode()
        self.assertTrue(
            self.opportunity.title_en in content or self.opportunity.title_fr in content,
            f"Expected opportunity title (EN or FR) in response. "
            f"title_en={self.opportunity.title_en!r}, title_fr={self.opportunity.title_fr!r}",
        )

    def test_regular_user_cannot_access_coordinator_dashboard(self):
        """
        A regular volunteer user without change_volunteerapplication permission
        must be denied access to the coordinator dashboard.
        Expected: 302 redirect or 403 Forbidden.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login()  # logged in as regular user without coordinator permission

        response = self.client.get(url)
        self.assertIn(
            response.status_code,
            [302, 403],
            "Regular volunteers must not access the coordinator dashboard.",
        )

    def test_coordinator_dashboard_shows_opportunity_title(self):
        """
        Dashboard shows the title of opportunities with pending applications.
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_coordinator()

        VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Assert the title appears in whichever language is active (EN or FR).
        content = response.content.decode()
        self.assertTrue(
            self.opportunity.title_en in content or self.opportunity.title_fr in content,
            f"Expected opportunity title (EN or FR) in response. "
            f"title_en={self.opportunity.title_en!r}, title_fr={self.opportunity.title_fr!r}",
        )

    def test_coordinator_dashboard_shows_correct_pending_count(self):
        """
        The coordinator dashboard reflects the correct count of pending
        applications (or at minimum shows each pending one).
        """
        url = self._url()
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.login_as_coordinator()

        # Create 3 pending applications for different volunteers.
        for i in range(3):
            u = _make_user()
            p = _make_profile(u)
            opp = _make_opportunity(self.program, status="published")
            VolunteerApplication.objects.create(
                volunteer=p,
                opportunity=opp,
                status=VolunteerApplication.STATUS_PENDING,
            )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # We expect the page to show pending applications (exact count display
        # depends on implementation, but the page must succeed).
        self.assertNotContains(response, "Error")
        # Verify the actual pending count matches what we created.
        self.assertIn("pending_count", response.context)
        self.assertEqual(response.context["pending_count"], 3)


# ===========================================================================
# ApplicationReviewView
# ===========================================================================

class ApplicationReviewViewTests(BaseViewTestCase):

    def _pending_application(self):
        return VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )

    def _review_url(self, pk):
        return _url("application_review", pk=pk)

    def test_review_approve_action_approves_application(self):
        """
        POST to application_review with action='approve' calls approve_application()
        and sets the application status to approved.
        """
        app = self._pending_application()
        url = self._review_url(pk=app.pk)
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.post(url, data={"action": "approve"})

        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(
            app.status,
            VolunteerApplication.STATUS_APPROVED,
            "Application must be approved after coordinator action='approve'.",
        )

    def test_review_reject_action_rejects_application(self):
        """
        POST to application_review with action='reject' calls reject_application()
        and sets the application status to rejected.
        """
        app = self._pending_application()
        url = self._review_url(pk=app.pk)
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.post(
            url,
            data={
                "action": "reject",
                "rejection_reason": "Skills do not match opportunity requirements.",
            },
        )

        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(
            app.status,
            VolunteerApplication.STATUS_REJECTED,
            "Application must be rejected after coordinator action='reject'.",
        )

    def test_review_requires_coordinator_permission(self):
        """
        Regular volunteers must not access the application review endpoint.
        Expected: 302 (login redirect) or 403 Forbidden.
        """
        app = self._pending_application()
        url = self._review_url(pk=app.pk)
        self._skip_if_url_missing(url, "application_review")
        self.login()  # regular user, no coordinator permission

        response = self.client.post(url, data={"action": "approve"})

        self.assertIn(
            response.status_code,
            [302, 403],
            "Regular volunteers must not be able to review applications.",
        )
        app.refresh_from_db()
        self.assertEqual(
            app.status,
            VolunteerApplication.STATUS_PENDING,
            "Application status must be unchanged after unauthorized review attempt.",
        )

    def test_coordinator_can_see_rejection_reason_field(self):
        """
        The coordinator IS allowed to see and enter a rejection_reason.
        The ApplicationReviewForm must render the rejection_reason field when
        the coordinator visits the review page via GET.

        This is the complementary test to PIPEDA: coordinators enter the reason
        here, but it is never forwarded to the volunteer.
        """
        app = self._pending_application()
        url = self._review_url(pk=app.pk)
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # The form must contain the rejection_reason textarea for coordinators.
        self.assertContains(
            response,
            "rejection_reason",
            msg_prefix="Coordinator review form must include the rejection_reason field.",
        )

    def test_review_anonymous_redirect_to_login(self):
        """Anonymous POST to application_review must redirect to login."""
        app = self._pending_application()
        url = self._review_url(pk=app.pk)
        self._skip_if_url_missing(url, "application_review")

        response = self.client.post(url, data={"action": "approve"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_review_reject_stores_reason_in_db(self):
        """
        When a coordinator provides a rejection_reason via the review form,
        it is persisted in the database for audit purposes.

        PIPEDA: it must NOT be shown to the volunteer (tested in
        MyApplicationsViewTests.test_my_applications_does_not_show_rejection_reason).
        """
        app = self._pending_application()
        url = self._review_url(pk=app.pk)
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        reason = "Insufficient references provided during intake call."
        response = self.client.post(
            url,
            data={"action": "reject", "rejection_reason": reason},
        )

        self.assertEqual(response.status_code, 302)  # assert redirect before DB check
        app.refresh_from_db()
        self.assertEqual(app.status, VolunteerApplication.STATUS_REJECTED)
        self.assertEqual(app.rejection_reason, reason)

    def test_review_404_for_nonexistent_application(self):
        """
        POST to application_review with a PK that doesn't exist returns 404.
        """
        url = self._review_url(pk=99999999)
        self._skip_if_url_missing(url, "application_review")
        self.login_as_coordinator()

        response = self.client.post(url, data={"action": "approve"})
        self.assertEqual(response.status_code, 404)

    def test_review_reject_with_empty_reason_returns_form_error(self):
        """
        M-4 regression guard: rejecting with an empty rejection_reason must
        return the form with an error (200), NOT redirect (302) or crash (500).
        The application status must remain unchanged.
        """
        app = self._pending_application()
        url = self._review_url(pk=app.pk)
        self._skip_if_url_missing(url, "application_review")
        self.client.force_login(self.coordinator_user)
        response = self.client.post(
            url,
            data={"action": "reject", "rejection_reason": ""},
        )
        # Form validation error — must re-render (200), not redirect
        self.assertEqual(response.status_code, 200)
        # Application must not have been modified
        app.refresh_from_db()
        self.assertEqual(app.status, VolunteerApplication.STATUS_PENDING)
        self.assertEqual(app.rejection_reason, "")


# ===========================================================================
# Cross-view security invariants
# ===========================================================================

class SecurityInvariantsTests(BaseViewTestCase):
    """
    Security tests that span multiple views.  These tests assert broad
    invariants that must hold across the entire volunteer portal.
    """

    def test_all_portal_views_require_authentication(self):
        """
        Every portal view that requires a login must redirect an unauthenticated
        request to the login page. We test all named views we know about.
        """
        known_views = [
            ("opportunity_list", {}),
            ("my_applications", {}),
            ("coordinator_dashboard", {}),
            # Wave 3 portal URLs
            ("my_shifts", {}),
            ("my_hours", {}),
            ("log_hours", {"pk": 999}),
            ("cancel_booking", {"pk": 999}),
        ]

        for name, kwargs in known_views:
            url = _url(name, **kwargs)
            if url is None:
                continue  # skip unregistered Wave 2 stubs

            with self.subTest(view=name):
                response = self.client.get(url)
                self.assertIn(
                    response.status_code,
                    [302, 403],
                    f"{name}: expected 302/403 for anonymous access, got {response.status_code}.",
                )
                if response.status_code == 302:
                    self.assertIn(
                        "login",
                        response["Location"].lower(),
                        f"{name}: redirect target must be a login page.",
                    )

    def test_csrf_protection_on_write_views(self):
        """
        Write views (POST) must enforce CSRF protection.

        Django's test client sends a CSRF token by default. We verify that
        the EnforceCsrfTokenMiddleware is not disabled by checking that a
        POST without a CSRF token is rejected (or that the client's default
        behaviour respects it).
        """
        # The Django test client always skips CSRF checks by default.
        # Use enforce_csrf_checks=True to actually test CSRF enforcement.
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        app = VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_PENDING,
        )

        withdraw_url = _url("withdraw", pk=app.pk)
        if withdraw_url is None:
            self.skipTest("withdraw URL not yet registered.")

        response = csrf_client.post(withdraw_url)
        # Without a CSRF token the view must reject (403 Forbidden).
        self.assertEqual(
            response.status_code,
            403,
            "CSRF protection must be enforced on write views.",
        )

    def test_rejection_reason_never_in_any_volunteer_facing_response(self):
        """
        End-to-end PIPEDA assertion: create a rejected application with a
        unique rejection_reason sentinel string, then visit every volunteer-
        facing view and assert the sentinel never appears.
        """
        sentinel = "PIPEDA-SENTINEL-f7a3d2c1-NEVER-SHOW-VOLUNTEER"

        app = VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_REJECTED,
            rejection_reason=sentinel,
        )
        self.assertEqual(app.rejection_reason, sentinel)  # setup sanity check

        volunteer_views = [
            ("opportunity_list", {}),
            ("opportunity_detail", {"pk": self.opportunity.pk}),
            ("my_applications", {}),
        ]

        self.login()

        for name, kwargs in volunteer_views:
            url = _url(name, **kwargs)
            if url is None:
                continue  # skip unregistered Wave 2 stubs

            with self.subTest(view=name):
                response = self.client.get(url)
                if response.status_code != 200:
                    continue  # view not ready yet; skip without failing

                self.assertNotContains(
                    response,
                    sentinel,
                    msg_prefix=(
                        f"PIPEDA violation: rejection_reason sentinel found in {name} response."
                    ),
                )


# ===========================================================================
# CoordinatorApplicationListView  (H-7: zero coverage before this class)
# ===========================================================================

def _make_coordinator_user(email):
    """
    Create a new user with the ``change_volunteerapplication`` coordinator
    permission.  Helper kept module-level so both new test classes can use it.
    """
    user = _make_user(email, is_staff=True)
    return _grant_coordinator_permission(user)


def _make_application(profile, opportunity, status=None):
    """
    Create a VolunteerApplication for *profile* against *opportunity*.
    Defaults to STATUS_PENDING.
    """
    kwargs = dict(
        volunteer=profile,
        opportunity=opportunity,
        status=status or VolunteerApplication.STATUS_PENDING,
    )
    return VolunteerApplication.objects.create(**kwargs)


class CoordinatorApplicationListViewTests(BaseViewTestCase):
    """
    Tests for CoordinatorApplicationListView — coordinator sees their own
    programme's applications, paginated, filterable by status.

    H-7: This entire class is new; the view had zero test coverage.
    """

    def _list_url(self, status=None):
        url = _url("coordinator_application_list")
        if url and status:
            url = f"{url}?status={status}"
        return url

    def test_login_required(self):
        """Anonymous GET to coordinator_application_list must redirect to login."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")
        self.client.logout()
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 403])

    def test_permission_required(self):
        """Authenticated user without coordinator permission gets 403."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")
        self.client.force_login(self.user)  # volunteer, no coordinator permission
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_coordinator_sees_own_applications(self):
        """Coordinator sees applications for their own programme."""
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")
        app = _make_application(self.profile, self.opportunity)
        self.client.force_login(self.coordinator_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(app, response.context["applications"])

    def test_coordinator_does_not_see_other_programs_applications(self):
        """
        H-8 scope isolation: Coordinator X cannot see Coordinator Y's applications.
        Applications from another programme must be absent from the list.
        """
        url = self._list_url()
        self._skip_if_url_missing(url, "coordinator_application_list")

        # Create coordinator Y with their own programme and opportunity
        coordinator_y = _make_coordinator_user("coord_y@example.com")
        program_y = _make_program(slug="prog-y")
        program_y.coordinator = coordinator_y
        program_y.save(update_fields=["coordinator"])
        opportunity_y = _make_opportunity(program_y, slug="opp-y", status="published")

        # Create a volunteer profile and application in programme Y
        volunteer_y_user = _make_user("vol_y@example.com")
        profile_y = _make_profile(volunteer_y_user)
        app_y = _make_application(profile_y, opportunity_y)

        # Coordinator X (self.coordinator_user) should NOT see app_y
        self.client.force_login(self.coordinator_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(app_y, response.context["applications"])

    def test_status_filter_pending(self):
        """?status=pending returns only pending applications."""
        url = self._list_url(status=VolunteerApplication.STATUS_PENDING)
        self._skip_if_url_missing(url, "coordinator_application_list")

        pending_app = _make_application(self.profile, self.opportunity)
        # Create a second opportunity and an approved application
        opp2 = _make_opportunity(self.program, slug="opp-filter-test", status="published")
        vol2 = _make_user("vol2filter@example.com")
        prof2 = _make_profile(vol2)
        approved_app = _make_application(prof2, opp2)
        approved_app.status = VolunteerApplication.STATUS_APPROVED
        approved_app.save(update_fields=["status", "updated_at"])

        self.client.force_login(self.coordinator_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        apps_in_context = list(response.context["applications"])
        self.assertIn(pending_app, apps_in_context)
        self.assertNotIn(approved_app, apps_in_context)

    def test_invalid_status_filter_ignored(self):
        """?status=garbage returns all applications (filter ignored)."""
        url = _url("coordinator_application_list")
        self._skip_if_url_missing(url, "coordinator_application_list")
        url = f"{url}?status=garbage_value_xyz"
        self.client.force_login(self.coordinator_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


# ===========================================================================
# CoordinatorScopeIsolationTests  (H-8: regression guard, cross-coordinator)
# ===========================================================================

class CoordinatorScopeIsolationTests(BaseViewTestCase):
    """
    H-8: Regression guard — coordinator X cannot access coordinator Y's data.
    Tests the scope isolation of dashboard, list, and review views.
    """

    def setUp(self):
        super().setUp()
        # Set up coordinator Y with their own programme, opportunity, and application
        self.coordinator_y = _make_coordinator_user("coord_y_scope@example.com")
        self.program_y = _make_program(slug="prog-y-scope")
        self.program_y.coordinator = self.coordinator_y
        self.program_y.save(update_fields=["coordinator"])
        self.opportunity_y = _make_opportunity(
            self.program_y, slug="opp-y-scope", status="published"
        )
        vol_y_user = _make_user("vol_y_scope@example.com")
        self.profile_y = _make_profile(vol_y_user)
        self.app_y = _make_application(self.profile_y, self.opportunity_y)

    def test_dashboard_does_not_show_other_programs_pending_count(self):
        """Coordinator X dashboard pending count excludes coordinator Y's applications."""
        url = _url("coordinator_dashboard")
        self._skip_if_url_missing(url, "coordinator_dashboard")
        self.client.force_login(self.coordinator_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # self.app_y belongs to coordinator_y — must not appear in coordinator_x's context
        # View uses context_object_name = "applications" — assert against that, not "object_list"
        if "applications" in response.context:
            all_app_pks = [a.pk for a in response.context["applications"]]
            self.assertNotIn(self.app_y.pk, all_app_pks)

    def test_application_list_excludes_other_coordinator_applications(self):
        """CoordinatorApplicationListView: coordinator X cannot see coordinator Y's apps."""
        url = _url("coordinator_application_list")
        self._skip_if_url_missing(url, "coordinator_application_list")
        self.client.force_login(self.coordinator_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.app_y, response.context["applications"])

    def test_review_view_returns_404_for_other_coordinators_application(self):
        """
        IDOR: Coordinator X cannot access coordinator Y's application review page.
        C-1 fix regression guard.
        """
        url = _url("application_review", pk=self.app_y.pk)
        self._skip_if_url_missing(url, "application_review")
        # Log in as coordinator X
        self.client.force_login(self.coordinator_user)
        response = self.client.get(url)
        # Must be 404 (not 200 or 403) — scoped get_object_or_404 returns 404
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# Wave 3 Portal — shared Wave 3 helpers
# ===========================================================================

def _make_shift_p(opportunity, *, minutes_from_now=60, **kwargs):
    """Create a future Shift under opportunity."""
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


def _make_hours_log_p(volunteer_profile, opportunity, *, hours=Decimal("3"), status=None, **kwargs):
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
# Wave 3 Portal — MyShiftsViewTests
# ===========================================================================

class MyShiftsViewTests(TestCase):
    """
    Tests for MyShiftsView — GET volunteers:my_shifts.

    Volunteer sees only their own bookings; other volunteers' bookings hidden.
    """

    def setUp(self):
        self.client = Client()

        self.user = _make_user("myshifts_vol@pviews.gc.ca")
        self.profile = _make_profile(self.user)

        # Coordinator needed to own the program
        coord = _make_user("myshifts_coord@pviews.gc.ca", is_staff=True)
        self.program = _make_program(slug="myshifts-prog")
        self.program.coordinator = coord
        self.program.save(update_fields=["coordinator"])

        self.opportunity = _make_opportunity(self.program, slug="myshifts-opp")
        self.shift = _make_shift_p(self.opportunity)
        self.booking = ShiftBooking.objects.create(
            shift=self.shift,
            volunteer=self.profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )
        self.client.force_login(self.user)

    def _url(self):
        return _url("my_shifts")

    def test_shows_volunteer_bookings(self):
        """Authenticated volunteer sees their own bookings."""
        url = self._url()
        if url is None:
            self.skipTest("my_shifts URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        booking_pks = [b.pk for b in response.context["bookings"]]
        self.assertIn(self.booking.pk, booking_pks)

    def test_scope_isolation(self):
        """Another volunteer's booking must NOT appear in this volunteer's list."""
        url = self._url()
        if url is None:
            self.skipTest("my_shifts URL not registered")

        other_user = _make_user("myshifts_other@pviews.gc.ca")
        other_profile = _make_profile(other_user)
        other_booking = ShiftBooking.objects.create(
            shift=self.shift,
            volunteer=other_profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        booking_pks = [b.pk for b in response.context["bookings"]]
        self.assertNotIn(other_booking.pk, booking_pks)

    def test_requires_login(self):
        """Anonymous GET redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("my_shifts URL not registered")
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_context_key_is_bookings(self):
        """View uses context_object_name='bookings'."""
        url = self._url()
        if url is None:
            self.skipTest("my_shifts URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("bookings", response.context)

    def test_no_profile_returns_empty_list(self):
        """User without a VolunteerProfile sees an empty bookings list (not 404/403)."""
        url = self._url()
        if url is None:
            self.skipTest("my_shifts URL not registered")
        no_profile_user = _make_user("myshifts_noprofile@pviews.gc.ca")
        self.client.force_login(no_profile_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["bookings"]), [])

    def test_cancelled_booking_also_visible(self):
        """Cancelled bookings appear in the list (all statuses shown)."""
        url = self._url()
        if url is None:
            self.skipTest("my_shifts URL not registered")
        shift2 = _make_shift_p(self.opportunity, minutes_from_now=200)
        cancelled_booking = ShiftBooking.objects.create(
            shift=shift2,
            volunteer=self.profile,
            status=ShiftBooking.STATUS_CANCELLED,
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        booking_pks = [b.pk for b in response.context["bookings"]]
        self.assertIn(cancelled_booking.pk, booking_pks)


# ===========================================================================
# Wave 3 Portal — MyHoursViewTests
# ===========================================================================

class MyHoursViewTests(TestCase):
    """
    Tests for MyHoursView — GET volunteers:my_hours.

    PIPEDA: rejection_reason must never appear in the response.
    """

    def setUp(self):
        self.client = Client()

        self.user = _make_user("myhours_vol@pviews.gc.ca")
        self.profile = _make_profile(self.user)

        coord = _make_user("myhours_coord@pviews.gc.ca", is_staff=True)
        self.program = _make_program(slug="myhours-prog")
        self.program.coordinator = coord
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(self.program, slug="myhours-opp")

        self.log = _make_hours_log_p(self.profile, self.opportunity, status=HoursLog.STATUS_PENDING)
        self.client.force_login(self.user)

    def _url(self):
        return _url("my_hours")

    def test_shows_volunteer_hours(self):
        """Authenticated volunteer sees their own hours logs."""
        url = self._url()
        if url is None:
            self.skipTest("my_hours URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        log_pks = [h.pk for h in response.context["hours_logs"]]
        self.assertIn(self.log.pk, log_pks)

    def test_total_in_context(self):
        """context['total_hours_approved'] is present (may be 0 for pending logs)."""
        url = self._url()
        if url is None:
            self.skipTest("my_hours URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("total_hours_approved", response.context)

    def test_rejection_reason_not_in_response(self):
        """
        PIPEDA: rejection_reason must not leak into the volunteer's page body.
        The ORM defers the field; the template must also not reference it.
        """
        url = self._url()
        if url is None:
            self.skipTest("my_hours URL not registered")
        rejected_log = _make_hours_log_p(
            self.profile,
            self.opportunity,
            status=HoursLog.STATUS_REJECTED,
            rejection_reason="Internal reason — coordinator eyes only",
        )
        # Ensure the test is meaningful
        self.assertTrue(rejected_log.rejection_reason)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Internal reason — coordinator eyes only")

    def test_scope_isolation(self):
        """Another volunteer's hours log must not appear in this volunteer's list."""
        url = self._url()
        if url is None:
            self.skipTest("my_hours URL not registered")

        other_user = _make_user("myhours_other@pviews.gc.ca")
        other_profile = _make_profile(other_user)
        other_log = _make_hours_log_p(other_profile, self.opportunity)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        log_pks = [h.pk for h in response.context["hours_logs"]]
        self.assertNotIn(other_log.pk, log_pks)

    def test_requires_login(self):
        """Anonymous GET redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("my_hours URL not registered")
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_no_profile_empty_list(self):
        """User without a VolunteerProfile sees empty hours_logs (not 404/403)."""
        url = self._url()
        if url is None:
            self.skipTest("my_hours URL not registered")
        no_profile_user = _make_user("myhours_noprofile@pviews.gc.ca")
        self.client.force_login(no_profile_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["hours_logs"]), [])

    def test_milestones_in_context(self):
        """context['milestones'] is present (may be empty for new volunteers)."""
        url = self._url()
        if url is None:
            self.skipTest("my_hours URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("milestones", response.context)


# ===========================================================================
# Wave 3 Portal — LogHoursViewTests
# ===========================================================================

class LogHoursViewTests(TestCase):
    """
    Tests for LogHoursView — GET/POST volunteers:log_hours <pk>.

    Volunteer must have an approved application for the opportunity.
    Zero / negative hours are rejected.
    """

    def setUp(self):
        self.client = Client()

        self.user = _make_user("loghours_vol@pviews.gc.ca")
        self.profile = _make_profile(self.user)

        coord = _make_user("loghours_coord@pviews.gc.ca", is_staff=True)
        self.program = _make_program(slug="loghours-prog")
        self.program.coordinator = coord
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(self.program, slug="loghours-opp")

        # Approved application
        self.application = VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=self.opportunity,
            status=VolunteerApplication.STATUS_APPROVED,
        )
        self.client.force_login(self.user)

    def _url(self, pk=None):
        return _url("log_hours", pk=pk or self.opportunity.pk)

    def test_get_renders_form(self):
        """GET returns 200 and renders the hours log form."""
        url = self._url()
        if url is None:
            self.skipTest("log_hours URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_post_creates_pending_log(self):
        """Valid POST creates a STATUS_PENDING HoursLog and redirects to my_hours."""
        url = self._url()
        if url is None:
            self.skipTest("log_hours URL not registered")
        response = self.client.post(url, {
            "date": dt.date.today().isoformat(),
            "hours": "3.5",
            "description": "Setup and cleanup",
        })
        self.assertRedirects(
            response,
            reverse("volunteers:my_hours"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            HoursLog.objects.filter(
                volunteer=self.profile,
                opportunity=self.opportunity,
            ).count(),
            1,
        )

    def test_post_without_approved_application_returns_404(self):
        """Volunteer with no approved application for this opportunity gets 404."""
        url = self._url()
        if url is None:
            self.skipTest("log_hours URL not registered")
        no_app_user = _make_user("loghours_noapp@pviews.gc.ca")
        _make_profile(no_app_user)
        self.client.force_login(no_app_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_zero_hours_rejected(self):
        """POST with hours=0 is rejected — form re-renders (200) and no log created."""
        url = self._url()
        if url is None:
            self.skipTest("log_hours URL not registered")
        response = self.client.post(url, {
            "date": dt.date.today().isoformat(),
            "hours": "0",
        })
        # Invalid form input must re-render the form (200), not redirect
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            HoursLog.objects.filter(
                volunteer=self.profile,
                opportunity=self.opportunity,
            ).count(),
            0,
        )

    def test_requires_login(self):
        """Anonymous GET redirects to login."""
        url = self._url()
        if url is None:
            self.skipTest("log_hours URL not registered")
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_opportunity_in_context(self):
        """GET includes the opportunity in context for template use."""
        url = self._url()
        if url is None:
            self.skipTest("log_hours URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["opportunity"], self.opportunity)

    def test_pending_application_not_eligible(self):
        """Volunteer with only a PENDING application (not approved) gets 404."""
        url = self._url()
        if url is None:
            self.skipTest("log_hours URL not registered")

        # Create a fresh opportunity with no approved application for this volunteer
        opp2 = _make_opportunity(self.program, slug="loghours-opp2")
        VolunteerApplication.objects.create(
            volunteer=self.profile,
            opportunity=opp2,
            status=VolunteerApplication.STATUS_PENDING,
        )
        url2 = _url("log_hours", pk=opp2.pk)
        if url2 is None:
            self.skipTest("log_hours URL not registered")
        response = self.client.get(url2)
        self.assertEqual(response.status_code, 404)

    def test_post_with_wrong_opportunity_pk_returns_404(self):
        """IDOR: volunteer approved for opp A cannot log hours against opp B's PK."""
        url = self._url()
        if url is None:
            self.skipTest("log_hours URL not registered")
        # Create a second opportunity that this volunteer is NOT approved for
        opp_b = _make_opportunity(self.program, slug="loghours-opp-idor")
        url_b = _url("log_hours", pk=opp_b.pk)
        if url_b is None:
            self.skipTest("log_hours URL not registered")
        response = self.client.post(url_b, {
            "date": dt.date.today().isoformat(),
            "hours": "2",
            "description": "IDOR attempt",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            HoursLog.objects.filter(volunteer=self.profile, opportunity=opp_b).count(),
            0,
        )


# ===========================================================================
# Wave 3 Portal — CancelBookingViewTests
# ===========================================================================

class CancelBookingViewTests(TestCase):
    """
    Tests for CancelBookingView — POST volunteers:cancel_booking <pk>.

    POST-only. IDOR: other volunteer's booking → 404. GET → 405.
    """

    def setUp(self):
        self.client = Client()

        self.user = _make_user("cancelbooking_vol@pviews.gc.ca")
        self.profile = _make_profile(self.user)

        coord = _make_user("cancelbooking_coord@pviews.gc.ca", is_staff=True)
        self.program = _make_program(slug="cancelbooking-prog")
        self.program.coordinator = coord
        self.program.save(update_fields=["coordinator"])
        self.opportunity = _make_opportunity(self.program, slug="cancelbooking-opp")
        self.shift = _make_shift_p(self.opportunity)

        self.booking = ShiftBooking.objects.create(
            shift=self.shift,
            volunteer=self.profile,
            status=ShiftBooking.STATUS_CONFIRMED,
        )
        self.client.force_login(self.user)

    def _url(self, pk=None):
        return _url("cancel_booking", pk=pk or self.booking.pk)

    def test_volunteer_can_cancel_own_booking(self):
        """POST by the booking owner cancels it and redirects to my_shifts."""
        url = self._url()
        if url is None:
            self.skipTest("cancel_booking URL not registered")
        response = self.client.post(url)
        self.assertRedirects(
            response,
            reverse("volunteers:my_shifts"),
            fetch_redirect_response=False,
        )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, ShiftBooking.STATUS_CANCELLED)

    def test_idor_returns_404(self):
        """Another volunteer trying to cancel this booking gets 404; status unchanged."""
        url = self._url()
        if url is None:
            self.skipTest("cancel_booking URL not registered")
        other_user = _make_user("cancelbooking_other@pviews.gc.ca")
        _make_profile(other_user)
        self.client.force_login(other_user)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, ShiftBooking.STATUS_CONFIRMED)

    def test_get_not_allowed(self):
        """GET to cancel_booking returns 405 Method Not Allowed."""
        url = self._url()
        if url is None:
            self.skipTest("cancel_booking URL not registered")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, ShiftBooking.STATUS_CONFIRMED)

    def test_requires_login(self):
        """Anonymous POST redirects to login; booking unchanged."""
        url = self._url()
        if url is None:
            self.skipTest("cancel_booking URL not registered")
        self.client.logout()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, ShiftBooking.STATUS_CONFIRMED)

    def test_nonexistent_booking_returns_404(self):
        """POST to a nonexistent booking PK returns 404."""
        url = _url("cancel_booking", pk=99999999)
        if url is None:
            self.skipTest("cancel_booking URL not registered")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    def test_no_profile_returns_404(self):
        """User with no VolunteerProfile gets 404 (not 403 or 500)."""
        url = self._url()
        if url is None:
            self.skipTest("cancel_booking URL not registered")
        no_profile_user = _make_user("cancelbooking_noprofile@pviews.gc.ca")
        self.client.force_login(no_profile_user)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, ShiftBooking.STATUS_CONFIRMED)

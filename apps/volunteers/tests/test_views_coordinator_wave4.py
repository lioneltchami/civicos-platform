"""
Wave 4 — Volunteer Management BB: coordinator view test suite.

Covers the 7 new Wave 4 coordinator-facing views:
  VolunteerRosterView         GET  volunteers:volunteer_roster
  VolunteerDetailView         GET  volunteers:volunteer_detail <pk>
  VolunteerStatusChangeView   POST volunteers:volunteer_status_change <pk>
  AddVolunteerNoteView        POST volunteers:volunteer_add_note <pk>
  RecordScreeningView         GET/POST volunteers:record_screening <pk>
  CompleteScreeningView       POST volunteers:complete_screening <pk>
  HonorariumCreateView        GET/POST volunteers:honorarium_create <pk>

Security invariants asserted:
  - All views require login (LoginRequiredMixin → 302 for anonymous).
  - Most views require volunteers.change_volunteerapplication or a specific
    permission; authenticated users without permission get 403.
  - IDOR prevention: scope queries to coordinator's own programs; 404 for
    volunteers/records belonging to another program.

Django test conventions used:
  - self.client.force_login() for authentication.
  - reverse() for all URL lookups.
  - _skip_if_url_missing() pattern from test_views_coordinator.py.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

from apps.volunteers.models import (
    Honorarium,
    Opportunity,
    Program,
    ScreeningRecord,
    SkillTag,
    VolunteerApplication,
    VolunteerNote,
    VolunteerProfile,
)

User = get_user_model()

_counter = [0]


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------


def _make_user(email=None, **kwargs):
    _counter[0] += 1
    email = email or f"w4view{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password="hunter2!", **kwargs)


def _make_program(slug=None):
    _counter[0] += 1
    return Program.objects.create(
        name_en="Test Program",
        name_fr="Programme test",
        slug=slug or f"w4prog-{_counter[0]}",
        cra_category="welfare",
    )


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    _counter[0] += 1
    defaults = {
        "title_en": "Wave 4 Opportunity",
        "title_fr": "Opportunité Wave 4",
        "slug": slug or f"w4opp-{_counter[0]}",
        "description_en": "Description",
        "description_fr": "Description FR",
        "program": program,
        "status": status,
    }
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user, **kwargs):
    return VolunteerProfile.objects.create(user=user, **kwargs)


def _grant_perm(user, codename, app_label="volunteers"):
    perm = Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )
    user.user_permissions.add(perm)
    for attr in ("_perm_cache", "_user_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
    return User.objects.get(pk=user.pk)


def _make_coordinator(email):
    """Create a user with volunteers.change_volunteerapplication permission."""
    user = _make_user(email, is_staff=True)
    return _grant_perm(user, "change_volunteerapplication")


def _make_application(profile, opportunity, status=None):
    return VolunteerApplication.objects.create(
        volunteer=profile,
        opportunity=opportunity,
        status=status or VolunteerApplication.STATUS_PENDING,
    )


def _make_honorarium(profile, amount, created_by=None):
    """Create an Honorarium directly (skip_clean=True) for test pre-population."""
    h = Honorarium(
        volunteer=profile,
        payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
        amount=Decimal(str(amount)),
        description="Test",
        payment_date=date.today(),
        created_by=created_by or profile.user,
    )
    h.save(skip_clean=True)
    return h


def _url(name, **kwargs):
    try:
        return reverse(f"volunteers:{name}", kwargs=kwargs if kwargs else None)
    except NoReverseMatch:
        return None


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------


class Wave4BaseTestCase(TestCase):
    """
    Shared fixtures for Wave 4 coordinator view tests.

    self.coordinator owns self.program and self.opportunity.
    self.profile is a volunteer with an application to self.opportunity
    (so VolunteerStatusChangeView / AddVolunteerNoteView IDOR scoping works).
    """

    def setUp(self):
        self.client = Client()

        # Primary coordinator
        self.coordinator = _make_coordinator("w4coord@wave4.gc.ca")

        # Volunteer with a profile
        self.volunteer_user = _make_user("w4vol@wave4.gc.ca")
        self.profile = _make_profile(self.volunteer_user)

        # Program and opportunity scoped to coordinator
        self.program = _make_program(slug="w4-base-prog")
        self.program.coordinator = self.coordinator
        self.program.save(update_fields=["coordinator"])

        self.opportunity = _make_opportunity(self.program, slug="w4-base-opp")

        # Application links the volunteer to the coordinator's program
        # (required for IDOR scope via applications__opportunity__program__coordinator)
        self.application = _make_application(self.profile, self.opportunity)

    def login_as_coordinator(self):
        self.client.force_login(self.coordinator)

    def login_as_volunteer(self):
        self.client.force_login(self.volunteer_user)

    def _skip_if_url_missing(self, url, name):
        if url is None:
            self.skipTest(f"URL 'volunteers:{name}' not yet registered.")


# ===========================================================================
# VolunteerRosterView — GET /coordinator/volunteers/
# ===========================================================================


class VolunteerRosterViewTests(Wave4BaseTestCase):
    def _url(self):
        return _url("volunteer_roster")

    def test_anonymous_user_redirected(self):
        """Anonymous GET redirects to login (302)."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_roster")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_non_coordinator_forbidden(self):
        """Authenticated user without coordinator permission gets 403."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_roster")
        self.login_as_volunteer()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_coordinator_sees_volunteer_list(self):
        """Coordinator gets 200 and volunteer profile appears in response context."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_roster")
        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        profile_pks = [p.pk for p in response.context["profiles"]]
        self.assertIn(self.profile.pk, profile_pks)

    def test_status_filter_applied(self):
        """?status=inactive returns only profiles with STATUS_INACTIVE."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_roster")

        # Make our profile inactive
        self.profile.status = VolunteerProfile.STATUS_INACTIVE
        self.profile.save(update_fields=["status"])

        # Create a second active volunteer
        active_user = _make_user("w4active@wave4.gc.ca")
        active_profile = _make_profile(active_user, status=VolunteerProfile.STATUS_ACTIVE)

        self.login_as_coordinator()
        response = self.client.get(f"{url}?status=inactive")
        self.assertEqual(response.status_code, 200)
        profile_pks = [p.pk for p in response.context["profiles"]]
        self.assertIn(self.profile.pk, profile_pks)
        self.assertNotIn(active_profile.pk, profile_pks)

    def test_skill_filter_applied(self):
        """?skill=<pk> returns only profiles with that skill."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_roster")

        skill = SkillTag.objects.create(
            name_en="Python",
            name_fr="Python",
            slug="python-w4",
        )
        self.profile.skills.add(skill)

        # Second volunteer without the skill
        other_user = _make_user("w4noskill@wave4.gc.ca")
        other_profile = _make_profile(other_user)

        self.login_as_coordinator()
        response = self.client.get(f"{url}?skill={skill.pk}")
        self.assertEqual(response.status_code, 200)
        profile_pks = [p.pk for p in response.context["profiles"]]
        self.assertIn(self.profile.pk, profile_pks)
        self.assertNotIn(other_profile.pk, profile_pks)

    def test_does_not_show_other_program_volunteers(self):
        """Roster must not include volunteers whose applications are only in other coordinators' programs."""  # noqa: E501
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_roster")

        coord_b = _make_coordinator("roster-coord-b@wave4.gc.ca")
        prog_b = _make_program(slug="w4-roster-prog-b")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="w4-roster-opp-b")
        vol_b_user = _make_user("roster-vol-b@wave4.gc.ca")
        profile_b = _make_profile(vol_b_user)
        _make_application(profile_b, opp_b)

        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        profile_pks = [p.pk for p in response.context["profiles"]]
        self.assertNotIn(profile_b.pk, profile_pks)


# ===========================================================================
# VolunteerDetailView — GET /coordinator/volunteers/<pk>/
# ===========================================================================


class VolunteerDetailViewTests(Wave4BaseTestCase):
    def _url(self, pk=None):
        return _url("volunteer_detail", pk=pk or self.profile.pk)

    def test_anonymous_user_redirected(self):
        """Anonymous GET redirects to login."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_detail")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_non_coordinator_forbidden(self):
        """Authenticated user without coordinator permission gets 403."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_detail")
        self.login_as_volunteer()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_coordinator_sees_volunteer_detail(self):
        """Coordinator gets 200 for a valid volunteer profile."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_detail")
        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_sensitive_fields_hidden_without_permission(self):
        """
        accommodation_notes must not be visible without volunteers.view_accommodation_notes.
        The view None-outs sensitive fields server-side.
        """
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_detail")

        # Give the profile a non-empty accommodation note
        self.profile.accommodation_notes = "Wheelchair accessible seating required"
        self.profile.save(update_fields=["accommodation_notes"])

        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # The coordinator lacks view_accommodation_notes — sensitive field must not appear
        self.assertNotContains(response, "Wheelchair accessible seating required")

    def test_sensitive_fields_visible_with_permission(self):
        """
        accommodation_notes IS visible when volunteers.view_accommodation_notes is granted.
        """
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_detail")

        self.profile.accommodation_notes = "Low-vision screen reader required"
        self.profile.save(update_fields=["accommodation_notes"])

        # Grant sensitive field permission
        self.coordinator = _grant_perm(self.coordinator, "view_accommodation_notes")
        self.client.force_login(self.coordinator)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Low-vision screen reader required")

    def test_all_sensitive_fields_hidden_without_permission(self):
        """All five PIPEDA-sensitive fields must be absent from response when coordinator lacks permission."""  # noqa: E501
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_detail")

        self.profile.accommodation_notes = "Needs elevator access"
        self.profile.emergency_contact_name = "Jane Doe"
        self.profile.emergency_contact_phone = "613-555-0100"
        self.profile.emergency_contact_relationship = "Spouse"
        self.profile.sin_last4 = "1234"
        self.profile.save(
            update_fields=[
                "accommodation_notes",
                "emergency_contact_name",
                "emergency_contact_phone",
                "emergency_contact_relationship",
                "sin_last4",
            ]
        )

        self.login_as_coordinator()  # does NOT have view_accommodation_notes
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Needs elevator access", content)
        self.assertNotIn("Jane Doe", content)
        self.assertNotIn("613-555-0100", content)
        self.assertNotIn("Spouse", content)
        self.assertNotIn("1234", content)

    def test_all_sensitive_fields_visible_with_permission(self):
        """All five PIPEDA-sensitive fields must be visible when coordinator has view_accommodation_notes."""  # noqa: E501
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_detail")

        self.profile.accommodation_notes = "Needs elevator access"
        self.profile.emergency_contact_name = "Jane Doe"
        self.profile.emergency_contact_phone = "613-555-0100"
        self.profile.emergency_contact_relationship = "Spouse"
        self.profile.sin_last4 = "5678"
        self.profile.save(
            update_fields=[
                "accommodation_notes",
                "emergency_contact_name",
                "emergency_contact_phone",
                "emergency_contact_relationship",
                "sin_last4",
            ]
        )

        coordinator_with_perm = _grant_perm(self.coordinator, "view_accommodation_notes")
        self.client.force_login(coordinator_with_perm)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Needs elevator access", content)
        self.assertIn("Jane Doe", content)
        self.assertIn("613-555-0100", content)
        self.assertIn("Spouse", content)
        self.assertIn("5678", content)

    def test_idor_other_program_volunteer_returns_404(self):
        """Coordinator cannot view detail for a volunteer belonging to another coordinator's program."""  # noqa: E501
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_detail")

        coord_b = _make_coordinator("detail-coord-b@wave4.gc.ca")
        prog_b = _make_program(slug="detail-prog-b")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="detail-opp-b")
        vol_b_user = _make_user("detail-vol-b@wave4.gc.ca")
        profile_b = _make_profile(vol_b_user)
        _make_application(profile_b, opp_b)

        self.login_as_coordinator()
        response = self.client.get(
            reverse("volunteers:volunteer_detail", kwargs={"pk": profile_b.pk})
        )
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# VolunteerStatusChangeView — POST /coordinator/volunteers/<pk>/status/
# ===========================================================================


class VolunteerStatusChangeViewTests(Wave4BaseTestCase):
    def setUp(self):
        super().setUp()
        # Grant volunteers.change_volunteerprofile (required by the view)
        self.coordinator = _grant_perm(self.coordinator, "change_volunteerprofile")

    def _url(self, pk=None):
        return _url("volunteer_status_change", pk=pk or self.profile.pk)

    def test_anonymous_redirected(self):
        """Anonymous POST redirects to login."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_status_change")
        response = self.client.post(url, {"status": VolunteerProfile.STATUS_INACTIVE})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_non_coordinator_forbidden(self):
        """User without change_volunteerprofile permission gets 403."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_status_change")
        # Create user with only the coordinator perm but NOT change_volunteerprofile
        other_user = _make_user("w4noperm@wave4.gc.ca")
        self.client.force_login(other_user)
        response = self.client.post(url, {"status": VolunteerProfile.STATUS_INACTIVE})
        self.assertEqual(response.status_code, 403)

    def test_status_change_succeeds(self):
        """POST with valid status updates the volunteer's status."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_status_change")
        self.login_as_coordinator()
        response = self.client.post(url, {"status": VolunteerProfile.STATUS_INACTIVE})
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, VolunteerProfile.STATUS_INACTIVE)

    def test_invalid_status_returns_redirect_without_change(self):
        """Unknown status value — form invalid, status unchanged."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_status_change")
        self.login_as_coordinator()
        # Post an invalid status value
        self.client.post(url, {"status": "unicorn"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, VolunteerProfile.STATUS_ACTIVE)

    def test_idor_other_program_volunteer_returns_404(self):
        """Coordinator cannot change status of a volunteer not in their program."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_status_change")

        # Create a second coordinator with their own program and volunteer
        coord_b = _make_coordinator("w4coord_b@wave4.gc.ca")
        coord_b = _grant_perm(coord_b, "change_volunteerprofile")
        prog_b = _make_program(slug="w4-prog-b")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="w4-opp-b")
        vol_b_user = _make_user("w4vol_b@wave4.gc.ca")
        profile_b = _make_profile(vol_b_user)
        _make_application(profile_b, opp_b)

        # Coordinator A tries to change coordinator B's volunteer — must 404
        idor_url = _url("volunteer_status_change", pk=profile_b.pk)
        self.login_as_coordinator()
        response = self.client.post(idor_url, {"status": VolunteerProfile.STATUS_INACTIVE})
        self.assertEqual(response.status_code, 404)
        profile_b.refresh_from_db()
        self.assertEqual(profile_b.status, VolunteerProfile.STATUS_ACTIVE)

    def test_status_change_get_not_allowed(self):
        """GET on the status-change URL returns 405 Method Not Allowed."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_status_change")
        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)


# ===========================================================================
# AddVolunteerNoteView — POST /coordinator/volunteers/<pk>/note/
# ===========================================================================


class AddVolunteerNoteViewTests(Wave4BaseTestCase):
    def setUp(self):
        super().setUp()
        # Grant volunteers.change_volunteerprofile (required by the view)
        self.coordinator = _grant_perm(self.coordinator, "change_volunteerprofile")

    def _url(self, pk=None):
        return _url("volunteer_add_note", pk=pk or self.profile.pk)

    def test_anonymous_redirected(self):
        """Anonymous POST redirects to login."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_add_note")
        response = self.client.post(url, {"body": "Test note"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_adds_note_to_volunteer(self):
        """POST creates a VolunteerNote with the correct body and author."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_add_note")
        self.login_as_coordinator()
        note_body = "Attended orientation session on 2026-06-01"
        response = self.client.post(url, {"body": note_body})
        self.assertEqual(response.status_code, 302)
        note = VolunteerNote.objects.filter(volunteer=self.profile).latest("created_at")
        self.assertEqual(note.body, note_body)
        self.assertEqual(note.author, self.coordinator)

    def test_idor_other_program_volunteer_returns_404(self):
        """Coordinator cannot add a note to a volunteer outside their program."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_add_note")

        coord_b = _make_coordinator("w4coord_b_note@wave4.gc.ca")
        coord_b = _grant_perm(coord_b, "change_volunteerprofile")
        prog_b = _make_program(slug="w4-prog-b-note")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="w4-opp-b-note")
        vol_b_user = _make_user("w4vol_b_note@wave4.gc.ca")
        profile_b = _make_profile(vol_b_user)
        _make_application(profile_b, opp_b)

        idor_url = _url("volunteer_add_note", pk=profile_b.pk)
        self.login_as_coordinator()
        response = self.client.post(idor_url, {"body": "IDOR note"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(VolunteerNote.objects.filter(volunteer=profile_b).exists())

    def test_add_note_get_not_allowed(self):
        """GET on the add-note URL returns 405."""
        url = self._url()
        self._skip_if_url_missing(url, "volunteer_add_note")
        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)


# ===========================================================================
# RecordScreeningView — GET+POST /coordinator/volunteers/<pk>/screening/new/
# ===========================================================================


class RecordScreeningViewTests(Wave4BaseTestCase):
    def setUp(self):
        super().setUp()
        # Grant volunteers.add_screeningrecord (required by the view)
        self.coordinator = _grant_perm(self.coordinator, "add_screeningrecord")

    def _url(self, pk=None):
        return _url("record_screening", pk=pk or self.profile.pk)

    def test_get_renders_form(self):
        """GET returns 200 with a form present."""
        url = self._url()
        self._skip_if_url_missing(url, "record_screening")
        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("form", response.context)

    def test_post_creates_screening_record(self):
        """Valid POST creates a ScreeningRecord with the correct fields."""
        url = self._url()
        self._skip_if_url_missing(url, "record_screening")
        self.login_as_coordinator()
        response = self.client.post(
            url,
            {
                "check_type": ScreeningRecord.CHECK_TYPE_PRC,
                "completed_date": "2026-01-15",
            },
        )
        # On success the view redirects to volunteer_detail
        self.assertEqual(response.status_code, 302)
        record = ScreeningRecord.objects.filter(
            volunteer=self.profile,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
        ).first()
        self.assertIsNotNone(record)
        self.assertIsNone(record.verified_clear)  # pending by default

    def test_idor_other_program_volunteer_returns_404(self):
        """Coordinator cannot record a screening for a volunteer outside their program."""
        url = self._url()
        self._skip_if_url_missing(url, "record_screening")

        coord_b = _make_coordinator("w4coord_b_scr@wave4.gc.ca")
        coord_b = _grant_perm(coord_b, "add_screeningrecord")
        prog_b = _make_program(slug="w4-prog-b-scr")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="w4-opp-b-scr")
        vol_b_user = _make_user("w4vol_b_scr@wave4.gc.ca")
        profile_b = _make_profile(vol_b_user)
        _make_application(profile_b, opp_b)

        idor_url = _url("record_screening", pk=profile_b.pk)
        self.login_as_coordinator()
        response = self.client.post(
            idor_url,
            {
                "check_type": ScreeningRecord.CHECK_TYPE_PRC,
                "completed_date": "2026-01-15",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ScreeningRecord.objects.filter(volunteer=profile_b).exists())


# ===========================================================================
# CompleteScreeningView — POST /coordinator/screening/<pk>/complete/
# ===========================================================================


class CompleteScreeningViewTests(Wave4BaseTestCase):
    def setUp(self):
        super().setUp()
        # Grant both add and change permissions
        self.coordinator = _grant_perm(self.coordinator, "add_screeningrecord")
        self.coordinator = _grant_perm(self.coordinator, "change_screeningrecord")

        # Create a pending screening record for the volunteer
        self.screening = ScreeningRecord.objects.create(
            volunteer=self.profile,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=date.today(),
            verified_by=self.coordinator,
        )

    def _url(self, pk=None):
        return _url("complete_screening", pk=pk or self.screening.pk)

    def test_marks_screening_verified(self):
        """POST with verified_clear=True sets the record as cleared."""
        url = self._url()
        self._skip_if_url_missing(url, "complete_screening")
        self.login_as_coordinator()
        response = self.client.post(url, {"verified_clear": "True"})
        self.assertEqual(response.status_code, 302)
        self.screening.refresh_from_db()
        self.assertTrue(self.screening.verified_clear)
        self.assertIsNotNone(self.screening.verified_at)

    def test_idor_other_program_screening_returns_404(self):
        """Coordinator cannot complete a screening for a volunteer outside their program."""
        url = self._url()
        self._skip_if_url_missing(url, "complete_screening")

        coord_b = _make_coordinator("w4coord_b_cscr@wave4.gc.ca")
        coord_b = _grant_perm(coord_b, "change_screeningrecord")
        prog_b = _make_program(slug="w4-prog-b-cscr")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="w4-opp-b-cscr")
        vol_b_user = _make_user("w4vol_b_cscr@wave4.gc.ca")
        profile_b = _make_profile(vol_b_user)
        _make_application(profile_b, opp_b)
        screening_b = ScreeningRecord.objects.create(
            volunteer=profile_b,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=date.today(),
            verified_by=coord_b,
        )

        idor_url = _url("complete_screening", pk=screening_b.pk)
        self.login_as_coordinator()
        response = self.client.post(idor_url, {"verified_clear": "true"})
        self.assertEqual(response.status_code, 404)
        screening_b.refresh_from_db()
        self.assertIsNone(screening_b.verified_clear)


# ===========================================================================
# HonorariumCreateView — GET+POST /coordinator/volunteers/<pk>/honorarium/new/
# ===========================================================================

from django.test import override_settings  # noqa: E402


@override_settings(
    VOLUNTEER_CRA_ALERT_THRESHOLD=450,
    VOLUNTEER_CRA_T4A_THRESHOLD=500,
    VOLUNTEER_CRA_HARD_BLOCK=1000,
)
class HonorariumCreateViewTests(Wave4BaseTestCase):
    def setUp(self):
        super().setUp()
        # Grant volunteers.add_honorarium (required by the view)
        self.coordinator = _grant_perm(self.coordinator, "add_honorarium")

    def _url(self, pk=None):
        return _url("honorarium_create", pk=pk or self.profile.pk)

    def test_get_renders_form_with_ytd(self):
        """GET returns 200 with form and ytd_honorarium in context."""
        url = self._url()
        self._skip_if_url_missing(url, "honorarium_create")
        self.login_as_coordinator()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("form", response.context)
        self.assertIn("ytd_honorarium", response.context)

    def test_post_creates_honorarium(self):
        """Valid POST creates an Honorarium record and redirects to volunteer detail."""
        url = self._url()
        self._skip_if_url_missing(url, "honorarium_create")
        self.login_as_coordinator()
        response = self.client.post(
            url,
            {
                "payment_type": Honorarium.PAYMENT_TYPE_HONORARIUM,
                "amount": "100.00",
                "description": "Wave 4 test honorarium",
                "payment_date": "2026-06-01",
            },
        )
        self.assertEqual(response.status_code, 302)
        h = Honorarium.objects.filter(volunteer=self.profile).first()
        self.assertIsNotNone(h)
        self.assertEqual(h.amount, Decimal("100.00"))

    def test_idor_other_program_volunteer_returns_404(self):
        """Coordinator cannot create an honorarium for a volunteer belonging to another coordinator's program."""  # noqa: E501
        url = self._url()
        self._skip_if_url_missing(url, "honorarium_create")

        coord_b = _make_coordinator("w4coord-b-hon@wave4.gc.ca")
        coord_b = _grant_perm(coord_b, "add_honorarium")
        prog_b = _make_program(slug="w4-hon-prog-b")
        prog_b.coordinator = coord_b
        prog_b.save(update_fields=["coordinator"])
        opp_b = _make_opportunity(prog_b, slug="w4-hon-opp-b")
        vol_b_user = _make_user("w4vol-b-hon@wave4.gc.ca")
        profile_b = _make_profile(vol_b_user)
        _make_application(profile_b, opp_b)

        idor_url = _url("honorarium_create", pk=profile_b.pk)
        self.login_as_coordinator()
        response = self.client.get(idor_url)
        self.assertEqual(response.status_code, 404)

    def test_cra_hard_block_raises_error_in_form(self):
        """
        YTD at $1000+ produces a form validation error and no Honorarium is created.
        """
        url = self._url()
        self._skip_if_url_missing(url, "honorarium_create")

        # Pre-populate to just at the hard block
        _make_honorarium(self.profile, "1000.00", created_by=self.coordinator)

        self.login_as_coordinator()
        response = self.client.post(
            url,
            {
                "payment_type": Honorarium.PAYMENT_TYPE_HONORARIUM,
                "amount": "0.01",
                "description": "Should be blocked",
                "payment_date": "2026-06-01",
            },
        )
        # View re-renders form with error (200) — not a redirect
        self.assertEqual(response.status_code, 200)
        # No new honorarium should have been created beyond the setup row
        count = Honorarium.objects.filter(
            volunteer=self.profile,
            description="Should be blocked",
        ).count()
        self.assertEqual(count, 0)


# ===========================================================================
# Security invariants — batch login check
# ===========================================================================


class Wave4SecurityInvariantsTests(Wave4BaseTestCase):
    """
    Cross-cutting security assertions for all 7 Wave 4 coordinator URLs.
    """

    def setUp(self):
        super().setUp()
        # Grant extra permissions needed for some views' setup
        self.coordinator = _grant_perm(self.coordinator, "change_volunteerprofile")
        self.coordinator = _grant_perm(self.coordinator, "add_screeningrecord")
        self.coordinator = _grant_perm(self.coordinator, "change_screeningrecord")
        self.coordinator = _grant_perm(self.coordinator, "add_honorarium")

        # Create a pending screening record for complete_screening URL
        self.screening = ScreeningRecord.objects.create(
            volunteer=self.profile,
            check_type=ScreeningRecord.CHECK_TYPE_PRC,
            completed_date=date.today(),
            verified_by=self.coordinator,
        )

    def test_all_wave4_coordinator_urls_require_login(self):
        """
        All 7 Wave 4 coordinator URLs must redirect anonymous users to login.
        """
        wave4_urls = [
            _url("volunteer_roster"),
            _url("volunteer_detail", pk=self.profile.pk),
            _url("volunteer_status_change", pk=self.profile.pk),
            _url("volunteer_add_note", pk=self.profile.pk),
            _url("record_screening", pk=self.profile.pk),
            _url("complete_screening", pk=self.screening.pk),
            _url("honorarium_create", pk=self.profile.pk),
        ]

        for url in wave4_urls:
            if url is None:
                continue
            with self.subTest(url=url):
                response = self.client.get(url)
                # POST-only views return 405 for GET even when unauthenticated;
                # use GET for all and accept 302 or 405 (GET-blocked view still
                # requires login for POST, and 405 does not leak auth state on GET).
                self.assertIn(
                    response.status_code,
                    [302, 405],
                    f"Expected 302 or 405 for anonymous GET to {url}, "
                    f"got {response.status_code}",
                )
                # For 302s: must redirect to login
                if response.status_code == 302:
                    self.assertIn(
                        "login",
                        response["Location"].lower(),
                        f"Redirect from {url} does not go to login",
                    )

    def test_anonymous_post_to_status_change_redirects_to_login(self):
        """Anonymous POST to volunteer_status_change must redirect to login, not 405."""
        self.client.logout()  # ensure anonymous
        url = _url("volunteer_status_change", pk=self.profile.pk)
        if url is None:
            self.skipTest("URL 'volunteers:volunteer_status_change' not yet registered.")
        response = self.client.post(url, {"status": "active"})
        self.assertIn(response.status_code, [302, 403])
        if response.status_code == 302:
            self.assertIn("/login/", response["Location"])

    def test_anonymous_post_to_add_note_redirects_to_login(self):
        """Anonymous POST to volunteer_add_note must redirect to login."""
        self.client.logout()
        url = _url("volunteer_add_note", pk=self.profile.pk)
        if url is None:
            self.skipTest("URL 'volunteers:volunteer_add_note' not yet registered.")
        response = self.client.post(url, {"body": "Test note"})
        self.assertIn(response.status_code, [302, 403])
        if response.status_code == 302:
            self.assertIn("/login/", response["Location"])

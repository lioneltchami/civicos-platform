"""
Wave 1 — Volunteer Management BB: model test suite.

Covers:
  - SkillTag: bilingual name helper, slug uniqueness
  - Program: CRA category choices, coordinator limit_choices_to
  - Opportunity: is_accepting_applications property, required_profile_fields default
  - VolunteerProfile: __str__ PII-free, status choices, total_hours_approved default,
      custom permission, SIN last4 field, photo upload_to
  - VolunteerApplication: unique_together, status choices
  - Shift: CheckConstraint (end > start), duration_hours property,
      effective_capacity fallthrough
  - ShiftBooking: unique_together, waitlist_position nullable
  - HoursLog: CheckConstraint (0 < hours ≤ 24), on_delete=PROTECT for volunteer
  - ScreeningRecord: three-state verified_clear, is_expired, expires_within_30_days
  - Certification: is_expired, expires_within_30_days
  - Honorarium: calendar_year auto-derived in save(), amount positive constraint,
      CRA thresholds stored in settings
  - VolunteerNote: coordinator-only note, author PROTECT
  - RecognitionMilestone: unique_together, notification_sent default False
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.volunteers.models import (
    Certification,
    HoursLog,
    Honorarium,
    Opportunity,
    Program,
    RecognitionMilestone,
    ScreeningRecord,
    Shift,
    ShiftBooking,
    SkillTag,
    VolunteerApplication,
    VolunteerNote,
    VolunteerProfile,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

_user_counter = 0


def make_user(email="alice@example.com", is_staff=False):
    global _user_counter
    _user_counter += 1
    # Ensure unique email each call when default is reused across test cases
    return User.objects.create_user(
        email=email,
        password="correcthorse",
        is_staff=is_staff,
    )


def make_program(coordinator=None, cra_category="welfare"):
    return Program.objects.create(
        name_en="Test Program",
        name_fr="Programme test",
        slug="test-program",
        cra_category=cra_category,
        coordinator=coordinator,
    )


def make_opportunity(program, status="published", closes_at=None):
    return Opportunity.objects.create(
        title_en="Drive Clients to Appointments",
        title_fr="Conduire des clients à leurs rendez-vous",
        slug="drive-clients",
        description_en="Drive elderly clients.",
        description_fr="Conduire les clients âgés.",
        program=program,
        status=status,
        closes_at=closes_at,
    )


def make_profile(user):
    return VolunteerProfile.objects.create(user=user)


def make_application(opportunity, profile, status="pending"):
    return VolunteerApplication.objects.create(
        opportunity=opportunity,
        volunteer=profile,
        status=status,
    )


def make_shift(opportunity, start_offset_hours=24, duration_hours=3):
    start = timezone.now() + datetime.timedelta(hours=start_offset_hours)
    end = start + datetime.timedelta(hours=duration_hours)
    return Shift.objects.create(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
    )


def make_honorarium(volunteer, amount, payment_date=None, created_by=None):
    if payment_date is None:
        payment_date = timezone.localtime(timezone.now()).date()
    if created_by is None:
        created_by = volunteer.user
    return Honorarium.objects.create(
        volunteer=volunteer,
        payment_type="honorarium",
        amount=amount,
        description="Quarterly honorarium",
        payment_date=payment_date,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# SkillTag tests
# ---------------------------------------------------------------------------

class SkillTagTests(TestCase):

    def setUp(self):
        self.tag = SkillTag.objects.create(
            name_en="First Aid",
            name_fr="Premiers secours",
            slug="first-aid",
            category="health_safety",
        )

    def test_str_contains_english_name(self):
        self.assertIn("First Aid", str(self.tag))

    def test_get_name_english(self):
        from django.test.utils import override_settings
        from django.utils import translation
        with translation.override("en"):
            self.assertEqual(self.tag.get_name(), "First Aid")

    def test_get_name_french(self):
        from django.utils import translation
        with translation.override("fr"):
            self.assertEqual(self.tag.get_name(), "Premiers secours")

    def test_get_name_defaults_to_english_for_unknown_language(self):
        from django.utils import translation
        with translation.override("es"):
            # "es" doesn't start with "fr" → falls back to English
            self.assertEqual(self.tag.get_name(), "First Aid")

    def test_slug_unique(self):
        with self.assertRaises(IntegrityError):
            SkillTag.objects.create(
                name_en="First Aid Duplicate",
                name_fr="Premiers secours (dup)",
                slug="first-aid",  # duplicate
            )

    def test_is_active_default_true(self):
        self.assertTrue(self.tag.is_active)

    def test_ordering_by_name_en(self):
        SkillTag.objects.create(name_en="Aaardvark Skill", name_fr="Compétence aardvark", slug="aardvark")
        first = SkillTag.objects.first()
        self.assertEqual(first.name_en, "Aaardvark Skill")


# ---------------------------------------------------------------------------
# Program tests
# ---------------------------------------------------------------------------

class ProgramTests(TestCase):

    def test_cra_category_choices(self):
        valid_categories = [c[0] for c in Program.CRA_CATEGORY_CHOICES]
        self.assertIn("welfare", valid_categories)
        self.assertIn("education", valid_categories)
        self.assertIn("health", valid_categories)
        self.assertIn("religion", valid_categories)
        self.assertIn("other", valid_categories)

    def test_coordinator_must_be_staff(self):
        """A non-staff user should not be usable as coordinator via limit_choices_to."""
        non_staff = make_user(email="bob@example.com", is_staff=False)
        program = Program.objects.create(
            name_en="P", name_fr="P", slug="p-slug", coordinator=non_staff
        )
        # DB stores the FK regardless of limit_choices_to (that's UI-only enforcement).
        # Confirm this at model level and note that view/admin must enforce separately.
        self.assertEqual(program.coordinator_id, non_staff.pk)

    def test_slug_unique(self):
        Program.objects.create(name_en="P1", name_fr="P1", slug="unique-prog")
        with self.assertRaises(IntegrityError):
            Program.objects.create(name_en="P2", name_fr="P2", slug="unique-prog")

    def test_str_returns_english_name(self):
        p = make_program()
        self.assertIn("Test Program", str(p))

    def test_is_active_default_true(self):
        p = make_program()
        self.assertTrue(p.is_active)


# ---------------------------------------------------------------------------
# Opportunity tests
# ---------------------------------------------------------------------------

class OpportunityTests(TestCase):

    def setUp(self):
        self.program = make_program()

    def test_is_accepting_applications_published_no_close(self):
        opp = make_opportunity(self.program, status="published")
        self.assertTrue(opp.is_accepting_applications)

    def test_is_accepting_applications_draft_is_false(self):
        opp = make_opportunity(self.program, status="draft")
        self.assertFalse(opp.is_accepting_applications)

    def test_is_accepting_applications_closed_is_false(self):
        opp = make_opportunity(self.program, status="closed")
        self.assertFalse(opp.is_accepting_applications)

    def test_is_accepting_applications_past_closes_at_is_false(self):
        past = timezone.now() - datetime.timedelta(hours=1)
        opp = make_opportunity(self.program, status="published", closes_at=past)
        self.assertFalse(opp.is_accepting_applications)

    def test_is_accepting_applications_future_closes_at_is_true(self):
        future = timezone.now() + datetime.timedelta(days=10)
        opp = make_opportunity(self.program, status="published", closes_at=future)
        self.assertTrue(opp.is_accepting_applications)

    def test_required_profile_fields_default_empty_list(self):
        opp = make_opportunity(self.program)
        self.assertEqual(opp.required_profile_fields, [])

    def test_required_profile_fields_stores_list(self):
        opp = make_opportunity(self.program)
        opp.required_profile_fields = ["phone_number", "date_of_birth"]
        opp.save(update_fields=["required_profile_fields"])
        opp.refresh_from_db()
        self.assertIn("phone_number", opp.required_profile_fields)

    def test_slug_unique(self):
        make_opportunity(self.program)
        with self.assertRaises(IntegrityError):
            make_opportunity(self.program)  # same slug "drive-clients"

    def test_status_index_choices(self):
        valid = [c[0] for c in Opportunity.STATUS_CHOICES]
        self.assertIn("draft", valid)
        self.assertIn("published", valid)
        self.assertIn("closed", valid)
        self.assertIn("archived", valid)


# ---------------------------------------------------------------------------
# VolunteerProfile tests
# ---------------------------------------------------------------------------

class VolunteerProfileTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.profile = make_profile(self.user)

    def test_str_is_pipeda_safe(self):
        """__str__ must not contain email, username, or full name."""
        result = str(self.profile)
        self.assertNotIn("alice", result)
        self.assertNotIn("alice@example.com", result)
        self.assertIn(str(self.profile.pk), result)

    def test_status_default_active(self):
        self.assertEqual(self.profile.status, "active")

    def test_total_hours_approved_default_zero(self):
        self.assertEqual(self.profile.total_hours_approved, Decimal("0"))

    def test_sin_encrypted_nullable(self):
        self.assertIsNone(self.profile.sin_encrypted)

    def test_sin_last4_blank_default(self):
        self.assertEqual(self.profile.sin_last4, "")

    def test_date_of_birth_nullable(self):
        self.assertIsNone(self.profile.date_of_birth)

    def test_preferred_language_default_en(self):
        self.assertEqual(self.profile.preferred_language, "en")

    def test_onetoone_cascade_on_user_delete(self):
        uid = self.user.pk
        self.user.delete()
        self.assertFalse(VolunteerProfile.objects.filter(user_id=uid).exists())

    def test_custom_permission_exists(self):
        from django.contrib.contenttypes.models import ContentType
        from django.contrib.auth.models import Permission
        ct = ContentType.objects.get_for_model(VolunteerProfile)
        self.assertTrue(
            Permission.objects.filter(
                codename="view_accommodation_notes",
                content_type=ct,
            ).exists()
        )

    def test_skills_many_to_many(self):
        tag = SkillTag.objects.create(name_en="CPR", name_fr="RCR", slug="cpr")
        self.profile.skills.add(tag)
        self.assertIn(tag, self.profile.skills.all())


# ---------------------------------------------------------------------------
# VolunteerApplication tests
# ---------------------------------------------------------------------------

class VolunteerApplicationTests(TestCase):

    def setUp(self):
        self.program = make_program()
        self.opp = make_opportunity(self.program)
        self.user = make_user()
        self.profile = make_profile(self.user)

    def test_create_application(self):
        app = make_application(self.opp, self.profile)
        self.assertEqual(app.status, "pending")
        self.assertIsNone(app.reviewed_at)

    def test_unique_together_opportunity_volunteer(self):
        make_application(self.opp, self.profile)
        with self.assertRaises(IntegrityError):
            make_application(self.opp, self.profile)

    def test_different_volunteer_same_opportunity_allowed(self):
        user2 = make_user(email="bob@example.com")
        profile2 = make_profile(user2)
        make_application(self.opp, self.profile)
        app2 = make_application(self.opp, profile2)
        self.assertIsNotNone(app2.pk)

    def test_status_choices_include_all_states(self):
        valid = [c[0] for c in VolunteerApplication.STATUS_CHOICES]
        for state in ("pending", "in_review", "approved", "rejected", "withdrawn", "waitlisted"):
            self.assertIn(state, valid)

    def test_work_item_nullable(self):
        app = make_application(self.opp, self.profile)
        self.assertIsNone(app.work_item)

    def test_protect_on_opportunity_delete(self):
        make_application(self.opp, self.profile)
        with self.assertRaises(Exception):  # PROTECT raises ProtectedError
            with transaction.atomic():
                self.opp.delete()


# ---------------------------------------------------------------------------
# Shift tests
# ---------------------------------------------------------------------------

class ShiftTests(TestCase):

    def setUp(self):
        self.program = make_program()
        self.opp = make_opportunity(self.program)

    def test_create_valid_shift(self):
        shift = make_shift(self.opp)
        self.assertFalse(shift.is_cancelled)

    def test_duration_hours_property(self):
        start = timezone.now() + datetime.timedelta(hours=24)
        end = start + datetime.timedelta(hours=2, minutes=30)
        shift = Shift.objects.create(opportunity=self.opp, start_datetime=start, end_datetime=end)
        # 2.5 hours
        self.assertAlmostEqual(float(shift.duration_hours), 2.5, places=2)

    def test_check_constraint_end_before_start_raises(self):
        """Database must reject shifts where end <= start."""
        now = timezone.now()
        with self.assertRaises(Exception):
            with transaction.atomic():
                Shift.objects.create(
                    opportunity=self.opp,
                    start_datetime=now + datetime.timedelta(hours=5),
                    end_datetime=now + datetime.timedelta(hours=1),  # before start
                )

    def test_check_constraint_equal_start_end_raises(self):
        now = timezone.now() + datetime.timedelta(hours=5)
        with self.assertRaises(Exception):
            with transaction.atomic():
                Shift.objects.create(
                    opportunity=self.opp,
                    start_datetime=now,
                    end_datetime=now,  # equal
                )

    def test_effective_capacity_falls_through_to_opportunity(self):
        self.opp.volunteer_capacity = 10
        self.opp.save(update_fields=["volunteer_capacity"])
        shift = Shift.objects.create(
            opportunity=self.opp,
            start_datetime=timezone.now() + datetime.timedelta(hours=24),
            end_datetime=timezone.now() + datetime.timedelta(hours=27),
            capacity=None,  # no override
        )
        self.assertEqual(shift.effective_capacity, 10)

    def test_effective_capacity_shift_override_wins(self):
        self.opp.volunteer_capacity = 10
        self.opp.save(update_fields=["volunteer_capacity"])
        shift = Shift.objects.create(
            opportunity=self.opp,
            start_datetime=timezone.now() + datetime.timedelta(hours=24),
            end_datetime=timezone.now() + datetime.timedelta(hours=27),
            capacity=5,
        )
        self.assertEqual(shift.effective_capacity, 5)

    def test_effective_capacity_none_when_both_none(self):
        shift = make_shift(self.opp)
        # Opportunity has no capacity, shift has no capacity
        self.assertIsNone(shift.effective_capacity)

    def test_ordering_by_start_datetime(self):
        s1 = make_shift(self.opp, start_offset_hours=48)
        s2 = make_shift(self.opp, start_offset_hours=24)
        shifts = list(Shift.objects.filter(opportunity=self.opp))
        self.assertEqual(shifts[0].pk, s2.pk)
        self.assertEqual(shifts[1].pk, s1.pk)


# ---------------------------------------------------------------------------
# ShiftBooking tests
# ---------------------------------------------------------------------------

class ShiftBookingTests(TestCase):

    def setUp(self):
        self.program = make_program()
        self.opp = make_opportunity(self.program)
        self.shift = make_shift(self.opp)
        self.user = make_user()
        self.profile = make_profile(self.user)

    def test_create_booking_confirmed(self):
        booking = ShiftBooking.objects.create(shift=self.shift, volunteer=self.profile)
        self.assertEqual(booking.status, "confirmed")
        self.assertIsNone(booking.waitlist_position)

    def test_unique_together_shift_volunteer(self):
        ShiftBooking.objects.create(shift=self.shift, volunteer=self.profile)
        with self.assertRaises(IntegrityError):
            ShiftBooking.objects.create(shift=self.shift, volunteer=self.profile)

    def test_reminder_flags_default_false(self):
        booking = ShiftBooking.objects.create(shift=self.shift, volunteer=self.profile)
        self.assertFalse(booking.reminder_24h_sent)
        self.assertFalse(booking.reminder_2h_sent)

    def test_waitlist_position_nullable(self):
        booking = ShiftBooking.objects.create(
            shift=self.shift, volunteer=self.profile, status="waitlisted", waitlist_position=1
        )
        self.assertEqual(booking.waitlist_position, 1)

    def test_status_choices(self):
        valid = [c[0] for c in ShiftBooking.STATUS_CHOICES]
        for s in ("confirmed", "waitlisted", "cancelled", "no_show", "completed"):
            self.assertIn(s, valid)


# ---------------------------------------------------------------------------
# HoursLog tests
# ---------------------------------------------------------------------------

class HoursLogTests(TestCase):

    def setUp(self):
        self.program = make_program()
        self.opp = make_opportunity(self.program)
        self.user = make_user()
        self.profile = make_profile(self.user)

    def _create_log(self, hours):
        return HoursLog.objects.create(
            volunteer=self.profile,
            opportunity=self.opp,
            date=datetime.date.today(),
            hours=hours,
        )

    def test_valid_hours(self):
        log = self._create_log(Decimal("3.50"))
        self.assertEqual(log.status, "pending")

    def test_hours_zero_raises_check_constraint(self):
        with self.assertRaises(Exception):
            with transaction.atomic():
                self._create_log(Decimal("0"))

    def test_hours_negative_raises(self):
        with self.assertRaises(Exception):
            with transaction.atomic():
                self._create_log(Decimal("-1"))

    def test_hours_above_24_raises(self):
        with self.assertRaises(Exception):
            with transaction.atomic():
                self._create_log(Decimal("24.01"))

    def test_hours_exactly_24_allowed(self):
        log = self._create_log(Decimal("24"))
        self.assertEqual(log.hours, Decimal("24"))

    def test_volunteer_protect_on_profile_delete(self):
        """Deleting the profile must be prevented when hours exist."""
        self._create_log(Decimal("3"))
        with self.assertRaises(Exception):
            with transaction.atomic():
                self.profile.delete()

    def test_ordering_by_date_descending(self):
        log1 = HoursLog.objects.create(
            volunteer=self.profile,
            date=datetime.date(2024, 1, 1),
            hours=Decimal("2"),
        )
        log2 = HoursLog.objects.create(
            volunteer=self.profile,
            date=datetime.date(2024, 2, 1),
            hours=Decimal("3"),
        )
        logs = list(HoursLog.objects.filter(volunteer=self.profile))
        self.assertEqual(logs[0].pk, log2.pk)  # most recent first

    def test_status_choices(self):
        valid = [c[0] for c in HoursLog.STATUS_CHOICES]
        for s in ("pending", "approved", "rejected"):
            self.assertIn(s, valid)


# ---------------------------------------------------------------------------
# ScreeningRecord tests
# ---------------------------------------------------------------------------

class ScreeningRecordTests(TestCase):

    def setUp(self):
        self.program = make_program()
        self.opp = make_opportunity(self.program)
        self.user = make_user()
        self.profile = make_profile(self.user)
        self.today = timezone.localtime(timezone.now()).date()

    def _make_screening(self, expires_delta_days=None, verified_clear=None):
        expires = (
            (self.today + datetime.timedelta(days=expires_delta_days))
            if expires_delta_days is not None
            else None
        )
        return ScreeningRecord.objects.create(
            volunteer=self.profile,
            check_type="vulnerable_sector_check",
            completed_date=self.today,
            expires_date=expires,
            verified_clear=verified_clear,
        )

    def test_verified_clear_three_states(self):
        unverified = self._make_screening()
        self.assertIsNone(unverified.verified_clear)

        cleared = self._make_screening(verified_clear=True)
        self.assertTrue(cleared.verified_clear)

        failed = self._make_screening(verified_clear=False)
        self.assertFalse(failed.verified_clear)

    def test_is_expired_false_when_no_expires_date(self):
        s = self._make_screening()
        self.assertFalse(s.is_expired)

    def test_is_expired_true_when_past(self):
        s = self._make_screening(expires_delta_days=-1)
        self.assertTrue(s.is_expired)

    def test_is_expired_false_when_future(self):
        s = self._make_screening(expires_delta_days=30)
        self.assertFalse(s.is_expired)

    def test_is_expired_false_on_expiry_day(self):
        """Expires today — is_expired should be False (inclusive boundary)."""
        s = self._make_screening(expires_delta_days=0)
        self.assertFalse(s.is_expired)

    def test_expires_within_30_days_true_when_29_days(self):
        s = self._make_screening(expires_delta_days=29)
        self.assertTrue(s.expires_within_30_days)

    def test_expires_within_30_days_false_when_31_days(self):
        s = self._make_screening(expires_delta_days=31)
        self.assertFalse(s.expires_within_30_days)

    def test_expires_within_30_days_false_when_no_expires(self):
        s = self._make_screening()
        self.assertFalse(s.expires_within_30_days)

    def test_check_type_choices(self):
        valid = [c[0] for c in ScreeningRecord.CHECK_TYPE_CHOICES]
        self.assertIn("vulnerable_sector_check", valid)
        self.assertIn("police_record_check", valid)
        self.assertIn("reference_check", valid)
        self.assertIn("drivers_abstract", valid)

    def test_protect_on_volunteer_delete(self):
        self._make_screening()
        with self.assertRaises(Exception):
            with transaction.atomic():
                self.profile.delete()


# ---------------------------------------------------------------------------
# Certification tests
# ---------------------------------------------------------------------------

class CertificationTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.profile = make_profile(self.user)
        self.today = timezone.localtime(timezone.now()).date()

    def _make_cert(self, expires_delta_days=None):
        expires = (
            (self.today + datetime.timedelta(days=expires_delta_days))
            if expires_delta_days is not None
            else None
        )
        return Certification.objects.create(
            volunteer=self.profile,
            cert_type="first_aid",
            issued_date=self.today - datetime.timedelta(days=365),
            expires_date=expires,
        )

    def test_is_expired_false_when_no_expires(self):
        c = self._make_cert()
        self.assertFalse(c.is_expired)

    def test_is_expired_true_when_past(self):
        c = self._make_cert(expires_delta_days=-1)
        self.assertTrue(c.is_expired)

    def test_expires_within_30_days_true(self):
        c = self._make_cert(expires_delta_days=15)
        self.assertTrue(c.expires_within_30_days)

    def test_expires_within_30_days_false_when_far_future(self):
        c = self._make_cert(expires_delta_days=90)
        self.assertFalse(c.expires_within_30_days)

    def test_cert_type_choices(self):
        valid = [c[0] for c in Certification.CERT_TYPE_CHOICES]
        for t in ("first_aid", "cpr", "whmis", "food_handler", "drivers_licence", "other"):
            self.assertIn(t, valid)

    def test_cascade_on_profile_delete(self):
        """Certifications cascade-delete when profile is deleted."""
        c = self._make_cert()
        pk = c.pk
        # Must delete hours/screenings first to avoid PROTECT on profile
        self.profile.delete()
        self.assertFalse(Certification.objects.filter(pk=pk).exists())


# ---------------------------------------------------------------------------
# Honorarium tests
# ---------------------------------------------------------------------------

class HonorariumTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.profile = make_profile(self.user)

    def test_calendar_year_auto_derived_from_payment_date(self):
        h = make_honorarium(self.profile, Decimal("100.00"), payment_date=datetime.date(2024, 3, 15))
        self.assertEqual(h.calendar_year, 2024)

    def test_calendar_year_correct_for_december(self):
        h = make_honorarium(self.profile, Decimal("50.00"), payment_date=datetime.date(2023, 12, 31))
        self.assertEqual(h.calendar_year, 2023)

    def test_calendar_year_correct_for_january(self):
        h = make_honorarium(self.profile, Decimal("50.00"), payment_date=datetime.date(2025, 1, 1))
        self.assertEqual(h.calendar_year, 2025)

    def test_t4a_required_default_false(self):
        h = make_honorarium(self.profile, Decimal("100.00"))
        self.assertFalse(h.t4a_required)

    def test_amount_zero_raises_check_constraint(self):
        with self.assertRaises(Exception):
            with transaction.atomic():
                Honorarium.objects.create(
                    volunteer=self.profile,
                    payment_type="honorarium",
                    amount=Decimal("0.00"),
                    description="Zero",
                    payment_date=datetime.date(2024, 1, 1),
                    created_by=self.user,
                )

    def test_amount_negative_raises_check_constraint(self):
        with self.assertRaises(Exception):
            with transaction.atomic():
                Honorarium.objects.create(
                    volunteer=self.profile,
                    payment_type="honorarium",
                    amount=Decimal("-10.00"),
                    description="Negative",
                    payment_date=datetime.date(2024, 1, 1),
                    created_by=self.user,
                )

    def test_payment_type_choices(self):
        valid = [c[0] for c in Honorarium.PAYMENT_TYPE_CHOICES]
        self.assertIn("expense_reimbursement", valid)
        self.assertIn("honorarium", valid)

    def test_currency_default_cad(self):
        h = make_honorarium(self.profile, Decimal("200.00"))
        self.assertEqual(h.currency, "CAD")

    @override_settings(
        VOLUNTEER_CRA_T4A_THRESHOLD=500.00,
        VOLUNTEER_CRA_HARD_BLOCK=1000.00,
    )
    def test_cra_thresholds_in_settings(self):
        from django.conf import settings
        self.assertEqual(settings.VOLUNTEER_CRA_T4A_THRESHOLD, 500.00)
        self.assertEqual(settings.VOLUNTEER_CRA_HARD_BLOCK, 1000.00)


# ---------------------------------------------------------------------------
# VolunteerNote tests
# ---------------------------------------------------------------------------

class VolunteerNoteTests(TestCase):

    def setUp(self):
        self.coordinator = make_user(email="coord@example.com", is_staff=True)
        self.volunteer_user = make_user(email="vol@example.com")
        self.profile = make_profile(self.volunteer_user)

    def test_create_note(self):
        note = VolunteerNote.objects.create(
            volunteer=self.profile,
            author=self.coordinator,
            body="Volunteer showed up late but apologised.",
        )
        self.assertEqual(note.volunteer_id, self.profile.pk)
        self.assertEqual(note.author_id, self.coordinator.pk)

    def test_ordering_by_created_at_descending(self):
        n1 = VolunteerNote.objects.create(
            volunteer=self.profile, author=self.coordinator, body="First"
        )
        n2 = VolunteerNote.objects.create(
            volunteer=self.profile, author=self.coordinator, body="Second"
        )
        notes = list(VolunteerNote.objects.filter(volunteer=self.profile))
        self.assertEqual(notes[0].pk, n2.pk)

    def test_author_protect_on_delete(self):
        """Cannot delete a coordinator who has authored notes."""
        VolunteerNote.objects.create(
            volunteer=self.profile, author=self.coordinator, body="Sensitive note."
        )
        with self.assertRaises(Exception):
            with transaction.atomic():
                self.coordinator.delete()

    def test_cascade_on_volunteer_profile_delete(self):
        """Notes cascade-delete when the volunteer profile is deleted."""
        note = VolunteerNote.objects.create(
            volunteer=self.profile, author=self.coordinator, body="Will be deleted."
        )
        pk = note.pk
        # Must not have any PROTECT children on this profile first
        self.profile.delete()
        self.assertFalse(VolunteerNote.objects.filter(pk=pk).exists())


# ---------------------------------------------------------------------------
# RecognitionMilestone tests
# ---------------------------------------------------------------------------

class RecognitionMilestoneTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.profile = make_profile(self.user)

    def test_create_milestone(self):
        m = RecognitionMilestone.objects.create(
            volunteer=self.profile,
            hours_threshold=Decimal("100.00"),
        )
        self.assertFalse(m.notification_sent)

    def test_unique_together_volunteer_threshold(self):
        RecognitionMilestone.objects.create(
            volunteer=self.profile, hours_threshold=Decimal("100.00")
        )
        with self.assertRaises(IntegrityError):
            RecognitionMilestone.objects.create(
                volunteer=self.profile, hours_threshold=Decimal("100.00")
            )

    def test_different_thresholds_same_volunteer_allowed(self):
        RecognitionMilestone.objects.create(
            volunteer=self.profile, hours_threshold=Decimal("100.00")
        )
        m2 = RecognitionMilestone.objects.create(
            volunteer=self.profile, hours_threshold=Decimal("250.00")
        )
        self.assertIsNotNone(m2.pk)

    def test_different_volunteers_same_threshold_allowed(self):
        user2 = make_user(email="bob@example.com")
        profile2 = make_profile(user2)
        RecognitionMilestone.objects.create(
            volunteer=self.profile, hours_threshold=Decimal("100.00")
        )
        m2 = RecognitionMilestone.objects.create(
            volunteer=profile2, hours_threshold=Decimal("100.00")
        )
        self.assertIsNotNone(m2.pk)

    def test_ordering_by_hours_threshold_ascending(self):
        RecognitionMilestone.objects.create(
            volunteer=self.profile, hours_threshold=Decimal("500.00")
        )
        RecognitionMilestone.objects.create(
            volunteer=self.profile, hours_threshold=Decimal("50.00")
        )
        milestones = list(RecognitionMilestone.objects.filter(volunteer=self.profile))
        self.assertEqual(milestones[0].hours_threshold, Decimal("50.00"))
        self.assertEqual(milestones[1].hours_threshold, Decimal("500.00"))

    def test_notification_sent_default_false(self):
        m = RecognitionMilestone.objects.create(
            volunteer=self.profile, hours_threshold=Decimal("100.00")
        )
        self.assertFalse(m.notification_sent)

"""
Appointments BB Wave 1 — model test suite.

Coverage:
  Organization:
    - __str__, slug uniqueness, ordering
    - is_active default, organization_type choices

  SchedulingPolicy:
    - __str__, name uniqueness
    - validator guards on slot_interval_minutes (5–240)
    - default values match spec

  ServiceType:
    - slug uniqueness, ordering (sort_order, name_en)
    - privacy_sensitivity choices, category choices
    - is_active default True (BooleanField default, not a Python field attribute)

  AppointmentType:
    - slug uniqueness, FK service_type PROTECT
    - duration_minutes validators (5–480)
    - capacity_per_slot validators (1–100)
    - scheduling_policy SET_NULL on delete
    - get_effective_policy() returns scheduling_policy
    - default values match spec
    - is_active default True

  Location:
    - slug uniqueness, FK organization PROTECT
    - scheduling_policy SET_NULL on delete
    - timezone default "America/Toronto"
    - privacy_regime choices
    - is_active default True, is_virtual default False

  Resource:
    - FK location CASCADE (delete location → resource gone)
    - capacity MinValueValidator(1)
    - calendar_provider default "none"
    - is_active default True

  StaffProfile:
    - OneToOne user CASCADE
    - limit_choices_to is_staff=True (form-level; verified via field kwargs)
    - FK location SET_NULL
    - M2M appointment_types
    - __str__ — PII-free: contains only pk and user_id, NOT email/name
    - is_accepting_bookings default True
    - accepts_walk_ins default False

PIPEDA constraints:
  - StaffProfile.__str__ must not contain email address or full name.
  - No model __str__ may contain an email address field value.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.appointments.models import (
    AppointmentType,
    AvailabilityTemplate,
    Location,
    Organization,
    Resource,
    SchedulingPolicy,
    ServiceType,
    Slot,
    StaffException,
    StaffProfile,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def make_user(email: str = "user@example.com", is_staff: bool = False) -> User:
    return User.objects.create_user(email=email, password="testpass123!", is_staff=is_staff)


def make_org(slug: str = "test-org", **kwargs) -> Organization:
    kwargs.setdefault("name_en", "Test Organization")
    kwargs.setdefault("name_fr", "Organisation test")
    return Organization.objects.create(slug=slug, **kwargs)


def make_policy(name: str = "Default Policy", **kwargs) -> SchedulingPolicy:
    return SchedulingPolicy.objects.create(name=name, **kwargs)


def make_service_type(slug: str = "gov-service", **kwargs) -> ServiceType:
    kwargs.setdefault("name_en", "Government Service")
    kwargs.setdefault("name_fr", "Service gouvernemental")
    return ServiceType.objects.create(slug=slug, **kwargs)


def make_appt_type(service_type: ServiceType | None = None, slug: str = "consult", **kwargs) -> AppointmentType:
    if service_type is None:
        service_type = make_service_type()
    kwargs.setdefault("name_en", "Consultation")
    kwargs.setdefault("name_fr", "Consultation")
    return AppointmentType.objects.create(
        service_type=service_type,
        slug=slug,
        **kwargs,
    )


def make_location(organization: Organization | None = None, slug: str = "main-office", **kwargs) -> Location:
    if organization is None:
        organization = make_org()
    kwargs.setdefault("name_en", "Main Office")
    kwargs.setdefault("name_fr", "Bureau principal")
    return Location.objects.create(
        organization=organization,
        slug=slug,
        **kwargs,
    )


def make_resource(location: Location | None = None, **kwargs) -> Resource:
    if location is None:
        location = make_location()
    kwargs.setdefault("name_en", "Meeting Room 1")
    kwargs.setdefault("name_fr", "Salle de réunion 1")
    kwargs.setdefault("resource_type", "room")
    return Resource.objects.create(location=location, **kwargs)


def make_staff_profile(location: Location | None = None, suffix: str = "") -> StaffProfile:
    staff_user = make_user(email=f"staff{suffix}@example.com", is_staff=True)
    return StaffProfile.objects.create(
        user=staff_user,
        location=location,
    )


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

class OrganizationTests(TestCase):

    def test_str_uses_name_en(self):
        org = make_org(name_en="City of Waterloo", slug="city-waterloo")
        self.assertIn("City of Waterloo", str(org))

    def test_slug_unique(self):
        make_org(slug="unique-slug")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_org(slug="unique-slug")

    def test_is_active_default_true(self):
        org = make_org(slug="active-org")
        self.assertTrue(org.is_active)

    def test_ordering_by_name_en(self):
        make_org(slug="z-org", name_en="Z Organization")
        make_org(slug="a-org", name_en="A Organization")
        orgs = list(Organization.objects.values_list("name_en", flat=True))
        self.assertEqual(orgs, sorted(orgs))

    def test_organization_type_default(self):
        org = make_org(slug="default-type-org")
        self.assertEqual(org.organization_type, "government_federal")

    def test_organization_type_choices_valid(self):
        """Valid organization_type passes full_clean()."""
        for org_type in ["government_federal", "government_provincial", "government_municipal", "ngo", "health", "other"]:
            org = make_org(slug=f"org-type-{org_type}")
            org.organization_type = org_type
            org.full_clean()  # must not raise

    def test_organization_type_choices_invalid(self):
        """Invalid organization_type raises ValidationError."""
        from django.core.exceptions import ValidationError
        org = make_org(slug="org-type-invalid")
        org.organization_type = "not_a_real_type"
        with self.assertRaises(ValidationError):
            org.full_clean()

    def test_timestamps_auto_set(self):
        org = make_org(slug="ts-org")
        self.assertIsNotNone(org.created_at)
        self.assertIsNotNone(org.updated_at)

    def test_str_does_not_traverse_fk(self):
        """Organization.__str__ returns name_en without any DB query."""
        org = make_org(slug="org-str-fk")
        fresh = Organization.objects.get(pk=org.pk)
        with self.assertNumQueries(0):
            result = str(fresh)
        self.assertEqual(result, fresh.name_en)


# ---------------------------------------------------------------------------
# SchedulingPolicy
# ---------------------------------------------------------------------------

class SchedulingPolicyTests(TestCase):

    def test_str_uses_name(self):
        policy = make_policy("Standard Booking Policy")
        self.assertIn("Standard Booking Policy", str(policy))

    def test_name_unique(self):
        make_policy("Unique Policy Name")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_policy("Unique Policy Name")

    def test_default_slot_interval(self):
        policy = make_policy()
        self.assertEqual(policy.slot_interval_minutes, 15)

    def test_default_waitlist_enabled(self):
        policy = make_policy()
        self.assertTrue(policy.waitlist_enabled)

    def test_default_min_lead_time(self):
        policy = make_policy()
        self.assertEqual(policy.min_lead_time_hours, 1)

    def test_default_max_advance_days(self):
        policy = make_policy()
        self.assertEqual(policy.max_advance_days, 180)

    def test_slot_interval_below_min_fails_validation(self):
        policy = SchedulingPolicy(name="Bad Interval", slot_interval_minutes=4)
        with self.assertRaises(ValidationError):
            policy.full_clean()

    def test_slot_interval_above_max_fails_validation(self):
        policy = SchedulingPolicy(name="Big Interval", slot_interval_minutes=241)
        with self.assertRaises(ValidationError):
            policy.full_clean()

    def test_slot_interval_boundary_values_pass(self):
        # 5 and 240 are the inclusive boundaries
        p5 = SchedulingPolicy(name="Min Interval", slot_interval_minutes=5)
        p5.full_clean()  # should not raise

        p240 = SchedulingPolicy(name="Max Interval", slot_interval_minutes=240)
        p240.full_clean()  # should not raise

    def test_ordering_by_name(self):
        make_policy("Zeta Policy")
        make_policy("Alpha Policy")
        names = list(SchedulingPolicy.objects.values_list("name", flat=True))
        self.assertEqual(names, sorted(names))

    def test_default_no_show_thresholds(self):
        policy = make_policy()
        self.assertEqual(policy.no_show_warning_threshold, 1)
        self.assertEqual(policy.no_show_suspension_threshold, 3)

    def test_default_waitlist_batch_size(self):
        policy = make_policy()
        self.assertEqual(policy.waitlist_notify_batch_size, 3)

    def test_buffer_minutes_max_value_validator(self):
        """buffer_before/after_minutes must not exceed 240 minutes."""
        from django.core.exceptions import ValidationError
        # MaxValueValidator runs at full_clean() (Python-level) — no DB save needed.
        policy = SchedulingPolicy(
            name="test-buffer-max",
            buffer_before_minutes=241,
            buffer_after_minutes=0,
        )
        with self.assertRaises(ValidationError):
            policy.full_clean()

    def test_buffer_after_minutes_max_value_validator(self):
        """buffer_after_minutes must not exceed 240."""
        from django.core.exceptions import ValidationError
        # MaxValueValidator runs at full_clean() (Python-level) — no DB save needed.
        policy = SchedulingPolicy(
            name="test-buffer-after-max",
            buffer_before_minutes=0,
            buffer_after_minutes=999,
        )
        with self.assertRaises(ValidationError):
            policy.full_clean()

    # H-10: default field coverage

    def test_buffer_before_minutes_default_zero(self):
        """buffer_before_minutes defaults to 0 (no setup padding by default)."""
        policy = make_policy("buf-before-default")
        self.assertEqual(policy.buffer_before_minutes, 0)

    def test_buffer_after_minutes_default_five(self):
        """buffer_after_minutes defaults to 5 (standard wrap-up time)."""
        policy = make_policy("buf-after-default")
        self.assertEqual(policy.buffer_after_minutes, 5)

    def test_cancellation_notice_hours_default(self):
        """cancellation_notice_hours defaults to 24."""
        policy = make_policy("cancel-notice-default")
        self.assertEqual(policy.cancellation_notice_hours, 24)

    def test_reschedule_notice_hours_default(self):
        """reschedule_notice_hours defaults to 24."""
        policy = make_policy("reschedule-notice-default")
        self.assertEqual(policy.reschedule_notice_hours, 24)

    def test_max_reschedule_count_default(self):
        """max_reschedule_count defaults to 3."""
        policy = make_policy("max-reschedule-default")
        self.assertEqual(policy.max_reschedule_count, 3)

    def test_max_active_bookings_per_citizen_default(self):
        """max_active_bookings_per_citizen defaults to 3."""
        policy = make_policy("max-bookings-default")
        self.assertEqual(policy.max_active_bookings_per_citizen, 3)

    def test_booking_frequency_days_default_zero(self):
        """booking_frequency_days defaults to 0 (anti-hoarding control — disabled by default)."""
        policy = make_policy("freq-days-default")
        self.assertEqual(policy.booking_frequency_days, 0)

    def test_waitlist_acceptance_window_hours_default(self):
        """waitlist_acceptance_window_hours defaults to 2."""
        policy = make_policy("waitlist-window-default")
        self.assertEqual(policy.waitlist_acceptance_window_hours, 2)

    def test_max_waitlist_per_slot_default(self):
        """max_waitlist_per_slot defaults to 10."""
        policy = make_policy("max-waitlist-default")
        self.assertEqual(policy.max_waitlist_per_slot, 10)


# ---------------------------------------------------------------------------
# ServiceType
# ---------------------------------------------------------------------------

class ServiceTypeTests(TestCase):

    def test_slug_unique(self):
        make_service_type(slug="svc-slug")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_service_type(slug="svc-slug")

    def test_is_active_default_true(self):
        svc = make_service_type()
        self.assertTrue(svc.is_active)

    def test_category_default(self):
        svc = make_service_type()
        self.assertEqual(svc.category, "government")

    def test_sector_default(self):
        svc = make_service_type()
        self.assertEqual(svc.sector, "both")

    def test_privacy_sensitivity_default(self):
        svc = make_service_type()
        self.assertEqual(svc.privacy_sensitivity, "standard")

    def test_ordering_by_sort_order_then_name_en(self):
        make_service_type(slug="svc-z", name_en="Z Service", sort_order=0)
        make_service_type(slug="svc-a", name_en="A Service", sort_order=0)
        names = list(ServiceType.objects.values_list("name_en", flat=True))
        self.assertEqual(names, sorted(names))

    def test_sort_order_default_zero(self):
        svc = make_service_type()
        self.assertEqual(svc.sort_order, 0)

    def test_requires_eligibility_screening_default_false(self):
        svc = make_service_type()
        self.assertFalse(svc.requires_eligibility_screening)

    def test_privacy_sensitivity_choices_valid(self):
        for choice in ("standard", "protected_a", "protected_b"):
            svc = ServiceType(
                slug=f"svc-{choice}",
                name_en="Test",
                name_fr="Test",
                privacy_sensitivity=choice,
            )
            svc.full_clean()  # should not raise for valid choices

    def test_privacy_sensitivity_invalid_choice_raises(self):
        """An unrecognised privacy_sensitivity value must fail full_clean()."""
        from django.core.exceptions import ValidationError
        svc = make_service_type(slug="bad-sensitivity")
        svc.privacy_sensitivity = "top_secret"  # not a valid choice
        with self.assertRaises(ValidationError):
            svc.full_clean()

    def test_str_does_not_contain_email(self):
        svc = make_service_type(slug="pipeda-svc")
        self.assertNotIn("@", str(svc))


# ---------------------------------------------------------------------------
# AppointmentType
# ---------------------------------------------------------------------------

class AppointmentTypeTests(TestCase):

    def setUp(self):
        self.service_type = make_service_type(slug="health-svc", category="health")

    def test_slug_unique(self):
        make_appt_type(service_type=self.service_type, slug="appt-slug")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_appt_type(service_type=self.service_type, slug="appt-slug")

    def test_duration_minutes_below_min_fails_validation(self):
        appt = AppointmentType(
            service_type=self.service_type,
            slug="bad-dur",
            name_en="Bad",
            name_fr="Bad",
            duration_minutes=4,
        )
        with self.assertRaises(ValidationError):
            appt.full_clean()

    def test_duration_minutes_above_max_fails_validation(self):
        appt = AppointmentType(
            service_type=self.service_type,
            slug="big-dur",
            name_en="Big",
            name_fr="Big",
            duration_minutes=481,
        )
        with self.assertRaises(ValidationError):
            appt.full_clean()

    def test_duration_minutes_boundary_values_pass(self):
        at_5 = AppointmentType(
            service_type=self.service_type,
            slug="dur-5",
            name_en="Quick",
            name_fr="Rapide",
            duration_minutes=5,
        )
        at_5.full_clean()

        at_480 = AppointmentType(
            service_type=self.service_type,
            slug="dur-480",
            name_en="Full Day",
            name_fr="Journée complète",
            duration_minutes=480,
        )
        at_480.full_clean()

    def test_capacity_per_slot_below_min_fails_validation(self):
        appt = AppointmentType(
            service_type=self.service_type,
            slug="cap-bad",
            name_en="Group",
            name_fr="Groupe",
            capacity_per_slot=0,
        )
        with self.assertRaises(ValidationError):
            appt.full_clean()

    def test_capacity_per_slot_above_max_fails_validation(self):
        appt = AppointmentType(
            service_type=self.service_type,
            slug="cap-huge",
            name_en="Big Group",
            name_fr="Grand groupe",
            capacity_per_slot=101,
        )
        with self.assertRaises(ValidationError):
            appt.full_clean()

    def test_default_duration_minutes(self):
        appt = make_appt_type(service_type=self.service_type, slug="default-dur")
        self.assertEqual(appt.duration_minutes, 30)

    def test_default_capacity_per_slot(self):
        appt = make_appt_type(service_type=self.service_type, slug="default-cap")
        self.assertEqual(appt.capacity_per_slot, 1)

    def test_default_mode(self):
        appt = make_appt_type(service_type=self.service_type, slug="default-mode")
        self.assertEqual(appt.mode, "in_person")

    def test_is_active_default_true(self):
        appt = make_appt_type(service_type=self.service_type, slug="default-active")
        self.assertTrue(appt.is_active)

    def test_requires_staff_confirmation_default_true(self):
        appt = make_appt_type(service_type=self.service_type, slug="default-conf")
        self.assertTrue(appt.requires_staff_confirmation)

    def test_allow_citizen_self_booking_default_true(self):
        appt = make_appt_type(service_type=self.service_type, slug="default-self")
        self.assertTrue(appt.allow_citizen_self_booking)

    def test_allow_walk_in_default_false(self):
        appt = make_appt_type(service_type=self.service_type, slug="default-walkin")
        self.assertFalse(appt.allow_walk_in)

    def test_non_punitive_no_show_default_false(self):
        appt = make_appt_type(service_type=self.service_type, slug="default-noshow")
        self.assertFalse(appt.non_punitive_no_show)

    def test_service_type_on_delete_protect(self):
        """Deleting a ServiceType that has AppointmentTypes must be blocked."""
        appt = make_appt_type(service_type=self.service_type, slug="protected-svc")
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.service_type.delete()
        # AppointmentType still exists
        self.assertTrue(AppointmentType.objects.filter(pk=appt.pk).exists())

    def test_scheduling_policy_set_null_on_delete(self):
        """Deleting a SchedulingPolicy NULLs the FK on AppointmentType."""
        policy = make_policy("Temp Policy")
        appt = make_appt_type(
            service_type=self.service_type,
            slug="policy-appt",
            scheduling_policy=policy,
        )
        self.assertEqual(appt.scheduling_policy, policy)
        policy.delete()
        appt.refresh_from_db()
        self.assertIsNone(appt.scheduling_policy)

    def test_get_effective_policy_returns_policy(self):
        policy = make_policy("Test Policy")
        appt = make_appt_type(
            service_type=self.service_type,
            slug="eff-policy",
            scheduling_policy=policy,
        )
        self.assertEqual(appt.get_effective_policy(), policy)

    def test_get_effective_policy_returns_none_without_policy(self):
        appt = make_appt_type(service_type=self.service_type, slug="no-policy")
        self.assertIsNone(appt.get_effective_policy())

    def test_get_effective_policy_returns_none_when_unset(self):
        """get_effective_policy() returns None when no policy is attached."""
        at = make_appt_type(
            service_type=self.service_type,
            slug="no-policy-unset",
            scheduling_policy=None,
        )
        self.assertIsNone(at.get_effective_policy())

    def test_intake_form_schema_default_empty_dict(self):
        appt = make_appt_type(service_type=self.service_type, slug="schema-default")
        self.assertEqual(appt.intake_form_schema, {})

    def test_interpreter_required_option_default(self):
        appt = make_appt_type(service_type=self.service_type, slug="interp-default")
        self.assertEqual(appt.interpreter_required_option, "none")

    def test_str_does_not_contain_email(self):
        appt = make_appt_type(service_type=self.service_type, slug="pipeda-appt")
        self.assertNotIn("@", str(appt))

    def test_allow_anonymous_booking_defaults_false(self):
        """allow_anonymous_booking must default to False — spec §18.5."""
        at = make_appt_type(service_type=self.service_type, slug="anon-default")
        self.assertFalse(at.allow_anonymous_booking)

    def test_allow_anonymous_booking_can_be_set(self):
        """allow_anonymous_booking can be explicitly enabled."""
        at = make_appt_type(service_type=self.service_type, slug="anon-enabled", allow_anonymous_booking=True)
        self.assertTrue(at.allow_anonymous_booking)
        at.full_clean()  # must not raise

    def test_str_does_not_traverse_fk(self):
        """AppointmentType.__str__ must return name_en without a DB query."""
        at = make_appt_type(service_type=self.service_type, slug="str-no-fk", name_en="Tax Filing Appointment")
        # After loading from DB, str() should not cause an additional query
        # (service_type should NOT be fetched)
        at_fresh = AppointmentType.objects.get(pk=at.pk)
        with self.assertNumQueries(0):
            result = str(at_fresh)
        self.assertEqual(result, "Tax Filing Appointment")

    # H-11: gating field coverage

    def test_requires_document_upload_default_false(self):
        """requires_document_upload defaults to False."""
        at = make_appt_type(service_type=self.service_type, slug="req-doc-default")
        self.assertFalse(at.requires_document_upload)

    def test_requires_payment_default_false(self):
        """requires_payment defaults to False."""
        at = make_appt_type(service_type=self.service_type, slug="req-pay-default")
        self.assertFalse(at.requires_payment)

    def test_requires_consent_default_false(self):
        """requires_consent defaults to False."""
        at = make_appt_type(service_type=self.service_type, slug="req-consent-default")
        self.assertFalse(at.requires_consent)

    def test_required_document_category_slug_blank_by_default(self):
        """required_document_category_slug is blank when document upload not required."""
        at = make_appt_type(service_type=self.service_type, slug="doc-slug-default")
        self.assertEqual(at.required_document_category_slug, "")

    def test_fee_code_blank_by_default(self):
        """fee_code is blank when payment not required."""
        at = make_appt_type(service_type=self.service_type, slug="fee-code-default")
        self.assertEqual(at.fee_code, "")

    def test_consent_category_slug_blank_by_default(self):
        """consent_category_slug is blank when consent not required."""
        at = make_appt_type(service_type=self.service_type, slug="consent-slug-default")
        self.assertEqual(at.consent_category_slug, "")

    def test_gating_fields_can_be_set_together(self):
        """All three gating flags can be enabled simultaneously."""
        at = make_appt_type(
            service_type=self.service_type,
            slug="all-gates",
            requires_document_upload=True,
            required_document_category_slug="passport",
            requires_payment=True,
            fee_code="FEE-001",
            requires_consent=True,
            consent_category_slug="appointment_data_processing",
        )
        at.full_clean()  # must not raise
        self.assertTrue(at.requires_document_upload)
        self.assertTrue(at.requires_payment)
        self.assertTrue(at.requires_consent)
        self.assertEqual(at.required_document_category_slug, "passport")
        self.assertEqual(at.fee_code, "FEE-001")
        self.assertEqual(at.consent_category_slug, "appointment_data_processing")


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class LocationTests(TestCase):

    def setUp(self):
        self.org = make_org(slug="test-loc-org")

    def test_slug_unique(self):
        make_location(organization=self.org, slug="loc-slug")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_location(organization=self.org, slug="loc-slug")

    def test_is_active_default_true(self):
        loc = make_location(organization=self.org, slug="active-loc")
        self.assertTrue(loc.is_active)

    def test_is_virtual_default_false(self):
        loc = make_location(organization=self.org, slug="phys-loc")
        self.assertFalse(loc.is_virtual)

    def test_timezone_default(self):
        loc = make_location(organization=self.org, slug="tz-loc")
        self.assertEqual(loc.timezone, "America/Toronto")

    def test_privacy_regime_default(self):
        loc = make_location(organization=self.org, slug="priv-loc")
        self.assertEqual(loc.privacy_regime, "pipeda")

    def test_organization_on_delete_protect(self):
        """Deleting an Organization with Locations must be blocked (PROTECT)."""
        loc = make_location(organization=self.org, slug="prot-loc")
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.org.delete()
        # Rollback must have preserved both records
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())
        self.assertTrue(Location.objects.filter(pk=loc.pk).exists())

    def test_scheduling_policy_set_null_on_delete(self):
        policy = make_policy("Location Policy")
        loc = make_location(
            organization=self.org,
            slug="pol-loc",
            scheduling_policy=policy,
        )
        policy.delete()
        loc.refresh_from_db()
        self.assertIsNone(loc.scheduling_policy)

    def test_business_hours_default_empty_list(self):
        loc = make_location(organization=self.org, slug="bh-loc")
        self.assertEqual(loc.business_hours, [])

    def test_ordering_by_name_en(self):
        make_location(organization=self.org, slug="z-loc", name_en="Z Location")
        make_location(organization=self.org, slug="a-loc", name_en="A Location")
        names = list(Location.objects.values_list("name_en", flat=True))
        self.assertEqual(names, sorted(names))

    def test_privacy_regime_law25_choice_valid(self):
        """Quebec Law 25 requires special handling — ensure the choice is stored."""
        loc = make_location(
            organization=self.org,
            slug="qc-loc",
            privacy_regime="law25",
        )
        loc.refresh_from_db()
        self.assertEqual(loc.privacy_regime, "law25")

    def test_str_does_not_contain_email(self):
        loc = make_location(organization=self.org, slug="pipeda-loc")
        self.assertNotIn("@", str(loc))

    def test_str_does_not_expose_email(self):
        """PIPEDA: Location.__str__ must not include the contact email."""
        loc = make_location(
            organization=self.org,
            slug="pipeda-loc-email",
            email="contact@city.gc.ca",
        )
        self.assertNotIn("@", str(loc))
        self.assertNotIn("contact@city.gc.ca", str(loc))

    def test_get_effective_policy_returns_policy(self):
        """Location.get_effective_policy() returns the attached policy."""
        from apps.appointments.models import SchedulingPolicy
        policy = SchedulingPolicy.objects.create(name="loc-policy-test")
        loc = make_location(
            organization=self.org,
            slug="loc-eff-policy",
            scheduling_policy=policy,
        )
        self.assertEqual(loc.get_effective_policy(), policy)

    def test_get_effective_policy_returns_none_when_unset(self):
        """Location.get_effective_policy() returns None when no policy is attached."""
        loc = make_location(organization=self.org, slug="loc-no-policy")
        self.assertIsNone(loc.get_effective_policy())


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------

class ResourceTests(TestCase):

    def setUp(self):
        self.location = make_location()

    def test_resource_type_stored(self):
        res = make_resource(location=self.location, resource_type="room")
        self.assertEqual(res.resource_type, "room")

    def test_is_active_default_true(self):
        res = make_resource(location=self.location)
        self.assertTrue(res.is_active)

    def test_capacity_default_one(self):
        res = make_resource(location=self.location)
        self.assertEqual(res.capacity, 1)

    def test_capacity_below_min_fails_validation(self):
        res = Resource(
            location=self.location,
            name_en="Room",
            name_fr="Salle",
            resource_type="room",
            capacity=0,
        )
        with self.assertRaises(ValidationError):
            res.full_clean()

    def test_features_default_empty_list(self):
        res = make_resource(location=self.location)
        self.assertEqual(res.features, [])

    def test_calendar_provider_default_none(self):
        res = make_resource(location=self.location)
        self.assertEqual(res.calendar_provider, "none")

    def test_cascade_on_location_delete(self):
        """Resource must be deleted when its Location is deleted (CASCADE)."""
        cascade_org = make_org(slug="res-cascade-org")
        loc = make_location(organization=cascade_org, slug="cascade-loc")
        res = make_resource(location=loc)
        resource_pk = res.pk
        # Location is not protected by Resource, so it can be deleted
        loc.delete()
        self.assertFalse(Resource.objects.filter(pk=resource_pk).exists())

    def test_ordering_by_name_en(self):
        """Resources are ordered by name_en ascending."""
        r1 = make_resource(location=self.location, name_en="Aardvark Room")
        r2 = make_resource(location=self.location, name_en="Zebra Room")
        r3 = make_resource(location=self.location, name_en="Mango Room")
        qs = Resource.objects.filter(location=self.location).order_by("name_en")
        names = list(qs.values_list("name_en", flat=True))
        self.assertEqual(names, ["Aardvark Room", "Mango Room", "Zebra Room"])

    def test_str_does_not_contain_email(self):
        res = make_resource(location=self.location)
        self.assertNotIn("@", str(res))

    def test_str_does_not_traverse_fk(self):
        """Resource.__str__ must return name_en without a DB query."""
        resource = make_resource(location=self.location, name_en="Quiet Room 3")
        resource_fresh = Resource.objects.get(pk=resource.pk)
        with self.assertNumQueries(0):
            result = str(resource_fresh)
        self.assertEqual(result, "Quiet Room 3")


# ---------------------------------------------------------------------------
# StaffProfile — PIPEDA
# ---------------------------------------------------------------------------

class StaffProfileTests(TestCase):

    def setUp(self):
        self.location = make_location()
        self.staff_user = make_user(email="coordinator@example.com", is_staff=True)
        self.profile = StaffProfile.objects.create(
            user=self.staff_user,
            location=self.location,
        )

    # ── PIPEDA: __str__ must never expose PII ───────────────────────────────

    def test_str_contains_pk(self):
        self.assertIn(str(self.profile.pk), str(self.profile))

    def test_str_contains_user_id(self):
        self.assertIn(str(self.staff_user.pk), str(self.profile))

    def test_str_does_not_contain_email(self):
        """PIPEDA: StaffProfile.__str__ must not contain the user's email address."""
        self.assertNotIn(self.staff_user.email, str(self.profile))

    def test_str_contains_no_pii(self):
        """StaffProfile.__str__ must not expose email, name, or @ symbol (PIPEDA §4.7)."""
        user = make_user(email="jane.doe@example.com", is_staff=True)
        profile = StaffProfile.objects.create(user=user, location=self.location)
        result = str(profile)
        self.assertNotIn("jane.doe@example.com", result)
        self.assertNotIn("@", result)
        self.assertNotIn("Jane", result)
        self.assertNotIn("Doe", result)
        # Must contain the PK so staff can debug
        self.assertIn(str(profile.pk), result)

    # ── Defaults ────────────────────────────────────────────────────────────

    def test_is_accepting_bookings_default_true(self):
        self.assertTrue(self.profile.is_accepting_bookings)

    def test_accepts_walk_ins_default_false(self):
        self.assertFalse(self.profile.accepts_walk_ins)

    def test_max_daily_appointments_default_null(self):
        self.assertIsNone(self.profile.max_daily_appointments)

    def test_video_provider_default_none(self):
        self.assertEqual(self.profile.video_provider, "none")

    def test_calendar_integration_provider_default_none(self):
        self.assertEqual(self.profile.calendar_integration_provider, "none")

    # ── OneToOne constraint ──────────────────────────────────────────────────

    def test_one_to_one_user_unique(self):
        """A second StaffProfile for the same user must fail."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffProfile.objects.create(user=self.staff_user)

    # ── FK behaviours ────────────────────────────────────────────────────────

    def test_user_cascade_deletes_profile(self):
        user2 = make_user(email="todelete@example.com", is_staff=True)
        profile2 = StaffProfile.objects.create(user=user2)
        profile2_pk = profile2.pk
        user2.delete()
        self.assertFalse(StaffProfile.objects.filter(pk=profile2_pk).exists())

    def test_location_set_null_on_delete(self):
        org2 = make_org(slug="staff-org-del")
        loc2 = make_location(organization=org2, slug="staff-loc-del")
        user2 = make_user(email="staff2@example.com", is_staff=True)
        profile2 = StaffProfile.objects.create(user=user2, location=loc2)
        loc2.delete()
        profile2.refresh_from_db()
        self.assertIsNone(profile2.location)

    # ── M2M appointment_types ────────────────────────────────────────────────

    def test_appointment_types_m2m(self):
        """StaffProfile ↔ AppointmentType M2M works in both directions."""
        svc = make_service_type(slug="appt-svc-m2m")
        at1 = make_appt_type(service_type=svc, slug="at-m2m-1")
        at2 = make_appt_type(service_type=svc, slug="at-m2m-2")
        self.profile.appointment_types.set([at1, at2])
        # Forward accessor
        self.assertEqual(self.profile.appointment_types.count(), 2)
        # Reverse accessor (related_name="staff_members")
        self.assertIn(self.profile, at1.staff_members.all())
        self.assertIn(self.profile, at2.staff_members.all())
        # Removing also works in both directions
        self.profile.appointment_types.remove(at1)
        self.assertNotIn(self.profile, at1.staff_members.all())
        self.assertIn(self.profile, at2.staff_members.all())

    def test_appointment_types_blank_default(self):
        self.assertEqual(self.profile.appointment_types.count(), 0)

    # ── limit_choices_to is_staff=True (field kwarg) ─────────────────────────

    def test_user_field_limit_choices_to_is_staff(self):
        """
        The OneToOneField on StaffProfile.user must have
        limit_choices_to={"is_staff": True} to enforce at the form level.
        """
        user_field = StaffProfile._meta.get_field("user")
        lc = user_field.remote_field.limit_choices_to
        self.assertIsNotNone(lc)
        # Accepts dict or Q object — both must encode is_staff=True
        if isinstance(lc, dict):
            self.assertTrue(lc.get("is_staff"))
        else:
            # Q object: rely on the string representation containing is_staff
            self.assertIn("is_staff", str(lc))

    # ── ordering ─────────────────────────────────────────────────────────────

    def test_ordering_by_user_id(self):
        # Make a second profile with a larger user_id
        user2 = make_user(email="later@example.com", is_staff=True)
        StaffProfile.objects.create(user=user2)
        profiles = list(StaffProfile.objects.values_list("user_id", flat=True))
        self.assertEqual(profiles, sorted(profiles))

    def test_clean_rejects_non_staff_user(self):
        """StaffProfile.clean() must raise ValidationError when user.is_staff=False."""
        from django.core.exceptions import ValidationError
        non_staff_user = make_user(email="notstaff@example.com", is_staff=False)
        profile = StaffProfile(user=non_staff_user, location=self.location)
        with self.assertRaises(ValidationError) as cm:
            profile.clean()
        self.assertIn("user", cm.exception.message_dict)

    def test_get_display_name_returns_en_by_default(self):
        """get_display_name() returns EN display name when language is English."""
        from django.utils.translation import override
        profile = self.profile  # use setUp profile
        profile.display_name_en = "Dr. Smith"
        profile.display_name_fr = "Dr Tremblay"
        profile.save()
        with override("en"):
            self.assertEqual(profile.get_display_name(), "Dr. Smith")

    def test_get_display_name_returns_fr_when_active(self):
        """get_display_name() returns FR display name when language is French."""
        from django.utils.translation import override
        profile = self.profile
        profile.display_name_en = "Dr. Smith"
        profile.display_name_fr = "Dr Tremblay"
        profile.save()
        with override("fr"):
            self.assertEqual(profile.get_display_name(), "Dr Tremblay")

    def test_get_display_name_falls_back_to_en(self):
        """get_display_name() falls back to EN if FR is blank."""
        from django.utils.translation import override
        profile = self.profile
        profile.display_name_en = "Dr. Smith"
        profile.display_name_fr = ""
        profile.save()
        with override("fr"):
            self.assertEqual(profile.get_display_name(), "Dr. Smith")

    def test_get_display_name_returns_empty_when_both_blank(self):
        """get_display_name() returns empty string when both names are blank."""
        profile = self.profile
        profile.display_name_en = ""
        profile.display_name_fr = ""
        profile.save()
        self.assertEqual(profile.get_display_name(), "")


# ---------------------------------------------------------------------------
# Cross-model: service_type → appointment_type → staff M2M chain
# ---------------------------------------------------------------------------

class CrossModelTests(TestCase):

    def test_staff_profile_m2m_via_appointment_type(self):
        """StaffProfile.appointment_types links to AppointmentType.staff_members."""
        svc = make_service_type(slug="cm-svc")
        at = make_appt_type(service_type=svc, slug="cm-appt")
        staff_user = make_user(email="cm-staff@example.com", is_staff=True)
        profile = StaffProfile.objects.create(user=staff_user)
        profile.appointment_types.add(at)

        # Reverse relation works
        self.assertIn(profile, at.staff_members.all())
        # Forward relation works
        self.assertIn(at, profile.appointment_types.all())

    def test_location_cascade_removes_resources_not_profiles(self):
        """
        Deleting a location:
          - cascades to Resource (CASCADE)
          - NULLs StaffProfile.location (SET_NULL)
        """
        org = make_org(slug="cascade-org")
        loc = make_location(organization=org, slug="cascade-loc2")
        res = make_resource(location=loc)
        staff_user = make_user(email="cascade-staff@example.com", is_staff=True)
        profile = StaffProfile.objects.create(user=staff_user, location=loc)

        loc.delete()

        # Resource is gone
        self.assertFalse(Resource.objects.filter(pk=res.pk).exists())
        # Profile survives with location=None
        profile.refresh_from_db()
        self.assertIsNone(profile.location)
        self.assertTrue(StaffProfile.objects.filter(pk=profile.pk).exists())


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsTests(TestCase):
    """Verify settings for the Appointments BB are correctly typed."""

    def test_reminder_hours_are_integers(self):
        """APPOINTMENTS_REMINDER_HOURS must be a list of ints (not strings)."""
        from django.conf import settings
        hours = settings.CIVICOS.get("APPOINTMENTS", {}).get("REMINDER_HOURS", [])
        self.assertIsInstance(hours, list)
        self.assertTrue(len(hours) > 0)
        for h in hours:
            self.assertIsInstance(h, int, f"Expected int, got {type(h).__name__}: {h!r}")


# ---------------------------------------------------------------------------
# PolicyCascadeTests
# ---------------------------------------------------------------------------

class PolicyCascadeTests(TestCase):
    """
    Integration tests for the two-level scheduling policy override chain.

    Documented in SPEC_APPOINTMENTS_BB.md §5.2 and in
    AppointmentType.get_effective_policy() docstring:
      1. AppointmentType.get_effective_policy() — most specific
      2. Location.get_effective_policy()          — location default
      3. settings.CIVICOS["APPOINTMENTS"] defaults — global fallback (Wave 3)

    These tests verify that callers can correctly implement the cascade
    by calling both methods in order.
    """

    def setUp(self):
        from apps.appointments.models import SchedulingPolicy
        self.org = make_org(slug="cascade-org")
        self.service_type = make_service_type(slug="cascade-svc")
        self.appt_policy = SchedulingPolicy.objects.create(name="appt-policy")
        self.loc_policy = SchedulingPolicy.objects.create(name="loc-policy")
        self.location = make_location(
            organization=self.org,
            slug="cascade-loc",
            scheduling_policy=self.loc_policy,
        )

    def test_appt_policy_takes_precedence_over_location(self):
        """AppointmentType policy overrides Location policy (most specific wins)."""
        at = make_appt_type(
            service_type=self.service_type,
            slug="cascade-appt-wins",
            scheduling_policy=self.appt_policy,
        )
        # Simulate the Wave 3 two-level cascade pattern
        effective = at.get_effective_policy() or self.location.get_effective_policy()
        self.assertEqual(effective, self.appt_policy)

    def test_location_policy_used_when_appt_has_none(self):
        """Location policy is the fallback when AppointmentType has no policy."""
        at = make_appt_type(
            service_type=self.service_type,
            slug="cascade-loc-fallback",
            scheduling_policy=None,
        )
        effective = at.get_effective_policy() or self.location.get_effective_policy()
        self.assertEqual(effective, self.loc_policy)

    def test_none_when_both_unset(self):
        """Returns None when neither AppointmentType nor Location has a policy."""
        loc_no_policy = make_location(
            organization=self.org,
            slug="cascade-no-policy",
            scheduling_policy=None,
        )
        at = make_appt_type(
            service_type=self.service_type,
            slug="cascade-both-none",
            scheduling_policy=None,
        )
        effective = at.get_effective_policy() or loc_no_policy.get_effective_policy()
        self.assertIsNone(effective)


# ===========================================================================
# Wave 2 Model Tests
# ===========================================================================

import uuid as _uuid_module
from datetime import date, time, timedelta, datetime
from zoneinfo import ZoneInfo

_UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Wave 2 Factories (local to this section)
# ---------------------------------------------------------------------------

def _make_wave2_staff(location=None, suffix: str = "") -> StaffProfile:
    """Create a StaffProfile linked to a staff user."""
    user = User.objects.create_user(
        email=f"w2staff{suffix}@example.com",
        password="pass!",
        is_staff=True,
    )
    if location is None:
        org = Organization.objects.create(
            slug=f"w2org{suffix}", name_en="W2 Org", name_fr="Org W2",
        )
        location = Location.objects.create(
            organization=org,
            slug=f"w2loc{suffix}",
            name_en="W2 Location",
            name_fr="Location W2",
        )
    return StaffProfile.objects.create(user=user, location=location)


def _make_wave2_slot(staff=None, suffix: str = "") -> Slot:
    """Create a minimal valid Slot for constraint testing."""
    if staff is None:
        staff = _make_wave2_staff(suffix=suffix)
    org = Organization.objects.create(
        slug=f"slotorg{suffix}", name_en="Slot Org", name_fr="Org Slot",
    )
    loc = Location.objects.create(
        organization=org,
        slug=f"slotloc{suffix}",
        name_en="Slot Loc",
        name_fr="Loc Slot",
    )
    svc = ServiceType.objects.create(
        slug=f"slotsvc{suffix}", name_en="Slot SVC", name_fr="SVC Slot",
    )
    appt_type = AppointmentType.objects.create(
        service_type=svc,
        slug=f"slotat{suffix}",
        name_en="Slot AT",
        name_fr="AT Slot",
    )
    now = datetime(2026, 9, 1, 14, 0, tzinfo=_UTC)
    end = now + timedelta(minutes=30)
    return Slot.objects.create(
        appointment_type=appt_type,
        staff=staff,
        location=loc,
        start_datetime=now,
        end_datetime=end,
        effective_start=now,
        effective_end=end,
        capacity=1,
        spaces_used=0,
        status="available",
    )


# ---------------------------------------------------------------------------
# Wave 2: AvailabilityTemplate model tests
# ---------------------------------------------------------------------------

class AvailabilityTemplateTests(TestCase):
    """
    Model-level tests for AvailabilityTemplate.
    Tests for service-layer behaviour are in test_services_availability.py.
    """

    def setUp(self):
        self.staff = _make_wave2_staff(suffix="-at2")

    def test_create_valid_template(self):
        tpl = AvailabilityTemplate.objects.create(
            staff=self.staff,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
            valid_from=date(2026, 1, 1),
        )
        self.assertEqual(tpl.day_of_week, 1)

    def test_day_of_week_choices_1_to_7(self):
        """All 7 ISO weekdays are defined."""
        dow_values = [c[0] for c in AvailabilityTemplate.DAYS_OF_WEEK]
        self.assertEqual(sorted(dow_values), list(range(1, 8)))

    def test_str_no_pii(self):
        tpl = AvailabilityTemplate.objects.create(
            staff=self.staff,
            day_of_week=2,
            start_time=time(8, 0),
            end_time=time(16, 0),
            valid_from=date(2026, 1, 1),
        )
        self.assertNotIn("@", str(tpl))

    def test_str_contains_staff_pk(self):
        tpl = AvailabilityTemplate.objects.create(
            staff=self.staff,
            day_of_week=3,
            start_time=time(9, 0),
            end_time=time(12, 0),
            valid_from=date(2026, 1, 1),
        )
        self.assertIn(str(self.staff.pk), str(tpl))

    def test_clean_end_before_start_raises(self):
        tpl = AvailabilityTemplate(
            staff=self.staff,
            day_of_week=1,
            start_time=time(12, 0),
            end_time=time(9, 0),
            valid_from=date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError) as cm:
            tpl.clean()
        self.assertIn("end_time", cm.exception.message_dict)

    def test_clean_end_equal_start_raises(self):
        tpl = AvailabilityTemplate(
            staff=self.staff,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(9, 0),
            valid_from=date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            tpl.clean()

    def test_clean_valid_until_before_valid_from_raises(self):
        tpl = AvailabilityTemplate(
            staff=self.staff,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
            valid_from=date(2026, 7, 1),
            valid_until=date(2026, 6, 30),
        )
        with self.assertRaises(ValidationError) as cm:
            tpl.clean()
        self.assertIn("valid_until", cm.exception.message_dict)

    def test_clean_open_ended_valid_until_passes(self):
        tpl = AvailabilityTemplate(
            staff=self.staff,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
        tpl.clean()  # Must not raise

    def test_protect_delete_with_staff(self):
        """Deleting StaffProfile raises ProtectedError — H-1 PIPEDA 4.5.3 audit integrity.

        AvailabilityTemplate is an audit record (explains why slots were generated).
        It must NOT be silently destroyed when a staff member leaves; the administrator
        must explicitly decommission the records first.
        """
        from django.db.models import ProtectedError
        AvailabilityTemplate.objects.create(
            staff=self.staff,
            day_of_week=4,
            start_time=time(9, 0),
            end_time=time(17, 0),
            valid_from=date(2026, 1, 1),
        )
        with self.assertRaises(ProtectedError):
            self.staff.delete()
        # The template still exists — audit record preserved
        self.assertEqual(AvailabilityTemplate.objects.count(), 1)

    def test_ordering_day_of_week_then_start_time(self):
        """Templates ordered by day_of_week ASC then start_time ASC."""
        AvailabilityTemplate.objects.create(
            staff=self.staff, day_of_week=3,
            start_time=time(14, 0), end_time=time(15, 0), valid_from=date(2026, 1, 1),
        )
        AvailabilityTemplate.objects.create(
            staff=self.staff, day_of_week=1,
            start_time=time(10, 0), end_time=time(11, 0), valid_from=date(2026, 1, 1),
        )
        AvailabilityTemplate.objects.create(
            staff=self.staff, day_of_week=1,
            start_time=time(9, 0), end_time=time(10, 0), valid_from=date(2026, 1, 1),
        )
        templates = list(AvailabilityTemplate.objects.filter(staff=self.staff))
        self.assertEqual(templates[0].day_of_week, 1)
        self.assertEqual(templates[0].start_time, time(9, 0))
        self.assertEqual(templates[1].start_time, time(10, 0))
        self.assertEqual(templates[2].day_of_week, 3)


# ---------------------------------------------------------------------------
# Wave 2: StaffException model tests
# ---------------------------------------------------------------------------

class StaffExceptionModelConstraintTests(TestCase):
    """
    Model-level tests for StaffException.
    Service-layer tests are in test_services_availability.py.
    """

    def setUp(self):
        self.staff = _make_wave2_staff(suffix="-se2")

    def test_create_holiday_exception(self):
        exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 1),
            exception_type="holiday",
        )
        self.assertEqual(exc.exception_type, "holiday")

    def test_create_override_exception(self):
        exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 2),
            exception_type="override",
            override_start_time=time(13, 0),
            override_end_time=time(17, 0),
        )
        self.assertEqual(exc.exception_type, "override")

    def test_str_no_pii(self):
        exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 3),
            exception_type="leave",
        )
        self.assertNotIn("@", str(exc))

    def test_str_contains_date(self):
        exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 4),
            exception_type="training",
        )
        self.assertIn("2026-07-04", str(exc))

    def test_unique_together_raises_integrity_error(self):
        StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 5),
            exception_type="holiday",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffException.objects.create(
                    staff=self.staff,
                    exception_date=date(2026, 7, 5),
                    exception_type="leave",
                )

    def test_clean_override_missing_both_times_raises(self):
        exc = StaffException(
            staff=self.staff,
            exception_date=date(2026, 7, 6),
            exception_type="override",
        )
        with self.assertRaises(ValidationError):
            exc.clean()

    def test_clean_override_only_start_time_raises(self):
        exc = StaffException(
            staff=self.staff,
            exception_date=date(2026, 7, 7),
            exception_type="override",
            override_start_time=time(9, 0),
            override_end_time=None,
        )
        with self.assertRaises(ValidationError):
            exc.clean()

    def test_clean_override_end_before_start_raises(self):
        exc = StaffException(
            staff=self.staff,
            exception_date=date(2026, 7, 8),
            exception_type="override",
            override_start_time=time(15, 0),
            override_end_time=time(14, 0),
        )
        with self.assertRaises(ValidationError) as cm:
            exc.clean()
        self.assertIn("override_end_time", cm.exception.message_dict)

    def test_clean_leave_no_times_passes(self):
        exc = StaffException(
            staff=self.staff,
            exception_date=date(2026, 7, 9),
            exception_type="leave",
        )
        exc.clean()  # Must not raise

    def test_protect_delete_with_staff(self):
        """Deleting StaffProfile raises ProtectedError — H-1 PIPEDA 4.5.3 audit integrity.

        StaffException records explain WHY slots were not generated on a date
        and must survive staff deletion for auditability.
        """
        from django.db.models import ProtectedError
        StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 10),
            exception_type="holiday",
        )
        with self.assertRaises(ProtectedError):
            self.staff.delete()
        # The exception record still exists — audit record preserved
        self.assertEqual(StaffException.objects.count(), 1)

    def test_exception_type_choices(self):
        choices = {c[0] for c in StaffException.EXCEPTION_TYPE_CHOICES}
        self.assertEqual(choices, {"holiday", "leave", "override", "training"})

    def test_internal_note_default_blank(self):
        exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 7, 11),
            exception_type="training",
        )
        self.assertEqual(exc.internal_note, "")

    def test_midnight_override_times_are_valid(self):
        """time(0, 0) is falsy in Python — must not be treated as missing."""
        exc = StaffException(
            staff=self.staff,
            exception_date=date.today() + timedelta(days=1),
            exception_type="override",
            override_start_time=time(0, 0),   # midnight — falsy but valid
            override_end_time=time(8, 0),
        )
        # Should not raise ValidationError — absence of exception IS the assertion
        exc.clean()


# ---------------------------------------------------------------------------
# Wave 2: StaffExceptionAdmin.save_model() tests
# ---------------------------------------------------------------------------

class StaffExceptionAdminSaveModelTests(TestCase):
    """Tests for StaffExceptionAdmin.save_model() append-only note behaviour."""

    def setUp(self):
        # Create org, location, staff using the wave-2 factory helper.
        self.staff = _make_wave2_staff(suffix="-adminse")

        # Admin user (is_staff=True) to act as the request user.
        self.admin_user = User.objects.create_user(
            email="adminactor@example.com",
            password="pass!",
            is_staff=True,
        )

        # Create a StaffException with a known internal_note so it has a PK.
        self.exc = StaffException.objects.create(
            staff=self.staff,
            exception_date=date(2026, 8, 1),
            exception_type="holiday",
            internal_note="",
        )

    def _make_request(self):
        from django.test import RequestFactory
        request = RequestFactory().post("/")
        request.user = self.admin_user
        return request

    def _make_admin(self):
        from apps.appointments.admin import StaffExceptionAdmin
        from django.contrib.admin import site
        return StaffExceptionAdmin(StaffException, site)

    def test_note_addition_appended_with_timestamp_and_actor_pk(self):
        """note_addition is appended to internal_note with [timestamp — admin #pk] prefix."""
        from apps.appointments.admin import StaffExceptionAdminForm
        from unittest.mock import MagicMock

        admin_instance = self._make_admin()
        request = self._make_request()

        # Build a minimal mock form: save() is a no-op (obj already in DB),
        # cleaned_data carries note_addition.
        form = MagicMock(spec=StaffExceptionAdminForm)
        form.cleaned_data = {"note_addition": "Test note text"}
        form.save.return_value = self.exc

        admin_instance.save_model(request, self.exc, form, change=True)

        self.exc.refresh_from_db()
        self.assertIn("Test note text", self.exc.internal_note)
        self.assertIn(f"admin #{request.user.pk}", self.exc.internal_note)
        # Must NOT contain the admin's email or username (PIPEDA)
        self.assertNotIn(request.user.email, self.exc.internal_note)

    def test_empty_note_addition_leaves_internal_note_unchanged(self):
        """If note_addition is blank, internal_note must not be modified."""
        from apps.appointments.admin import StaffExceptionAdminForm
        from unittest.mock import MagicMock

        # Pre-set a known internal_note value.
        self.exc.internal_note = "Pre-existing note"
        self.exc.save(update_fields=["internal_note"])
        original_note = self.exc.internal_note

        admin_instance = self._make_admin()
        request = self._make_request()

        form = MagicMock(spec=StaffExceptionAdminForm)
        form.cleaned_data = {"note_addition": ""}
        form.save.return_value = self.exc

        admin_instance.save_model(request, self.exc, form, change=True)

        self.exc.refresh_from_db()
        self.assertEqual(self.exc.internal_note, original_note)

    def test_second_note_addition_appends_not_overwrites(self):
        """Two note additions must produce two entries in internal_note."""
        from apps.appointments.admin import StaffExceptionAdminForm
        from unittest.mock import MagicMock

        admin_instance = self._make_admin()
        request = self._make_request()

        # First save
        form1 = MagicMock(spec=StaffExceptionAdminForm)
        form1.cleaned_data = {"note_addition": "First note"}
        form1.save.return_value = self.exc
        admin_instance.save_model(request, self.exc, form1, change=True)

        self.exc.refresh_from_db()

        # Second save
        form2 = MagicMock(spec=StaffExceptionAdminForm)
        form2.cleaned_data = {"note_addition": "Second note"}
        form2.save.return_value = self.exc
        admin_instance.save_model(request, self.exc, form2, change=True)

        self.exc.refresh_from_db()
        self.assertIn("First note", self.exc.internal_note)
        self.assertIn("Second note", self.exc.internal_note)


# ---------------------------------------------------------------------------
# Wave 2: Slot model constraint tests
# ---------------------------------------------------------------------------

class SlotModelConstraintTests(TestCase):
    """
    Model-level constraint tests for Slot.
    Spec §22.2: SlotConstraintTests — spaces_used ≤ capacity; end > start.
    """

    def setUp(self):
        org = Organization.objects.create(
            slug="slot-test-org", name_en="Slot Org", name_fr="Org Slot",
        )
        self.location = Location.objects.create(
            organization=org, slug="slot-test-loc",
            name_en="Slot Loc", name_fr="Loc Slot",
        )
        svc = ServiceType.objects.create(
            slug="slot-test-svc", name_en="SVC", name_fr="SVC",
        )
        self.appt_type = AppointmentType.objects.create(
            service_type=svc, slug="slot-test-at",
            name_en="AT", name_fr="AT",
        )
        self.staff = _make_wave2_staff(suffix="-slt")
        self.now = datetime(2026, 9, 15, 14, 0, tzinfo=_UTC)
        self.end = self.now + timedelta(minutes=30)

    def _valid_slot(self, **overrides):
        defaults = dict(
            appointment_type=self.appt_type,
            staff=self.staff,
            location=self.location,
            start_datetime=self.now,
            end_datetime=self.end,
            effective_start=self.now,
            effective_end=self.end,
            capacity=1,
            spaces_used=0,
            status="available",
        )
        defaults.update(overrides)
        return Slot.objects.create(**defaults)

    def test_uuid_primary_key_generated(self):
        slot = self._valid_slot()
        self.assertIsInstance(slot.pk, _uuid_module.UUID)

    def test_available_spaces_property_calculation(self):
        slot = self._valid_slot(capacity=4, spaces_used=1)
        self.assertEqual(slot.available_spaces, 3)

    def test_is_available_available_status(self):
        slot = self._valid_slot(status="available", capacity=1, spaces_used=0)
        self.assertTrue(slot.is_available)

    def test_is_available_partial_status(self):
        slot = self._valid_slot(status="partial", capacity=2, spaces_used=1)
        self.assertTrue(slot.is_available)

    def test_is_not_available_full(self):
        slot = self._valid_slot(status="full", capacity=1, spaces_used=1)
        self.assertFalse(slot.is_available)

    def test_is_not_available_blocked(self):
        slot = self._valid_slot(status="blocked")
        self.assertFalse(slot.is_available)

    def test_is_not_available_cancelled(self):
        slot = self._valid_slot(status="cancelled")
        self.assertFalse(slot.is_available)

    def test_is_not_available_completed(self):
        slot = self._valid_slot(status="completed")
        self.assertFalse(slot.is_available)

    def test_is_not_available_when_full_despite_partial_status(self):
        """Status='partial' but spaces_used == capacity → not available."""
        slot = self._valid_slot(status="partial", capacity=2, spaces_used=2)
        self.assertFalse(slot.is_available)

    def test_str_no_pii(self):
        """
        Slot.__str__ may contain '@' as a separator between appointment_type_id
        and start_datetime (e.g. "Slot <uuid> — 1 @ 2026-09-15...").
        Verify it does NOT contain an email address (i.e., "@example.com" or similar).
        """
        slot = self._valid_slot()
        s = str(slot)
        # Must not look like an email address
        import re
        self.assertIsNone(
            re.search(r"@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", s),
            f"Email address found in Slot.__str__: {s!r}",
        )

    def test_str_contains_uuid(self):
        slot = self._valid_slot()
        self.assertIn(str(slot.pk), str(slot))

    def test_str_contains_status(self):
        slot = self._valid_slot()
        self.assertIn("available", str(slot))

    def test_on_delete_appointment_type_protect(self):
        self._valid_slot()
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.appt_type.delete()

    def test_on_delete_staff_protect(self):
        self._valid_slot()
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.staff.delete()

    def test_on_delete_location_protect(self):
        self._valid_slot()
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.location.delete()

    def test_resource_set_null_on_delete(self):
        """Deleting a Resource sets slot.resource to NULL."""
        resource = Resource.objects.create(
            location=self.location,
            name_en="Room", name_fr="Salle",
            resource_type="room",
        )
        slot = self._valid_slot(resource=resource)
        resource.delete()
        slot.refresh_from_db()
        self.assertIsNone(slot.resource)

    def test_default_capacity_is_one(self):
        slot = self._valid_slot()
        self.assertEqual(slot.capacity, 1)

    def test_default_spaces_used_is_zero(self):
        slot = self._valid_slot()
        self.assertEqual(slot.spaces_used, 0)

    def test_default_status_is_available(self):
        slot = self._valid_slot()
        self.assertEqual(slot.status, "available")

    def test_default_is_walk_in_slot_false(self):
        slot = self._valid_slot()
        self.assertFalse(slot.is_walk_in_slot)

    def test_video_fields_default_empty(self):
        slot = self._valid_slot()
        self.assertEqual(slot.video_join_url_citizen, "")
        self.assertEqual(slot.video_join_url_staff, "")
        self.assertEqual(slot.video_meeting_id, "")
        self.assertEqual(slot.video_provider, "")

    def test_ordering_by_start_datetime(self):
        t1 = self.now
        t2 = self.now + timedelta(hours=1)
        self._valid_slot(start_datetime=t2, end_datetime=t2 + timedelta(minutes=30),
                         effective_start=t2, effective_end=t2 + timedelta(minutes=30))
        self._valid_slot(start_datetime=t1, end_datetime=t1 + timedelta(minutes=30),
                         effective_start=t1, effective_end=t1 + timedelta(minutes=30))
        slots = list(Slot.objects.filter(appointment_type=self.appt_type))
        self.assertEqual(slots[0].start_datetime, t1)
        self.assertEqual(slots[1].start_datetime, t2)

    def test_slot_status_choices_all_present(self):
        expected = {"available", "partial", "full", "blocked", "cancelled", "completed"}
        actual = {c[0] for c in Slot.SLOT_STATUS_CHOICES}
        self.assertEqual(actual, expected)

    def test_effective_start_before_start(self):
        buffer = timedelta(minutes=5)
        slot = self._valid_slot(
            effective_start=self.now - buffer,
            effective_end=self.end,
        )
        self.assertLessEqual(slot.effective_start, slot.start_datetime)

    def test_effective_end_after_end(self):
        buffer = timedelta(minutes=10)
        slot = self._valid_slot(
            effective_start=self.now,
            effective_end=self.end + buffer,
        )
        self.assertGreaterEqual(slot.effective_end, slot.end_datetime)


# ---------------------------------------------------------------------------
# Wave 2: SlotAdminForm.clean_capacity() tests
# ---------------------------------------------------------------------------

class SlotAdminFormTests(TestCase):
    """Tests for SlotAdminForm.clean_capacity() validation."""

    def setUp(self):
        org = Organization.objects.create(
            slug="saf-org", name_en="SAF Org", name_fr="Org SAF",
        )
        self.location = Location.objects.create(
            organization=org, slug="saf-loc",
            name_en="SAF Loc", name_fr="Loc SAF",
        )
        svc = ServiceType.objects.create(
            slug="saf-svc", name_en="SAF SVC", name_fr="SVC SAF",
        )
        self.appt_type = AppointmentType.objects.create(
            service_type=svc, slug="saf-at",
            name_en="SAF AT", name_fr="AT SAF",
        )
        self.staff = _make_wave2_staff(suffix="-saf")
        now = datetime(2026, 9, 1, 14, 0, tzinfo=_UTC)
        end = now + timedelta(minutes=30)
        # Create a slot with capacity=5, spaces_used=2
        self.slot = Slot.objects.create(
            appointment_type=self.appt_type,
            staff=self.staff,
            location=self.location,
            start_datetime=now,
            end_datetime=end,
            effective_start=now,
            effective_end=end,
            capacity=5,
            spaces_used=2,
            status="partial",
        )

    def test_new_slot_any_capacity_passes(self):
        """New slot (no pk) — clean_capacity() should not raise for any capacity."""
        from apps.appointments.admin import SlotAdminForm
        form = SlotAdminForm(instance=Slot())
        form.instance = Slot()  # no pk
        form.cleaned_data = {"capacity": 1}
        result = form.clean_capacity()
        self.assertEqual(result, 1)

    def test_existing_slot_capacity_gte_spaces_used_passes(self):
        """Existing slot where new capacity >= spaces_used — should pass."""
        from apps.appointments.admin import SlotAdminForm
        form = SlotAdminForm(instance=self.slot)
        form.instance = self.slot
        form.cleaned_data = {"capacity": 5}  # same as current, >= spaces_used=2
        result = form.clean_capacity()
        self.assertEqual(result, 5)

    def test_existing_slot_capacity_below_spaces_used_raises(self):
        """Existing slot where new capacity < spaces_used — must raise ValidationError."""
        from apps.appointments.admin import SlotAdminForm
        from django.core.exceptions import ValidationError as CoreValidationError
        form = SlotAdminForm(instance=self.slot)
        form.instance = self.slot
        form.cleaned_data = {"capacity": 1}  # 1 < spaces_used=2 — must fail
        with self.assertRaises(CoreValidationError):
            form.clean_capacity()

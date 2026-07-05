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
    Location,
    Organization,
    Resource,
    SchedulingPolicy,
    ServiceType,
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
        make_resource(location=self.location, name_en="Z Room")
        make_resource(location=self.location, name_en="A Room")
        names = list(Resource.objects.filter(location=self.location).values_list("name_en", flat=True))
        self.assertEqual(names, sorted(names))

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

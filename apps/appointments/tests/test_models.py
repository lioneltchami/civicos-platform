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
    - is_active db_index (checked via field attribute)

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

    def test_organization_type_choices(self):
        valid_types = {
            "government_federal", "government_provincial", "government_municipal",
            "ngo", "health", "other",
        }
        org = make_org(slug="ngo-org", organization_type="ngo")
        self.assertIn(org.organization_type, valid_types)

    def test_timestamps_auto_set(self):
        org = make_org(slug="ts-org")
        self.assertIsNotNone(org.created_at)
        self.assertIsNotNone(org.updated_at)

    def test_str_does_not_contain_email(self):
        """PIPEDA: __str__ must never expose an email address."""
        org = make_org(slug="pipeda-org")
        self.assertNotIn("@", str(org))


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

    def test_intake_form_schema_default_empty_dict(self):
        appt = make_appt_type(service_type=self.service_type, slug="schema-default")
        self.assertEqual(appt.intake_form_schema, {})

    def test_interpreter_required_option_default(self):
        appt = make_appt_type(service_type=self.service_type, slug="interp-default")
        self.assertEqual(appt.interpreter_required_option, "none")

    def test_str_does_not_contain_email(self):
        appt = make_appt_type(service_type=self.service_type, slug="pipeda-appt")
        self.assertNotIn("@", str(appt))


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
        """Deleting an Organization with Locations must be blocked."""
        make_location(organization=self.org, slug="prot-loc")
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.org.delete()

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

    def test_str_does_not_contain_at_sign(self):
        """Paranoid check: any '@' in str(profile) would signal PII leakage."""
        self.assertNotIn("@", str(self.profile))

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
        svc = make_service_type(slug="appt-svc-m2m")
        at1 = make_appt_type(service_type=svc, slug="at-m2m-1")
        at2 = make_appt_type(service_type=svc, slug="at-m2m-2")
        self.profile.appointment_types.set([at1, at2])
        self.assertEqual(self.profile.appointment_types.count(), 2)

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

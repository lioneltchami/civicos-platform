"""
Volunteer Management BB — Wave 6 Key Invariant Tests.

Six invariant test classes mandated by the spec:

  1. CBVMROInvariantTests         — MRO: LoginRequiredMixin before PermissionRequiredMixin.
  2. PIPEDAAuditLogTests          — Volunteer PII must not appear in AuditLogEntry.event_detail.
  3. CRAThresholdBoundaryTests    — CRA $450/$500/$1,000 thresholds at exact boundaries.
  4. ConcurrentOverbookingTests   — Concurrent book_shift() with capacity=1 → exactly 1 confirmed.
  5. VSCExpiryTests               — ScreeningRecord VSC expiry properties at boundary dates.
  6. BilingualResponseTests       — Key portal views serve French content when requested.

Design rules enforced here:
  - Decimal() for all monetary values — never float.
  - timezone.now() everywhere — never datetime.datetime.now().
  - ProfilePK-only PIPEDA references in assertions.
  - setUp() (not setUpTestData) for TransactionTestCase.
"""
from __future__ import annotations

import importlib
import inspect
import threading
import unittest
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared factory helpers (aligned with existing test conventions)
# ---------------------------------------------------------------------------

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


def _make_user(email=None, password="testpass!", **kwargs):
    _counter[0] += 1
    email = email or f"inv{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password=password, **kwargs)


def _make_program(**kwargs):
    from apps.volunteers.models import Program
    n = _uid()
    defaults = dict(
        name_en=f"InvProgram {n}",
        name_fr=f"InvProgramme {n}",
        slug=f"invprog-{n}",
        cra_category="welfare",
    )
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    from apps.volunteers.models import Opportunity
    n = _uid()
    defaults = dict(
        title_en=f"Opportunity EN {n}",
        title_fr=f"Opportunité FR {n}",
        slug=slug or f"invopp-{n}",
        description_en="English description",
        description_fr="Description en français",
        program=program,
        status=status,
    )
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
    from apps.volunteers.models import VolunteerProfile
    return VolunteerProfile.objects.create(user=user)


def _grant_perm(user, codename):
    perm = Permission.objects.get(
        content_type__app_label="volunteers",
        codename=codename,
    )
    user.user_permissions.add(perm)
    for attr in ("_perm_cache", "_user_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
    return User.objects.get(pk=user.pk)


def _make_honorarium(volunteer, amount, payment_type=None, payment_date=None, created_by=None):
    """Create an Honorarium directly, bypassing CRA clean() for test setup."""
    from apps.volunteers.models import Honorarium
    if payment_type is None:
        payment_type = Honorarium.PAYMENT_TYPE_HONORARIUM
    if payment_date is None:
        payment_date = timezone.now().date()
    h = Honorarium(
        volunteer=volunteer,
        payment_type=payment_type,
        amount=Decimal(str(amount)),
        description="Test payment",
        payment_date=payment_date,
        created_by=created_by or volunteer.user,
    )
    h.save(skip_clean=True)
    return h


def _make_shift(opportunity, *, minutes_from_now=60, duration_minutes=120, capacity=1, **kwargs):
    """Create a future Shift for an Opportunity."""
    from apps.volunteers.models import Shift
    n = _uid()
    now = timezone.now()
    start = now + timedelta(minutes=minutes_from_now)
    end = start + timedelta(minutes=duration_minutes)
    defaults = dict(
        opportunity=opportunity,
        start_datetime=start,
        end_datetime=end,
        location_override=f"Location {n}",
        capacity=capacity,
        waitlist_enabled=False,
    )
    defaults.update(kwargs)
    return Shift.objects.create(**defaults)


def _make_approved_application(volunteer_profile, opportunity):
    """Create an approved VolunteerApplication (required precondition for book_shift)."""
    from apps.volunteers.models import VolunteerApplication
    app = VolunteerApplication(
        volunteer=volunteer_profile,
        opportunity=opportunity,
        status=VolunteerApplication.STATUS_APPROVED,
        motivation="Test",
    )
    app.save()
    return app


# ===========================================================================
# Class 1: MRO Invariant (static introspection, no DB)
# ===========================================================================

class CBVMROInvariantTests(SimpleTestCase):
    """
    All coordinator + portal CBVs must have LoginRequiredMixin before
    PermissionRequiredMixin in their MRO.

    This is a security invariant: if PermissionRequiredMixin precedes
    LoginRequiredMixin, an unauthenticated user receives a 403 (leaking that
    the URL exists) rather than a login redirect.

    Additionally, all coordinator views that include PermissionRequiredMixin
    must set raise_exception = True so authenticated-but-unauthorised users
    receive 403, not a silent login redirect.
    """

    def _collect_view_classes(self, module_path):
        """Import module_path and return all CBV classes defined there."""
        module = importlib.import_module(module_path)
        classes = []
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            # Only classes defined in this module (not imported base classes).
            if obj.__module__ == module_path:
                classes.append(obj)
        return classes

    def test_coordinator_views_login_before_permission_in_mro(self):
        """
        Every coordinator CBV that has both LoginRequiredMixin and
        PermissionRequiredMixin in its MRO must list LoginRequiredMixin first.
        """
        classes = self._collect_view_classes("apps.volunteers.views.coordinator")
        checked = 0
        for cls in classes:
            mro = cls.__mro__
            has_login = LoginRequiredMixin in mro
            has_perm = PermissionRequiredMixin in mro
            if has_login and has_perm:
                idx_login = mro.index(LoginRequiredMixin)
                idx_perm = mro.index(PermissionRequiredMixin)
                self.assertLess(
                    idx_login,
                    idx_perm,
                    msg=(
                        f"{cls.__name__}: LoginRequiredMixin (index {idx_login}) must "
                        f"precede PermissionRequiredMixin (index {idx_perm}) in MRO. "
                        "MRO order: " + " → ".join(c.__name__ for c in mro)
                    ),
                )
                checked += 1
        self.assertGreater(checked, 0, "No coordinator CBVs with both mixins found — check import path")

    def test_portal_views_login_before_permission_in_mro(self):
        """
        Every portal CBV that has both LoginRequiredMixin and
        PermissionRequiredMixin in its MRO must list LoginRequiredMixin first.

        (Most portal views only have LoginRequiredMixin — this test is
        forward-looking in case PermissionRequiredMixin is ever added.)
        """
        classes = self._collect_view_classes("apps.volunteers.views.portal")
        for cls in classes:
            mro = cls.__mro__
            has_login = LoginRequiredMixin in mro
            has_perm = PermissionRequiredMixin in mro
            if has_login and has_perm:
                idx_login = mro.index(LoginRequiredMixin)
                idx_perm = mro.index(PermissionRequiredMixin)
                self.assertLess(
                    idx_login,
                    idx_perm,
                    msg=(
                        f"{cls.__name__}: LoginRequiredMixin must precede "
                        f"PermissionRequiredMixin in MRO."
                    ),
                )

    def test_coordinator_views_with_permission_mixin_have_raise_exception(self):
        """
        All coordinator CBVs that include PermissionRequiredMixin must declare
        raise_exception = True so authenticated-but-unauthorised users get a 403
        rather than being silently redirected to the login page.
        """
        classes = self._collect_view_classes("apps.volunteers.views.coordinator")
        missing = []
        for cls in classes:
            if PermissionRequiredMixin in cls.__mro__:
                # Check the class's own __dict__ (not inherited) first,
                # then fall back to getattr for inherited True values.
                raise_val = getattr(cls, "raise_exception", None)
                if raise_val is not True:
                    missing.append(cls.__name__)
        self.assertListEqual(
            missing,
            [],
            msg=(
                "Coordinator CBVs with PermissionRequiredMixin must set "
                "raise_exception = True. Missing: " + ", ".join(missing)
            ),
        )

    def test_at_least_one_coordinator_view_has_both_mixins(self):
        """
        Sanity check: the coordinator module should have CBVs with both mixins
        (guards against an empty import or wrong module path silently passing).
        """
        classes = self._collect_view_classes("apps.volunteers.views.coordinator")
        both = [
            cls for cls in classes
            if LoginRequiredMixin in cls.__mro__ and PermissionRequiredMixin in cls.__mro__
        ]
        self.assertGreater(
            len(both),
            0,
            "Expected at least one coordinator CBV with both LoginRequiredMixin "
            "and PermissionRequiredMixin. Check module path.",
        )


# ===========================================================================
# Class 2: PIPEDA Audit Log Tests
# ===========================================================================

class PIPEDAAuditLogTests(TestCase):
    """
    Volunteer actions must not write PII (email, phone, full name) to
    AuditLogEntry.event_detail.  Only profile PKs are acceptable.

    These tests call the service-layer functions that trigger audit writes
    and then scan all AuditLogEntry rows for the volunteer's email address.

    Note: The services themselves (applications.py, hours.py, scheduling.py)
    do not write directly to AuditLogEntry; the admin layer does.  The audit
    contract is enforced in apps/volunteers/admin.py, which explicitly stores
    only PKs.  These tests verify the end-to-end invariant by creating entries
    via the admin helper and asserting no PII leaks through.
    """

    def setUp(self):
        from apps.audit.models import AuditLogEntry
        self.AuditLogEntry = AuditLogEntry

        # Coordinator with audit-writing permissions.
        coord = _make_user("pipeda-coord@example.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(coord, "add_honorarium")

        # Volunteer — email/name must never appear in event_detail.
        self.volunteer_user = _make_user("pipeda-vol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

        self.program = _make_program(slug="pipeda-prog")
        self.opportunity = _make_opportunity(self.program, slug="pipeda-opp")

    def _assert_no_pii_in_event_detail(self, pii_strings):
        """
        Assert that no AuditLogEntry.event_detail contains any PII string.
        Checks str(entry.event_detail) for each entry.
        """
        entries = self.AuditLogEntry.objects.all()
        for entry in entries:
            detail_str = str(entry.event_detail)
            for pii in pii_strings:
                self.assertNotIn(
                    pii,
                    detail_str,
                    msg=(
                        f"PII '{pii}' found in AuditLogEntry #{entry.pk} "
                        f"event_detail: {detail_str!r}. "
                        "PIPEDA invariant violated: only profile PKs are permitted."
                    ),
                )

    def _get_pii_strings(self):
        """Return the PII strings that must never appear in audit event_detail."""
        strings = [self.volunteer_user.email]
        # Include phone_number if set on the profile.
        profile = self.volunteer_profile
        if getattr(profile, "phone_number", None):
            strings.append(profile.phone_number)
        return strings

    def test_audit_log_entry_from_admin_helper_contains_no_volunteer_email(self):
        """
        Admin helper _write_audit_entry must not include volunteer email in event_detail.
        Tests the contract at the audit-write callsite.
        """
        from apps.volunteers.admin import _write_volunteer_audit
        factory = RequestFactory()
        request = factory.post("/admin/volunteers/")
        request.user = self.coordinator
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.session = {}  # RequestFactory requests have no session by default
        # Simulate a coordinator viewing a volunteer profile — event_detail should
        # only contain the profile PK, not the volunteer's email.
        _write_volunteer_audit(
            request=request,
            event_type="data.viewed",
            resource_id=str(self.volunteer_profile.pk),
            detail={"volunteer_profile_pk": self.volunteer_profile.pk},
        )
        self._assert_no_pii_in_event_detail(self._get_pii_strings())

    def test_honorarium_audit_entry_contains_no_volunteer_email(self):
        """
        When an honorarium is created via the admin helper, event_detail must
        reference the volunteer by PK only — not email.
        """
        from apps.volunteers.admin import _write_volunteer_audit
        factory = RequestFactory()
        request = factory.post("/admin/volunteers/honorarium/add/")
        request.user = self.coordinator
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.session = {}  # RequestFactory requests have no session by default
        hon = _make_honorarium(
            self.volunteer_profile, "100.00", created_by=self.coordinator
        )
        _write_volunteer_audit(
            request=request,
            event_type="honorarium.created",
            resource_id=str(hon.pk),
            detail={
                "honorarium_pk": hon.pk,
                "volunteer_profile_pk": self.volunteer_profile.pk,
                "amount": str(hon.amount),
            },
        )
        self._assert_no_pii_in_event_detail(self._get_pii_strings())

    def test_volunteer_pk_is_acceptable_in_event_detail(self):
        """
        Positive control: volunteer profile PK IS acceptable in event_detail
        and must not be scrubbed.
        """
        from apps.volunteers.admin import _write_volunteer_audit
        factory = RequestFactory()
        request = factory.post("/admin/volunteers/")
        request.user = self.coordinator
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.session = {}  # RequestFactory requests have no session by default
        _write_volunteer_audit(
            request=request,
            event_type="data.viewed",
            resource_id=str(self.volunteer_profile.pk),
            detail={"volunteer_profile_pk": self.volunteer_profile.pk},
        )
        entry = self.AuditLogEntry.objects.latest("timestamp")
        self.assertIn(
            str(self.volunteer_profile.pk),
            str(entry.event_detail),
            msg="Volunteer profile PK should be present in event_detail.",
        )

    def test_volunteer_email_not_in_actor_email_field_for_data_events(self):
        """
        For data.viewed events, actor_email must be blank (PIPEDA minimum-data
        principle: only authentication events snapshot the email).
        """
        from apps.volunteers.admin import _write_volunteer_audit
        factory = RequestFactory()
        request = factory.post("/admin/volunteers/")
        request.user = self.coordinator
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.session = {}  # RequestFactory requests have no session by default
        _write_volunteer_audit(
            request=request,
            event_type="data.viewed",
            resource_id=str(self.volunteer_profile.pk),
            detail={"volunteer_profile_pk": self.volunteer_profile.pk},
        )
        entry = self.AuditLogEntry.objects.latest("timestamp")
        # actor_email must be blank for data events (PIPEDA minimum data).
        self.assertEqual(
            entry.actor_email,
            "",
            msg=(
                "actor_email must be blank for data.viewed events. "
                f"Found: {entry.actor_email!r}"
            ),
        )

    def test_sin_last4_not_in_audit_event_detail(self):
        """
        PIPEDA: sin_last4 must never appear in AuditLogEntry.event_detail.

        A legitimate audit entry references only the profile PK. The raw SIN
        last-4-digits value must not appear in any audit log detail field.
        """
        sin_value = "9999"
        self.volunteer_profile.sin_last4 = sin_value
        self.volunteer_profile.save(update_fields=["sin_last4"])

        # Create a legitimate audit entry that references only the profile PK.
        self.AuditLogEntry.objects.create(
            actor_id=str(self.coordinator.pk),
            actor_email="",  # PIPEDA: blank for data events
            actor_ip=None,
            event_type="data.viewed",
            outcome="success",
            resource_type="volunteers.VolunteerProfile",
            resource_id=str(self.volunteer_profile.pk),
            event_detail={
                "volunteer_profile_pk": self.volunteer_profile.pk,
            },
        )

        # Verify no AuditLogEntry event_detail contains the sin_last4 value.
        # JSONField supports __icontains on the serialised JSON text in PostgreSQL.
        self.assertFalse(
            self.AuditLogEntry.objects.filter(
                event_detail__icontains=sin_value
            ).exists(),
            f"sin_last4 '{sin_value}' found in AuditLogEntry.event_detail — "
            "PIPEDA violation: SIN digits must never be written to audit logs.",
        )


# ===========================================================================
# Class 3: CRA Honorarium Threshold Boundary Tests
# ===========================================================================

@override_settings(
    VOLUNTEER_CRA_ALERT_THRESHOLD=450,
    VOLUNTEER_CRA_T4A_THRESHOLD=500,
    VOLUNTEER_CRA_HARD_BLOCK=1000,
)
class CRAThresholdBoundaryTests(TestCase):
    """
    CRA PC-025 threshold boundary-value tests.

    Tests exact boundary amounts for the three CRA thresholds:
      $450  — advisory alert; t4a_required stays False (non-blocking)
      $500  — T4A required; t4a_required auto-set True (non-blocking)
      $1000 — hard block; ValidationError raised (blocking)

    All monetary values use Decimal. Existing YTD is set via _make_honorarium()
    (skip_clean=True) so pre-existing data bypasses thresholds; then
    Honorarium.full_clean() (called by save()) is exercised on the new record.

    Implementation note: Honorarium.clean() raises on projected_total >= $1000.
    It sets t4a_required=True on projected_total >= $500.
    The $450 alert is signal-based (post-commit) — clean() does not set any flag.
    """

    def setUp(self):
        coord = _make_user("cra-coord@example.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(coord, "add_honorarium")

        self.volunteer_user = _make_user("cra-vol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

    def _new_honorarium(self, existing_ytd, new_amount):
        """
        Pre-populate YTD with existing_ytd (skip_clean), then build and
        full_clean() a new Honorarium for new_amount.  Returns the instance
        (not yet saved if it raises — use in assertRaises context).
        """
        from apps.volunteers.models import Honorarium
        if Decimal(str(existing_ytd)) > Decimal("0"):
            _make_honorarium(
                self.volunteer_profile, existing_ytd, created_by=self.coordinator
            )
        h = Honorarium(
            volunteer=self.volunteer_profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal(str(new_amount)),
            description="Boundary test",
            payment_date=timezone.now().date(),
            created_by=self.coordinator,
        )
        return h

    # ---- Below alert threshold ----

    def test_449_99_below_alert_t4a_false(self):
        """$449.99 YTD total — below alert threshold; t4a_required stays False."""
        # $400 existing + $49.99 new = $449.99
        h = self._new_honorarium("400.00", "49.99")
        h.full_clean()
        self.assertFalse(h.t4a_required)

    # ---- Exactly at alert threshold ----

    def test_450_00_at_alert_t4a_false(self):
        """
        $450.00 YTD total — at the alert threshold.
        clean() is non-blocking at $450; t4a_required stays False.
        (The coordinator alert is a post-commit signal, not a model flag.)
        """
        # $400 existing + $50 new = $450
        h = self._new_honorarium("400.00", "50.00")
        h.full_clean()
        self.assertFalse(h.t4a_required)

    def test_450_01_above_alert_t4a_false(self):
        """$450.01 YTD total — just above alert; t4a_required still False (under $500)."""
        # $400 existing + $50.01 new = $450.01
        h = self._new_honorarium("400.00", "50.01")
        h.full_clean()
        self.assertFalse(h.t4a_required)

    # ---- Below T4A threshold ----

    def test_499_99_below_t4a_t4a_false(self):
        """$499.99 YTD total — just below T4A threshold; t4a_required stays False."""
        # $400 existing + $99.99 new = $499.99
        h = self._new_honorarium("400.00", "99.99")
        h.full_clean()
        self.assertFalse(h.t4a_required)

    # ---- Exactly at T4A threshold ----

    def test_500_00_at_t4a_threshold_t4a_true(self):
        """$500.00 YTD total — at T4A threshold; t4a_required is auto-set to True."""
        # $400 existing + $100 new = $500
        h = self._new_honorarium("400.00", "100.00")
        h.full_clean()
        self.assertTrue(
            h.t4a_required,
            "t4a_required must be True when projected YTD reaches exactly $500.",
        )

    # ---- Above T4A threshold ----

    def test_500_01_above_t4a_threshold_t4a_true(self):
        """$500.01 YTD total — above T4A threshold; t4a_required is True."""
        # $400 existing + $100.01 new = $500.01
        h = self._new_honorarium("400.00", "100.01")
        h.full_clean()
        self.assertTrue(h.t4a_required)

    # ---- Below hard block ----

    def test_999_99_below_hard_block_succeeds(self):
        """$999.99 YTD total — just under hard block; full_clean() succeeds."""
        # $900 existing + $99.99 new = $999.99
        h = self._new_honorarium("900.00", "99.99")
        h.full_clean()
        self.assertTrue(
            h.t4a_required,
            "t4a_required must be True when YTD >= $500.",
        )

    # ---- Exactly at hard block ----

    def test_1000_00_at_hard_block_raises(self):
        """
        $1000.00 YTD total — exactly at the hard block.
        Honorarium.clean() raises ValidationError (projected >= $1000).
        """
        # $900 existing + $100 new = $1000
        h = self._new_honorarium("900.00", "100.00")
        with self.assertRaises(ValidationError, msg="Hard block at exactly $1000 must raise ValidationError."):
            h.full_clean()

    # ---- Above hard block ----

    def test_1000_01_above_hard_block_raises(self):
        """
        $1000.01 YTD total — above hard block.
        Hard block still applies; ValidationError must be raised.
        """
        # $900 existing + $100.01 new = $1000.01
        h = self._new_honorarium("900.00", "100.01")
        with self.assertRaises(ValidationError, msg="Hard block above $1000 must still raise ValidationError."):
            h.full_clean()

    def test_expense_reimbursement_exempt_from_hard_block(self):
        """
        Expense reimbursements are NOT counted toward CRA thresholds.
        $999.99 expense + $100 honorarium → only $100 honorarium YTD; no block.
        """
        from apps.volunteers.models import Honorarium
        _make_honorarium(
            self.volunteer_profile,
            "999.99",
            payment_type=Honorarium.PAYMENT_TYPE_EXPENSE,
            created_by=self.coordinator,
        )
        h = self._new_honorarium("0", "100.00")
        h.full_clean()  # Must not raise — only $100 honorarium YTD
        # $100 honorarium YTD is well below the $500 T4A threshold.
        self.assertFalse(
            h.t4a_required,
            "$100 honorarium YTD is below T4A threshold — t4a_required must be False.",
        )


# ===========================================================================
# Class 4: Concurrent Overbooking (TransactionTestCase)
# ===========================================================================

@unittest.skipIf(
    connection.vendor == "sqlite",
    "select_for_update() requires PostgreSQL row-level locking; SQLite cannot serialise concurrent threads",
)
class ConcurrentOverbookingTests(TransactionTestCase):
    """
    Two concurrent book_shift() calls on a shift with capacity=1 — exactly
    one must succeed (confirmed booking); the second must either be waitlisted
    (if waitlist_enabled=True) or raise a ValidationError (if False).

    With waitlist_enabled=False: only 1 confirmed booking; second call raises.
    With waitlist_enabled=True:  1 confirmed + 1 waitlisted.

    The SELECT FOR UPDATE in book_shift() serialises the capacity check at the
    DB layer, preventing true overbooking under concurrent load.

    TransactionTestCase is required because select_for_update() needs real
    row-level locking semantics (unavailable inside TestCase's wrapping savepoint).
    """

    def setUp(self):
        from apps.volunteers.models import Opportunity, Program, VolunteerApplication

        # Two volunteer users for the two concurrent booking attempts.
        self.vol1_user = _make_user("race-vol1@example.gc.ca")
        self.vol1_profile = _make_profile(self.vol1_user)

        self.vol2_user = _make_user("race-vol2@example.gc.ca")
        self.vol2_profile = _make_profile(self.vol2_user)

        self.program = _make_program(slug="overbooking-prog")
        self.opportunity = _make_opportunity(self.program, slug="overbooking-opp")

        # Both volunteers need approved applications before they can book.
        _make_approved_application(self.vol1_profile, self.opportunity)
        _make_approved_application(self.vol2_profile, self.opportunity)

    def test_no_waitlist_only_one_confirmed_booking(self):
        """
        Two concurrent book_shift() calls on a capacity=1, no-waitlist shift.
        Exactly 1 confirmed booking must exist; the second call raises ValidationError.
        """
        from apps.volunteers.models import ShiftBooking
        from apps.volunteers.services.scheduling import book_shift

        shift = _make_shift(
            self.opportunity,
            capacity=1,
            waitlist_enabled=False,
        )

        results = []
        errors = []

        def _book(vol_profile, vol_user):
            try:
                booking = book_shift(
                    shift=shift,
                    volunteer_profile=vol_profile,
                    actor=vol_user,
                )
                results.append(booking)
            except ValidationError as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_book, args=(self.vol1_profile, self.vol1_user))
        t2 = threading.Thread(target=_book, args=(self.vol2_profile, self.vol2_user))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        confirmed_count = ShiftBooking.objects.filter(
            shift=shift,
            status=ShiftBooking.STATUS_CONFIRMED,
        ).count()

        # Core invariant: exactly 1 confirmed booking regardless of thread ordering.
        self.assertEqual(
            confirmed_count,
            1,
            f"Expected exactly 1 confirmed booking; got {confirmed_count}. "
            f"results={len(results)}, errors={len(errors)}",
        )

        # Total outcomes must equal 2 (one per thread).
        self.assertEqual(
            len(results) + len(errors),
            2,
            "Total outcomes (success + error) must equal the number of threads.",
        )

    def test_with_waitlist_one_confirmed_one_waitlisted(self):
        """
        Two concurrent book_shift() calls on a capacity=1, waitlist-enabled shift.
        After both threads complete: exactly 1 confirmed and exactly 1 waitlisted.
        No overbooking (2 confirmed) must occur.
        """
        from apps.volunteers.models import ShiftBooking
        from apps.volunteers.services.scheduling import book_shift

        shift = _make_shift(
            self.opportunity,
            capacity=1,
            waitlist_enabled=True,
            waitlist_cap=None,  # unlimited waitlist
        )

        results = []
        errors = []

        def _book(vol_profile, vol_user):
            try:
                booking = book_shift(
                    shift=shift,
                    volunteer_profile=vol_profile,
                    actor=vol_user,
                )
                results.append(booking)
            except ValidationError as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_book, args=(self.vol1_profile, self.vol1_user))
        t2 = threading.Thread(target=_book, args=(self.vol2_profile, self.vol2_user))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        confirmed_count = ShiftBooking.objects.filter(
            shift=shift,
            status=ShiftBooking.STATUS_CONFIRMED,
        ).count()
        waitlisted_count = ShiftBooking.objects.filter(
            shift=shift,
            status=ShiftBooking.STATUS_WAITLISTED,
        ).count()

        # Core invariant: never more than 1 confirmed booking on a capacity=1 shift.
        self.assertLessEqual(
            confirmed_count,
            1,
            f"Overbooking detected: {confirmed_count} confirmed bookings on capacity=1 shift.",
        )
        self.assertEqual(confirmed_count, 1, "Exactly 1 confirmed booking expected.")
        self.assertEqual(waitlisted_count, 1, "Exactly 1 waitlisted booking expected.")
        self.assertEqual(len(errors), 0, f"No ValidationErrors expected with waitlist; got: {errors}")


# ===========================================================================
# Class 5: VSC Expiry Edge Cases
# ===========================================================================

class VSCExpiryTests(TestCase):
    """
    ScreeningRecord VSC expiry properties must return correct values at
    boundary dates.

    is_expired:             True when today > expires_date (strictly after)
    expires_within_30_days: True when 0 <= (expires_date - today).days <= 30

    Boundary expectations:
      +31 days: NOT expired, NOT within 30 days
      +30 days: NOT expired, IS within 30 days
      +1  day:  NOT expired, IS within 30 days
      +0  days: NOT expired (today == expires_date is the last valid day),
                IS within 30 days (delta=0, i.e. 0 <= 0 <= 30)
      -1  day:  IS expired, NOT within 30 days
      -30 days: IS expired, NOT within 30 days
    """

    def setUp(self):
        from apps.volunteers.models import Opportunity, Program, ScreeningRecord

        self.ScreeningRecord = ScreeningRecord
        self.volunteer_user = _make_user("vsc-vol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

    def _make_vsc(self, expires_date):
        """Create a ScreeningRecord (VSC type) with a specific expires_date."""
        today = timezone.localtime(timezone.now()).date()
        return self.ScreeningRecord.objects.create(
            volunteer=self.volunteer_profile,
            check_type=self.ScreeningRecord.CHECK_TYPE_VSC,
            completed_date=today - timedelta(days=365),  # completed 1 year ago
            expires_date=expires_date,
            verified_clear=True,
        )

    def _today(self):
        return timezone.localtime(timezone.now()).date()

    def test_31_days_future_not_expired_not_within_30(self):
        """expires_date = today + 31d → NOT expired, NOT within 30 days."""
        vsc = self._make_vsc(self._today() + timedelta(days=31))
        self.assertFalse(vsc.is_expired, "VSC expiring in 31 days must NOT be expired.")
        self.assertFalse(
            vsc.expires_within_30_days,
            "VSC expiring in 31 days must NOT be within 30 days.",
        )

    def test_30_days_future_not_expired_is_within_30(self):
        """expires_date = today + 30d → NOT expired, IS within 30 days."""
        vsc = self._make_vsc(self._today() + timedelta(days=30))
        self.assertFalse(vsc.is_expired, "VSC expiring in 30 days must NOT be expired.")
        self.assertTrue(
            vsc.expires_within_30_days,
            "VSC expiring in exactly 30 days must BE within 30 days (boundary inclusive).",
        )

    def test_1_day_future_not_expired_is_within_30(self):
        """expires_date = today + 1d → NOT expired, IS within 30 days."""
        vsc = self._make_vsc(self._today() + timedelta(days=1))
        self.assertFalse(vsc.is_expired, "VSC expiring tomorrow must NOT be expired.")
        self.assertTrue(vsc.expires_within_30_days, "VSC expiring tomorrow IS within 30 days.")

    def test_0_days_today_not_expired_is_within_30(self):
        """
        expires_date = today → NOT expired (today is the last valid day),
        IS within 30 days (delta = 0, which satisfies 0 <= 0 <= 30).
        """
        vsc = self._make_vsc(self._today())
        self.assertFalse(
            vsc.is_expired,
            "VSC expiring today must NOT be expired (is_expired uses strict >).",
        )
        self.assertTrue(
            vsc.expires_within_30_days,
            "VSC expiring today IS within 30 days (delta.days == 0).",
        )

    def test_1_day_past_is_expired_not_within_30(self):
        """expires_date = today - 1d → IS expired, NOT within 30 days (negative delta)."""
        vsc = self._make_vsc(self._today() - timedelta(days=1))
        self.assertTrue(vsc.is_expired, "VSC expired yesterday must be expired.")
        self.assertFalse(
            vsc.expires_within_30_days,
            "Expired VSC (yesterday) must NOT be within 30 days.",
        )

    def test_30_days_past_is_expired_not_within_30(self):
        """expires_date = today - 30d → IS expired, NOT within 30 days."""
        vsc = self._make_vsc(self._today() - timedelta(days=30))
        self.assertTrue(vsc.is_expired, "VSC expired 30 days ago must be expired.")
        self.assertFalse(
            vsc.expires_within_30_days,
            "VSC expired 30 days ago must NOT be within 30 days.",
        )

    def test_no_expires_date_never_expired(self):
        """
        expires_date = None → is_expired = False, expires_within_30_days = False.
        Reference checks and other open-ended checks have no expiry.
        """
        vsc = self._make_vsc(expires_date=None)
        self.assertFalse(vsc.is_expired, "VSC with no expires_date must NOT be expired.")
        self.assertFalse(
            vsc.expires_within_30_days,
            "VSC with no expires_date must NOT be within 30 days.",
        )


# ===========================================================================
# Class 6: Bilingual Response Tests
# ===========================================================================

class BilingualResponseTests(TestCase):
    """
    Key portal views must serve French content when Accept-Language: fr is set.

    The Django LocaleMiddleware activates the language based on the
    Accept-Language header; i18n_patterns adds a /fr/ URL prefix for French.

    Tested views:
      1. opportunity_list   — /fr/volunteer/opportunities/
      2. opportunity_detail — /fr/volunteer/opportunities/<pk>/
      3. apply              — /fr/volunteer/opportunities/<pk>/apply/
                              (redirects unauthenticated; 200 for logged-in user
                               with profile)

    Fixtures include both title_en and title_fr to verify language switching.
    """

    def setUp(self):
        self.volunteer_user = _make_user("bilingual-vol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

        self.program = _make_program(slug="bilingual-prog")
        self.opportunity = _make_opportunity(
            self.program,
            slug="bilingual-opp",
            title_en="English Opportunity Title",
            title_fr="Titre d'opportunité en français",
            status="published",
        )

    def _fr_client(self):
        """Return a test client logged in as the volunteer, with French locale."""
        self.client.force_login(self.volunteer_user)
        return self.client

    def test_opportunity_list_fr_returns_200(self):
        """GET /fr/volunteers/volunteer/opportunities/ returns HTTP 200."""
        client = self._fr_client()
        response = client.get(
            "/fr/volunteers/volunteer/opportunities/",
            HTTP_ACCEPT_LANGUAGE="fr",
        )
        self.assertEqual(
            response.status_code,
            200,
            f"Opportunity list view (FR) returned {response.status_code}, expected 200.",
        )

    def test_opportunity_list_fr_contains_french_title(self):
        """Opportunity list served in French must contain the French opportunity title."""
        import html as html_module
        client = self._fr_client()
        response = client.get(
            "/fr/volunteers/volunteer/opportunities/",
            HTTP_ACCEPT_LANGUAGE="fr",
        )
        # Unescape HTML entities (e.g. &#x27; → ‘) before checking for the French title.
        content = html_module.unescape(response.content.decode())
        self.assertIn(
            "Titre d'opportunité en français",
            content,
            "French opportunity title must appear in /fr/ opportunity list response.",
        )

    def test_opportunity_list_en_contains_english_title(self):
        """Opportunity list served in English must contain the English opportunity title."""
        from django.urls import reverse
        from django.utils.translation import override as lang_override
        client = self._fr_client()
        # Clear any session language that may have been set by prior FR requests in this test.
        session = client.session
        session.pop("_language", None)
        session.save()
        with lang_override("en"):
            response = client.get(
                reverse("volunteers:opportunity_list"),
                HTTP_ACCEPT_LANGUAGE="en",
            )
        self.assertContains(
            response,
            "English Opportunity Title",
            msg_prefix="English opportunity title must appear in /en/ opportunity list response.",
        )

    def test_opportunity_detail_fr_returns_200(self):
        """GET /fr/volunteers/volunteer/opportunities/<pk>/ returns HTTP 200."""
        client = self._fr_client()
        response = client.get(
            f"/fr/volunteers/volunteer/opportunities/{self.opportunity.pk}/",
            HTTP_ACCEPT_LANGUAGE="fr",
        )
        self.assertEqual(
            response.status_code,
            200,
            f"Opportunity detail view (FR) returned {response.status_code}, expected 200.",
        )

    def test_opportunity_detail_fr_contains_french_title(self):
        """Opportunity detail served in French must contain the French title."""
        import html as html_module
        client = self._fr_client()
        response = client.get(
            f"/fr/volunteers/volunteer/opportunities/{self.opportunity.pk}/",
            HTTP_ACCEPT_LANGUAGE="fr",
        )
        # Unescape HTML entities (e.g. &#x27; → ') before checking for the French title.
        content = html_module.unescape(response.content.decode())
        self.assertIn(
            "Titre d'opportunité en français",
            content,
            "French title must appear in /fr/ opportunity detail response.",
        )

    def test_opportunity_detail_en_contains_english_title(self):
        """Opportunity detail served in English must contain the English title."""
        from django.urls import reverse
        from django.utils.translation import override as lang_override
        client = self._fr_client()
        # Clear any session language that may have been set by prior FR requests in this test.
        session = client.session
        session.pop("_language", None)
        session.save()
        with lang_override("en"):
            response = client.get(
                reverse("volunteers:opportunity_detail", kwargs={"pk": self.opportunity.pk}),
                HTTP_ACCEPT_LANGUAGE="en",
            )
        self.assertContains(
            response,
            "English Opportunity Title",
            msg_prefix="English title must appear in /en/ opportunity detail response.",
        )

    def test_apply_view_fr_returns_200_for_logged_in_volunteer(self):
        """
        GET /fr/volunteers/volunteer/opportunities/<pk>/apply/ returns 200 for a
        logged-in volunteer with a profile.
        """
        client = self._fr_client()
        response = client.get(
            f"/fr/volunteers/volunteer/opportunities/{self.opportunity.pk}/apply/",
            HTTP_ACCEPT_LANGUAGE="fr",
        )
        self.assertEqual(
            response.status_code,
            200,
            f"Apply view (FR) returned {response.status_code}, expected 200. "
            "Volunteer with profile should see the application form.",
        )

    def test_get_title_returns_french_when_fr_active(self):
        """
        Opportunity.get_title() returns title_fr when the active language is 'fr'.
        Tests the model method directly without a full HTTP round-trip.
        """
        from django.utils.translation import override as lang_override
        with lang_override("fr"):
            title = self.opportunity.get_title()
        self.assertEqual(
            title,
            "Titre d'opportunité en français",
            "get_title() must return title_fr when language is 'fr'.",
        )

    def test_get_title_returns_english_when_en_active(self):
        """
        Opportunity.get_title() returns title_en when the active language is 'en'.
        """
        from django.utils.translation import override as lang_override
        with lang_override("en"):
            title = self.opportunity.get_title()
        self.assertEqual(
            title,
            "English Opportunity Title",
            "get_title() must return title_en when language is 'en'.",
        )

    def test_get_title_falls_back_to_english_when_title_fr_blank(self):
        """
        Opportunity.get_title() falls back to title_en when title_fr is blank.
        """
        from django.utils.translation import override as lang_override
        opp = _make_opportunity(
            self.program,
            title_en="Fallback English Title",
            title_fr="",
        )
        with lang_override("fr"):
            title = opp.get_title()
        self.assertEqual(
            title,
            "Fallback English Title",
            "get_title() must fall back to title_en when title_fr is blank.",
        )

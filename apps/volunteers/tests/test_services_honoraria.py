"""
Wave 4 — Volunteer Management BB: honorarium service test suite.

Tests CRA PC-025 threshold enforcement, concurrent creation, T4A logic,
and signal dispatch for apps/volunteers/services/honoraria.py.

Conventions:
  - captureOnCommitCallbacks(execute=True) to trigger on_commit signal paths.
  - TransactionTestCase for concurrency / select_for_update tests.
  - setUp() (not setUpTestData) for TransactionTestCase.
  - Decimal() for all monetary comparisons.
  - PIPEDA: volunteers referenced by profile PK only in assertions.
"""
from __future__ import annotations

import threading
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, TransactionTestCase, override_settings

from apps.volunteers.models import (
    Honorarium,
    Opportunity,
    Program,
    VolunteerProfile,
)
from apps.volunteers.services.honoraria import create_honorarium, cumulative_ytd

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared factory helpers  (verbatim pattern from test_services_hours.py)
# ---------------------------------------------------------------------------

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


def _make_user(email=None, password="testpass!", **kwargs):
    _counter[0] += 1
    email = email or f"h{_counter[0]}@example.gc.ca"
    return User.objects.create_user(email=email, password=password, **kwargs)


def _make_program(**kwargs):
    n = _uid()
    defaults = dict(
        name_en=f"Program {n}",
        name_fr=f"Programme {n}",
        slug=f"hprog-{n}",
        cra_category="welfare",
    )
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_opportunity(program, *, slug=None, status="published", **kwargs):
    n = _uid()
    defaults = dict(
        title_en=f"Opportunity {n}",
        title_fr=f"Opportunité {n}",
        slug=slug or f"hopp-{n}",
        description_en="Description",
        description_fr="Description FR",
        program=program,
        status=status,
    )
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


def _make_profile(user):
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
    """
    Create an Honorarium directly (bypassing CRA service) for test setup.
    Uses skip_clean=True to avoid threshold checks on pre-existing data.
    """
    if payment_type is None:
        payment_type = Honorarium.PAYMENT_TYPE_HONORARIUM
    if payment_date is None:
        payment_date = date.today()
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


# ---------------------------------------------------------------------------
# Shared base — coordinator + volunteer profile
# ---------------------------------------------------------------------------

class HonorariumBaseTestCase(TestCase):
    """
    Shared fixtures for honorarium service tests.

    Attributes:
        coordinator       — User with volunteers.add_honorarium perm
        volunteer_user    — regular volunteer User
        volunteer_profile — VolunteerProfile for volunteer_user
        program, opportunity
    """

    def setUp(self):
        coord = _make_user("hcoord@example.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(coord, "add_honorarium")

        self.volunteer_user = _make_user("hvol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

        self.program = _make_program(slug="hon-base-prog")
        self.opportunity = _make_opportunity(self.program, slug="hon-base-opp")

    def _create_via_service(self, amount, payment_type=None, payment_date=None):
        """Shortcut for calling create_honorarium() with defaults."""
        if payment_type is None:
            payment_type = Honorarium.PAYMENT_TYPE_HONORARIUM
        return create_honorarium(
            volunteer_profile=self.volunteer_profile,
            payment_type=payment_type,
            amount=Decimal(str(amount)),
            description="Service test payment",
            payment_date=payment_date or date.today(),
            created_by=self.coordinator,
        )


# ===========================================================================
# cumulative_ytd() tests
# ===========================================================================

class CumulativeYtdTests(HonorariumBaseTestCase):

    def test_returns_zero_for_no_records(self):
        """cumulative_ytd() returns Decimal('0') when the volunteer has no honoraria."""
        total = cumulative_ytd(self.volunteer_profile, year=2026)
        self.assertEqual(total, Decimal("0"))

    def test_counts_honorarium_type_only(self):
        """Expense reimbursements are NOT counted toward CRA thresholds."""
        _make_honorarium(self.volunteer_profile, "300.00",
                         payment_type=Honorarium.PAYMENT_TYPE_EXPENSE)
        _make_honorarium(self.volunteer_profile, "100.00",
                         payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM)
        total = cumulative_ytd(self.volunteer_profile, year=date.today().year)
        self.assertEqual(total, Decimal("100.00"))

    def test_counts_multiple_honoraria_in_same_year(self):
        """Multiple PAYMENT_TYPE_HONORARIUM rows are summed correctly."""
        _make_honorarium(self.volunteer_profile, "150.00")
        _make_honorarium(self.volunteer_profile, "200.00")
        _make_honorarium(self.volunteer_profile, "75.50")
        total = cumulative_ytd(self.volunteer_profile, year=date.today().year)
        self.assertEqual(total, Decimal("425.50"))

    def test_excludes_different_year(self):
        """Honoraria in a different calendar year are not counted."""
        last_year = date.today().year - 1
        _make_honorarium(self.volunteer_profile, "400.00",
                         payment_date=date(last_year, 6, 1))
        total = cumulative_ytd(self.volunteer_profile, year=date.today().year)
        self.assertEqual(total, Decimal("0"))

    def test_returns_decimal_type(self):
        """cumulative_ytd() always returns a Decimal, not float or None."""
        _make_honorarium(self.volunteer_profile, "50.00")
        total = cumulative_ytd(self.volunteer_profile, year=date.today().year)
        self.assertIsInstance(total, Decimal)


# ===========================================================================
# create_honorarium() — permission tests
# ===========================================================================

class CreateHonorariumPermissionTests(HonorariumBaseTestCase):

    def test_raises_permission_denied_without_add_honorarium_perm(self):
        """create_honorarium() raises PermissionDenied for users lacking add_honorarium."""
        with self.assertRaises(PermissionDenied):
            create_honorarium(
                volunteer_profile=self.volunteer_profile,
                payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
                amount=Decimal("100.00"),
                description="Should be denied",
                payment_date=date.today(),
                created_by=self.volunteer_user,
            )

    def test_coordinator_with_permission_succeeds(self):
        """User with volunteers.add_honorarium can create an honorarium."""
        h = self._create_via_service(100)
        self.assertIsNotNone(h.pk)
        self.assertEqual(h.volunteer, self.volunteer_profile)
        self.assertEqual(h.amount, Decimal("100.00"))


# ===========================================================================
# create_honorarium() — payment type validation
# ===========================================================================

class CreateHonorariumPaymentTypeTests(HonorariumBaseTestCase):

    def test_raises_value_error_for_invalid_payment_type(self):
        """create_honorarium() raises ValueError for unknown payment_type strings."""
        with self.assertRaises(ValueError):
            create_honorarium(
                volunteer_profile=self.volunteer_profile,
                payment_type="unicorn_payment",
                amount=Decimal("100.00"),
                description="Invalid type",
                payment_date=date.today(),
                created_by=self.coordinator,
            )

    @override_settings(
        VOLUNTEER_CRA_ALERT_THRESHOLD=450,
        VOLUNTEER_CRA_T4A_THRESHOLD=500,
        VOLUNTEER_CRA_HARD_BLOCK=1000,
    )
    def test_expense_reimbursement_not_cra_blocked(self):
        """A $900 expense_reimbursement is allowed because expenses are exempt."""
        h = create_honorarium(
            volunteer_profile=self.volunteer_profile,
            payment_type=Honorarium.PAYMENT_TYPE_EXPENSE,
            amount=Decimal("900.00"),
            description="Travel reimbursement",
            payment_date=date.today(),
            created_by=self.coordinator,
        )
        self.assertIsNotNone(h.pk)
        self.assertEqual(h.payment_type, Honorarium.PAYMENT_TYPE_EXPENSE)

    @override_settings(
        VOLUNTEER_CRA_ALERT_THRESHOLD=450,
        VOLUNTEER_CRA_T4A_THRESHOLD=500,
        VOLUNTEER_CRA_HARD_BLOCK=1000,
    )
    def test_creates_honorarium_below_all_thresholds(self):
        """$100 honorarium is created cleanly without any CRA threshold side-effects."""
        h = self._create_via_service(100)
        self.assertIsNotNone(h.pk)
        self.assertFalse(h.t4a_required)
        self.assertEqual(h.payment_type, Honorarium.PAYMENT_TYPE_HONORARIUM)


# ===========================================================================
# CRA threshold boundary tests
# ===========================================================================

@override_settings(
    VOLUNTEER_CRA_ALERT_THRESHOLD=450,
    VOLUNTEER_CRA_T4A_THRESHOLD=500,
    VOLUNTEER_CRA_HARD_BLOCK=1000,
)
class CraThresholdTests(HonorariumBaseTestCase):
    """
    Boundary-value tests for CRA PC-025 thresholds.

    Pattern: pre-populate YTD via _make_honorarium() (skip_clean), then call
    create_honorarium() for the new amount and assert the resulting state.
    """

    def test_below_alert_threshold_no_t4a(self):
        """YTD=$400 + $49.99 = $449.99 — below alert; t4a_required stays False."""
        _make_honorarium(self.volunteer_profile, "400.00")
        h = self._create_via_service("49.99")
        self.assertFalse(h.t4a_required)

    def test_at_alert_threshold_no_t4a(self):
        """YTD=$400 + $50 = $450 — alert threshold reached; t4a_required stays False."""
        _make_honorarium(self.volunteer_profile, "400.00")
        h = self._create_via_service("50.00")
        self.assertFalse(h.t4a_required)

    def test_below_t4a_threshold(self):
        """YTD=$400 + $99.99 = $499.99 — just under T4A threshold; t4a_required=False."""
        _make_honorarium(self.volunteer_profile, "400.00")
        h = self._create_via_service("99.99")
        self.assertFalse(h.t4a_required)

    def test_at_t4a_threshold_sets_flag(self):
        """YTD=$400 + $100 = $500 — T4A threshold met; t4a_required auto-set to True."""
        _make_honorarium(self.volunteer_profile, "400.00")
        h = self._create_via_service("100.00")
        self.assertTrue(h.t4a_required)

    def test_above_t4a_threshold_sets_flag(self):
        """YTD=$600 + $50 = $650 — above T4A threshold; t4a_required=True."""
        _make_honorarium(self.volunteer_profile, "600.00")
        h = self._create_via_service("50.00")
        self.assertTrue(h.t4a_required)

    def test_at_hard_block_limit_raises_error(self):
        """YTD=$900 + $100 = $1000 — hits hard block exactly; raises ValidationError."""
        _make_honorarium(self.volunteer_profile, "900.00")
        with self.assertRaises(ValidationError):
            self._create_via_service("100.00")

    def test_below_hard_block_succeeds(self):
        """YTD=$900 + $99.99 = $999.99 — just under hard block; succeeds."""
        _make_honorarium(self.volunteer_profile, "900.00")
        h = self._create_via_service("99.99")
        self.assertIsNotNone(h.pk)
        self.assertTrue(h.t4a_required)

    def test_expense_reimbursement_does_not_count_toward_honorarium_ytd(self):
        """
        $900 expense_reimbursement + $500 PAYMENT_TYPE_HONORARIUM should NOT trigger
        the hard block because expenses are excluded from CRA threshold calculation.
        """
        _make_honorarium(self.volunteer_profile, "900.00",
                         payment_type=Honorarium.PAYMENT_TYPE_EXPENSE)
        # Only $500 honorarium — should NOT hit hard block
        h = self._create_via_service("500.00")
        self.assertIsNotNone(h.pk)
        self.assertTrue(h.t4a_required)  # $500 = T4A threshold

    def test_above_hard_block_limit_also_raises(self):
        """H-1: $1000.01 must also raise ValidationError (not just exact $1000)."""
        # YTD=$999.99 + $0.02 = $1000.01 — above hard block.
        _make_honorarium(self.volunteer_profile, "999.99")
        with self.assertRaises(ValidationError):
            self._create_via_service("0.02")


# ===========================================================================
# Signal dispatch tests — uses captureOnCommitCallbacks
# ===========================================================================

@override_settings(
    VOLUNTEER_CRA_ALERT_THRESHOLD=450,
    VOLUNTEER_CRA_T4A_THRESHOLD=500,
    VOLUNTEER_CRA_HARD_BLOCK=1000,
)
class HonorariumSignalTests(HonorariumBaseTestCase):
    """
    Tests that on_commit signals fire correctly after create_honorarium().

    ATOMIC_REQUESTS=True means we're always inside a transaction; we must use
    captureOnCommitCallbacks(execute=True) to flush the on_commit queue.
    Signal targets must be patched as apps.volunteers.signals.<signal_name>
    (not via the service module namespace).
    """

    def test_honorarium_created_signal_fired(self):
        """honorarium_created signal fires once after a successful create."""
        from apps.volunteers.signals import honorarium_created
        received = []

        def handler(sender, **kwargs):
            received.append((sender, kwargs))

        honorarium_created.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                h = self._create_via_service("100.00")
            self.assertEqual(len(received), 1)
            # sender is the refreshed Honorarium instance from on_commit
            sender, kwargs = received[0]
            self.assertEqual(sender.pk, h.pk)
        finally:
            honorarium_created.disconnect(handler)

    def test_t4a_signal_fired_when_threshold_reached(self):
        """t4a_threshold_reached fires when YTD crosses $500."""
        from apps.volunteers.signals import t4a_threshold_reached
        _make_honorarium(self.volunteer_profile, "400.00")
        received = []

        def handler(sender, **kwargs):
            received.append((sender, kwargs))

        t4a_threshold_reached.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._create_via_service("100.00")  # total = $500
            self.assertEqual(len(received), 1)
        finally:
            t4a_threshold_reached.disconnect(handler)

    def test_cra_alert_signal_fired_when_near_threshold(self):
        """cra_alert_threshold_reached fires when YTD reaches $450 but is below $500."""
        from apps.volunteers.signals import cra_alert_threshold_reached
        _make_honorarium(self.volunteer_profile, "400.00")
        received = []

        def handler(sender, **kwargs):
            received.append((sender, kwargs))

        cra_alert_threshold_reached.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._create_via_service("50.00")  # total = $450 — alert, not T4A
            self.assertEqual(len(received), 1)
            # ytd_total should be included in the signal kwargs
            self.assertIn("ytd_total", received[0][1])
            # H-3 fix: assert the exact YTD value ($400 pre-existing + $50 new = $450)
            self.assertEqual(received[0][1]["ytd_total"], Decimal("450.00"))
        finally:
            cra_alert_threshold_reached.disconnect(handler)

    def test_no_alert_signal_below_alert_threshold(self):
        """No cra_alert_threshold_reached or t4a_threshold_reached for $100 honorarium."""
        from apps.volunteers.signals import cra_alert_threshold_reached, t4a_threshold_reached
        alert_received = []
        t4a_received = []

        def _alert_handler(sender, **kw):
            alert_received.append(kw)

        def _t4a_handler(sender, **kw):
            t4a_received.append(kw)

        cra_alert_threshold_reached.connect(_alert_handler, weak=False)
        t4a_threshold_reached.connect(_t4a_handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._create_via_service("100.00")
            self.assertEqual(len(alert_received), 0)
            self.assertEqual(len(t4a_received), 0)
        finally:
            cra_alert_threshold_reached.disconnect(_alert_handler)
            t4a_threshold_reached.disconnect(_t4a_handler)

    def test_t4a_signal_not_fired_for_expense_reimbursement(self):
        """No threshold signals fire for expense_reimbursement, even for large amounts."""
        from apps.volunteers.signals import cra_alert_threshold_reached, t4a_threshold_reached
        alert_received = []
        t4a_received = []

        def _alert_handler(sender, **kw):
            alert_received.append(kw)

        def _t4a_handler(sender, **kw):
            t4a_received.append(kw)

        cra_alert_threshold_reached.connect(_alert_handler, weak=False)
        t4a_threshold_reached.connect(_t4a_handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                create_honorarium(
                    volunteer_profile=self.volunteer_profile,
                    payment_type=Honorarium.PAYMENT_TYPE_EXPENSE,
                    amount=Decimal("800.00"),
                    description="Large expense",
                    payment_date=date.today(),
                    created_by=self.coordinator,
                )
            self.assertEqual(len(alert_received), 0)
            self.assertEqual(len(t4a_received), 0)
        finally:
            cra_alert_threshold_reached.disconnect(_alert_handler)
            t4a_threshold_reached.disconnect(_t4a_handler)

    def test_honorarium_created_signal_kwargs_include_created_by(self):
        """honorarium_created signal passes created_by in kwargs."""
        from apps.volunteers.signals import honorarium_created
        received_kwargs = {}

        def handler(sender, **kwargs):
            received_kwargs.update(kwargs)

        honorarium_created.connect(handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._create_via_service("50.00")
            self.assertIn("created_by", received_kwargs)
            self.assertEqual(received_kwargs["created_by"], self.coordinator)
        finally:
            honorarium_created.disconnect(handler)

    def test_t4a_signal_fires_cra_alert_does_not(self):
        """
        H-2: When T4A threshold is reached, cra_alert signal must NOT also fire
        (they are mutually exclusive via elif in the service logic).
        """
        from apps.volunteers.signals import cra_alert_threshold_reached, t4a_threshold_reached
        # Pre-populate so that $100 tips exactly to T4A threshold ($500).
        _make_honorarium(self.volunteer_profile, "400.00")
        t4a_received = []
        alert_received = []

        def _t4a_handler(sender, **kwargs):
            t4a_received.append(True)

        def _alert_handler(sender, **kwargs):
            alert_received.append(True)

        t4a_threshold_reached.connect(_t4a_handler, weak=False)
        cra_alert_threshold_reached.connect(_alert_handler, weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._create_via_service("100.00")  # $400 + $100 = $500 → T4A
            self.assertEqual(len(t4a_received), 1, "T4A signal must fire")
            self.assertEqual(len(alert_received), 0,
                             "CRA alert must NOT fire when T4A fires (elif)")
        finally:
            t4a_threshold_reached.disconnect(_t4a_handler)
            cra_alert_threshold_reached.disconnect(_alert_handler)


# ===========================================================================
# Honorarium field persistence tests
# ===========================================================================

@override_settings(
    VOLUNTEER_CRA_ALERT_THRESHOLD=450,
    VOLUNTEER_CRA_T4A_THRESHOLD=500,
    VOLUNTEER_CRA_HARD_BLOCK=1000,
)
class HonorariumFieldPersistenceTests(HonorariumBaseTestCase):
    """Tests that fields are correctly persisted after create_honorarium()."""

    def test_amount_persisted_correctly(self):
        """The created honorarium persists the exact amount passed."""
        h = self._create_via_service("123.45")
        h.refresh_from_db()
        self.assertEqual(h.amount, Decimal("123.45"))

    def test_payment_date_persisted(self):
        """payment_date is saved verbatim."""
        target_date = date(2026, 3, 15)
        h = create_honorarium(
            volunteer_profile=self.volunteer_profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("50.00"),
            description="Test",
            payment_date=target_date,
            created_by=self.coordinator,
        )
        h.refresh_from_db()
        self.assertEqual(h.payment_date, target_date)

    def test_created_by_persisted(self):
        """created_by FK is stored correctly."""
        h = self._create_via_service("50.00")
        h.refresh_from_db()
        self.assertEqual(h.created_by, self.coordinator)

    def test_t4a_required_false_below_threshold(self):
        """t4a_required is False for a $100 honorarium when no prior YTD exists."""
        h = self._create_via_service("100.00")
        self.assertFalse(h.t4a_required)

    def test_t4a_issued_defaults_false(self):
        """t4a_issued defaults to False on a fresh honorarium."""
        h = self._create_via_service("100.00")
        self.assertFalse(h.t4a_issued)

    def test_create_honorarium_returns_honorarium_instance(self):
        """M-3: create_honorarium() returns the saved Honorarium instance."""
        h = create_honorarium(
            volunteer_profile=self.volunteer_profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("100.00"),
            description="Return type check",
            payment_date=date.today(),
            created_by=self.coordinator,
        )
        self.assertIsInstance(h, Honorarium)
        self.assertEqual(h.amount, Decimal("100.00"))
        self.assertEqual(h.description, "Return type check")


# ===========================================================================
# Concurrency tests — TransactionTestCase (real DB transactions)
# ===========================================================================

@override_settings(
    VOLUNTEER_CRA_ALERT_THRESHOLD=450,
    VOLUNTEER_CRA_T4A_THRESHOLD=500,
    VOLUNTEER_CRA_HARD_BLOCK=1000,
)
class ConcurrentHonorariumTests(TransactionTestCase):
    """
    Verify that concurrent create_honorarium() calls for the same volunteer
    that together exceed the $1,000 hard block result in exactly one success
    and one ValidationError.

    TransactionTestCase is required because select_for_update() needs real
    row-level locking semantics, unavailable inside TestCase's wrapping savepoint.
    setUp() (not setUpTestData) is required for TransactionTestCase — tables are
    truncated between tests.
    """

    def setUp(self):
        coord = _make_user("race-coord@example.gc.ca", is_staff=True)
        self.coordinator = _grant_perm(coord, "add_honorarium")

        self.volunteer_user = _make_user("race-vol@example.gc.ca")
        self.volunteer_profile = _make_profile(self.volunteer_user)

        self.program = _make_program(slug="race-hon-prog")
        self.opportunity = _make_opportunity(self.program, slug="race-hon-opp")

        # Pre-populate $900 YTD to put the volunteer near the $1,000 hard block.
        _make_honorarium(self.volunteer_profile, "900.00",
                         created_by=self.coordinator)

    def test_concurrent_creation_enforces_hard_block(self):
        """
        Two concurrent create_honorarium() calls that together would push the YTD
        from $900 to $1,100 must result in exactly one success and one ValidationError.

        The select_for_update() inside atomic() serialises the two requests so the
        second caller re-reads the committed total from the first and raises.
        """
        results = []
        errors = []

        def _create():
            try:
                h = create_honorarium(
                    volunteer_profile=self.volunteer_profile,
                    payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
                    amount=Decimal("100.00"),
                    description="Concurrent test",
                    payment_date=date.today(),
                    created_by=self.coordinator,
                )
                results.append(h)
            except ValidationError as e:
                errors.append(e)

        t1 = threading.Thread(target=_create)
        t2 = threading.Thread(target=_create)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one should succeed and one should raise ValidationError.
        self.assertEqual(len(results) + len(errors), 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)

        # The DB must contain exactly one new honorarium beyond the $900 setup row.
        count = Honorarium.objects.filter(
            volunteer=self.volunteer_profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
        ).count()
        # 1 setup row + 1 successful concurrent row = 2
        self.assertEqual(count, 2)

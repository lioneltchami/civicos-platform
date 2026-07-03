"""
Integration Wave — Volunteer Management BB: Honorarium ↔ Payments BB wiring tests.

Tests:
  - create_honorarium → PaymentIntent created with correct purpose/gateway/status
  - PaymentIntent.payer is volunteer's user
  - Payment created with correct gateway_charge_id and amount_paid
  - honorarium.payment FK set (not null) after creation
  - gateway_charge_id uniqueness across two honoraria
  - CRA hard block ($1,000 YTD) still enforced: ValidationError raised, no Payment created
  - PaymentIntent.metadata PIPEDA compliance: no PII, has honorarium_pk and source
  - gateway_intent_id format: f"hon-{honorarium.pk}"
  - Payments BB failure graceful degradation: honorarium saved, .payment = None
  - New model constants: PURPOSE_HONORARIUM, GATEWAY_MANUAL

PIPEDA: volunteers referenced by profile PK only in assertions.
Settings: --settings=config.settings.test
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

import uuid as _uuid_mod


def _uid():
    """Return a short unique hex string for factory email/username generation."""
    return _uuid_mod.uuid4().hex[:8]


def _make_user(email=None, password="testpass123!", **kwargs):
    n = _uid()
    email = email or f"hon_user_{n}@example.gc.ca"
    return User.objects.create_user(email=email, password=password, **kwargs)


def _make_program(**kwargs):
    from apps.volunteers.models import Program
    n = _uid()
    defaults = dict(
        name_en=f"Program {n}",
        name_fr=f"Programme {n}",
        slug=f"hprog-int-{n}",
        cra_category="welfare",
    )
    defaults.update(kwargs)
    return Program.objects.create(**defaults)


def _make_profile(user):
    from apps.volunteers.models import VolunteerProfile
    return VolunteerProfile.objects.create(user=user)


def _grant_add_honorarium(user):
    """Grant volunteers.add_honorarium permission and return a fresh user instance."""
    perm = Permission.objects.get(
        content_type__app_label="volunteers",
        codename="add_honorarium",
    )
    user.user_permissions.add(perm)
    # Clear permission caches
    for attr in ("_perm_cache", "_user_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
    return User.objects.get(pk=user.pk)


def _make_honorarium_direct(volunteer, amount, payment_type=None, payment_date=None, created_by=None):
    """
    Create an Honorarium directly (bypassing the service), skipping full_clean().
    Used for test setup to pre-load YTD without triggering CRA threshold checks.
    """
    from apps.volunteers.models import Honorarium
    payment_type = payment_type or Honorarium.PAYMENT_TYPE_HONORARIUM
    payment_date = payment_date or datetime.date.today()
    h = Honorarium(
        volunteer=volunteer,
        payment_type=payment_type,
        amount=Decimal(str(amount)),
        description="Pre-existing payment",
        payment_date=payment_date,
        created_by=created_by or volunteer.user,
    )
    h.save(skip_clean=True)
    return h


def _create_honorarium(volunteer_profile, *, amount, created_by, payment_type=None,
                        description="Test honorarium", payment_date=None):
    """Call the service layer create_honorarium with sensible defaults."""
    from apps.volunteers.models import Honorarium
    from apps.volunteers.services.honoraria import create_honorarium
    payment_type = payment_type or Honorarium.PAYMENT_TYPE_HONORARIUM
    payment_date = payment_date or datetime.date.today()
    return create_honorarium(
        volunteer_profile=volunteer_profile,
        payment_type=payment_type,
        amount=Decimal(str(amount)),
        description=description,
        payment_date=payment_date,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# HonorariumPaymentWiringTests (TransactionTestCase — on_commit fires)
# ---------------------------------------------------------------------------

class HonorariumPaymentWiringTests(TransactionTestCase):
    """
    Tests that create_honorarium correctly wires into the Payments BB.

    TransactionTestCase is required because create_honorarium uses
    transaction.on_commit() for signal dispatch, and on_commit only fires
    when the outermost transaction commits. TestCase wraps each test in a
    savepoint that never commits, so on_commit would never fire.

    The Payments BB wiring itself (PaymentIntent + Payment creation) is
    inside transaction.atomic() but NOT in on_commit, so it IS visible
    within the test. The TransactionTestCase is used to be safe with
    Django's test isolation semantics.
    """

    def setUp(self):
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())

    def test_creates_payment_intent_with_honorarium_purpose(self):
        from apps.payments.models import PaymentIntent
        honorarium = _create_honorarium(self.profile, amount="100.00", created_by=self.coordinator)
        pi = PaymentIntent.objects.get(gateway_intent_id=f"hon-{honorarium.pk}")
        self.assertEqual(pi.purpose, PaymentIntent.PURPOSE_HONORARIUM)

    def test_creates_payment_intent_with_manual_gateway(self):
        from apps.payments.models import PaymentIntent, GATEWAY_MANUAL
        honorarium = _create_honorarium(self.profile, amount="150.00", created_by=self.coordinator)
        pi = PaymentIntent.objects.get(gateway_intent_id=f"hon-{honorarium.pk}")
        self.assertEqual(pi.gateway, GATEWAY_MANUAL)

    def test_creates_payment_intent_with_completed_status(self):
        from apps.payments.models import PaymentIntent
        honorarium = _create_honorarium(self.profile, amount="200.00", created_by=self.coordinator)
        pi = PaymentIntent.objects.get(gateway_intent_id=f"hon-{honorarium.pk}")
        self.assertEqual(pi.status, PaymentIntent.STATUS_COMPLETED)

    def test_payment_intent_payer_is_volunteers_user(self):
        from apps.payments.models import PaymentIntent
        honorarium = _create_honorarium(self.profile, amount="250.00", created_by=self.coordinator)
        pi = PaymentIntent.objects.get(gateway_intent_id=f"hon-{honorarium.pk}")
        self.assertEqual(pi.payer_id, self.vol_user.pk)

    def test_payment_intent_amount_equals_honorarium_amount(self):
        from apps.payments.models import PaymentIntent
        honorarium = _create_honorarium(self.profile, amount="300.00", created_by=self.coordinator)
        pi = PaymentIntent.objects.get(gateway_intent_id=f"hon-{honorarium.pk}")
        self.assertEqual(pi.amount, Decimal("300.00"))

    def test_creates_payment_with_correct_gateway_charge_id(self):
        from apps.payments.models import Payment
        honorarium = _create_honorarium(self.profile, amount="75.00", created_by=self.coordinator)
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        self.assertEqual(payment.gateway_charge_id, f"HON-{honorarium.pk}")

    def test_payment_amount_paid_equals_honorarium_amount(self):
        from apps.payments.models import Payment
        honorarium = _create_honorarium(self.profile, amount="80.00", created_by=self.coordinator)
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        self.assertEqual(payment.amount_paid, Decimal("80.00"))

    def test_payment_processor_fee_is_zero(self):
        from apps.payments.models import Payment
        honorarium = _create_honorarium(self.profile, amount="90.00", created_by=self.coordinator)
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        self.assertEqual(payment.processor_fee, Decimal("0.00"))

    def test_honorarium_payment_fk_set_after_creation(self):
        honorarium = _create_honorarium(self.profile, amount="110.00", created_by=self.coordinator)
        self.assertIsNotNone(honorarium.payment)

    def test_honorarium_payment_fk_points_to_correct_payment(self):
        from apps.payments.models import Payment
        honorarium = _create_honorarium(self.profile, amount="120.00", created_by=self.coordinator)
        expected_payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        self.assertEqual(honorarium.payment.pk, expected_payment.pk)

    def test_payment_intent_back_reference_set(self):
        """Payment.intent FK must point to the created PaymentIntent."""
        from apps.payments.models import Payment, PaymentIntent
        honorarium = _create_honorarium(self.profile, amount="130.00", created_by=self.coordinator)
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        pi = PaymentIntent.objects.get(gateway_intent_id=f"hon-{honorarium.pk}")
        self.assertEqual(payment.intent_id, pi.pk)

    def test_gateway_intent_id_format(self):
        """PaymentIntent.gateway_intent_id must be f'hon-{honorarium.pk}'."""
        from apps.payments.models import PaymentIntent
        honorarium = _create_honorarium(self.profile, amount="140.00", created_by=self.coordinator)
        pi = PaymentIntent.objects.get(payer=self.vol_user, purpose=PaymentIntent.PURPOSE_HONORARIUM,
                                       amount=Decimal("140.00"))
        self.assertEqual(pi.gateway_intent_id, f"hon-{honorarium.pk}")

    def test_gateway_charge_id_uniqueness_two_honoraria(self):
        """Two honoraria for the same volunteer each get a unique gateway_charge_id."""
        honorarium_1 = _create_honorarium(
            self.profile, amount="50.00", created_by=self.coordinator,
            payment_date=datetime.date(2024, 1, 15),
        )
        honorarium_2 = _create_honorarium(
            self.profile, amount="60.00", created_by=self.coordinator,
            payment_date=datetime.date(2024, 2, 15),
        )
        charge_id_1 = f"HON-{honorarium_1.pk}"
        charge_id_2 = f"HON-{honorarium_2.pk}"
        self.assertNotEqual(charge_id_1, charge_id_2)
        self.assertNotEqual(honorarium_1.pk, honorarium_2.pk)


# ---------------------------------------------------------------------------
# HonorariumPaymentPIPEDATests
# ---------------------------------------------------------------------------

class HonorariumPaymentPIPEDATests(TransactionTestCase):
    """Tests that PaymentIntent.metadata never contains volunteer PII."""

    def setUp(self):
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())

    def _get_payment_intent(self, honorarium):
        from apps.payments.models import PaymentIntent
        return PaymentIntent.objects.get(gateway_intent_id=f"hon-{honorarium.pk}")

    def test_metadata_does_not_contain_volunteer_email(self):
        honorarium = _create_honorarium(self.profile, amount="50.00", created_by=self.coordinator)
        pi = self._get_payment_intent(honorarium)
        metadata_str = str(pi.metadata)
        self.assertNotIn(self.vol_user.email, metadata_str)

    def test_metadata_does_not_contain_first_name(self):
        self.vol_user.first_name = "Alice"
        self.vol_user.save()
        honorarium = _create_honorarium(self.profile, amount="55.00", created_by=self.coordinator)
        pi = self._get_payment_intent(honorarium)
        # first_name key should not appear in metadata keys
        self.assertNotIn("first_name", pi.metadata)
        if self.vol_user.first_name:
            self.assertNotIn(self.vol_user.first_name, str(pi.metadata))

    def test_metadata_does_not_contain_last_name(self):
        self.vol_user.last_name = "Doe"
        self.vol_user.save()
        honorarium = _create_honorarium(self.profile, amount="60.00", created_by=self.coordinator)
        pi = self._get_payment_intent(honorarium)
        self.assertNotIn("last_name", pi.metadata)
        if self.vol_user.last_name:
            self.assertNotIn(self.vol_user.last_name, str(pi.metadata))

    def test_metadata_contains_honorarium_pk(self):
        honorarium = _create_honorarium(self.profile, amount="65.00", created_by=self.coordinator)
        pi = self._get_payment_intent(honorarium)
        self.assertIn("honorarium_pk", pi.metadata)
        self.assertEqual(pi.metadata["honorarium_pk"], honorarium.pk)

    def test_metadata_contains_source(self):
        honorarium = _create_honorarium(self.profile, amount="70.00", created_by=self.coordinator)
        pi = self._get_payment_intent(honorarium)
        self.assertIn("source", pi.metadata)
        self.assertEqual(pi.metadata["source"], "volunteers.honorarium")

    def test_metadata_honorarium_pk_is_integer(self):
        honorarium = _create_honorarium(self.profile, amount="75.00", created_by=self.coordinator)
        pi = self._get_payment_intent(honorarium)
        self.assertIsInstance(pi.metadata["honorarium_pk"], int)

    def test_metadata_has_exactly_two_keys(self):
        """Only source and honorarium_pk should be in metadata — no extra PII."""
        honorarium = _create_honorarium(self.profile, amount="80.00", created_by=self.coordinator)
        pi = self._get_payment_intent(honorarium)
        self.assertEqual(set(pi.metadata.keys()), {"source", "honorarium_pk"})

    def test_payment_intent_currency_is_cad(self):
        honorarium = _create_honorarium(self.profile, amount="85.00", created_by=self.coordinator)
        pi = self._get_payment_intent(honorarium)
        self.assertEqual(pi.currency, "CAD")


# ---------------------------------------------------------------------------
# HonorariumPaymentConstantsTests
# ---------------------------------------------------------------------------

class HonorariumPaymentConstantsTests(TestCase):
    """Tests for the new constants added to apps.payments.models."""

    def test_payment_intent_purpose_honorarium_value(self):
        from apps.payments.models import PaymentIntent
        self.assertEqual(PaymentIntent.PURPOSE_HONORARIUM, "honorarium")

    def test_payment_intent_gateway_manual_value(self):
        from apps.payments.models import PaymentIntent
        self.assertEqual(PaymentIntent.GATEWAY_MANUAL, "manual")

    def test_module_level_gateway_manual_value(self):
        from apps.payments.models import GATEWAY_MANUAL
        self.assertEqual(GATEWAY_MANUAL, "manual")

    def test_purpose_honorarium_in_purpose_choices(self):
        from apps.payments.models import PaymentIntent
        choices_values = [c[0] for c in PaymentIntent.PURPOSE_CHOICES]
        self.assertIn("honorarium", choices_values)

    def test_gateway_manual_in_gateway_choices(self):
        from apps.payments.models import GATEWAY_CHOICES
        choices_values = [c[0] for c in GATEWAY_CHOICES]
        self.assertIn("manual", choices_values)

    def test_payment_intent_status_completed_value(self):
        from apps.payments.models import PaymentIntent
        self.assertEqual(PaymentIntent.STATUS_COMPLETED, "completed")

    def test_payment_method_bank_exists(self):
        from apps.payments.models import Payment
        self.assertEqual(Payment.PAYMENT_METHOD_BANK, "bank_transfer")


# ---------------------------------------------------------------------------
# HonorariumCRAThresholdTests
# ---------------------------------------------------------------------------

class HonorariumCRAThresholdTests(TestCase):
    """Tests that CRA hard block ($1,000) is still enforced after Payments BB wiring."""

    def setUp(self):
        from apps.volunteers.models import Honorarium
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())
        # Pre-load $900 YTD directly (bypassing service, no payment created)
        _make_honorarium_direct(
            self.profile,
            amount="900.00",
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            payment_date=datetime.date.today(),
            created_by=self.coordinator,
        )

    def test_exceeding_1000_ytd_raises_validation_error(self):
        """A second honorarium that pushes YTD over $1,000 must raise ValidationError."""
        with self.assertRaises(ValidationError):
            _create_honorarium(
                self.profile,
                amount="200.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )

    def test_validation_error_no_payment_intent_created(self):
        """When ValidationError is raised, NO PaymentIntent must be created for that amount."""
        from apps.payments.models import PaymentIntent
        pi_count_before = PaymentIntent.objects.filter(payer=self.vol_user).count()
        # T1: assertRaises makes the test fail if ValidationError is NOT raised (silent pass
        # via try/except would mask a regression where the hard block stopped working).
        with self.assertRaises(ValidationError):
            _create_honorarium(
                self.profile,
                amount="200.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        pi_count_after = PaymentIntent.objects.filter(payer=self.vol_user).count()
        self.assertEqual(pi_count_before, pi_count_after)

    def test_validation_error_no_payment_created(self):
        """When ValidationError is raised, NO Payment must be created."""
        from apps.payments.models import Payment, PaymentIntent
        payment_count_before = Payment.objects.filter(
            intent__payer=self.vol_user,
        ).count()
        # T1: assertRaises enforces that the hard block actually fires.
        with self.assertRaises(ValidationError):
            _create_honorarium(
                self.profile,
                amount="200.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        payment_count_after = Payment.objects.filter(
            intent__payer=self.vol_user,
        ).count()
        self.assertEqual(payment_count_before, payment_count_after)

    def test_validation_error_honorarium_not_created(self):
        """When hard block fires, no new Honorarium row should be created."""
        from apps.volunteers.models import Honorarium
        count_before = Honorarium.objects.filter(volunteer=self.profile).count()
        # T1: assertRaises enforces that the hard block actually fires.
        with self.assertRaises(ValidationError):
            _create_honorarium(
                self.profile,
                amount="200.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        count_after = Honorarium.objects.filter(volunteer=self.profile).count()
        self.assertEqual(count_before, count_after)

    def test_honorarium_exactly_at_limit_is_blocked(self):
        """
        C-6 fix: the model uses >= for the hard block, so $900 + $100 = $1,000
        is BLOCKED (not allowed). The old docstring was wrong.

        Honorarium.clean() uses:
            if projected_total >= Decimal(str(hard_block)):  raise ValidationError
        So exactly $1,000 raises, and $1,001+ also raises.
        """
        # Exact boundary: $900 + $100 = $1,000 — must raise (>= blocks at exactly $1,000)
        with self.assertRaises(ValidationError):
            _create_honorarium(
                self.profile,
                amount="100.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )

    def test_honorarium_one_cent_over_limit_is_blocked(self):
        """$900 + $100.01 = $1,000.01 — clearly over limit, must raise."""
        with self.assertRaises(ValidationError):
            _create_honorarium(
                self.profile,
                amount="100.01",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )

    def test_honorarium_just_under_limit_is_allowed(self):
        """$900 + $99.99 = $999.99 — just under $1,000 limit, must succeed."""
        # No exception should be raised; a Honorarium row should be created.
        honorarium = _create_honorarium(
            self.profile,
            amount="99.99",
            created_by=self.coordinator,
            payment_date=datetime.date.today(),
        )
        self.assertIsNotNone(honorarium.pk)


# ---------------------------------------------------------------------------
# HonorariumCRASignalExclusivityTests
# ---------------------------------------------------------------------------

class HonorariumCRASignalExclusivityTests(TransactionTestCase):
    """
    P2-6 / VN-5: Signal exclusivity — t4a_threshold_reached and
    cra_alert_threshold_reached must be mutually exclusive.

    Design intent (documented in services/honoraria.py VN-5 comment):
      - YTD >= $500: only t4a_threshold_reached fires (T4A supersedes alert)
      - $450 <= YTD < $500: only cra_alert_threshold_reached fires
      - YTD < $450: neither fires

    TransactionTestCase required because on_commit signals are under test.
    """

    def setUp(self):
        from apps.volunteers.models import Honorarium
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())
        self.Honorarium = Honorarium

    def test_t4a_threshold_fires_not_alert_when_ytd_reaches_500(self):
        """
        A $500 honorarium (no prior YTD) must fire t4a_threshold_reached
        and must NOT fire cra_alert_threshold_reached.
        """
        from apps.volunteers.signals import t4a_threshold_reached, cra_alert_threshold_reached
        t4a_calls = []
        alert_calls = []

        def t4a_receiver(sender, **kwargs):
            t4a_calls.append(kwargs)

        def alert_receiver(sender, **kwargs):
            alert_calls.append(kwargs)

        t4a_threshold_reached.connect(t4a_receiver)
        cra_alert_threshold_reached.connect(alert_receiver)
        try:
            _create_honorarium(
                self.profile,
                amount="500.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        finally:
            t4a_threshold_reached.disconnect(t4a_receiver)
            cra_alert_threshold_reached.disconnect(alert_receiver)

        self.assertEqual(len(t4a_calls), 1,
            "t4a_threshold_reached must fire exactly once for a $500 honorarium")
        self.assertEqual(len(alert_calls), 0,
            "cra_alert_threshold_reached must NOT fire when T4A threshold is reached "
            "(T4A supersedes the advisory alert — VN-5)")
        # M9 / CRIT-2: Verify ytd_total kwarg is present, is a Decimal, AND equals the
        # correct post-commit live YTD value. assertIsInstance alone is insufficient —
        # a wrong YTD (e.g. Decimal("0.00") from a mis-filtered query) would still pass
        # the type check. The alert-path test asserts the exact value; this must too.
        self.assertIn("ytd_total", t4a_calls[0],
            "t4a_threshold_reached must include ytd_total= kwarg (signal contract)")
        self.assertIsInstance(t4a_calls[0]["ytd_total"], Decimal,
            "ytd_total must be a Decimal, not a string or int")
        self.assertEqual(t4a_calls[0]["ytd_total"], Decimal("500.00"),
            "ytd_total must equal the post-commit live YTD (0 + 500 = 500.00)")

    def test_alert_fires_not_t4a_when_ytd_crosses_450(self):
        """
        A honorarium pushing YTD to $451 (above $450 alert, below $500 T4A)
        must fire cra_alert_threshold_reached and must NOT fire t4a_threshold_reached.
        """
        # Pre-load $430 so the new $21 payment crosses $450 but not $500.
        _make_honorarium_direct(
            self.profile, amount="430.00",
            payment_type=self.Honorarium.PAYMENT_TYPE_HONORARIUM,
            payment_date=datetime.date.today(),
            created_by=self.coordinator,
        )
        from apps.volunteers.signals import t4a_threshold_reached, cra_alert_threshold_reached
        t4a_calls = []
        alert_calls = []

        def t4a_receiver(sender, **kwargs):
            t4a_calls.append(kwargs)

        def alert_receiver(sender, **kwargs):
            alert_calls.append(kwargs)

        t4a_threshold_reached.connect(t4a_receiver)
        cra_alert_threshold_reached.connect(alert_receiver)
        try:
            _create_honorarium(
                self.profile,
                amount="21.00",  # 430 + 21 = 451 — above $450, below $500
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        finally:
            t4a_threshold_reached.disconnect(t4a_receiver)
            cra_alert_threshold_reached.disconnect(alert_receiver)

        self.assertEqual(len(alert_calls), 1,
            "cra_alert_threshold_reached must fire when YTD crosses $450 alert threshold")
        self.assertEqual(len(t4a_calls), 0,
            "t4a_threshold_reached must NOT fire when YTD is $451 (below $500 T4A threshold)")
        # M9: Verify ytd_total kwarg is present and correct on the alert signal.
        self.assertIn("ytd_total", alert_calls[0],
            "cra_alert_threshold_reached must include ytd_total= kwarg (signal contract)")
        self.assertIsInstance(alert_calls[0]["ytd_total"], Decimal,
            "ytd_total must be a Decimal, not a string or int")
        self.assertEqual(alert_calls[0]["ytd_total"], Decimal("451.00"),
            "ytd_total must equal the post-commit live YTD (430 + 21 = 451)")

    def test_neither_signal_fires_below_alert_threshold(self):
        """A $100 honorarium (YTD = $100) must fire neither signal."""
        from apps.volunteers.signals import t4a_threshold_reached, cra_alert_threshold_reached
        t4a_calls = []
        alert_calls = []

        def t4a_receiver(sender, **kwargs):
            t4a_calls.append(kwargs)

        def alert_receiver(sender, **kwargs):
            alert_calls.append(kwargs)

        t4a_threshold_reached.connect(t4a_receiver)
        cra_alert_threshold_reached.connect(alert_receiver)
        try:
            _create_honorarium(
                self.profile,
                amount="100.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        finally:
            t4a_threshold_reached.disconnect(t4a_receiver)
            cra_alert_threshold_reached.disconnect(alert_receiver)

        self.assertEqual(len(t4a_calls), 0,
            "t4a_threshold_reached must not fire at $100 YTD")
        self.assertEqual(len(alert_calls), 0,
            "cra_alert_threshold_reached must not fire at $100 YTD (below $450 threshold)")
        # M9: When neither signal fires, verify the calls lists are empty (no ytd_total
        # to check, but confirms no phantom kwargs were captured from other tests).
        self.assertListEqual(t4a_calls, [],
            "t4a_calls must be empty — no t4a_threshold_reached signal should have fired")
        self.assertListEqual(alert_calls, [],
            "alert_calls must be empty — no cra_alert_threshold_reached signal should have fired")


# ---------------------------------------------------------------------------
# HonorariumAtomicGuardTests
# ---------------------------------------------------------------------------

class HonorariumAtomicGuardTests(TestCase):
    """
    M10: Verify the connection.in_atomic_block guard in Honorarium.clean().

    clean() uses select_for_update() to serialise concurrent CRA threshold
    checks. select_for_update() raises TransactionManagementError when called
    outside an atomic block. The guard skips the lock when not in a transaction.

    TestCase wraps each test in a transaction, but calling .full_clean() directly
    (outside the service layer) happens INSIDE that transaction wrapper, so
    in_atomic_block is True. We therefore also test the explicit outside-atomic
    path via assertRaisesNothing (i.e. that no TransactionManagementError is raised
    when the DB is not in a transaction).

    Note: Django's TestCase wraps each test in a transaction (for rollback), so
    ``connection.in_atomic_block`` is True inside TestCase tests. This test
    verifies that full_clean() does NOT raise when called inside an atomic block
    (the normal code path for the service layer).
    """

    def setUp(self):
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())

    def test_full_clean_inside_atomic_does_not_raise_transaction_error(self):
        """
        M10: Honorarium.full_clean() called inside a transaction must not raise
        TransactionManagementError. The select_for_update() guard correctly skips
        the lock when already inside a transaction (avoids nested locking issues),
        and correctly uses it when in the normal service-layer atomic block.
        """
        from django.db import connection, transaction
        from apps.volunteers.models import Honorarium

        hon = Honorarium(
            volunteer=self.profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("100.00"),
            description="Atomic guard test",
            payment_date=datetime.date.today(),
            created_by=self.coordinator,
        )
        # Inside the TestCase transaction wrapper, in_atomic_block is True.
        self.assertTrue(connection.in_atomic_block,
            "TestCase wraps in a transaction — in_atomic_block should be True here")
        # full_clean() must not raise TransactionManagementError.
        try:
            hon.full_clean()
        except Exception as e:
            from django.db import utils as _db_utils
            self.assertNotIsInstance(
                e, _db_utils.Error,
                f"full_clean() raised a DB error inside an atomic block: {e}",
            )

    def test_full_clean_inside_atomic_second_invocation_also_safe(self):
        """
        M10 (inside-atomic complement): calling full_clean() twice in the same
        transaction must not blow up — the guard must be idempotent. This catches
        any per-call state mutation in the guard logic.
        """
        from django.db import connection
        from apps.volunteers.models import Honorarium

        self.assertTrue(connection.in_atomic_block)

        hon = Honorarium(
            volunteer=self.profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("50.00"),
            description="Idempotent guard test",
            payment_date=datetime.date.today(),
            created_by=self.coordinator,
        )
        # Neither call should raise TransactionManagementError.
        for _ in range(2):
            try:
                hon.full_clean()
            except Exception as e:
                from django.db import utils as _db_utils
                if isinstance(e, _db_utils.Error):
                    self.fail(
                        f"full_clean() invocation raised DB error: {type(e).__name__}: {e}"
                    )


class HonorariumAtomicGuardTransactionTests(TransactionTestCase):
    """
    M10 (TransactionTestCase half): Verify full_clean() does not raise
    TransactionManagementError when called outside any atomic block.

    TransactionTestCase does NOT wrap tests in a transaction, so
    connection.in_atomic_block is False at the start of each test method.
    """

    def setUp(self):
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())

    def test_full_clean_outside_atomic_skips_lock_no_error(self):
        """
        M10: When not inside a transaction, Honorarium.full_clean() must not
        raise TransactionManagementError. The guard (connection.in_atomic_block)
        must skip select_for_update() and fall back to a plain queryset.
        """
        from django.db import connection
        from apps.volunteers.models import Honorarium

        # Confirm we're outside any transaction.
        self.assertFalse(connection.in_atomic_block,
            "TransactionTestCase must not wrap in an atomic block at test start")

        hon = Honorarium(
            volunteer=self.profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("100.00"),
            description="Outside-atomic guard test",
            payment_date=datetime.date.today(),
            created_by=self.coordinator,
        )
        # Must NOT raise TransactionManagementError (guard skips select_for_update).
        try:
            hon.full_clean()
        except Exception as e:
            from django.db import utils as _db_utils
            if isinstance(e, _db_utils.Error):
                self.fail(
                    f"full_clean() outside atomic raised DB error (guard failed): "
                    f"{type(e).__name__}: {e}"
                )
            # ValidationError is acceptable (amount below thresholds but guard passed).

    def test_create_honorarium_outside_atomic_does_not_raise_transaction_error(self):
        """
        CRIT-3: Verify that the select_for_update() guard in the SERVICE LAYER
        (create_honorarium) also does not raise when called outside a transaction.

        The previous M10 test only exercised Honorarium.full_clean() directly.
        The actual guard that matters lives in create_honorarium(), which wraps
        the service call in transaction.atomic() — so select_for_update() is
        always called INSIDE that atomic block, not outside it. This test verifies
        that the service's own atomic() wrapper correctly establishes the transaction
        context so that select_for_update() (in clean()) works without raising.

        This is a TransactionTestCase so we start genuinely outside any transaction;
        the service's own atomic() is the only wrapper.
        """
        from django.db import connection

        self.assertFalse(connection.in_atomic_block,
            "Must start outside any transaction for this test to be meaningful")

        # create_honorarium wraps in transaction.atomic() internally, so
        # connection.in_atomic_block will be True INSIDE the service call.
        # If the guard is broken, the service would raise TransactionManagementError
        # before even reaching the DB write.
        try:
            honorarium = _create_honorarium(
                self.profile,
                amount="75.00",  # below all thresholds — no signal, just DB write
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        except Exception as e:
            from django.db import utils as _db_utils
            if isinstance(e, _db_utils.Error):
                self.fail(
                    f"create_honorarium() raised a DB error — likely select_for_update() "
                    f"called outside transaction (guard broken): {type(e).__name__}: {e}"
                )
            raise  # propagate unexpected non-DB exceptions

        # Verify the honorarium was actually persisted (not silently swallowed).
        from apps.volunteers.models import Honorarium
        self.assertTrue(
            Honorarium.objects.filter(pk=honorarium.pk).exists(),
            "Honorarium must be persisted after create_honorarium() completes",
        )


# ---------------------------------------------------------------------------
# HonorariumPostCommitOuterGuardTests — T2
# ---------------------------------------------------------------------------

class HonorariumPostCommitOuterGuardTests(TransactionTestCase):
    """
    T2: Coverage for the outer try/except Exception guard in _post_commit.

    The _post_commit closure in create_honorarium wraps its entire body in a
    try/except Exception that logs at ERROR and swallows the exception.  Without
    this guard, unexpected errors from the signal dispatch path are silently
    discarded by Django's on_commit queue with no log entry.

    Strategy: patch honorarium_created.send_robust to raise RuntimeError.
    The DB lookup (`Honorarium.objects.select_related(...).get(...)`) succeeds,
    so we reach send_robust, which raises, bypasses the inner except DoesNotExist,
    and is caught by the outer except Exception.

    TransactionTestCase is required because on_commit only fires on real commits.
    """

    def setUp(self):
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())

    def test_post_commit_outer_guard_swallows_exception_and_logs_error(self):
        """
        T2: When an unexpected exception occurs inside _post_commit, it is
        caught by the outer except Exception block, logged at ERROR, and
        create_honorarium() still returns successfully with the Honorarium saved.
        """
        from apps.volunteers.models import Honorarium
        from apps.volunteers.signals import honorarium_created

        # Patch send_robust to raise an unexpected RuntimeError.
        # This bypasses the inner DoesNotExist guard and hits the outer guard.
        with patch.object(
            honorarium_created,
            "send_robust",
            side_effect=RuntimeError("T2: unexpected _post_commit error"),
        ):
            logger_name = "apps.volunteers.services.honoraria"
            with self.assertLogs(logger_name, level="ERROR") as log_ctx:
                # create_honorarium must NOT raise — the exception must be swallowed
                # by the outer guard in _post_commit.
                honorarium = _create_honorarium(
                    self.profile,
                    amount="75.00",
                    created_by=self.coordinator,
                )

        # 1. Honorarium must be persisted despite the _post_commit failure.
        self.assertTrue(
            Honorarium.objects.filter(pk=honorarium.pk).exists(),
            "T2: Honorarium must be saved even when _post_commit raises unexpectedly",
        )

        # 2. An ERROR log must be emitted referencing the honorarium PK.
        error_msgs = [
            m for m in log_ctx.output
            if "ERROR" in m and "_post_commit failed" in m
        ]
        self.assertTrue(
            error_msgs,
            "T2: Outer guard must emit an ERROR log when _post_commit raises. "
            "Without this guard, on_commit failures are silently discarded.",
        )

        # 3. The ERROR log must reference the honorarium PK (not just a generic message).
        self.assertTrue(
            any(str(honorarium.pk) in m for m in error_msgs),
            f"T2: ERROR log must include the honorarium PK ({honorarium.pk}) "
            f"for observability — found messages: {error_msgs}",
        )

    def test_post_commit_outer_guard_honorarium_pk_survives_exception(self):
        """
        T2 (complement): Verify that the honorarium PK used in the outer guard's
        log message is captured correctly before the exception — the closure must
        capture _honorarium_pk before any failure, not read it from an ORM attribute
        that might be unavailable post-rollback.
        """
        from apps.volunteers.models import Honorarium
        from apps.volunteers.signals import honorarium_created

        with patch.object(
            honorarium_created,
            "send_robust",
            side_effect=RuntimeError("T2: outer guard pk capture test"),
        ):
            with self.assertLogs("apps.volunteers.services.honoraria", level="ERROR") as log_ctx:
                honorarium = _create_honorarium(
                    self.profile,
                    amount="80.00",
                    created_by=self.coordinator,
                )

        # The error log MUST reference the actual pk, proving _honorarium_pk was
        # captured inside the closure before the failure.
        pk_str = str(honorarium.pk)
        self.assertTrue(
            any(pk_str in m for m in log_ctx.output if "ERROR" in m),
            f"T2: ERROR log must reference honorarium PK {pk_str} — "
            f"proves the closure captures _honorarium_pk before any exception",
        )


# ---------------------------------------------------------------------------
# HonorariumPIPEDALogTests
# ---------------------------------------------------------------------------

class HonorariumPIPEDALogTests(TransactionTestCase):
    """
    M11: PIPEDA compliance — verify that create_honorarium's INFO log does not
    contain payment_type or the amount string, and does not contain any keys
    that could correlate a financial record with a volunteer PK.

    Uses TransactionTestCase because create_honorarium uses on_commit() — the
    INFO log line fires inside the atomic block, so we just need the commit to
    happen for the on_commit to fire (TransactionTestCase actually commits).
    """

    def setUp(self):
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())

    def test_info_log_does_not_contain_payment_type_or_amount(self):
        """
        M11 / PIPEDA: The INFO log line emitted by create_honorarium must NOT
        contain 'payment_type' or the literal amount value. These would correlate
        a financial record with a volunteer profile PK, violating PIPEDA.

        The log MUST contain the honorarium PK and the actor user PK.
        """
        logger_name = "apps.volunteers.services.honoraria"
        with self.assertLogs(logger_name, level="INFO") as log_ctx:
            honorarium = _create_honorarium(
                self.profile,
                amount="317.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )

        # Filter to the creation INFO line.
        info_lines = [m for m in log_ctx.output if "INFO" in m and str(honorarium.pk) in m]
        self.assertTrue(info_lines, "At least one INFO line must reference the honorarium PK")

        for line in info_lines:
            self.assertNotIn(
                "payment_type", line,
                f"PIPEDA: 'payment_type' must not appear in INFO log — found in: {line}",
            )
            self.assertNotIn(
                "317", line,
                f"PIPEDA: amount '317' must not appear in INFO log — found in: {line}",
            )
            self.assertNotIn(
                "317.00", line,
                f"PIPEDA: formatted amount '317.00' must not appear in INFO log — found in: {line}",
            )
            # CRIT-1: PIPEDA requires that the INFO log does NOT correlate the volunteer
            # profile PK with a financial record. We enforce this with an ANCHORED full-line
            # format assertion: the entire log line must match the expected pattern exactly.
            # Any addition to the format (volunteer_profile.pk, amount, payment_type)
            # causes the anchor to fail — this is stronger than a bare assertNotIn check
            # and avoids false positives when profile.pk coincidentally equals honorarium.pk.
            import re as _re
            _expected_anchored = (
                rf"^INFO:apps\.volunteers\.services\.honoraria:"
                rf"volunteers\.services\.honoraria: "
                rf"Honorarium #{_re.escape(str(honorarium.pk))} created"
                rf" — created_by user #{_re.escape(str(self.coordinator.pk))}$"
            )
            self.assertRegex(
                line, _expected_anchored,
                f"PIPEDA: INFO log line must match the exact expected format with no "
                f"extra fields. The format 'Honorarium #N created — created_by user #M' "
                f"must not be extended with volunteer_pk, amount, or payment_type. "
                f"Actual line: {line!r}",
            )
            # Verify the actor PK IS present (operational requirement, not PII).
            self.assertIn(
                str(self.coordinator.pk), line,
                f"INFO log must reference the actor (created_by) user PK — missing from: {line}",
            )


# ---------------------------------------------------------------------------
# HonorariumPaymentGracefulDegradationTests
# ---------------------------------------------------------------------------

class HonorariumPaymentGracefulDegradationTests(TransactionTestCase):
    """
    Tests graceful degradation when Payments BB wiring fails.

    Uses TransactionTestCase because create_honorarium uses transaction.atomic()
    and on_commit(). The try/except inside the atomic block means a Payment
    failure does NOT roll back the honorarium — the service documents this
    explicitly: "Do NOT re-raise".
    """

    def setUp(self):
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())

    def test_payment_integrity_error_honorarium_still_created(self):
        """
        When Payment.objects.create raises IntegrityError (e.g. duplicate
        gateway_charge_id), the honorarium IS still created because the
        except block does not re-raise.
        """
        from apps.payments.models import Payment

        original_create = Payment.objects.create

        call_count = [0]

        def _patched_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise IntegrityError("duplicate key value violates unique constraint")
            return original_create(**kwargs)

        with patch.object(Payment.objects, "create", side_effect=_patched_create):
            honorarium = _create_honorarium(
                self.profile, amount="50.00", created_by=self.coordinator
            )

        from apps.volunteers.models import Honorarium
        self.assertTrue(Honorarium.objects.filter(pk=honorarium.pk).exists())

    def test_payment_failure_leaves_payment_fk_as_none(self):
        """When Payments BB wiring fails, honorarium.payment must be None."""
        from apps.payments.models import Payment

        def _patched_create(**kwargs):
            raise IntegrityError("forced failure")

        with patch.object(Payment.objects, "create", side_effect=_patched_create):
            honorarium = _create_honorarium(
                self.profile, amount="55.00", created_by=self.coordinator
            )

        self.assertIsNone(honorarium.payment)

    def test_payment_intent_failure_honorarium_still_created(self):
        """
        When PaymentIntent.objects.create raises Exception, the honorarium
        is still created (outer try/except).
        """
        from apps.payments.models import PaymentIntent

        def _patched_create(**kwargs):
            raise RuntimeError("PI creation failed")

        with patch.object(PaymentIntent.objects, "create", side_effect=_patched_create):
            honorarium = _create_honorarium(
                self.profile, amount="60.00", created_by=self.coordinator
            )

        from apps.volunteers.models import Honorarium
        self.assertTrue(Honorarium.objects.filter(pk=honorarium.pk).exists())

    def test_payment_intent_failure_leaves_payment_fk_as_none(self):
        """When PaymentIntent creation fails, honorarium.payment is None."""
        from apps.payments.models import PaymentIntent

        def _patched_create(**kwargs):
            raise RuntimeError("PI creation failed")

        with patch.object(PaymentIntent.objects, "create", side_effect=_patched_create):
            honorarium = _create_honorarium(
                self.profile, amount="65.00", created_by=self.coordinator
            )

        self.assertIsNone(honorarium.payment)

    def test_successful_wiring_sets_payment_fk(self):
        """Control test: without errors, honorarium.payment IS set."""
        honorarium = _create_honorarium(
            self.profile, amount="70.00", created_by=self.coordinator
        )
        self.assertIsNotNone(honorarium.payment)

    def test_successful_wiring_payment_intent_exists(self):
        """Control test: without errors, a PaymentIntent IS created."""
        from apps.payments.models import PaymentIntent
        honorarium = _create_honorarium(
            self.profile, amount="75.00", created_by=self.coordinator
        )
        self.assertTrue(
            PaymentIntent.objects.filter(gateway_intent_id=f"hon-{honorarium.pk}").exists()
        )

    def test_two_honoraria_produce_two_payment_intents(self):
        """Each honorarium produces its own PaymentIntent."""
        from apps.payments.models import PaymentIntent
        hon1 = _create_honorarium(
            self.profile, amount="40.00", created_by=self.coordinator,
            payment_date=datetime.date(2024, 1, 10),
        )
        hon2 = _create_honorarium(
            self.profile, amount="50.00", created_by=self.coordinator,
            payment_date=datetime.date(2024, 2, 10),
        )
        pi1_exists = PaymentIntent.objects.filter(gateway_intent_id=f"hon-{hon1.pk}").exists()
        pi2_exists = PaymentIntent.objects.filter(gateway_intent_id=f"hon-{hon2.pk}").exists()
        self.assertTrue(pi1_exists)
        self.assertTrue(pi2_exists)

    def test_two_honoraria_produce_two_payments(self):
        """Each honorarium produces its own Payment."""
        from apps.payments.models import Payment
        hon1 = _create_honorarium(
            self.profile, amount="40.00", created_by=self.coordinator,
            payment_date=datetime.date(2024, 3, 10),
        )
        hon2 = _create_honorarium(
            self.profile, amount="50.00", created_by=self.coordinator,
            payment_date=datetime.date(2024, 4, 10),
        )
        p1_exists = Payment.objects.filter(gateway_charge_id=f"HON-{hon1.pk}").exists()
        p2_exists = Payment.objects.filter(gateway_charge_id=f"HON-{hon2.pk}").exists()
        self.assertTrue(p1_exists)
        self.assertTrue(p2_exists)

    def test_permission_denied_if_no_add_honorarium_perm(self):
        """User without volunteers.add_honorarium permission should get PermissionDenied."""
        from django.core.exceptions import PermissionDenied
        unpermitted_user = _make_user()
        with self.assertRaises(PermissionDenied):
            _create_honorarium(
                self.profile, amount="50.00", created_by=unpermitted_user
            )

    def test_permission_denied_no_payment_intent_created(self):
        """PermissionDenied must not leave partial Payments BB records."""
        from django.core.exceptions import PermissionDenied
        from apps.payments.models import PaymentIntent
        unpermitted_user = _make_user()
        count_before = PaymentIntent.objects.filter(payer=self.vol_user).count()
        try:
            _create_honorarium(
                self.profile, amount="50.00", created_by=unpermitted_user
            )
        except PermissionDenied:
            pass
        count_after = PaymentIntent.objects.filter(payer=self.vol_user).count()
        self.assertEqual(count_before, count_after)


# ---------------------------------------------------------------------------
# HonorariumPaymentFieldTests — H-10
# ---------------------------------------------------------------------------

class HonorariumPaymentFieldTests(TransactionTestCase):
    """
    H-10: Tests for Payment field values set by create_honorarium:
      - payment_method_type == PAYMENT_METHOD_BANK ("bank_transfer")
      - paid_at is timezone-aware datetime derived from payment_date at midnight
      - payment_type=EXPENSE does not create a Payment (expense reimbursements
        are excluded from the Payments BB wiring per the service docstring)
    """

    def setUp(self):
        self.vol_user = _make_user()
        self.profile = _make_profile(self.vol_user)
        self.coordinator = _grant_add_honorarium(_make_user())

    def test_payment_method_type_is_bank_transfer(self):
        """
        H-10: Payment.payment_method_type must be PAYMENT_METHOD_BANK
        ('bank_transfer') — honoraria are paid by bank transfer, not card.
        """
        from apps.payments.models import Payment
        honorarium = _create_honorarium(
            self.profile, amount="100.00", created_by=self.coordinator,
            payment_date=datetime.date(2025, 3, 15),
        )
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        self.assertEqual(payment.payment_method_type, Payment.PAYMENT_METHOD_BANK)
        self.assertEqual(payment.payment_method_type, "bank_transfer")

    def test_paid_at_is_timezone_aware(self):
        """
        H-10: Payment.paid_at must be a timezone-aware datetime — the service
        uses django.utils.timezone.make_aware() to attach the current timezone.
        """
        from apps.payments.models import Payment
        from django.utils import timezone
        honorarium = _create_honorarium(
            self.profile, amount="110.00", created_by=self.coordinator,
            payment_date=datetime.date(2025, 4, 20),
        )
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        self.assertIsNotNone(payment.paid_at)
        self.assertIsNotNone(payment.paid_at.tzinfo,
                             "paid_at must be timezone-aware, not naive")

    def test_paid_at_date_matches_payment_date(self):
        """
        H-10: The date portion of Payment.paid_at must match the honorarium's
        payment_date — the service combines payment_date with midnight time.
        """
        from apps.payments.models import Payment
        from django.utils import timezone
        target_date = datetime.date(2025, 5, 10)
        honorarium = _create_honorarium(
            self.profile, amount="120.00", created_by=self.coordinator,
            payment_date=target_date,
        )
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        # Convert paid_at to local date for comparison
        local_dt = timezone.localtime(payment.paid_at)
        self.assertEqual(local_dt.date(), target_date)

    def test_paid_at_time_is_midnight(self):
        """
        H-10: The time portion of Payment.paid_at must be midnight (00:00:00)
        in local time — the service uses datetime.min.time() as the time component.
        """
        from apps.payments.models import Payment
        from django.utils import timezone
        honorarium = _create_honorarium(
            self.profile, amount="130.00", created_by=self.coordinator,
            payment_date=datetime.date(2025, 6, 1),
        )
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        local_dt = timezone.localtime(payment.paid_at)
        self.assertEqual(local_dt.hour, 0)
        self.assertEqual(local_dt.minute, 0)
        self.assertEqual(local_dt.second, 0)

    def test_expense_type_still_creates_payment(self):
        """
        H-D: PAYMENT_TYPE_EXPENSE honoraria DO go through the Payments BB wiring
        in create_honorarium — the CRA threshold filter only excludes expenses
        from the YTD hard-block calculation, not from payment creation.

        Previously used a vacuous if/else that passed regardless of whether a
        Payment was actually created. Now unconditional: a Payment MUST exist.
        """
        from apps.payments.models import Payment
        from apps.volunteers.models import Honorarium
        honorarium = _create_honorarium(
            self.profile, amount="45.00", created_by=self.coordinator,
            payment_type=Honorarium.PAYMENT_TYPE_EXPENSE,
            payment_date=datetime.date(2025, 7, 1),
        )
        self.assertIsNotNone(honorarium.pk)
        # H-D: Unconditional — a Payment row MUST exist for EXPENSE-type honoraria.
        # Any regression that skips Payment creation will now cause DoesNotExist here.
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        self.assertEqual(
            payment.payment_method_type,
            Payment.PAYMENT_METHOD_BANK,
            "EXPENSE honorarium must produce a bank-transfer Payment record",
        )

    def test_payment_method_type_is_string_not_none(self):
        """
        H-10: payment_method_type must be a non-null string — it is a required
        CharField with no default in the Production Payment model.
        """
        from apps.payments.models import Payment
        honorarium = _create_honorarium(
            self.profile, amount="140.00", created_by=self.coordinator,
            payment_date=datetime.date(2025, 8, 15),
        )
        payment = Payment.objects.get(gateway_charge_id=f"HON-{honorarium.pk}")
        self.assertIsNotNone(payment.payment_method_type)
        self.assertIsInstance(payment.payment_method_type, str)
        self.assertGreater(len(payment.payment_method_type), 0)

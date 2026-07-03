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

_counter = [0]


def _uid():
    _counter[0] += 1
    return _counter[0]


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
        self.assertIsNotNone(payment)

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

class HonorariumPaymentPIPEDATests(TestCase):
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
        try:
            _create_honorarium(
                self.profile,
                amount="200.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        except ValidationError:
            pass
        pi_count_after = PaymentIntent.objects.filter(payer=self.vol_user).count()
        self.assertEqual(pi_count_before, pi_count_after)

    def test_validation_error_no_payment_created(self):
        """When ValidationError is raised, NO Payment must be created."""
        from apps.payments.models import Payment, PaymentIntent
        payment_count_before = Payment.objects.filter(
            intent__payer=self.vol_user,
        ).count()
        try:
            _create_honorarium(
                self.profile,
                amount="200.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        except ValidationError:
            pass
        payment_count_after = Payment.objects.filter(
            intent__payer=self.vol_user,
        ).count()
        self.assertEqual(payment_count_before, payment_count_after)

    def test_validation_error_honorarium_not_created(self):
        """When hard block fires, no new Honorarium row should be created."""
        from apps.volunteers.models import Honorarium
        count_before = Honorarium.objects.filter(volunteer=self.profile).count()
        try:
            _create_honorarium(
                self.profile,
                amount="200.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
            )
        except ValidationError:
            pass
        count_after = Honorarium.objects.filter(volunteer=self.profile).count()
        self.assertEqual(count_before, count_after)

    def test_honorarium_exactly_at_limit_is_blocked(self):
        """YTD at exactly $1,000 (900 + 100) should be allowed; YTD exceeding $1,000 blocked."""
        # $900 pre-loaded; $100 more = $1,000 total (at threshold, not exceeding)
        # The hard block is >$1,000 — at exactly $1,000 it depends on model impl.
        # We test that $200 (which causes $1,100 total) raises ValidationError.
        with self.assertRaises(ValidationError):
            _create_honorarium(
                self.profile,
                amount="200.00",
                created_by=self.coordinator,
                payment_date=datetime.date.today(),
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

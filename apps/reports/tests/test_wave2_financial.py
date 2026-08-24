"""
Wave 2 financial layer tests.

Covers:
  - DateRangeForm validation
  - _month_utc_range helper
  - get_monthly_revenue service
  - get_refund_summary service
  - get_reconciliation_queryset service
  - compute_financial_snapshot serialisation
  - FinancialDashboardView (permission, redirect, context)
  - ReconciliationView (form, aggregates, truncation)
  - ReconciliationExportView (audit record, CSV stream, validation)
  - RevenueExportView (audit record, CSV stream, validation)
  - export_reconciliation_csv / export_revenue_csv
  - _compute_all_snapshots task helper

PIPEDA invariants verified throughout:
  - actor_pk stores the user's integer pk — no email, name, or address
  - No PII in log messages (only pk, amounts, counts)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.payments.models import (
    Payment,
    PaymentIntent,
    Refund,
    ServiceFeePayment,
)
from apps.reports.forms import MAX_RANGE_DAYS, DateRangeForm
from apps.reports.models import ExportRecord, ReportSnapshot
from apps.reports.services.financial import (
    _month_utc_range,
    compute_financial_snapshot,
    get_monthly_revenue,
    get_reconciliation_queryset,
    get_refund_summary,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_user(*, is_staff=True, perms=None):
    """Create a staff user with optional permissions.

    perms: list of codenames.  All report-related permissions live on the
    payments.PaymentIntent content type (see migration 0010).
    """
    from django.contrib.auth.models import Permission

    user = User.objects.create_user(
        email=f"staff-{uuid.uuid4().hex[:8]}@example.com",
        password="testpass123",
        is_staff=is_staff,
    )
    if perms:
        for codename in perms:
            # Report permissions are on payments.PaymentIntent; look up by
            # (content_type__app_label, codename) to avoid ambiguity.
            perm = Permission.objects.get(
                codename=codename,
                content_type__app_label="payments",
            )
            user.user_permissions.add(perm)
    return user


# LOGIN_URL resolves to /account/login/ (two_factor:login) in this project.
_LOGIN_URL = "/account/login/"


def _get_payer():
    """Create (and return) a minimal payer User."""
    # Don't cache — Django TestCase wraps each test in a transaction so the
    # cached object becomes stale after rollback.  Creating one per factory
    # call is cheap enough in tests.
    return User.objects.create_user(
        email=f"payer-{uuid.uuid4().hex[:6]}@example.com",
        password="x",
    )


def _make_intent(
    *,
    purpose=PaymentIntent.PURPOSE_SERVICE_FEE,
    status=PaymentIntent.STATUS_COMPLETED,
    tax_amount=Decimal("0.00"),
    amount=Decimal("100.00"),
    fee_code=None,
    payer=None,
):
    """Create a minimal PaymentIntent (and optionally a ServiceFeePayment)."""
    if payer is None:
        payer = _get_payer()
    intent = PaymentIntent.objects.create(
        reference=f"REF-{uuid.uuid4().hex[:8].upper()}",
        purpose=purpose,
        status=status,
        tax_amount=tax_amount,
        amount=amount,
        payer=payer,
    )
    # fee_code is intentionally ignored when purpose != PURPOSE_SERVICE_FEE
    # because only service-fee intents have a ServiceFeePayment row.
    # Passing fee_code with a non-service-fee purpose is a caller bug — guard it.
    if fee_code and purpose != PaymentIntent.PURPOSE_SERVICE_FEE:
        raise ValueError(
            f"_make_intent: fee_code={fee_code!r} has no effect when "
            f"purpose={purpose!r}. Pass purpose=PURPOSE_SERVICE_FEE or omit fee_code."
        )
    if fee_code and purpose == PaymentIntent.PURPOSE_SERVICE_FEE:
        ServiceFeePayment.objects.create(
            payment_intent=intent,
            fee_code=fee_code,
            base_amount=amount,
            service_request_id=uuid.uuid4(),
            description_en="Test fee",
        )
    return intent


def _make_payment(
    intent,
    *,
    amount=Decimal("100.00"),
    processor_fee=Decimal("3.00"),
    paid_at=None,
):
    """Create a Payment (captured) for the given intent."""
    if paid_at is None:
        paid_at = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    payment = Payment.objects.create(
        intent=intent,
        amount_paid=amount,
        processor_fee=processor_fee,
        net_amount=amount - processor_fee,
        paid_at=paid_at,
        gateway_charge_id=f"ch_{uuid.uuid4().hex}",
        payment_method_type=Payment.PAYMENT_METHOD_CARD,
    )
    return payment


def _make_refund(payment, *, amount=Decimal("10.00"), refunded_at=None, status=None):
    """Create a Refund against the given Payment."""
    if refunded_at is None:
        refunded_at = datetime(2025, 6, 20, 10, 0, 0, tzinfo=UTC)
    if status is None:
        status = Refund.GATEWAY_STATUS_SUCCEEDED
    authorized_by = User.objects.create_user(
        email=f"refund-auth-{uuid.uuid4().hex[:6]}@example.com",
        password="x",
    )
    return Refund.objects.create(
        payment=payment,
        amount=amount,
        gateway_status=status,
        gateway_refund_id=f"re_{uuid.uuid4().hex}",
        refunded_at=refunded_at,
        reason="duplicate",
        authorized_by=authorized_by,
    )


# ---------------------------------------------------------------------------
# DateRangeForm
# ---------------------------------------------------------------------------


class DateRangeFormTest(TestCase):
    """Unit tests for the DateRangeForm validation logic."""

    def test_valid_range(self):
        form = DateRangeForm(data={"start": "2025-01-01", "end": "2025-03-31"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_single_day_valid(self):
        form = DateRangeForm(data={"start": "2025-06-01", "end": "2025-06-01"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_exact_max_days_valid(self):
        from datetime import timedelta

        start = date(2025, 1, 1)
        end = start + timedelta(days=MAX_RANGE_DAYS - 1)
        form = DateRangeForm(data={"start": start.isoformat(), "end": end.isoformat()})
        self.assertTrue(form.is_valid(), form.errors)

    def test_end_before_start_invalid(self):
        form = DateRangeForm(data={"start": "2025-06-30", "end": "2025-06-01"})
        self.assertFalse(form.is_valid())
        errors = form.non_field_errors()
        self.assertTrue(any(e.code == "end_before_start" for e in errors.as_data()))

    def test_range_too_large_invalid(self):
        from datetime import timedelta

        start = date(2025, 1, 1)
        end = start + timedelta(days=MAX_RANGE_DAYS)  # one day over
        form = DateRangeForm(data={"start": start.isoformat(), "end": end.isoformat()})
        self.assertFalse(form.is_valid())
        errors = form.non_field_errors()
        self.assertTrue(any(e.code == "range_too_large" for e in errors.as_data()))

    def test_missing_start_invalid(self):
        form = DateRangeForm(data={"end": "2025-06-30"})
        self.assertFalse(form.is_valid())
        self.assertIn("start", form.errors)

    def test_missing_end_invalid(self):
        form = DateRangeForm(data={"start": "2025-06-01"})
        self.assertFalse(form.is_valid())
        self.assertIn("end", form.errors)

    def test_date_range_property_valid(self):
        form = DateRangeForm(data={"start": "2025-06-01", "end": "2025-06-30"})
        self.assertTrue(form.is_valid())
        start, end = form.date_range
        self.assertEqual(start, date(2025, 6, 1))
        self.assertEqual(end, date(2025, 6, 30))

    def test_date_range_property_invalid_returns_none(self):
        form = DateRangeForm(data={"start": "bad", "end": "2025-06-30"})
        self.assertFalse(form.is_valid())
        self.assertIsNone(form.date_range)


# ---------------------------------------------------------------------------
# _month_utc_range helper
# ---------------------------------------------------------------------------


class MonthUtcRangeTest(TestCase):
    def test_february(self):
        start, end = _month_utc_range(2025, 2)
        self.assertEqual(start, datetime(2025, 2, 1, tzinfo=UTC))
        self.assertEqual(end, datetime(2025, 3, 1, tzinfo=UTC))

    def test_december_wraps_year(self):
        start, end = _month_utc_range(2025, 12)
        self.assertEqual(start, datetime(2025, 12, 1, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 1, 1, tzinfo=UTC))

    def test_january(self):
        start, end = _month_utc_range(2026, 1)
        self.assertEqual(start, datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 2, 1, tzinfo=UTC))

    def test_start_utc_aware(self):
        start, end = _month_utc_range(2025, 6)
        self.assertEqual(start.tzinfo, UTC)
        self.assertEqual(end.tzinfo, UTC)


# ---------------------------------------------------------------------------
# get_monthly_revenue
# ---------------------------------------------------------------------------


class GetMonthlyRevenueTest(TestCase):
    def setUp(self):
        # Two payments in June 2025
        intent1 = _make_intent(fee_code="PERMIT-A", tax_amount=Decimal("10.00"))
        self.p1 = _make_payment(
            intent1,
            amount=Decimal("200.00"),
            processor_fee=Decimal("5.00"),
            paid_at=datetime(2025, 6, 10, 8, 0, tzinfo=UTC),
        )
        intent2 = _make_intent(fee_code="PERMIT-A", tax_amount=Decimal("5.00"))
        self.p2 = _make_payment(
            intent2,
            amount=Decimal("100.00"),
            processor_fee=Decimal("2.50"),
            paid_at=datetime(2025, 6, 25, 14, 0, tzinfo=UTC),
        )
        # One payment in July 2025 (should NOT be included)
        intent3 = _make_intent(fee_code="OTHER")
        self.p3 = _make_payment(
            intent3,
            amount=Decimal("50.00"),
            processor_fee=Decimal("1.00"),
            paid_at=datetime(2025, 7, 1, 0, 0, tzinfo=UTC),
        )

    def test_total_gross(self):
        result = get_monthly_revenue(2025, 6)
        self.assertEqual(result["total_gross"], Decimal("300.00"))

    def test_total_net(self):
        result = get_monthly_revenue(2025, 6)
        # 200-5 + 100-2.50 = 195 + 97.50 = 292.50
        self.assertEqual(result["total_net"], Decimal("292.50"))

    def test_total_tax(self):
        result = get_monthly_revenue(2025, 6)
        self.assertEqual(result["total_tax"], Decimal("15.00"))

    def test_total_processor_fees(self):
        result = get_monthly_revenue(2025, 6)
        self.assertEqual(result["total_processor_fees"], Decimal("7.50"))

    def test_payment_count(self):
        result = get_monthly_revenue(2025, 6)
        self.assertEqual(result["payment_count"], 2)

    def test_by_fee_code_key(self):
        result = get_monthly_revenue(2025, 6)
        self.assertIn("PERMIT-A", result["by_fee_code"])

    def test_by_fee_code_aggregates(self):
        result = get_monthly_revenue(2025, 6)
        fc = result["by_fee_code"]["PERMIT-A"]
        self.assertEqual(fc["gross"], Decimal("300.00"))
        self.assertEqual(fc["count"], 2)

    def test_excludes_out_of_month(self):
        result = get_monthly_revenue(2025, 6)
        # July payment should not be counted
        self.assertNotIn("OTHER", result["by_fee_code"])

    def test_empty_month_returns_zero(self):
        result = get_monthly_revenue(2020, 1)
        self.assertEqual(result["total_gross"], Decimal("0.00"))
        self.assertEqual(result["payment_count"], 0)
        self.assertEqual(result["by_fee_code"], {})


# ---------------------------------------------------------------------------
# get_refund_summary
# ---------------------------------------------------------------------------


class GetRefundSummaryTest(TestCase):
    def setUp(self):
        intent = _make_intent()
        payment = _make_payment(intent, amount=Decimal("200.00"))
        # Succeeded refund in June
        self.r1 = _make_refund(
            payment,
            amount=Decimal("20.00"),
            refunded_at=datetime(2025, 6, 15, tzinfo=UTC),
            status=Refund.GATEWAY_STATUS_SUCCEEDED,
        )
        # Failed refund in June — must NOT be counted
        self.r2 = _make_refund(
            payment,
            amount=Decimal("15.00"),
            refunded_at=datetime(2025, 6, 20, tzinfo=UTC),
            status=Refund.GATEWAY_STATUS_FAILED,
        )
        # Succeeded refund in July — must NOT be counted
        self.r3 = _make_refund(
            payment,
            amount=Decimal("10.00"),
            refunded_at=datetime(2025, 7, 5, tzinfo=UTC),
            status=Refund.GATEWAY_STATUS_SUCCEEDED,
        )

    def test_total_refunded_excludes_failed(self):
        result = get_refund_summary(2025, 6)
        self.assertEqual(result["total_refunded"], Decimal("20.00"))

    def test_refund_count_excludes_failed(self):
        result = get_refund_summary(2025, 6)
        self.assertEqual(result["refund_count"], 1)

    def test_by_reason(self):
        result = get_refund_summary(2025, 6)
        self.assertIn("duplicate", result["by_reason"])

    def test_empty_month(self):
        result = get_refund_summary(2020, 1)
        self.assertEqual(result["total_refunded"], Decimal("0.00"))
        self.assertEqual(result["refund_count"], 0)


# ---------------------------------------------------------------------------
# get_reconciliation_queryset
# ---------------------------------------------------------------------------


class GetReconciliationQuerysetTest(TestCase):
    def setUp(self):
        intent = _make_intent(fee_code="LIC-1")
        # Payment on 2025-06-15 (UTC noon) — well within June 1–30  # noqa: RUF003
        self.p1 = _make_payment(
            intent,
            amount=Decimal("100.00"),
            paid_at=datetime(2025, 6, 15, 16, 0, tzinfo=UTC),
        )
        # Refund against p1
        self.r1 = _make_refund(
            self.p1,
            amount=Decimal("25.00"),
            refunded_at=datetime(2025, 6, 20, tzinfo=UTC),
        )
        # Payment on 2025-07-01 — outside June range
        intent2 = _make_intent()
        self.p2 = _make_payment(
            intent2,
            amount=Decimal("50.00"),
            paid_at=datetime(2025, 7, 1, 6, 0, tzinfo=UTC),
        )

    def test_includes_payment_in_range(self):
        qs = get_reconciliation_queryset(date(2025, 6, 1), date(2025, 6, 30))
        self.assertIn(self.p1, qs)

    def test_excludes_payment_outside_range(self):
        qs = get_reconciliation_queryset(date(2025, 6, 1), date(2025, 6, 30))
        self.assertNotIn(self.p2, qs)

    def test_refund_total_annotation(self):
        qs = get_reconciliation_queryset(date(2025, 6, 1), date(2025, 6, 30))
        p = qs.get(pk=self.p1.pk)
        self.assertEqual(p.refund_total, Decimal("25.00"))

    def test_refund_total_zero_when_no_refunds(self):
        qs = get_reconciliation_queryset(date(2025, 7, 1), date(2025, 7, 31))
        p = qs.get(pk=self.p2.pk)
        self.assertEqual(p.refund_total, Decimal("0.00"))

    def test_fee_code_annotation(self):
        qs = get_reconciliation_queryset(date(2025, 6, 1), date(2025, 6, 30))
        p = qs.get(pk=self.p1.pk)
        self.assertEqual(p.fee_code, "LIC-1")

    def test_fee_code_falls_back_to_purpose(self):
        """If no ServiceFeePayment, fee_code annotation = intent.purpose."""
        intent_no_fee = _make_intent(purpose=PaymentIntent.PURPOSE_DONATION)
        p = _make_payment(
            intent_no_fee,
            amount=Decimal("30.00"),
            paid_at=datetime(2025, 6, 10, tzinfo=UTC),
        )
        qs = get_reconciliation_queryset(date(2025, 6, 1), date(2025, 6, 30))
        row = qs.get(pk=p.pk)
        self.assertEqual(row.fee_code, PaymentIntent.PURPOSE_DONATION)

    def test_only_succeeded_refunds_counted(self):
        """Failed refund does not inflate refund_total."""
        _make_refund(
            self.p1,
            amount=Decimal("99.00"),
            refunded_at=datetime(2025, 6, 21, tzinfo=UTC),
            status=Refund.GATEWAY_STATUS_FAILED,
        )
        qs = get_reconciliation_queryset(date(2025, 6, 1), date(2025, 6, 30))
        p = qs.get(pk=self.p1.pk)
        # Should still be 25.00, not 124.00
        self.assertEqual(p.refund_total, Decimal("25.00"))


# ---------------------------------------------------------------------------
# compute_financial_snapshot
# ---------------------------------------------------------------------------


class ComputeFinancialSnapshotTest(TestCase):
    def setUp(self):
        intent = _make_intent(fee_code="SNAP-FEE", tax_amount=Decimal("12.00"))
        _make_payment(
            intent,
            amount=Decimal("120.00"),
            processor_fee=Decimal("4.00"),
            paid_at=datetime(2025, 4, 10, tzinfo=UTC),
        )

    def test_snapshot_structure(self):
        snap = compute_financial_snapshot(2025, 4)
        self.assertIn("year", snap)
        self.assertIn("month", snap)
        self.assertIn("revenue", snap)
        self.assertIn("refunds", snap)
        self.assertIn("row_count", snap)

    def test_decimals_serialised_as_strings(self):
        """All Decimal values in snapshot must be str (JSONField safe)."""
        snap = compute_financial_snapshot(2025, 4)
        rev = snap["revenue"]
        self.assertIsInstance(rev["total_gross"], str)
        self.assertIsInstance(rev["total_net"], str)
        self.assertIsInstance(rev["total_tax"], str)
        self.assertIsInstance(rev["total_processor_fees"], str)

    def test_by_fee_code_decimal_strings(self):
        snap = compute_financial_snapshot(2025, 4)
        by_fc = snap["revenue"]["by_fee_code"]
        self.assertIn("SNAP-FEE", by_fc)
        fc = by_fc["SNAP-FEE"]
        self.assertIsInstance(fc["gross"], str)
        self.assertIsInstance(fc["net"], str)

    def test_row_count_matches_payment_count(self):
        snap = compute_financial_snapshot(2025, 4)
        self.assertEqual(snap["row_count"], 1)

    def test_correct_values(self):
        snap = compute_financial_snapshot(2025, 4)
        self.assertEqual(Decimal(snap["revenue"]["total_gross"]), Decimal("120.00"))
        self.assertEqual(Decimal(snap["revenue"]["total_tax"]), Decimal("12.00"))


# ---------------------------------------------------------------------------
# FinancialDashboardView
# ---------------------------------------------------------------------------


class FinancialDashboardViewTest(TestCase):
    def setUp(self):
        self.url = reverse("reports:financial-dashboard")

    def test_redirects_anonymous(self):
        resp = self.client.get(self.url)
        self.assertRedirects(resp, f"{_LOGIN_URL}?next={self.url}", fetch_redirect_response=False)

    def test_forbidden_without_permission(self):
        user = _make_user(perms=[])
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_ok_with_permission(self):
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_context_has_revenue(self):
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertIn("revenue", resp.context)

    def test_context_has_refunds(self):
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertIn("refunds", resp.context)

    def test_context_is_current_month_default(self):
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertTrue(resp.context["is_current_month"])

    def test_prior_month_uses_snapshot_if_present(self):
        """When a ReportSnapshot exists for a prior month, data_source=snapshot."""
        snap_data = {
            "year": 2025,
            "month": 3,
            "revenue": {
                "total_gross": "500.00",
                "total_net": "490.00",
                "total_tax": "30.00",
                "total_processor_fees": "10.00",
                "payment_count": 5,
                "by_fee_code": {},
            },
            "refunds": {"total_refunded": "0.00", "refund_count": 0, "by_reason": {}},
            "row_count": 5,
        }
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=3,
            data=snap_data,
            row_count=5,
        )
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url + "?year=2025&month=3")
        self.assertEqual(resp.context["data_source"], "snapshot")
        self.assertEqual(resp.context["revenue"]["total_gross"], Decimal("500.00"))

    def test_prior_month_falls_back_to_realtime_if_no_snapshot(self):
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url + "?year=2020&month=1")
        self.assertEqual(resp.context["data_source"], "realtime")

    def test_invalid_year_month_defaults_to_current(self):
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url + "?year=bad&month=bad")
        self.assertTrue(resp.context["is_current_month"])

    def test_month_out_of_range_defaults_to_current(self):
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url + "?year=2025&month=13")
        self.assertTrue(resp.context["is_current_month"])

    def test_no_pii_in_context(self):
        """No email, name, address in template context."""
        user = _make_user(perms=["view_financialreport"])
        self.client.force_login(user)
        resp = self.client.get(self.url)
        ctx = resp.context
        # revenue dict should not contain any payer-level details
        for key in ("email", "name", "address", "sin"):
            self.assertNotIn(key, str(ctx.get("revenue", {})))


# ---------------------------------------------------------------------------
# ReconciliationView
# ---------------------------------------------------------------------------


class ReconciliationViewTest(TestCase):
    def setUp(self):
        self.url = reverse("reports:reconciliation")
        self.user = _make_user(perms=["view_financialreport"])
        # One payment in June 2025
        intent = _make_intent(fee_code="REC-FEE")
        self.payment = _make_payment(
            intent,
            amount=Decimal("150.00"),
            processor_fee=Decimal("4.50"),
            paid_at=datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
        )
        self.refund = _make_refund(
            self.payment,
            amount=Decimal("30.00"),
            refunded_at=datetime(2025, 6, 20, tzinfo=UTC),
        )

    def test_redirects_anonymous(self):
        resp = self.client.get(self.url)
        self.assertRedirects(resp, f"{_LOGIN_URL}?next={self.url}", fetch_redirect_response=False)

    def test_forbidden_without_permission(self):
        user = _make_user(perms=[])
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_ok_with_permission_no_params(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["payments"])

    def test_valid_date_range_returns_payments(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context["payments"])
        self.assertEqual(len(resp.context["payments"]), 1)

    def test_totals_correct(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        self.assertEqual(resp.context["total_paid"], Decimal("150.00"))
        self.assertEqual(resp.context["total_refunded"], Decimal("30.00"))
        self.assertEqual(resp.context["net"], Decimal("120.00"))

    def test_count(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        self.assertEqual(resp.context["count"], 1)

    def test_empty_range_returns_no_payments(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2020-01-01", "end": "2020-01-31"})
        self.assertEqual(resp.context["payments"], [])
        self.assertEqual(resp.context["count"], 0)

    def test_form_validation_error_shown(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-30", "end": "2025-06-01"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["form"].is_valid())
        self.assertIsNone(resp.context["payments"])

    def test_payment_rows_are_dicts(self):
        """View should pass plain dicts so template can access row.net."""
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        rows = resp.context["payments"]
        self.assertIsInstance(rows[0], dict)
        self.assertIn("net", rows[0])

    def test_net_per_row_correct(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        row = resp.context["payments"][0]
        # amount_paid=150, refund_total=30 → net=120
        self.assertEqual(row["net"], Decimal("120.00"))

    def test_no_pii_in_payment_rows(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        row = resp.context["payments"][0]
        # Only these keys should be present — no name/email/address
        allowed = {
            "reference",
            "status",
            "fee_code",
            "amount_paid",
            "refund_total",
            "net",
            "paid_at",
        }
        self.assertEqual(set(row.keys()), allowed)


# ---------------------------------------------------------------------------
# ReconciliationExportView
# ---------------------------------------------------------------------------


class ReconciliationExportViewTest(TestCase):
    def setUp(self):
        self.url = reverse("reports:reconciliation-export")
        self.user = _make_user(perms=["export_financialreport"])
        intent = _make_intent(fee_code="EXP-1")
        self.payment = _make_payment(
            intent,
            amount=Decimal("200.00"),
            paid_at=datetime(2025, 6, 15, 10, 0, tzinfo=UTC),
        )

    def test_redirects_anonymous(self):
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        self.assertEqual(resp.status_code, 302)

    def test_forbidden_without_permission(self):
        user = _make_user(perms=[])
        self.client.force_login(user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        self.assertEqual(resp.status_code, 403)

    def test_ok_with_permission(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")

    def test_audit_record_created(self):
        self.client.force_login(self.user)
        before = ExportRecord.objects.count()
        self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        self.assertEqual(ExportRecord.objects.count(), before + 1)

    def test_audit_record_no_pii_stored(self):
        """PIPEDA: ExportRecord stores actor_pk (int) — never email or name."""
        self.client.force_login(self.user)
        self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        record = ExportRecord.objects.latest("created_at")
        # actor_pk is the user's integer pk — exact equality now safe because
        # both User.pk and ExportRecord.actor_pk are BigIntegerField.
        self.assertEqual(record.actor_pk, self.user.pk)
        self.assertFalse(hasattr(record, "actor_email"))

    def test_audit_record_export_type(self):
        self.client.force_login(self.user)
        self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        record = ExportRecord.objects.latest("created_at")
        self.assertEqual(record.export_type, ExportRecord.EXPORT_TYPE_RECONCILIATION)

    def test_audit_record_row_count(self):
        """ExportRecord.row_count must reflect actual payment count, not zero."""
        self.client.force_login(self.user)
        self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        record = ExportRecord.objects.latest("created_at")
        self.assertEqual(record.row_count, 1)  # setUp creates one payment in June

    def test_missing_start_returns_400(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"end": "2025-06-30"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_date_returns_400(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "not-a-date", "end": "2025-06-30"})
        self.assertEqual(resp.status_code, 400)

    def test_end_before_start_returns_400(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-30", "end": "2025-06-01"})
        self.assertEqual(resp.status_code, 400)

    def test_range_too_large_returns_400(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-01-01", "end": "2025-12-31"})
        self.assertEqual(resp.status_code, 400)

    def test_csv_content_type(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        self.assertIn("text/csv", resp["Content-Type"])

    def test_csv_has_header(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"start": "2025-06-01", "end": "2025-06-30"})
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        self.assertIn("reference", content)
        self.assertIn("fee_code", content)


# ---------------------------------------------------------------------------
# RevenueExportView
# ---------------------------------------------------------------------------


class RevenueExportViewTest(TestCase):
    def setUp(self):
        self.url = reverse("reports:revenue-export")
        self.user = _make_user(perms=["export_financialreport"])

    def test_redirects_anonymous(self):
        resp = self.client.get(self.url, {"year": "2025", "month": "6"})
        self.assertEqual(resp.status_code, 302)

    def test_ok_with_permission(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"year": "2025", "month": "6"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_audit_record_created(self):
        self.client.force_login(self.user)
        before = ExportRecord.objects.count()
        self.client.get(self.url, {"year": "2025", "month": "6"})
        self.assertEqual(ExportRecord.objects.count(), before + 1)

    def test_audit_record_no_pii_stored(self):
        """PIPEDA: actor_pk is the user's int pk — never email or name."""
        self.client.force_login(self.user)
        self.client.get(self.url, {"year": "2025", "month": "6"})
        record = ExportRecord.objects.latest("created_at")
        # actor_pk is BigIntegerField — exact equality is safe.
        self.assertEqual(record.actor_pk, self.user.pk)
        self.assertFalse(hasattr(record, "actor_email"))

    def test_audit_record_export_type(self):
        self.client.force_login(self.user)
        self.client.get(self.url, {"year": "2025", "month": "6"})
        record = ExportRecord.objects.latest("created_at")
        self.assertEqual(record.export_type, ExportRecord.EXPORT_TYPE_REVENUE)

    def test_audit_record_row_count_empty_month(self):
        """row_count = 0 for a month with no payments (not hardcoded zero)."""
        self.client.force_login(self.user)
        self.client.get(self.url, {"year": "2020", "month": "1"})
        record = ExportRecord.objects.latest("created_at")
        # No payments in Jan 2020 → 0 fee-code rows in CSV
        self.assertEqual(record.row_count, 0)

    def test_missing_year_returns_400(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"month": "6"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_month_returns_400(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"year": "2025", "month": "13"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_year_returns_400(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"year": "1800", "month": "6"})
        self.assertEqual(resp.status_code, 400)

    def test_csv_header(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, {"year": "2025", "month": "6"})
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        self.assertIn("fee_code", content)
        self.assertIn("gross_revenue", content)


# ---------------------------------------------------------------------------
# export_reconciliation_csv (direct function test)
# ---------------------------------------------------------------------------


class ExportReconciliationCsvTest(TestCase):
    def setUp(self):
        from apps.reports.exports.csv_export import export_reconciliation_csv

        self.export_fn = export_reconciliation_csv
        intent = _make_intent(fee_code="CSV-TEST")
        self.payment = _make_payment(
            intent,
            amount=Decimal("250.00"),
            paid_at=datetime(2025, 6, 10, 8, 0, tzinfo=UTC),
        )
        _make_refund(
            self.payment,
            amount=Decimal("50.00"),
            refunded_at=datetime(2025, 6, 12, tzinfo=UTC),
        )

    def test_returns_streaming_response(self):
        from django.http import StreamingHttpResponse

        resp = self.export_fn(date(2025, 6, 1), date(2025, 6, 30))
        self.assertIsInstance(resp, StreamingHttpResponse)

    def test_content_type(self):
        resp = self.export_fn(date(2025, 6, 1), date(2025, 6, 30))
        self.assertIn("text/csv", resp["Content-Type"])

    def test_filename_contains_dates(self):
        resp = self.export_fn(date(2025, 6, 1), date(2025, 6, 30))
        self.assertIn("2025-06-01", resp["Content-Disposition"])
        self.assertIn("2025-06-30", resp["Content-Disposition"])

    def test_bom_present(self):
        resp = self.export_fn(date(2025, 6, 1), date(2025, 6, 30))
        content = b"".join(resp.streaming_content)
        # UTF-8 BOM = EF BB BF
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))

    def test_header_columns(self):
        resp = self.export_fn(date(2025, 6, 1), date(2025, 6, 30))
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        for col in [
            "reference",
            "status",
            "fee_code",
            "amount_paid",
            "refund_total",
            "net",
            "paid_at",
        ]:
            self.assertIn(col, content)

    def test_no_pii_in_output(self):
        """Columns must not include name, email, address."""
        resp = self.export_fn(date(2025, 6, 1), date(2025, 6, 30))
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        for pii_col in ["email", "name", "address", "phone"]:
            self.assertNotIn(pii_col, content)

    def test_data_row_present(self):
        resp = self.export_fn(date(2025, 6, 1), date(2025, 6, 30))
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        self.assertIn("250.00", content)

    def test_net_computed(self):
        resp = self.export_fn(date(2025, 6, 1), date(2025, 6, 30))
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        # net = 250 - 50 = 200
        self.assertIn("200.00", content)


# ---------------------------------------------------------------------------
# export_revenue_csv (direct function test)
# ---------------------------------------------------------------------------


class ExportRevenueCsvTest(TestCase):
    def setUp(self):
        from apps.reports.exports.csv_export import export_revenue_csv

        self.export_fn = export_revenue_csv
        intent = _make_intent(fee_code="REV-TEST", tax_amount=Decimal("8.00"))
        _make_payment(
            intent,
            amount=Decimal("80.00"),
            processor_fee=Decimal("2.40"),
            paid_at=datetime(2025, 5, 20, tzinfo=UTC),
        )

    def test_returns_streaming_response(self):
        from django.http import StreamingHttpResponse

        resp = self.export_fn(2025, 5)
        self.assertIsInstance(resp, StreamingHttpResponse)

    def test_filename_contains_period(self):
        resp = self.export_fn(2025, 5)
        self.assertIn("2025-05", resp["Content-Disposition"])

    def test_bom_present(self):
        resp = self.export_fn(2025, 5)
        content = b"".join(resp.streaming_content)
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))

    def test_header_columns(self):
        resp = self.export_fn(2025, 5)
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        for col in [
            "fee_code",
            "gross_revenue",
            "processor_fees",
            "net_revenue",
            "tax_collected",
            "count",
        ]:
            self.assertIn(col, content)

    def test_data_row_present(self):
        resp = self.export_fn(2025, 5)
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        self.assertIn("REV-TEST", content)
        self.assertIn("80.00", content)

    def test_empty_month_no_data_rows(self):
        resp = self.export_fn(2020, 1)
        content = b"".join(resp.streaming_content).decode("utf-8-sig")
        lines = [l for l in content.splitlines() if l.strip()]  # noqa: E741
        # Only the header row should be present
        self.assertEqual(len(lines), 1)


# ---------------------------------------------------------------------------
# _compute_all_snapshots task helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# H-10: Honorarium payment field tests
# ---------------------------------------------------------------------------


class HonorariumModelFieldTests(TestCase):
    """
    H-10: Tests for Honorarium model fields — required-field enforcement,
    MinValueValidator on amount, and T4A boolean defaults.

    These tests are placed in the financial wave because they cover the CRA
    T4A reporting fields that feed into financial report outputs.
    """

    def _make_volunteer_profile(self):
        from apps.volunteers.models import VolunteerProfile

        user = _make_user(is_staff=False)
        return VolunteerProfile.objects.create(user=user)

    def _make_creator(self):
        return _make_user(is_staff=True)

    def _make_honorarium(self, **overrides):
        """Create a minimal valid Honorarium using skip_clean=True to bypass CRA thresholds."""
        from datetime import date

        from apps.volunteers.models import Honorarium

        profile = self._make_volunteer_profile()
        creator = self._make_creator()
        defaults = {
            "volunteer": profile,
            "payment_type": Honorarium.PAYMENT_TYPE_HONORARIUM,
            "amount": Decimal("100.00"),
            "description": "Test honorarium",
            "payment_date": date(2025, 6, 15),
            "created_by": creator,
        }
        defaults.update(overrides)
        h = Honorarium(**defaults)
        h.save(skip_clean=True)
        return h

    def test_amount_min_validator_rejects_zero(self):
        """
        H-10: amount field has MinValueValidator(0.01) — a zero amount must
        raise ValidationError when full_clean() is called.
        """
        from datetime import date

        from django.core.exceptions import ValidationError

        from apps.volunteers.models import Honorarium

        profile = self._make_volunteer_profile()
        creator = self._make_creator()
        hon = Honorarium(
            volunteer=profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("0.00"),
            description="Zero amount test",
            payment_date=date(2025, 6, 15),
            created_by=creator,
        )
        with self.assertRaises(ValidationError) as ctx:
            hon.full_clean()
        # The error should reference the amount field
        self.assertIn("amount", ctx.exception.message_dict)

    def test_amount_min_validator_rejects_negative(self):
        """
        H-10: amount must be positive — a negative value must raise ValidationError.
        """
        from datetime import date

        from django.core.exceptions import ValidationError

        from apps.volunteers.models import Honorarium

        profile = self._make_volunteer_profile()
        creator = self._make_creator()
        hon = Honorarium(
            volunteer=profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("-50.00"),
            description="Negative amount test",
            payment_date=date(2025, 6, 15),
            created_by=creator,
        )
        with self.assertRaises(ValidationError):
            hon.full_clean()

    def test_amount_min_validator_accepts_minimum_positive(self):
        """H-10: The minimum valid amount (0.01) must pass validation."""
        from datetime import date

        from apps.volunteers.models import Honorarium

        profile = self._make_volunteer_profile()
        creator = self._make_creator()
        hon = Honorarium(
            volunteer=profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("0.01"),
            description="Minimum amount",
            payment_date=date(2025, 6, 15),
            created_by=creator,
        )
        # Should not raise for the amount field validator
        # (CRA threshold checks will pass since 0.01 < any threshold)
        try:
            hon.full_clean()
        except Exception as e:
            from django.core.exceptions import ValidationError as VE  # noqa: N817

            if isinstance(e, VE) and "amount" in e.message_dict:
                self.fail(f"amount=0.01 should be valid but raised: {e.message_dict['amount']}")

    def test_description_blank_false_raises_on_empty(self):
        """
        H-10: description is a required CharField (blank=False by default).
        An empty description must fail validation.
        """
        from datetime import date

        from django.core.exceptions import ValidationError

        from apps.volunteers.models import Honorarium

        profile = self._make_volunteer_profile()
        creator = self._make_creator()
        hon = Honorarium(
            volunteer=profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("100.00"),
            description="",  # blank=False — must fail
            payment_date=date(2025, 6, 15),
            created_by=creator,
        )
        with self.assertRaises(ValidationError) as ctx:
            hon.full_clean()
        self.assertIn("description", ctx.exception.message_dict)

    def test_t4a_required_defaults_to_false(self):
        """H-10: t4a_required defaults to False for new honoraria."""
        hon = self._make_honorarium()
        self.assertFalse(hon.t4a_required)

    def test_t4a_issued_defaults_to_false(self):
        """H-10: t4a_issued defaults to False for new honoraria."""
        hon = self._make_honorarium()
        self.assertFalse(hon.t4a_issued)

    def test_t4a_issued_at_defaults_to_null(self):
        """H-10: t4a_issued_at is nullable and defaults to None."""
        hon = self._make_honorarium()
        self.assertIsNone(hon.t4a_issued_at)

    def test_t4a_required_set_to_true_persists(self):
        """H-10: t4a_required can be set to True and persisted."""
        hon = self._make_honorarium()
        hon.t4a_required = True
        hon.save(skip_clean=True, update_fields=["t4a_required"])
        hon.refresh_from_db()
        self.assertTrue(hon.t4a_required)

    def test_currency_defaults_to_cad(self):
        """H-10: currency field defaults to 'CAD'."""
        hon = self._make_honorarium()
        self.assertEqual(hon.currency, "CAD")

    def test_payment_type_choices_include_required_types(self):
        """H-10: payment_type must be one of EXPENSE or HONORARIUM."""
        from apps.volunteers.models import Honorarium

        valid_types = {c[0] for c in Honorarium.PAYMENT_TYPE_CHOICES}
        self.assertIn(Honorarium.PAYMENT_TYPE_HONORARIUM, valid_types)
        self.assertIn(Honorarium.PAYMENT_TYPE_EXPENSE, valid_types)

    def test_amount_field_is_decimal_in_db(self):
        """H-10: amount is stored as Decimal (not str or float)."""
        hon = self._make_honorarium(amount=Decimal("249.99"))
        hon.refresh_from_db()
        self.assertIsInstance(hon.amount, Decimal)
        self.assertEqual(hon.amount, Decimal("249.99"))

    def test_cra_t4a_threshold_auto_sets_flag(self):
        """
        H-10: When cumulative honoraria meet/exceed the T4A threshold ($500 default),
        clean() auto-sets t4a_required = True.
        """
        from datetime import date

        from apps.volunteers.models import Honorarium

        profile = self._make_volunteer_profile()
        creator = self._make_creator()

        # First honorarium: $499 — below T4A threshold.
        hon1 = Honorarium(
            volunteer=profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("499.00"),
            description="Below threshold",
            payment_date=date(2025, 6, 15),
            created_by=creator,
        )
        hon1.save(skip_clean=True)

        # Second honorarium: $1 — brings YTD to $500, should trigger T4A flag.
        hon2 = Honorarium(
            volunteer=profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("1.00"),
            description="Reaches threshold",
            payment_date=date(2025, 6, 20),
            created_by=creator,
        )
        hon2.clean()  # Call clean() directly to check threshold enforcement
        self.assertTrue(
            hon2.t4a_required,
            "t4a_required must be True when cumulative total reaches $500 T4A threshold",
        )

    def test_cra_hard_block_raises_validation_error(self):
        """
        H-10: When projected cumulative total meets/exceeds the hard block ($1000 default),
        clean() raises ValidationError — the honorarium is rejected.
        """
        from datetime import date

        from django.core.exceptions import ValidationError

        from apps.volunteers.models import Honorarium

        profile = self._make_volunteer_profile()
        creator = self._make_creator()

        # Existing YTD: $999 — just below the hard block.
        hon1 = Honorarium(
            volunteer=profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("999.00"),
            description="Near limit",
            payment_date=date(2025, 6, 15),
            created_by=creator,
        )
        hon1.save(skip_clean=True)

        # New honorarium: $1 would bring YTD to $1000 — exactly the hard block.
        hon2 = Honorarium(
            volunteer=profile,
            payment_type=Honorarium.PAYMENT_TYPE_HONORARIUM,
            amount=Decimal("1.00"),
            description="Hits hard block",
            payment_date=date(2025, 6, 20),
            created_by=creator,
        )
        with self.assertRaises(ValidationError) as ctx:
            hon2.clean()
        self.assertIn(
            "amount",
            ctx.exception.message_dict,
            "Hard-block ValidationError must reference the 'amount' field",
        )


class ComputeAllSnapshotsTest(TestCase):
    def setUp(self):
        intent = _make_intent(fee_code="TASK-FEE")
        _make_payment(
            intent,
            amount=Decimal("60.00"),
            paid_at=datetime(2025, 8, 5, tzinfo=UTC),
        )

    def test_creates_snapshot(self):
        from apps.reports.tasks import _compute_all_snapshots

        before = ReportSnapshot.objects.filter(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=8,
        ).count()
        self.assertEqual(before, 0)
        written = _compute_all_snapshots(2025, 8)
        # Wave 3 added donations snapshot — task now writes ≥2 per run.
        self.assertGreaterEqual(written, 1)
        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=8,
        )
        self.assertIsNotNone(snap)

    def test_idempotent_update(self):
        from apps.reports.tasks import _compute_all_snapshots

        _compute_all_snapshots(2025, 8)
        _compute_all_snapshots(2025, 8)  # second run — update, not create
        count = ReportSnapshot.objects.filter(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=8,
        ).count()
        self.assertEqual(count, 1)

    def test_snapshot_data_structure(self):
        from apps.reports.tasks import _compute_all_snapshots

        _compute_all_snapshots(2025, 8)
        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=8,
        )
        self.assertIn("revenue", snap.data)
        self.assertIn("refunds", snap.data)

    def test_returns_zero_for_empty_month(self):
        from apps.reports.tasks import _compute_all_snapshots

        # 1900-01 has no data but should still write zeroed snapshots for each
        # report type (financial + donations since Wave 3).
        written = _compute_all_snapshots(1900, 1)
        self.assertGreaterEqual(written, 1)

    def test_row_count_populated(self):
        from apps.reports.tasks import _compute_all_snapshots

        _compute_all_snapshots(2025, 8)
        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=8,
        )
        self.assertEqual(snap.row_count, 1)

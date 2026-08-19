"""
GovStack Payments BB — URL routing.

Mounted at: /govstack/payments/  (configured in config/urls.py)
Namespace:  govstack_payments

All endpoints in this file are part of the GovStack bb-payments certification
surface. They must NOT be confused with or mixed into the CivicOS-internal
payments URL config (apps/payments/urls.py — Stripe, donations, etc.).

Endpoint naming:
  URL path segments exactly match the GovStack harness @endpoint annotations.
  e.g. @endpoint=/bulk-payment → path("bulk-payment", ...)

Voucher note:
  The harness uses two different HTTP methods on the same URL for
  voucherstatuscheck/{serial}: GET (status) and PATCH (cancellation).
  Both are handled by VoucherStatusCheckView.get() and .patch().

P2G note:
  P2G endpoints (Wave 5) are registered here in Wave 1 as stubs.
  They return HTTP 501 until Wave 5 is implemented.
"""
from django.urls import path

from apps.payments import govstack_views as gv
from apps.payments.platform_scope import ReconciliationReportView

app_name = "govstack_payments"

urlpatterns = [
    path("reconciliation/report", ReconciliationReportView.as_view(), name="reconciliation_report"),
    # ── G2P Beneficiary (Wave 2) ──────────────────────────────────────────
    path(
        "register-beneficiary",
        gv.RegisterBeneficiaryView.as_view(),
        name="register_beneficiary",
    ),
    path(
        "update-beneficiary-details",
        gv.UpdateBeneficiaryView.as_view(),
        name="update_beneficiary",
    ),

    # ── G2P Bulk Payment (Wave 3) ─────────────────────────────────────────
    path(
        "bulk-payment",
        gv.BulkPaymentView.as_view(),
        name="bulk_payment",
    ),
    path(
        "prepayment-validation",
        gv.PrepaymentValidationView.as_view(),
        name="prepayment_validation",
    ),
    path(
        "prepayment-validation-response",
        gv.PrepaymentValidationResponseView.as_view(),
        name="prepayment_validation_response",
    ),

    # ── Voucher Engine (Wave 4) ───────────────────────────────────────────
    # IMPORTANT: voucher_preactivation and voucher_activation use exact
    # underscore-joined names as specified in the harness @endpoint annotations.
    path(
        "vouchers/voucher_preactivation",
        gv.VoucherPreactivationView.as_view(),
        name="voucher_preactivation",
    ),
    path(
        "vouchers/voucher_activation",
        gv.VoucherActivationView.as_view(),
        name="voucher_activation",
    ),
    path(
        "vouchers/voucher_redemption",
        gv.VoucherRedemptionView.as_view(),
        name="voucher_redemption",
    ),
    # voucherstatuscheck handles both GET (status) and PATCH (cancellation).
    # URL param name must match the view method signature: voucherserialnumber.
    path(
        "vouchers/voucherstatuscheck/<str:voucherserialnumber>",
        gv.VoucherStatusCheckView.as_view(),
        name="voucher_status_check",
    ),

    # ── P2G — Bill Payments (Wave 5) ──────────────────────────────────────
    # ORDERING NOTE: bills/<str:bill_id>/mark-paid MUST appear before
    # bills/<str:bill_id> so Django's URL router tries the more specific
    # pattern first.  Django's <str:...> converter matches only a single
    # path segment (no slashes), so these patterns do NOT overlap, but
    # explicit ordering makes intent clear and guards against future changes.
    path(
        "bills/<str:bill_id>/mark-paid",
        gv.MarkBillPaidView.as_view(),
        name="mark_bill_paid",
    ),
    path(
        "bills/<str:bill_id>",
        gv.BillInquiryView.as_view(),
        name="bill_inquiry",
    ),
    path(
        "billTransferRequests",
        gv.BillTransferRequestView.as_view(),
        name="bill_transfer",
    ),
    path(
        "transferRequests/<str:transfer_request_id>",
        gv.TransferRequestStatusView.as_view(),
        name="transfer_request_status",
    ),
]

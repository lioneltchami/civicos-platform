"""
Payments building block URL configuration.
Views added in Wave 1 Round 2 (gateway + webhook endpoints).
Fee payment flow added in Wave 3, Round 3a.
Refund workflow added in Wave 3, Round 3b.
"""
from django.urls import path

from apps.payments.views.webhook import stripe_webhook
from apps.payments.views.fee_payment import (
    FeePaymentSelectView,
    FeePaymentConfirmView,
    FeePaymentSuccessView,
    FeePaymentCancelView,
    create_payment_intent_api,
)
from apps.payments.views.refund import RefundCreateView, RefundConfirmView, RefundDetailView

app_name = "payments"

urlpatterns = [
    # ── Stripe webhook (unauthenticated — signature-verified) ────────────
    path(
        "webhooks/stripe/",
        stripe_webhook,
        name="stripe_webhook",
    ),

    # ── Fee payment flow (authenticated citizens) ────────────────────────
    # Step 1: select fee code, province, quantity
    path(
        "fees/",
        FeePaymentSelectView.as_view(),
        name="fee_payment_select",
    ),
    # Step 2: confirm order summary + Stripe Elements card form
    path(
        "fees/confirm/",
        FeePaymentConfirmView.as_view(),
        name="fee_payment_confirm",
    ),
    # Step 3: JSON API — create Stripe PaymentIntent (CSRF-protected)
    path(
        "fees/api/create-intent/",
        create_payment_intent_api,
        name="create_payment_intent",
    ),
    # Step 4: success page
    path(
        "fees/success/",
        FeePaymentSuccessView.as_view(),
        name="fee_payment_success",
    ),
    # Step 5: cancel / back out
    path(
        "fees/cancel/",
        FeePaymentCancelView.as_view(),
        name="fee_payment_cancel",
    ),

    # ── Refund workflow (staff only) ─────────────────────────────────────
    path(
        "refunds/<uuid:payment_pk>/create/",
        RefundCreateView.as_view(),
        name="refund_create",
    ),
    path(
        "refunds/<uuid:payment_pk>/confirm/",
        RefundConfirmView.as_view(),
        name="refund_confirm",
    ),
    path(
        "refunds/<uuid:refund_pk>/",
        RefundDetailView.as_view(),
        name="refund_detail",
    ),
]

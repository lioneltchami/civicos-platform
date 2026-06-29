"""
Payments building block URL configuration.
Views added in Wave 1 Round 2 (gateway + webhook endpoints).
Fee payment flow added in Wave 3, Round 3a.
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
]

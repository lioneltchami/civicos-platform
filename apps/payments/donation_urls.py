"""
URL configuration for the public donation flow.
Wave 4, Track A — CivicOS Payments Building Block.

Namespace: donate
Mount point: /donate/ (configured in config/urls.py)
"""

from django.urls import path

from apps.payments.views.donation import (
    DonationCancelView,
    DonationConfirmView,
    DonationSelectView,
    DonationSuccessView,
    RecurringGiftCancelView,
    create_donation_intent_api,
)

app_name = "donate"

urlpatterns = [
    # Step 1: public donation form (campaign + amount + donor details)
    path("", DonationSelectView.as_view(), name="donation_select"),
    # Step 2: confirm order summary + Stripe Elements card form
    path("confirm/", DonationConfirmView.as_view(), name="donation_confirm"),
    # Step 3: CSRF-protected JSON API — creates Stripe PaymentIntent
    path("api/create-intent/", create_donation_intent_api, name="create_donation_intent"),
    # Step 4: success page (shown after Stripe.js confirms the payment)
    path("success/", DonationSuccessView.as_view(), name="donation_success"),
    # Step 5: cancel / back out
    path("cancel/", DonationCancelView.as_view(), name="donation_cancel"),
    # Recurring gift cancellation — authenticated donors only
    path(
        "recurring/<uuid:plan_pk>/cancel/",
        RecurringGiftCancelView.as_view(),
        name="recurring_cancel",
    ),
]

"""
Payments building block URL configuration.
Views added in Wave 1 Round 2 (gateway + webhook endpoints).
"""
from django.urls import path

from apps.payments.views.webhook import stripe_webhook

app_name = "payments"

urlpatterns = [
    path(
        "webhooks/stripe/",
        stripe_webhook,
        name="stripe_webhook",
    ),
]

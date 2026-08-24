"""
Appointments / Scheduling BB — URL configuration.

Views are implemented progressively across waves:
  Wave 5 — citizen booking flow and staff dashboard views.
  Wave 6 — notification reminder email links.
  Wave 7 — virtual appointment join view (auth-gated).
  Wave 8 — DRF REST API endpoints.

This stub registers the namespace so that the url include in config/urls.py
resolves without error during Waves 1–4 (models, services, tasks only).
"""  # noqa: RUF002

from django.urls import path

from .evidence_views import scheduler_status

app_name = "appointments"

urlpatterns: list = [
    path("scheduler/status/", scheduler_status, name="scheduler-status"),
    # Citizen views — Wave 5
    # Staff views — Wave 5
    # API endpoints — Wave 8
]

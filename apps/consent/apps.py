"""
Consent & Privacy Building Block — AppConfig.

PIPEDA-compliant consent lifecycle:
  • Citizens control what data is collected and how it is used
  • Consent can always be withdrawn without detriment
  • Every consent state change is recorded in an append-only audit trail
  • Data export requests are fulfilled within 30 days (PIPEDA s.4.9)
"""

from django.apps import AppConfig


class ConsentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.consent"
    verbose_name = "Consent & Privacy"

    def ready(self) -> None:
        import apps.consent.receivers
        import apps.consent.signals  # noqa: F401 — define signals

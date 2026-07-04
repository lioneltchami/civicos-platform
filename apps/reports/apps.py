"""
Analytics & Reporting Building Block — AppConfig.

Custom BB: no dedicated CivicOS Analytics spec exists (CivicOS 2.0 treats
analytics as an optional layer within the Security BB). This BB follows
CivicOS cross-cutting principles: audit trail, multi-tenancy, PIPEDA
data minimization.

Spec: docs/analytics-reporting-bb-spec.md
"""

from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reports"
    verbose_name = "Analytics & Reporting"

    def ready(self) -> None:
        # Import signals module so the t4a_generated Signal object is
        # registered at app-startup.  Receivers connect here via
        # @receiver decorators or explicit Signal.connect() calls
        # elsewhere in the codebase.
        import apps.reports.signals  # noqa: F401

"""
Analytics & Reporting Building Block — AppConfig.

Custom BB: no dedicated GovStack Analytics spec exists (GovStack 2.0 treats
analytics as an optional layer within the Security BB). This BB follows
GovStack cross-cutting principles: audit trail, multi-tenancy, PIPEDA
data minimization.

Spec: docs/analytics-reporting-bb-spec.md
"""

from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reports"
    verbose_name = "Analytics & Reporting"

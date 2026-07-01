"""
Volunteer Management Building Block — AppConfig.

Supports the full volunteer lifecycle for nonprofits and government organizations:
  Recruitment → Application → Screening → Onboarding → Scheduling →
  Hours Logging → Recognition → Offboarding

Spec: docs/volunteer-management-bb-spec.md

Canadian compliance:
  - Canadian Code for Volunteer Involvement (CCVI) — Volunteer Canada, 2017
  - CRA volunteer vs. employee distinction (PC-025)
  - Vulnerable Sector Check (VSC) tracking — RCMP framework
  - PIPEDA minimum-collection principle enforced at model + form level
"""
from django.apps import AppConfig


class VolunteersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.volunteers"
    label = "volunteers"
    verbose_name = "Volunteer Management"

    def ready(self):
        import apps.volunteers.signals   # noqa: F401 — define signals
        import apps.volunteers.receivers  # noqa: F401 — register signal receivers

"""
Appointments / Scheduling Building Block — AppConfig.

Enables citizens and clients to book time-slots with government staff and NGO
workers for in-person or virtual appointments. Integrates with the WorkItem,
Notifications, Documents, Payments, and Consent building blocks.

Governing standards:
  - GovStack Scheduler BB v1.0.1 (Apache 2.0) — ITU/UN DESA/UNDP/GIZ/Estonia
  - TM Forum TMF646 Appointment API R19.0.0
  - RFC 5545 iCalendar (email .ics attachments)

Privacy law applicability (per Location.privacy_regime):
  - Federal Privacy Act (RSC 1985, c. P-21) — government institutions
  - PIPEDA — federally-regulated NGOs and private sector
  - Quebec Law 25 (Act 25) — all Quebec-based organisations
  - Ontario PHIPA — health-sector organisations

Security invariants (enforced here and throughout the BB):
  - Citizens receive 404 (not 403) for booking PKs they do not own (IDOR).
  - Video join URLs are NEVER included in unauthenticated email — delivered
    only inside an authenticated portal session.
  - No PII (email, full name) in logs — use .pk / UUID only.
  - select_for_update() inside atomic() before any capacity check.
  - record_event() inside atomic() block (PIPEDA 4.5.3).

Spec: SPEC_APPOINTMENTS_BB.md
"""

import logging

from django.apps import AppConfig
from django.conf import settings
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class AppointmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.appointments"
    label = "appointments"
    verbose_name = _("Appointments & Scheduling")

    def ready(self) -> None:
        """
        Perform startup wiring:
        1. Import signals to register them with Django's signal dispatcher.
        2. Import receivers to connect signal handlers (Wave 3+).
        3. Validate video conference configuration (Wave 7+).
        """
        # Import signals module to instantiate Signal() objects and make them
        # available for other apps to connect to via their ready() hooks.
        import apps.appointments.signals  # noqa: F401 — side-effect import

        # Conditionally import receivers so Wave 1 works before receivers.py
        # is implemented in later waves. Uses the same safe-import pattern as
        # apps.documents.apps — distinguishes "file absent" (skip silently)
        # from "file present but broken import" (re-raise so operator sees error).
        import importlib.util

        _receivers_spec = importlib.util.find_spec("apps.appointments.receivers")
        if _receivers_spec is not None:
            import apps.appointments.receivers  # noqa: F401

        # Validate iCalendar organizer email in production (RFC 5545 §3.8.4.3).
        if not settings.DEBUG:
            civicos = getattr(settings, "CIVICOS", {})
            email = civicos.get("APPOINTMENTS", {}).get("ICS_ORGANIZER_EMAIL", "")
            if not email:
                logger.warning(
                    "APPOINTMENTS_ICS_ORGANIZER_EMAIL is not configured. "
                    "iCalendar invitations will be sent without an ORGANIZER field, "
                    "which violates RFC 5545 §3.8.4.3 and may be rejected by some clients."
                )

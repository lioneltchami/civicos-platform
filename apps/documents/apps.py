"""
Document Management Building Block — AppConfig.

Provides controlled, auditable, PIPEDA-compliant document storage for CivicOS.
All uploads go through: validation → presigned S3 POST → ClamAV scan → active storage.

Privacy constraints (enforced here and throughout the BB):
  - storage_key is NEVER exposed in templates, serializers, admin, or API responses.
  - original_filename is NEVER written to audit event_detail (may contain PII).
  - Quarantine notifications to admin must NOT include uploader PII.
  - Citizens receive 404 (not 403) for non-owned document PKs (IDOR prevention).
  - legal_hold=True blocks ALL automated disposal absolutely.

Governing law: PIPEDA, Privacy Act s.6(1), TBS SPIN 2023-06-13, LAC DA #2016/001.
"""

import importlib.util
import logging
import sys

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class DocumentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.documents"
    label = "documents"
    verbose_name = _("Document Management")

    def ready(self) -> None:
        """
        Perform startup wiring:
        1. Import signals and receivers (connects all signal handlers).
        2. Validate ClamAV configuration in production.
        """
        # Import signals to register them with Django's signal dispatcher.
        import apps.documents.signals  # noqa: F401  # registers signals

        # Import receivers to connect signal handlers.
        # (Receivers are in a separate module to keep signals.py declaration-only.)
        # Use importlib.util.find_spec() to distinguish two cases:
        #   - receivers.py does not exist yet (Wave 3 deferred): silently skip.
        #   - receivers.py exists but has a broken import inside it: re-raise so
        #     operators see the real error rather than silent signal-handler loss.
        _receivers_spec = importlib.util.find_spec("apps.documents.receivers")
        if _receivers_spec is not None:
            import apps.documents.receivers  # noqa: F401
        # else: module file absent — Wave 3 not yet implemented, skip silently.

        self._check_clamav_config()

    def _check_clamav_config(self) -> None:
        """
        Validate that ClamAV is configured in production.

        - In dev/test: log a warning but do not block startup.
        - In production (CLAMAV_REQUIRED=True): raise ImproperlyConfigured
          if the host/port are not set. This prevents silent data-at-rest
          uploads bypassing virus scanning.
        """
        if self._is_test_run():
            return

        civicos = getattr(settings, "CIVICOS", {})
        clamav_required = civicos.get("CLAMAV_REQUIRED", False)
        clamav_host = civicos.get("CLAMAV_HOST", "")
        clamav_port = civicos.get("CLAMAV_PORT", 3310)

        if not clamav_host:
            msg = (
                "CIVICOS['CLAMAV_HOST'] is not configured. "
                "Document uploads will bypass ClamAV virus scanning. "
                "Set CIVICOS['CLAMAV_HOST'] to the clamd socket host."
            )
            if clamav_required:
                raise ImproperlyConfigured(msg)
            else:
                logger.warning(msg)

        if not isinstance(clamav_port, int) or not (1 <= clamav_port <= 65535):
            msg = (
                "CIVICOS['CLAMAV_PORT'] must be an integer between 1 and 65535. "
                f"Got: {clamav_port!r}."
            )
            if clamav_required:
                raise ImproperlyConfigured(msg)
            else:
                logger.warning(msg)

    @staticmethod
    def _is_test_run() -> bool:
        """True when running under Django's test runner or pytest."""
        if getattr(settings, "TESTING", False):
            return True
        argv = sys.argv
        return bool(argv) and argv[0].endswith(("manage.py", "pytest")) and (
            len(argv) > 1 and argv[1] in ("test", "pytest")
        )

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

import sys

from django.apps import AppConfig


class VolunteersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.volunteers"
    label = "volunteers"
    verbose_name = "Volunteer Management"

    def ready(self) -> None:
        import apps.volunteers.receivers
        import apps.volunteers.signals  # noqa: F401 — define signals

        self._check_sin_key_config()
        self._check_media_storage_config()

    @staticmethod
    def _is_test_run() -> bool:
        """
        Return True if we are running inside a test harness.

        Checks both the settings flag (TESTING=True, set in config/settings/test.py)
        and sys.argv so that test runners invoked without the test settings file
        (e.g. `pytest` with DJANGO_SETTINGS_MODULE still pointing at dev settings)
        are also detected reliably.
        """
        from django.conf import settings

        return getattr(settings, "TESTING", False) or (
            len(sys.argv) >= 2 and sys.argv[1] in ("test", "pytest")
        )

    def _check_sin_key_config(self) -> None:
        """
        Warn at startup if SIN encryption keys are not configured.
        Raises ImproperlyConfigured in production (DEBUG=False, not a test run)
        to prevent silently storing unencrypted SINs.
        TESTING=True (set in config/settings/test.py) suppresses the hard error
        so the test suite can boot without real Fernet keys.
        """
        import logging

        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        logger = logging.getLogger(__name__)

        keys = getattr(settings, "VOLUNTEER_SIN_FERNET_KEYS", [])
        debug = getattr(settings, "DEBUG", False)

        if not keys:
            msg = (
                "VOLUNTEER_SIN_FERNET_KEYS is not set. "
                "SIN encryption is non-functional. "
                "Set this to a list of Fernet keys in your environment."
            )
            if not debug and not self._is_test_run():
                raise ImproperlyConfigured(msg)
            else:
                logger.warning("volunteers: %s", msg)

    def _check_media_storage_config(self) -> None:
        """
        Ensure private volunteer media (photos, certifications) is not accidentally
        served via FileSystemStorage in a non-debug environment.
        TESTING=True (set in config/settings/test.py) suppresses the hard error
        so the test suite can boot with the temp-directory FileSystemStorage.
        """
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        if getattr(settings, "DEBUG", False):
            return  # Development: FileSystemStorage is acceptable

        if self._is_test_run():
            return  # Test suite: FileSystemStorage pointed at tempfile.mkdtemp() is safe

        storages = getattr(settings, "STORAGES", {})
        default_storage = storages.get("default", {})
        if not isinstance(default_storage, dict):
            return  # non-standard config (e.g. a storage class instance), skip check
        default_backend = default_storage.get("BACKEND", "")
        if "FileSystemStorage" in default_backend:
            raise ImproperlyConfigured(
                "STORAGES['default'] is FileSystemStorage in a non-debug environment. "
                "Volunteer photos and certification documents would be publicly accessible. "
                "Configure S3 or equivalent private storage in production.py."
            )

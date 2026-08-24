from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payments"
    label = "payments"
    verbose_name = "Payments"

    def ready(self) -> None:
        # Guard: FERNET_KEYS is required for encrypting/decrypting sensitive fields.
        # Without it the app falls back to SECRET_KEY (see models._get_fernet), which
        # ties encryption-key rotation to the Django signing key — a security risk.
        # Raise at app-load time so the misconfiguration is caught at startup, not
        # when the first encrypted field is accessed in a live request.
        from django.conf import settings

        import apps.payments.receivers
        import apps.payments.signals  # noqa: F401 — ensure signals module is loaded

        if (
            not getattr(settings, "FERNET_KEYS", None)
            and not getattr(settings, "DEBUG", False)
            and not getattr(settings, "TESTING", False)
        ):
            raise ImproperlyConfigured(
                "FERNET_KEYS is required for the Payments app in non-debug environments. "
                "Set it as a comma-separated list of Fernet keys. "
                'Generate a key with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )

        from apps.payments.receivers import on_donation_completed, on_receipt_issued
        from apps.payments.signals import donation_completed, receipt_issued

        donation_completed.connect(
            on_donation_completed,
            dispatch_uid="payments.on_donation_completed",
        )
        receipt_issued.connect(
            on_receipt_issued,
            dispatch_uid="payments.on_receipt_issued",
        )

        # Warn if WeasyPrint's system dependencies (Pango, Cairo) are missing.
        # A missing shared library causes PDF generation to fail at runtime with
        # a confusing ImportError or OSError, not at startup.
        try:
            import weasyprint  # noqa: F401
        except OSError as exc:
            import logging

            logging.getLogger(__name__).warning(
                "payments.startup.weasyprint_unavailable exc_type=%s — "
                "PDF receipt generation will fail. Install system deps: "
                "libpango, libcairo, libgdk-pixbuf.",
                type(exc).__name__,
            )
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "payments.startup.weasyprint_not_installed — "
                "PDF receipt generation will fail. Run: pip install weasyprint"
            )

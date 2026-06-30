from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payments"
    label = "payments"
    verbose_name = "Payments"

    def ready(self):
        import apps.payments.signals   # noqa: F401 — ensure signals module is loaded
        import apps.payments.receivers  # noqa: F401

        from apps.payments.signals import donation_completed, receipt_issued
        from apps.payments.receivers import on_donation_completed, on_receipt_issued

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
                "payments.startup.weasyprint_unavailable: %s — "
                "PDF receipt generation will fail. Install system deps: "
                "libpango, libcairo, libgdk-pixbuf. exc_type=%s",
                type(exc).__name__,
                type(exc).__name__,
            )
        except ImportError:
            import logging
            logging.getLogger(__name__).warning(
                "payments.startup.weasyprint_not_installed — "
                "PDF receipt generation will fail. Run: pip install weasyprint"
            )

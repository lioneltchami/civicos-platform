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

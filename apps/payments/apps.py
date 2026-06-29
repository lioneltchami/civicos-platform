from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payments"
    label = "payments"
    verbose_name = "Payments"

    def ready(self):
        import apps.payments.signals   # noqa: F401
        import apps.payments.receivers  # noqa: F401

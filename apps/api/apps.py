from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = "apps.api"
    verbose_name = "API"

    def ready(self) -> None:
        """Register drf-spectacular extensions during Django app loading."""
        from apps.api import schema as _schema  # noqa: F401

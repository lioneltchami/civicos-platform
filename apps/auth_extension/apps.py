from django.apps import AppConfig


class GovstackAuthConfig(AppConfig):
    name = "apps.auth_extension"
    verbose_name = "Authentication"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        import apps.auth_extension.signals  # noqa: F401

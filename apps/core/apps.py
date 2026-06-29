from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Core"

    def ready(self) -> None:
        # Import signals module so Signal objects are instantiated and available
        # for other apps to connect to via their own AppConfig.ready() hooks.
        import apps.core.signals  # noqa: F401  — side-effect import; defines signals

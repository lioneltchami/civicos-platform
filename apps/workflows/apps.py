from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workflows"
    verbose_name = "Workflows"

    def ready(self) -> None:
        # Connect signal handlers declared in handlers.py
        import apps.workflows.handlers  # noqa: F401

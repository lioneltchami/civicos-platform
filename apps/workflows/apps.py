from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    # Omit default_auto_field — BaseModel supplies a UUID PK, so this setting
    # would only apply to any future model that forgets to declare a PK.
    # Inheriting from the project-wide DEFAULT_AUTO_FIELD in settings is safer.
    name = "apps.workflows"
    verbose_name = "Workflows"

    def ready(self) -> None:
        # Import handler functions (this module is imported for the first time
        # here, so no double-connect risk from @receiver decorators).
        from apps.core.signals import service_request_submitted
        from apps.workflows.handlers import (
            audit_work_item_assigned,
            audit_work_item_created,
            audit_work_item_escalated,
            audit_work_item_status_changed,
            on_service_request_submitted,
        )
        from apps.workflows.signals import (
            work_item_assigned,
            work_item_created,
            work_item_escalated,
            work_item_status_changed,
        )

        # Portal → Workflows
        service_request_submitted.connect(on_service_request_submitted)

        # Workflows → Audit
        work_item_created.connect(audit_work_item_created)
        work_item_assigned.connect(audit_work_item_assigned)
        work_item_status_changed.connect(audit_work_item_status_changed)
        work_item_escalated.connect(audit_work_item_escalated)

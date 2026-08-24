from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class FormsConfig(AppConfig):
    name = "apps.forms"
    verbose_name = _("Form Builder")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Signal handlers would be imported here
        pass

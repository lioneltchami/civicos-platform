from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BackofficeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.backoffice"
    verbose_name = _("Back-office")

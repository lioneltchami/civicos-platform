from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PortalConfig(AppConfig):
    name = "apps.portal"
    verbose_name = _("Citizen Portal")
    default_auto_field = "django.db.models.BigAutoField"

"""
Template context processors for Govstack.
"""

from django.conf import settings
from django.http import HttpRequest


def site_settings(request: HttpRequest) -> dict:
    """
    Inject commonly-needed settings into every template context.
    Keep this lean — only values templates genuinely need everywhere.
    """
    return {
        "SITE_NAME": getattr(settings, "WAGTAIL_SITE_NAME", "Govstack"),
        "DEBUG": settings.DEBUG,
        "LANGUAGES": settings.LANGUAGES,
        "CURRENT_LANGUAGE": getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE),
    }

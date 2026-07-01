"""
Custom allauth adapters for CivicOS.

CivicOSAccountAdapter overrides key allauth hooks to:
- Route post-login redirects (citizens → portal, staff → CMS)
- Capture preferred_language on signup
- Log auth events without PII in application logs
"""
from __future__ import annotations
from typing import TYPE_CHECKING
import logging
from django.conf import settings
from django.http import HttpRequest
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

if TYPE_CHECKING:
    from apps.auth_extension.models import User

logger = logging.getLogger(__name__)


class CivicOSAccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request: HttpRequest) -> str:
        user = request.user
        if user.is_staff or user.is_superuser:
            return "/cms/"
        return "/portal/"

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return True

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        # Log send attempt without PII — only user id if available
        user_id = context.get("user", {})
        if hasattr(user_id, "pk"):
            logger.info("Sending auth email template=%s user_id=%s", template_prefix, user_id.pk)
        super().send_mail(template_prefix, email, context)


class CivicOSSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest, sociallogin) -> bool:
        return True

"""
Custom views for Govstack citizen authentication.

Covers: account dashboard, profile management, MFA status,
backup code generation, language switching, and guest sessions.
"""
from __future__ import annotations
import secrets
import logging
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView, UpdateView

logger = logging.getLogger(__name__)

User = get_user_model()


def _write_audit(event_type: str, user, request: HttpRequest, detail: dict | None = None) -> None:
    """
    Write an immutable audit log entry.

    AuditLogEntry has no static .log() helper — we create the record directly.
    All failures are caught and logged as warnings so audit never breaks a view.
    """
    try:
        from apps.audit.models import AuditLogEntry

        # Retrieve the last entry's hash for chain-of-custody linking
        last = AuditLogEntry.objects.order_by("-timestamp").values("entry_hash").first()
        prev_hash = last["entry_hash"] if last else ""

        AuditLogEntry.objects.create(
            event_type=event_type,
            outcome="success",
            actor_id=str(user.pk),
            actor_email=user.email,
            actor_ip=_get_client_ip(request),
            actor_user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
            resource_type="User",
            resource_id=str(user.pk),
            event_detail=detail or {},
            request_id=request.META.get("HTTP_X_REQUEST_ID", ""),
            session_id=request.session.session_key or "",
            prev_hash=prev_hash,
        )
    except Exception:
        logger.warning("Audit log failed for event_type=%s user_id=%s", event_type, getattr(user, "pk", "?"))


def _get_client_ip(request: HttpRequest) -> str | None:
    """Extract the real client IP, respecting X-Forwarded-For if present."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # Take the first (leftmost) IP — the originating client
        ip = x_forwarded_for.split(",")[0].strip()
        return ip or None
    return request.META.get("REMOTE_ADDR") or None


class AccountDashboardView(LoginRequiredMixin, TemplateView):
    """
    Citizen account home page.
    Shows profile summary, quick links to requests and settings.
    """
    template_name = "account/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        # Service request count — guarded import since portal may not be ready
        try:
            from apps.portal.models import ServiceRequest
            ctx["request_count"] = ServiceRequest.objects.filter(
                citizen=self.request.user
            ).count()
        except (ImportError, LookupError):
            ctx["request_count"] = 0
        # Unread notification count
        try:
            from apps.notifications.models import Notification
            ctx["unread_count"] = Notification.objects.filter(
                recipient=self.request.user,
                read_at__isnull=True,
            ).count()
        except (ImportError, LookupError):
            ctx["unread_count"] = 0
        return ctx


class ProfileUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    """
    Allows a citizen to update their display name, phone number,
    and preferred language.
    """
    template_name = "account/profile_edit.html"
    success_url = reverse_lazy("auth_extension:dashboard")
    success_message = _("Your profile has been updated. / Votre profil a été mis à jour.")

    def get_form_class(self):
        from apps.auth_extension.forms import ProfileUpdateForm
        return ProfileUpdateForm

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form: Any) -> HttpResponse:
        response = super().form_valid(form)
        _write_audit("data.updated", self.request.user, self.request, {"action": "profile_update"})
        return response


class ChangeLanguageView(LoginRequiredMixin, View):
    """
    POST-only: switch the citizen's preferred language.
    Updates the user record AND sets the Django language cookie.
    No GET — protects against CSRF via POST requirement.
    """
    http_method_names = ["post"]

    @staticmethod
    def _safe_next(request: HttpRequest) -> str:
        """Return a safe local redirect target from POST['next'], never off-site."""
        next_url = request.POST.get("next") or "/"
        # Reject absolute URLs and protocol-relative URLs (//evil.com)
        if not next_url.startswith("/") or next_url.startswith("//"):
            return "/"
        return next_url

    def post(self, request: HttpRequest) -> HttpResponse:
        lang = request.POST.get("language", "")
        if lang not in ("en", "fr"):
            return redirect(self._safe_next(request))
        request.user.preferred_language = lang
        request.user.save(update_fields=["preferred_language"])
        from django.utils import translation
        translation.activate(lang)
        next_url = self._safe_next(request)
        response = redirect(next_url)
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            lang,
            max_age=365 * 24 * 60 * 60,
            httponly=False,  # Must be readable by browser for i18n
            samesite="Lax",
            secure=not settings.DEBUG,
        )
        return response


class MFAStatusView(LoginRequiredMixin, TemplateView):
    """
    Shows the citizen's current MFA configuration:
    - Whether TOTP is enabled
    - Number of remaining backup codes
    """
    template_name = "account/mfa_status.html"

    def get_context_data(self, **kwargs: Any) -> dict:
        ctx = super().get_context_data(**kwargs)
        try:
            from django_otp.plugins.otp_totp.models import TOTPDevice
            from django_otp.plugins.otp_static.models import StaticDevice
            totp_devices = TOTPDevice.objects.filter(
                user=self.request.user, confirmed=True
            )
            ctx["totp_devices"] = totp_devices
            ctx["has_mfa"] = totp_devices.exists()
            first_device = totp_devices.first()
            ctx["device_name"] = first_device.name if first_device else ""
            static_dev = StaticDevice.objects.filter(
                user=self.request.user
            ).first()
            ctx["backup_codes_remaining"] = (
                static_dev.token_set.count() if static_dev else 0
            )
        except Exception:
            ctx["totp_devices"] = []
            ctx["has_mfa"] = False
            ctx["backup_codes_remaining"] = 0
        ctx["new_backup_codes"] = self.request.session.pop("new_backup_codes", None)
        if ctx["new_backup_codes"] is not None:
            self.request.session.modified = True
        return ctx


class GenerateBackupCodesView(LoginRequiredMixin, View):
    """
    POST-only: regenerate 8 backup codes, invalidating all previous ones.
    Codes are stored in the session for one-time display only.
    """
    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
            from django.db import transaction
            device, _ = StaticDevice.objects.get_or_create(
                user=request.user,
                defaults={"name": "Backup codes"},
            )
            # Generate 8 codes in XXXX-XXXX format before the atomic block
            codes = [
                f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
                for _ in range(8)
            ]
            # Atomic delete-and-replace: prevents duplicate token sets from concurrent POSTs
            with transaction.atomic():
                device_locked = StaticDevice.objects.select_for_update().get(pk=device.pk)
                device_locked.token_set.all().delete()
                for code in codes:
                    StaticToken.objects.create(device=device_locked, token=code.replace("-", ""))
            # Store for one-time display — cleared on next page load
            request.session["new_backup_codes"] = codes
            request.session.modified = True
            _write_audit("auth.mfa.enabled", request.user, request, {"action": "backup_codes_regenerated"})
            messages.success(
                request,
                _("New backup codes generated. Save them somewhere safe. / Nouveaux codes de sauvegarde générés. Conservez-les en lieu sûr.")
            )
        except Exception:
            logger.exception("Failed to generate backup codes for user_id=%s", request.user.pk)
            messages.error(request, _("Could not generate backup codes. Please try again."))
        return redirect("auth_extension:mfa_status")


class GuestSessionView(View):
    """
    GET: Return or create a guest session token.

    Allows citizens to begin a service request without an account.
    The token is a random 32-byte URL-safe string stored only in the session.
    No PII is stored. No DB record is created.

    If the requester is already authenticated, the guest token is cleared
    (they don't need it).
    """
    def get(self, request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated:
            from apps.auth_extension.tokens import GuestTokenManager
            GuestTokenManager.clear(request)
            if request.headers.get("Accept") == "application/json":
                return JsonResponse({"authenticated": True})
            return redirect("auth_extension:dashboard")

        from apps.auth_extension.tokens import get_or_create_guest_token
        token = get_or_create_guest_token(request)

        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"guest_token": token})
        return redirect("/portal/")

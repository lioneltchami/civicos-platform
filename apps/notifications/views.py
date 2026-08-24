"""
Citizen notification inbox views.

Citizens can view their notification history and mark notifications as read.
All views require authentication.
"""

from __future__ import annotations

import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from .models import Notification

logger = logging.getLogger(__name__)


class NotificationListView(LoginRequiredMixin, ListView):
    """
    Citizen notification inbox — paginated list of all notifications.
    Marks all notifications as read when the page is loaded.
    Supports filtering by channel and read/unread status.
    """

    template_name = "notifications/inbox.html"
    context_object_name = "notifications"
    paginate_by = 20

    def get_queryset(self):  # noqa: ANN201
        qs = Notification.objects.filter(recipient=self.request.user).order_by("-created_at")

        # Filter by read/unread
        filter_param = self.request.GET.get("filter", "all")
        if filter_param == "unread":
            qs = qs.filter(read_at__isnull=True)
        elif filter_param == "read":
            qs = qs.filter(read_at__isnull=False)

        # Filter by channel
        channel = self.request.GET.get("channel", "")
        if channel in ("email", "sms", "in_app"):
            qs = qs.filter(channel=channel)

        return qs

    def get_context_data(self, **kwargs) -> dict:  # noqa: ANN003
        ctx = super().get_context_data(**kwargs)
        # Build a channel-scoped unread count matching the active channel filter
        unread_qs = Notification.objects.filter(recipient=self.request.user, read_at__isnull=True)
        channel = self.request.GET.get("channel", "")
        if channel in ("email", "sms", "in_app"):
            unread_qs = unread_qs.filter(channel=channel)
        ctx["unread_count"] = unread_qs.count()
        ctx["active_filter"] = self.request.GET.get("filter", "all")
        ctx["active_channel"] = self.request.GET.get("channel", "")
        return ctx


class MarkNotificationReadView(LoginRequiredMixin, View):
    """
    POST: Mark a single notification as read.
    Citizen can only mark their own notifications.
    """

    http_method_names = ["post"]  # noqa: RUF012

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])

        # If AJAX/fetch request, return JSON
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"status": "ok", "read_at": notification.read_at.isoformat()})

        # Otherwise redirect back to inbox or referrer.
        # Guard: only allow relative paths that start with exactly one "/".
        # Reject absolute URLs ("https://evil.com"), protocol-relative URLs
        # ("//evil.com") and empty strings.
        next_url = request.POST.get("next", "")
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect("notifications:inbox")


class MarkAllReadView(LoginRequiredMixin, View):
    """
    POST: Mark ALL unread notifications for the current user as read.
    """

    http_method_names = ["post"]  # noqa: RUF012

    def post(self, request: HttpRequest) -> HttpResponse:
        now = timezone.now()
        # Include updated_at explicitly: auto_now fields are NOT touched by
        # QuerySet.update() — only Model.save() honours auto_now.
        updated = Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True,
        ).update(read_at=now, updated_at=now)

        logger.info("Marked %d notifications as read for user_id=%s", updated, request.user.pk)

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"status": "ok", "marked_count": updated})
        return redirect("notifications:inbox")


class UnreadCountView(LoginRequiredMixin, View):
    """
    GET: Returns the unread notification count as JSON.
    Used by the frontend to update the bell badge without a full page reload.
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        count = Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True,
        ).count()
        return JsonResponse({"unread_count": count})

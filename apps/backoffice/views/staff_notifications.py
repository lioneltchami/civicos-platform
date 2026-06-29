"""
Staff notification management views.

StaffNotificationListView: browse all outbound notifications (all channels).
StaffNotificationSendView: send a one-off email notification to a citizen.
"""

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import ListView

from apps.backoffice.forms.staff_notifications import StaffNotificationSendForm
from apps.backoffice.mixins import StaffRequiredMixin
from apps.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
)

logger = logging.getLogger(__name__)

User = get_user_model()


class StaffNotificationListView(StaffRequiredMixin, ListView):
    """
    Browse all outbound notifications across channels.

    Supports filtering by channel, status, and free-text search on
    recipient email and subject.  Paginated at 25 entries per page.
    """

    template_name = "backoffice/staff_notifications/list.html"
    context_object_name = "notifications"
    paginate_by = 25

    def get_queryset(self):
        qs = Notification.objects.select_related("recipient").order_by("-created_at")

        # --- channel filter ---
        channel = self.request.GET.get("channel", "").strip()
        if channel:
            valid_channels = {choice[0] for choice in NotificationChannel.choices}
            if channel in valid_channels:
                qs = qs.filter(channel=channel)

        # --- status filter ---
        status = self.request.GET.get("status", "").strip()
        if status:
            valid_statuses = {choice[0] for choice in NotificationStatus.choices}
            if status in valid_statuses:
                qs = qs.filter(status=status)

        # --- free-text search (recipient email or subject) ---
        q = self.request.GET.get("q", "").strip()
        if q:
            from django.db.models import Q

            qs = qs.filter(
                Q(recipient__email__icontains=q) | Q(subject__icontains=q)
            )

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["channel_choices"] = NotificationChannel.choices
        ctx["status_choices"] = NotificationStatus.choices

        # Current filter values for re-populating the filter bar
        ctx["current_channel"] = self.request.GET.get("channel", "")
        ctx["current_status"] = self.request.GET.get("status", "")
        ctx["current_q"] = self.request.GET.get("q", "")

        # Preserve filter params for pagination links
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["filter_params"] = params.urlencode()
        return ctx


class StaffNotificationSendView(StaffRequiredMixin, View):
    """
    Allow staff to send a one-off email notification to a single citizen.

    GET  — render the send form.
    POST — validate, look up citizen, send email, redirect on success.

    Uses POST-redirect-GET to prevent form re-submission on browser refresh.
    Wraps the send operation in a database transaction so the Notification
    record is always consistent with the email send attempt.
    """

    template_name = "backoffice/staff_notifications/send.html"

    def get(self, request):
        form = StaffNotificationSendForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = StaffNotificationSendForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        recipient_email = form.cleaned_data["recipient_email"]
        subject = form.cleaned_data["subject"]
        body = form.cleaned_data["body"]

        # Look up the citizen — must exist and must not be staff.
        recipient = (
            User.objects.filter(email=recipient_email, is_staff=False)
            .first()
        )
        if recipient is None:
            form.add_error(
                "recipient_email",
                _("No citizen with that email address."),
            )
            return render(request, self.template_name, {"form": form})

        try:
            self._send_notification(request, recipient, subject, body)
        except Exception:
            # _send_notification logs the exception; re-render with a generic error.
            messages.error(
                request,
                _("The notification could not be sent due to a delivery error. Please try again."),
            )
            return render(request, self.template_name, {"form": form})

        messages.success(request, _("Notification sent."))
        return redirect("backoffice:notification-list")

    @staticmethod
    @transaction.atomic
    def _send_notification(request, recipient, subject: str, body: str) -> None:
        """
        Create the Notification record and send the email inside a transaction.

        We create the record with PENDING status first so we have a database
        record even if the SMTP call raises.  On success the status is updated
        to SENT; on failure we set FAILED and re-raise so the outer try/except
        can show the user an error.
        """
        from django.conf import settings

        notification = Notification.objects.create(
            recipient=recipient,
            channel=NotificationChannel.EMAIL,
            subject=subject,
            body=body,
            status=NotificationStatus.PENDING,
        )

        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient.email],
                fail_silently=False,
            )
            notification.status = NotificationStatus.SENT
            notification.sent_at = timezone.now()
            notification.save(update_fields=["status", "sent_at"])

            logger.info(
                "backoffice: email notification sent to citizen pk=%d by staff pk=%d",
                recipient.pk,
                request.user.pk,
            )

        except Exception:
            notification.status = NotificationStatus.FAILED
            notification.save(update_fields=["status"])
            logger.exception(
                "backoffice: failed to send email notification to citizen pk=%d by staff pk=%d",
                recipient.pk,
                request.user.pk,
            )
            raise

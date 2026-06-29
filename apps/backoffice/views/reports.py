"""
Back-office reports and statistics view.

Aggregate read-only stats for management reporting.
All queries use ORM aggregation — no raw SQL.
"""

import logging
from datetime import timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import TruncDay
from django.utils import timezone
from django.views.generic import TemplateView

from apps.backoffice.mixins import StaffRequiredMixin
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.portal.models import ServiceRequest, ServiceRequestStatus
from apps.workflows.models import WorkItem, WorkItemStatus

logger = logging.getLogger(__name__)


class ReportsView(StaffRequiredMixin, TemplateView):
    """
    Management reporting overview.

    Aggregates stats across service requests, work items, and notifications.
    All data is read-only — no mutations in this view.
    """

    template_name = "backoffice/reports/overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        thirty_days_ago = timezone.now() - timedelta(days=30)

        # -----------------------------------------------------------------------
        # Service request stats
        # -----------------------------------------------------------------------

        # Requests by status
        sr_by_status = (
            ServiceRequest.objects
            .values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        )

        # Top 10 services by request count
        sr_by_service = (
            ServiceRequest.objects
            .values("service_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        # Daily request counts for the last 30 days
        sr_recent = (
            ServiceRequest.objects
            .filter(created_at__gte=thirty_days_ago)
            .annotate(day=TruncDay("created_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )

        # Total request count
        sr_total = ServiceRequest.objects.count()

        # -----------------------------------------------------------------------
        # Work item stats
        # -----------------------------------------------------------------------

        # SLA breaches by priority
        sla_breaches = (
            WorkItem.objects
            .filter(sla_breached_at__isnull=False)
            .values("priority")
            .annotate(count=Count("id"))
            .order_by("priority")
        )

        # Average time to completion (completed items only)
        avg_completion = None
        try:
            avg_completion = (
                WorkItem.objects
                .filter(
                    status=WorkItemStatus.COMPLETED,
                    completed_at__isnull=False,
                )
                .annotate(
                    duration=ExpressionWrapper(
                        F("completed_at") - F("created_at"),
                        output_field=DurationField(),
                    )
                )
                .aggregate(avg=Avg("duration"))["avg"]
            )
        except Exception:
            # Gracefully handle cases where WorkItem fields are missing
            # (e.g. during parallel migrations from another agent).
            logger.warning("reports: could not compute avg_completion", exc_info=True)

        # -----------------------------------------------------------------------
        # Notification stats
        # -----------------------------------------------------------------------

        notif_by_channel = (
            Notification.objects
            .values("channel")
            .annotate(count=Count("id"))
            .order_by("channel")
        )

        notif_by_status = (
            Notification.objects
            .values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        )

        notif_total = Notification.objects.count()

        # -----------------------------------------------------------------------
        # Build context
        # -----------------------------------------------------------------------

        sr_by_status_list = list(sr_by_status)
        sr_max_count = max((row["count"] for row in sr_by_status_list), default=1)

        ctx.update(
            {
                # Service requests
                "sr_by_status": sr_by_status_list,
                "sr_max_count": sr_max_count,
                "sr_by_service": list(sr_by_service),
                "sr_recent": list(sr_recent),
                "sr_total": sr_total,
                # Work items
                "sla_breaches": list(sla_breaches),
                "avg_completion": avg_completion,
                # Notifications
                "notif_by_channel": list(notif_by_channel),
                "notif_by_status": list(notif_by_status),
                "notif_total": notif_total,
                # Metadata
                "generated_at": timezone.now(),
                # Choices for display labels in template
                "service_request_status_choices": dict(ServiceRequestStatus.choices),
                "notif_channel_choices": dict(NotificationChannel.choices),
                "notif_status_choices": dict(NotificationStatus.choices),
            }
        )
        return ctx

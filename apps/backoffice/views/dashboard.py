"""Back-office dashboard — aggregate stats for staff."""

import logging

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.audit.models import AuditLogEntry
from apps.backoffice.mixins import StaffRequiredMixin
from apps.portal.models import ServiceRequest, ServiceRequestStatus
from apps.workflows.models import WorkItem, WorkItemStatus

logger = logging.getLogger(__name__)
User = get_user_model()


class DashboardView(StaffRequiredMixin, TemplateView):
    """
    Back-office landing page showing aggregated platform statistics.

    Displays service request counts by status, work item queue health,
    recent audit log entries, and citizen counts.  All data is read-only.
    """

    template_name = "backoffice/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # ----------------------------------------------------------------
        # Service request stats
        # ----------------------------------------------------------------
        today = timezone.now().date()
        sr_counts = ServiceRequest.objects.aggregate(
            sr_total=Count("id"),
            sr_submitted=Count("id", filter=Q(status=ServiceRequestStatus.SUBMITTED)),
            sr_in_review=Count("id", filter=Q(status=ServiceRequestStatus.IN_REVIEW)),
            sr_awaiting=Count("id", filter=Q(status=ServiceRequestStatus.AWAITING_INFO)),
            sr_approved_today=Count(
                "id",
                filter=Q(status=ServiceRequestStatus.APPROVED, updated_at__date=today),
            ),
        )
        ctx.update(sr_counts)

        # ----------------------------------------------------------------
        # Work item stats (open = non-terminal statuses)
        # ----------------------------------------------------------------
        open_statuses = [
            WorkItemStatus.PENDING,
            WorkItemStatus.IN_PROGRESS,
            WorkItemStatus.WAITING,
        ]
        wi_counts = WorkItem.objects.filter(status__in=open_statuses).aggregate(
            wi_open=Count("id"),
            wi_overdue=Count("id", filter=Q(sla_breached_at__isnull=False)),
            wi_unassigned=Count("id", filter=Q(assigned_to__isnull=True)),
            wi_mine=Count("id", filter=Q(assigned_to=self.request.user)),
        )
        ctx.update(wi_counts)

        # ----------------------------------------------------------------
        # Citizen stats
        # ----------------------------------------------------------------
        ctx["citizen_count"] = User.objects.filter(
            is_staff=False, is_active=True
        ).count()

        # ----------------------------------------------------------------
        # Recent audit log (10 entries, actor pre-fetched)
        # ----------------------------------------------------------------
        ctx["recent_audit"] = (
            AuditLogEntry.objects.order_by("-timestamp")[:10]
        )

        return ctx

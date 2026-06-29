"""Back-office dashboard — aggregate stats for staff."""

import logging

from django.contrib.auth import get_user_model
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
        sr_qs = ServiceRequest.objects.all()
        ctx["sr_total"] = sr_qs.count()
        ctx["sr_submitted"] = sr_qs.filter(
            status=ServiceRequestStatus.SUBMITTED
        ).count()
        ctx["sr_in_review"] = sr_qs.filter(
            status=ServiceRequestStatus.IN_REVIEW
        ).count()
        ctx["sr_awaiting"] = sr_qs.filter(
            status=ServiceRequestStatus.AWAITING_INFO
        ).count()
        ctx["sr_approved_today"] = sr_qs.filter(
            status=ServiceRequestStatus.APPROVED,
            updated_at__date=timezone.now().date(),
        ).count()

        # ----------------------------------------------------------------
        # Work item stats (open = non-terminal statuses)
        # ----------------------------------------------------------------
        open_statuses = [
            WorkItemStatus.PENDING,
            WorkItemStatus.IN_PROGRESS,
            WorkItemStatus.WAITING,
        ]
        wi_qs = WorkItem.objects.filter(status__in=open_statuses)
        ctx["wi_open"] = wi_qs.count()
        ctx["wi_overdue"] = wi_qs.filter(sla_breached_at__isnull=False).count()
        ctx["wi_unassigned"] = wi_qs.filter(assigned_to__isnull=True).count()
        ctx["wi_mine"] = wi_qs.filter(assigned_to=self.request.user).count()

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

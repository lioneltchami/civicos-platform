"""
Audit log viewer for back-office staff.

Provides a filterable, paginated view of the immutable AuditLogEntry table.
Staff can filter by event type, date range, and outcome.
The audit log is read-only — no mutations ever.
"""

import logging
from datetime import date

from django.views.generic import ListView

from apps.audit.models import AuditEventType, AuditLogEntry
from apps.backoffice.mixins import StaffRequiredMixin

logger = logging.getLogger(__name__)


class AuditLogListView(StaffRequiredMixin, ListView):
    """
    Paginated, filterable view of the immutable audit log.

    Supports filtering by event type, outcome, date range, and resource type.
    All queries are read-only.  Pagination is 50 entries per page.
    """

    template_name = "backoffice/audit/list.html"
    context_object_name = "entries"
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLogEntry.objects.order_by("-timestamp")

        # --- event_type filter ---
        event_type = self.request.GET.get("event_type", "").strip()
        if event_type:
            valid_values = {choice[0] for choice in AuditEventType.choices}
            if event_type in valid_values:
                qs = qs.filter(event_type=event_type)

        # --- outcome filter ---
        outcome = self.request.GET.get("outcome", "").strip()
        if outcome in ("success", "failure"):
            qs = qs.filter(outcome=outcome)

        # --- date_from filter ---
        date_from_raw = self.request.GET.get("date_from", "").strip()
        if date_from_raw:
            try:
                date_from = date.fromisoformat(date_from_raw)
                qs = qs.filter(timestamp__date__gte=date_from)
            except (ValueError, TypeError):
                pass  # silently ignore malformed dates

        # --- date_to filter ---
        date_to_raw = self.request.GET.get("date_to", "").strip()
        if date_to_raw:
            try:
                date_to = date.fromisoformat(date_to_raw)
                qs = qs.filter(timestamp__date__lte=date_to)
            except (ValueError, TypeError):
                pass  # silently ignore malformed dates

        # --- resource_type filter ---
        resource_type = self.request.GET.get("resource_type", "").strip()
        if resource_type:
            qs = qs.filter(resource_type__icontains=resource_type)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event_type_choices"] = AuditEventType.choices
        ctx["total_count"] = self.get_queryset().count()

        # Preserve current filter params for pagination links and template display
        params = self.request.GET.copy()
        # Remove Django's page param so we don't double-encode it
        params.pop("page", None)
        ctx["filter_params"] = params.urlencode()
        ctx["current_event_type"] = self.request.GET.get("event_type", "")
        ctx["current_outcome"] = self.request.GET.get("outcome", "")
        ctx["current_date_from"] = self.request.GET.get("date_from", "")
        ctx["current_date_to"] = self.request.GET.get("date_to", "")
        ctx["current_resource_type"] = self.request.GET.get("resource_type", "")
        return ctx

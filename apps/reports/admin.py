"""
Analytics & Reporting BB — Django Admin.

Both models are read-only in the admin:
- ReportSnapshot: financial records, 7-year CRA retention. No add/change/delete.
- ExportRecord: audit trail. No add/change/delete.

Admin is primarily for ops visibility and debugging (e.g. checking if a snapshot
was computed, or auditing who exported what).
"""
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.reports.models import ExportRecord, ReportSnapshot


@admin.register(ReportSnapshot)
class ReportSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        "report_type",
        "period_year",
        "period_month",
        "row_count",
        "computed_at",
    ]
    list_filter = ["report_type", "period_year"]
    readonly_fields = [
        "report_type",
        "period_year",
        "period_month",
        "data",
        "row_count",
        "computed_at",
    ]
    ordering = ["-period_year", "-period_month", "report_type"]

    def has_add_permission(self, request):
        # Snapshots are written exclusively by the Celery task.
        return False

    def has_change_permission(self, request, obj=None):
        # Immutable once written — recomputation overwrites via update_or_create.
        return False

    def has_delete_permission(self, request, obj=None):
        # CRA financial records — 7-year retention minimum. Never delete via admin.
        return False


@admin.register(ExportRecord)
class ExportRecordAdmin(admin.ModelAdmin):
    list_display = [
        "export_type",
        "format",
        "period_start",
        "period_end",
        "actor_pk",
        "row_count",
        "created_at",
    ]
    list_filter = ["export_type", "format"]
    readonly_fields = [
        "export_type",
        "format",
        "period_start",
        "period_end",
        "actor_pk",
        "actor_ip",
        "row_count",
        "created_at",
    ]
    ordering = ["-created_at"]

    def has_add_permission(self, request):
        # Records are created by export views only.
        return False

    def has_change_permission(self, request, obj=None):
        # Audit trail is immutable.
        return False

    def has_delete_permission(self, request, obj=None):
        # Audit trail — never delete.
        return False

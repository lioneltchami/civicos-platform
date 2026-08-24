"""
Analytics & Reporting BB — Django Admin.

Both models are read-only in the admin:
- ReportSnapshot: financial records, 7-year CRA retention. No add/change/delete.
- ExportRecord: audit trail. No add/change/delete.

Admin is primarily for ops visibility and debugging (e.g. checking if a snapshot
was computed, or auditing who exported what).
"""

from django.contrib import admin

from apps.reports.models import ExportRecord, ReportSnapshot


@admin.register(ReportSnapshot)
class ReportSnapshotAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "report_type",
        "period_year",
        "period_month",
        "row_count",
        "computed_at",
    ]
    list_filter = ["report_type", "period_year"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "report_type",
        "period_year",
        "period_month",
        "data",
        "row_count",
        "computed_at",
    ]
    ordering = ["-period_year", "-period_month", "report_type"]  # noqa: RUF012

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        # Snapshots are written exclusively by the Celery task.
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Immutable once written — recomputation overwrites via update_or_create.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # CRA financial records — 7-year retention minimum. Never delete via admin.
        return False


@admin.register(ExportRecord)
class ExportRecordAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "export_type",
        "format",
        "period_start",
        "period_end",
        "actor_pk",
        "row_count",
        "created_at",
    ]
    list_filter = ["export_type", "format"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "export_type",
        "format",
        "period_start",
        "period_end",
        "actor_pk",
        "actor_ip",
        "row_count",
        "created_at",
    ]
    ordering = ["-created_at"]  # noqa: RUF012

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        # Records are created by export views only.
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Audit trail is immutable.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Audit trail — never delete.
        return False

"""
Django admin for the workflows building block.

WorkItems and their history are read-only in admin — mutations should happen
through the staff-facing queue views.  Admin is for superuser oversight only.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.workflows.models import WorkItem, WorkItemComment, WorkItemHistory


class WorkItemHistoryInline(admin.TabularInline):
    model = WorkItemHistory
    extra = 0
    readonly_fields = ("action", "old_status", "new_status", "actor", "notes", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


class WorkItemCommentInline(admin.TabularInline):
    model = WorkItemComment
    extra = 0
    readonly_fields = ("author", "body", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


@admin.register(WorkItem)
class WorkItemAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "priority_badge",
        "assigned_to",
        "due_at",
        "sla_status",
        "created_at",
    )
    list_filter = ("status", "priority", "escalation_level")
    search_fields = ("title", "description")
    # All fields are read-only — mutations must go through the service layer
    # (via the staff queue views) so the audit trail is never bypassed.
    readonly_fields = (
        "pk",
        "content_type",
        "object_id",
        "title",
        "description",
        "status",
        "assigned_to",
        "priority",
        "due_at",
        "sla_breached_at",
        "escalated_at",
        "escalation_level",
        "completed_at",
        "created_at",
        "updated_at",
    )
    inlines = [WorkItemHistoryInline, WorkItemCommentInline]  # noqa: RUF012
    ordering = ["priority", "due_at"]  # noqa: RUF012
    list_select_related = ("assigned_to",)

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Prevent any edits through admin — service layer is the only mutation path
        return False

    def priority_badge(self, obj):  # noqa: ANN001, ANN201
        colours = {1: "red", 2: "orange", 3: "blue", 4: "grey"}
        colour = colours.get(obj.priority, "grey")
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour,
            obj.get_priority_display(),
        )

    priority_badge.short_description = _("Priority")

    def sla_status(self, obj):  # noqa: ANN001, ANN201
        if obj.sla_breached_at:
            return format_html('<span style="color:red">⚠ Breached</span>')
        if obj.is_overdue:
            return format_html('<span style="color:orange">⚠ Overdue</span>')
        return format_html('<span style="color:green">✓ On track</span>')

    sla_status.short_description = _("SLA")

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        # Work items should be cancelled via the service layer, not deleted
        return False


@admin.register(WorkItemHistory)
class WorkItemHistoryAdmin(admin.ModelAdmin):
    list_display = ("work_item", "action", "old_status", "new_status", "actor", "created_at")
    list_filter = ("action",)
    search_fields = ("work_item__title", "notes")
    readonly_fields = (
        "work_item",
        "action",
        "old_status",
        "new_status",
        "actor",
        "notes",
        "created_at",
    )
    list_select_related = ("work_item", "actor")

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False

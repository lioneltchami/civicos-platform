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

    def has_add_permission(self, request, obj=None):
        return False


class WorkItemCommentInline(admin.TabularInline):
    model = WorkItemComment
    extra = 0
    readonly_fields = ("author", "body", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WorkItem)
class WorkItemAdmin(admin.ModelAdmin):
    list_display = (
        "title", "status", "priority_badge", "assigned_to",
        "due_at", "sla_status", "created_at",
    )
    list_filter = ("status", "priority", "escalation_level")
    search_fields = ("title", "description")
    readonly_fields = (
        "pk", "content_type", "object_id", "created_at", "updated_at",
        "sla_breached_at", "escalated_at", "completed_at",
    )
    inlines = [WorkItemHistoryInline, WorkItemCommentInline]
    ordering = ["priority", "due_at"]

    def priority_badge(self, obj):
        colours = {1: "red", 2: "orange", 3: "blue", 4: "grey"}
        colour = colours.get(obj.priority, "grey")
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour,
            obj.get_priority_display(),
        )
    priority_badge.short_description = _("Priority")

    def sla_status(self, obj):
        if obj.sla_breached_at:
            return format_html('<span style="color:red">⚠ Breached</span>')
        if obj.is_overdue:
            return format_html('<span style="color:orange">⚠ Overdue</span>')
        return format_html('<span style="color:green">✓ On track</span>')
    sla_status.short_description = _("SLA")

    def has_delete_permission(self, request, obj=None):
        # Work items should be cancelled via the service layer, not deleted
        return False


@admin.register(WorkItemHistory)
class WorkItemHistoryAdmin(admin.ModelAdmin):
    list_display = ("work_item", "action", "old_status", "new_status", "actor", "created_at")
    list_filter = ("action",)
    search_fields = ("work_item__title", "notes")
    readonly_fields = ("work_item", "action", "old_status", "new_status", "actor", "notes", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

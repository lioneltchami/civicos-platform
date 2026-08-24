"""Django admin for the notifications building block."""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Notification, NotificationStatus


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "recipient_email",
        "channel",
        "subject_preview",
        "status",
        "language",
        "sent_at",
        "read_at",
        "created_at",
    ]
    list_filter = ["channel", "status", "language", "created_at"]  # noqa: RUF012
    search_fields = ["recipient__email", "subject", "external_id"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "recipient",
        "channel",
        "subject",
        "body",
        "language",
        "status",
        "sent_at",
        "external_id",
        "read_at",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]  # noqa: RUF012

    actions = ["mark_as_failed"]  # noqa: RUF012

    def recipient_email(self, obj):  # noqa: ANN001, ANN201
        return obj.recipient.email

    recipient_email.short_description = _("Recipient")
    recipient_email.admin_order_field = "recipient__email"

    def subject_preview(self, obj):  # noqa: ANN001, ANN201
        return obj.subject[:60] + "…" if len(obj.subject) > 60 else obj.subject

    subject_preview.short_description = _("Subject")

    @admin.action(description=_("Mark selected as Failed"))
    def mark_as_failed(self, request, queryset) -> None:  # noqa: ANN001
        queryset.filter(status=NotificationStatus.PENDING).update(status=NotificationStatus.FAILED)

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False  # Notifications are system-generated only

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Immutable audit trail

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False  # Retain for audit

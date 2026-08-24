"""Django admin for the citizen portal — staff case management."""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import ServiceRequest, StatusUpdate
from .services import update_request_status


class StatusUpdateInline(admin.TabularInline):
    model = StatusUpdate
    extra = 0
    readonly_fields = ["old_status", "new_status", "changed_by", "public_note", "created_at"]  # noqa: RUF012
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "reference_number",
        "service_name",
        "citizen_email",
        "status_badge",
        "created_at",
        "updated_at",
    ]
    list_filter = ["status", "created_at"]  # noqa: RUF012
    search_fields = ["reference_number", "service_name", "citizen__email"]  # noqa: RUF012
    readonly_fields = [  # noqa: RUF012
        "reference_number",
        "citizen",
        "service_name",
        "service_page_id",
        "submission_data",
        "created_at",
        "updated_at",
        "expires_at",
    ]
    inlines = [StatusUpdateInline]  # noqa: RUF012
    actions = ["mark_in_review", "mark_awaiting_info", "mark_approved", "mark_rejected"]  # noqa: RUF012

    fieldsets = [  # noqa: RUF012
        (
            _("Request"),
            {
                "fields": [
                    "reference_number",
                    "service_name",
                    "service_page_id",
                    "status",
                ]
            },
        ),
        (
            _("Citizen"),
            {
                "fields": ["citizen"],
            },
        ),
        (
            _("Data"),
            {
                "fields": ["submission_data"],
                "classes": ["collapse"],
            },
        ),
        (
            _("Internal"),
            {
                "fields": [
                    "internal_notes",
                    "expires_at",
                    "created_at",
                    "updated_at",
                ]
            },
        ),
    ]

    def citizen_email(self, obj):  # noqa: ANN001, ANN201
        return obj.citizen.email

    citizen_email.short_description = _("Citizen email")

    def status_badge(self, obj):  # noqa: ANN001, ANN201
        colours = {
            "draft": "#6c757d",
            "submitted": "#0d6efd",
            "in_review": "#ffc107",
            "awaiting_info": "#fd7e14",
            "approved": "#198754",
            "rejected": "#dc3545",
            "closed": "#6c757d",
        }
        colour = colours.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px">{}</span>',
            colour,
            obj.get_status_display(),
        )

    status_badge.short_description = _("Status")

    @admin.action(description=_("Mark as In Review"))
    def mark_in_review(self, request, queryset) -> None:  # noqa: ANN001
        for sr in queryset:
            try:
                update_request_status(
                    sr,
                    "in_review",
                    request.user,
                    "Request is now under review. / La demande est en cours d'examen.",
                )
            except ValueError as exc:
                self.message_user(
                    request, f"{sr.reference_number}: {exc}", level=django_messages.WARNING
                )

    @admin.action(description=_("Mark as Awaiting Information"))
    def mark_awaiting_info(self, request, queryset) -> None:  # noqa: ANN001
        for sr in queryset:
            try:
                update_request_status(
                    sr,
                    "awaiting_info",
                    request.user,
                    "Additional information is required. / Des informations supplémentaires sont requises.",  # noqa: E501
                )
            except ValueError as exc:
                self.message_user(
                    request, f"{sr.reference_number}: {exc}", level=django_messages.WARNING
                )

    @admin.action(description=_("Mark as Approved"))
    def mark_approved(self, request, queryset) -> None:  # noqa: ANN001
        for sr in queryset:
            try:
                update_request_status(
                    sr,
                    "approved",
                    request.user,
                    "Your request has been approved. / Votre demande a été approuvée.",
                )
            except ValueError as exc:
                self.message_user(
                    request, f"{sr.reference_number}: {exc}", level=django_messages.WARNING
                )

    @admin.action(description=_("Mark as Rejected"))
    def mark_rejected(self, request, queryset) -> None:  # noqa: ANN001
        for sr in queryset:
            try:
                update_request_status(
                    sr,
                    "rejected",
                    request.user,
                    "Your request has been rejected. / Votre demande a été rejetée.",
                )
            except ValueError as exc:
                self.message_user(
                    request, f"{sr.reference_number}: {exc}", level=django_messages.WARNING
                )


@admin.register(StatusUpdate)
class StatusUpdateAdmin(admin.ModelAdmin):
    list_display = [  # noqa: RUF012
        "service_request",
        "old_status",
        "new_status",
        "changed_by",
        "created_at",
    ]
    readonly_fields = [  # noqa: RUF012
        "service_request",
        "old_status",
        "new_status",
        "changed_by",
        "public_note",
        "created_at",
    ]

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        return False

"""Django admin for the forms building block."""
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import FormField, FormPage, FormSubmission


@admin.register(FormSubmission)
class FormSubmissionAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "page_title", "submit_time", "consent_given",
        "has_pii_fields", "expires_at"
    ]
    list_filter = ["consent_given", "submit_time", "page"]
    readonly_fields = [
        "page", "form_data", "submit_time", "consent_given",
        "consent_text_shown", "submitter_ip", "expires_at"
    ]
    date_hierarchy = "submit_time"

    def page_title(self, obj):
        return obj.page.title
    page_title.short_description = _("Form page")

    def has_pii_fields(self, obj):
        return obj.page.form_fields.filter(is_pii=True).exists()
    has_pii_fields.boolean = True
    has_pii_fields.short_description = _("Has PII")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # Use redact_pii() instead of deletion

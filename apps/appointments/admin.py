"""
Appointments / Scheduling BB — Django admin (Wave 1: catalogue models).

Security contract:
  - No PII (email, name) is displayed in list views.  StaffProfile list shows
    only the PK and user_id (consistent with the PIPEDA-safe __str__).
  - All admin views require `is_staff=True` (enforced by OTPAdminSite on the
    django-admin site). Additional per-model custom permissions can be added
    in later waves when editing live schedule data is needed.
  - read_only display only for Wave 1 — catalogue records are low-risk but
    changing them without understanding downstream slot/booking state can break
    schedules. Wave 2+ will introduce guards inside model .clean() and service
    functions before allowing programmatic edits.

Wave 1 models registered here:
  Organization, SchedulingPolicy, ServiceType, AppointmentType,
  Location, Resource, StaffProfile.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import (
    AppointmentType,
    Location,
    Organization,
    Resource,
    SchedulingPolicy,
    ServiceType,
    StaffProfile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bool_icon(value: bool) -> str:
    """Return a safe HTML ✓/✗ indicator."""
    if value:
        return format_html('<span style="color:green;" aria-label="yes">✓</span>')
    return format_html('<span style="color:#c00;" aria-label="no">✗</span>')


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("id", "name_en", "slug", "organization_type", "is_active_icon", "created_at")
    list_filter = ("organization_type", "is_active")
    search_fields = ("name_en", "name_fr", "slug")
    ordering = ("name_en",)
    prepopulated_fields = {"slug": ("name_en",)}
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": (
                ("name_en", "name_fr"),
                "slug",
                "organization_type",
                "is_active",
            ),
        }),
        (_("Description"), {
            "classes": ("collapse",),
            "fields": ("description_en", "description_fr"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description=_("Active"), boolean=False)
    def is_active_icon(self, obj: Organization) -> str:
        return _bool_icon(obj.is_active)


# ---------------------------------------------------------------------------
# SchedulingPolicy
# ---------------------------------------------------------------------------


@admin.register(SchedulingPolicy)
class SchedulingPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "min_lead_time_hours",
        "max_advance_days",
        "slot_interval_minutes",
        "waitlist_enabled",
        "created_at",
    )
    search_fields = ("name",)
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": ("name", "description"),
        }),
        (_("Booking window"), {
            "fields": (
                ("min_lead_time_hours", "max_advance_days"),
            ),
        }),
        (_("Slot configuration"), {
            "fields": (
                ("slot_interval_minutes", "buffer_before_minutes", "buffer_after_minutes"),
            ),
        }),
        (_("Citizen limits"), {
            "fields": (
                "max_active_bookings_per_citizen",
                "booking_frequency_days",
                ("cancellation_notice_hours", "reschedule_notice_hours", "max_reschedule_count"),
            ),
        }),
        (_("Waitlist"), {
            "fields": (
                "waitlist_enabled",
                ("waitlist_acceptance_window_hours", "max_waitlist_per_slot", "waitlist_notify_batch_size"),
            ),
        }),
        (_("No-show thresholds"), {
            "fields": (
                ("no_show_warning_threshold", "no_show_suspension_threshold"),
            ),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )


# ---------------------------------------------------------------------------
# ServiceType
# ---------------------------------------------------------------------------


@admin.register(ServiceType)
class ServiceTypeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "slug",
        "name_en",
        "category",
        "sector",
        "privacy_sensitivity",
        "is_active_icon",
        "sort_order",
    )
    list_filter = ("category", "sector", "privacy_sensitivity", "is_active")
    search_fields = ("slug", "name_en", "name_fr")
    ordering = ("sort_order", "name_en")
    prepopulated_fields = {"slug": ("name_en",)}
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": (
                ("name_en", "name_fr"),
                "slug",
                ("category", "sector", "privacy_sensitivity"),
                ("is_active", "sort_order"),
            ),
        }),
        (_("Description"), {
            "classes": ("collapse",),
            "fields": ("description_en", "description_fr"),
        }),
        (_("Eligibility"), {
            "classes": ("collapse",),
            "fields": (
                "requires_eligibility_screening",
                "eligibility_description_en",
                "eligibility_description_fr",
            ),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description=_("Active"), boolean=False)
    def is_active_icon(self, obj: ServiceType) -> str:
        return _bool_icon(obj.is_active)


# ---------------------------------------------------------------------------
# AppointmentType
# ---------------------------------------------------------------------------


class AppointmentTypeStaffInline(admin.TabularInline):
    """Read-only inline to preview which staff members are linked (Wave 1)."""

    model = StaffProfile.appointment_types.through
    verbose_name = _("Staff member")
    verbose_name_plural = _("Staff members")
    extra = 0
    can_delete = False
    readonly_fields = ("staffprofile", "appointmenttype")

    def has_add_permission(self, request, obj=None):  # type: ignore[override]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[override]
        return False


@admin.register(AppointmentType)
class AppointmentTypeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "slug",
        "name_en",
        "service_type",
        "mode",
        "duration_minutes",
        "capacity_per_slot",
        "is_active_icon",
        "sort_order",
    )
    list_filter = ("mode", "is_active", "service_type__category", "requires_staff_confirmation")
    search_fields = ("slug", "name_en", "name_fr", "service_type__slug")
    ordering = ("sort_order", "name_en")
    autocomplete_fields = ("service_type", "scheduling_policy")
    prepopulated_fields = {"slug": ("name_en",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [AppointmentTypeStaffInline]
    fieldsets = (
        (None, {
            "fields": (
                "service_type",
                ("name_en", "name_fr"),
                "slug",
                ("mode", "duration_minutes", "capacity_per_slot"),
                "scheduling_policy",
                ("is_active", "sort_order"),
            ),
        }),
        (_("Description"), {
            "classes": ("collapse",),
            "fields": ("description_en", "description_fr"),
        }),
        (_("Booking rules"), {
            "fields": (
                ("requires_staff_confirmation", "allow_citizen_self_booking", "allow_walk_in"),
                "non_punitive_no_show",
                "interpreter_required_option",
            ),
        }),
        (_("Document requirement"), {
            "classes": ("collapse",),
            "fields": ("requires_document_upload", "required_document_category_slug"),
        }),
        (_("Payment"), {
            "classes": ("collapse",),
            "fields": ("requires_payment", "fee_code"),
        }),
        (_("Consent"), {
            "classes": ("collapse",),
            "fields": ("requires_consent", "consent_category_slug"),
        }),
        (_("Intake form schema (JSON Schema draft-07)"), {
            "classes": ("collapse",),
            "fields": ("intake_form_schema",),
        }),
        (_("CMS"), {
            "classes": ("collapse",),
            "fields": ("cms_page_id",),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description=_("Active"), boolean=False)
    def is_active_icon(self, obj: AppointmentType) -> str:
        return _bool_icon(obj.is_active)


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


class ResourceInline(admin.TabularInline):
    """Read-only inline showing resources attached to a location."""

    model = Resource
    verbose_name = _("Resource")
    verbose_name_plural = _("Resources")
    extra = 0
    can_delete = False
    readonly_fields = ("name_en", "name_fr", "resource_type", "capacity", "is_active")

    def has_add_permission(self, request, obj=None):  # type: ignore[override]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[override]
        return False


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "slug",
        "name_en",
        "organization",
        "is_virtual_icon",
        "province",
        "city",
        "privacy_regime",
        "is_active_icon",
    )
    list_filter = ("organization", "province", "privacy_regime", "is_active", "is_virtual")
    search_fields = ("slug", "name_en", "name_fr", "city")
    ordering = ("name_en",)
    autocomplete_fields = ("organization", "scheduling_policy")
    prepopulated_fields = {"slug": ("name_en",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [ResourceInline]
    fieldsets = (
        (None, {
            "fields": (
                "organization",
                ("name_en", "name_fr"),
                "slug",
                ("is_virtual", "is_active"),
                "scheduling_policy",
                ("privacy_regime", "timezone"),
            ),
        }),
        (_("Physical address"), {
            "classes": ("collapse",),
            "fields": (
                "street_address",
                ("city", "province", "postal_code"),
            ),
        }),
        (_("Contact"), {
            "classes": ("collapse",),
            "fields": (("phone_en", "phone_fr"), "tty_phone", "email"),
        }),
        (_("Accessibility"), {
            "classes": ("collapse",),
            "fields": ("accessibility_features_en", "accessibility_features_fr"),
        }),
        (_("Business hours (JSON)"), {
            "classes": ("collapse",),
            "fields": ("business_hours",),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description=_("Active"), boolean=False)
    def is_active_icon(self, obj: Location) -> str:
        return _bool_icon(obj.is_active)

    @admin.display(description=_("Virtual"), boolean=False)
    def is_virtual_icon(self, obj: Location) -> str:
        return _bool_icon(obj.is_virtual)


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name_en",
        "location",
        "resource_type",
        "capacity",
        "calendar_provider",
        "is_active_icon",
    )
    list_filter = ("resource_type", "calendar_provider", "is_active", "location__organization")
    search_fields = ("name_en", "name_fr", "location__name_en")
    ordering = ("location", "name_en")
    autocomplete_fields = ("location",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": (
                "location",
                ("name_en", "name_fr"),
                ("resource_type", "capacity"),
                "is_active",
            ),
        }),
        (_("Features (JSON)"), {
            "classes": ("collapse",),
            "fields": ("features",),
        }),
        (_("Calendar integration (Wave 7)"), {
            "classes": ("collapse",),
            "fields": ("calendar_provider", "external_calendar_id"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description=_("Active"), boolean=False)
    def is_active_icon(self, obj: Resource) -> str:
        return _bool_icon(obj.is_active)


# ---------------------------------------------------------------------------
# StaffProfile — PIPEDA: list view never shows email or full name.
# ---------------------------------------------------------------------------


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    """
    PIPEDA constraint: list_display must never include user.email or user
    fields that expose personal information. We show only the PK, user_id,
    location, and operational flags. The detail view shows display_name_en/fr
    (which are optional, non-PII-required fields chosen by the organization).
    """

    list_display = (
        "id",
        "user_id_display",
        "location",
        "is_accepting_bookings_icon",
        "accepts_walk_ins_icon",
        "created_at",
    )
    list_filter = ("is_accepting_bookings", "accepts_walk_ins", "location__organization", "video_provider")
    search_fields = ("location__name_en",)
    # Do NOT add search_fields for user__email — that would expose PII in search.
    ordering = ("user_id",)
    autocomplete_fields = ("location",)
    # TODO (Wave 2 permission matrix): filter_horizontal lets any is_staff admin
    # reassign appointment types for any StaffProfile, including profiles in other
    # organizations. Restrict to profiles scoped to the acting admin's organization
    # once the fine-grained permission matrix is implemented.
    filter_horizontal = ("appointment_types",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "description": _(
                "⚠ PIPEDA: This record contains personal information. "
                "Access is logged. Do not export or share this data outside "
                "authorized channels."
            ),
            "fields": (
                "user",
                "location",
                ("is_accepting_bookings", "accepts_walk_ins"),
                "max_daily_appointments",
                "appointment_types",
            ),
        }),
        (_("Citizen-facing display name (optional)"), {
            "classes": ("collapse",),
            "fields": ("display_name_en", "display_name_fr"),
        }),
        (_("Video integration (Wave 7)"), {
            "classes": ("collapse",),
            "fields": ("video_provider", "video_external_user_id"),
        }),
        (_("Calendar integration (Wave 7)"), {
            "classes": ("collapse",),
            "fields": ("calendar_integration_provider", "external_calendar_id"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description=_("User ID"))
    def user_id_display(self, obj: StaffProfile) -> str:
        # PIPEDA: return user_id only — never email or name.
        return str(obj.user_id)

    @admin.display(description=_("Accepting"), boolean=False)
    def is_accepting_bookings_icon(self, obj: StaffProfile) -> str:
        return _bool_icon(obj.is_accepting_bookings)

    @admin.display(description=_("Walk-ins"), boolean=False)
    def accepts_walk_ins_icon(self, obj: StaffProfile) -> str:
        return _bool_icon(obj.accepts_walk_ins)

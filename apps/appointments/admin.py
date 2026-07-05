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
    AvailabilityTemplate,
    Location,
    Organization,
    Resource,
    SchedulingPolicy,
    ServiceType,
    Slot,
    StaffException,
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

    def get_queryset(self, request):
        """
        H-7 security fix: scope queryset to the acting admin's own organization.

        Without this, any is_staff admin can open StaffProfile records from
        any organization and reassign appointment types via filter_horizontal —
        a confirmed cross-org data exposure vulnerability (see TODO above).

        Superusers retain unrestricted access. Non-superuser admins see only
        profiles whose primary location belongs to their own organization.
        If the admin user has no staff_profile or their location is unset,
        return an empty queryset (fail-closed) rather than exposing all records.
        """
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        try:
            admin_org = request.user.staff_profile.location.organization
            return qs.filter(location__organization=admin_org)
        except AttributeError:
            # Admin user has no staff_profile, or location is None — show nothing.
            return qs.none()

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


# ===========================================================================
# Wave 2 Admin: AvailabilityTemplate, StaffException, Slot
# ===========================================================================


class AvailabilityTemplateInline(admin.TabularInline):
    """
    Read-only inline showing availability templates on a StaffProfile detail page.
    Added to StaffProfileAdmin.inlines after class definition (see bottom of file).
    """

    model = AvailabilityTemplate
    verbose_name = _("Availability template")
    verbose_name_plural = _("Availability templates")
    extra = 0
    can_delete = False
    readonly_fields = ("day_of_week", "start_time", "end_time", "valid_from", "valid_until")

    def has_add_permission(self, request, obj=None):  # type: ignore[override]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[override]
        return False


@admin.register(AvailabilityTemplate)
class AvailabilityTemplateAdmin(admin.ModelAdmin):
    """
    Admin for staff availability templates.
    PIPEDA: list view shows staff_id (PK), not name or email.
    """

    list_display = (
        "id",
        "staff_id_display",
        "day_of_week",
        "start_time",
        "end_time",
        "valid_from",
        "valid_until",
    )
    list_filter = ("day_of_week",)
    search_fields = ("staff__location__name_en",)
    ordering = ("staff_id", "day_of_week", "start_time")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": (
                "staff",
                "day_of_week",
                ("start_time", "end_time"),
                ("valid_from", "valid_until"),
            ),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description=_("Staff ID"))
    def staff_id_display(self, obj: AvailabilityTemplate) -> str:
        # PIPEDA: return PK only — no email or name.
        return str(obj.staff_id)


@admin.register(StaffException)
class StaffExceptionAdmin(admin.ModelAdmin):
    """
    Admin for date-level staff availability exceptions.
    PIPEDA: list view shows staff_id (PK), not name or email.
    note_internal is staff/admin-only — never shown to citizens.
    """

    list_display = (
        "id",
        "staff_id_display",
        "exception_date",
        "exception_type",
        "override_start_time",
        "override_end_time",
    )
    list_filter = ("exception_type",)
    search_fields = ("staff__location__name_en",)
    date_hierarchy = "exception_date"
    ordering = ("-exception_date",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": (
                "staff",
                "exception_date",
                "exception_type",
                ("override_start_time", "override_end_time"),
            ),
        }),
        (_("Internal note (staff only)"), {
            "description": _(
                "⚠ This note is for staff and admin use only. "
                "Never display to citizens."
            ),
            "fields": ("note_internal",),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description=_("Staff ID"))
    def staff_id_display(self, obj: StaffException) -> str:
        return str(obj.staff_id)


@admin.register(Slot)
class SlotAdmin(admin.ModelAdmin):
    """
    Admin for Slot records.

    SECURITY:
      - video_join_url_citizen is gated behind appointments.view_slot_video_urls
        permission — never shown by default.
      - list_display never includes the citizen video URL.
      - internal_note is staff/admin only.
    """

    list_display = (
        "pk_short",
        "appointment_type",
        "location",
        "staff_id_display",
        "start_datetime",
        "status",
        "spaces_used",
        "capacity",
        "is_walk_in_slot",
    )
    list_filter = (
        "status",
        "is_walk_in_slot",
        "appointment_type__mode",
        "location__organization",
    )
    search_fields = ("appointment_type__slug", "location__name_en")
    date_hierarchy = "start_datetime"
    ordering = ("-start_datetime",)
    readonly_fields = (
        "id",
        "appointment_type",
        "staff",
        "location",
        "resource",
        "start_datetime",
        "end_datetime",
        "effective_start",
        "effective_end",
        "spaces_used",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {
            "fields": (
                "id",
                "appointment_type",
                ("staff", "location", "resource"),
                ("start_datetime", "end_datetime"),
                ("effective_start", "effective_end"),
                ("capacity", "spaces_used"),
                ("status", "is_walk_in_slot"),
            ),
        }),
        (_("Internal note (staff only)"), {
            "classes": ("collapse",),
            "fields": ("internal_note",),
        }),
        (_("Video (Wave 7) — restricted"), {
            "classes": ("collapse",),
            "description": _(
                "⚠ SECURITY: Citizen video join URL must NEVER appear in "
                "unauthenticated email. Visible only to users with "
                "appointments.view_slot_video_urls permission."
            ),
            "fields": (
                "video_join_url_citizen",
                "video_join_url_staff",
                "video_meeting_id",
                "video_provider",
            ),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        """Gate video URLs behind explicit permission."""
        ro = list(self.readonly_fields)
        if not request.user.has_perm("appointments.view_slot_video_urls"):
            ro += [
                "video_join_url_citizen",
                "video_join_url_staff",
                "video_meeting_id",
                "video_provider",
            ]
        return ro

    def get_fieldsets(self, request, obj=None):
        """
        H-6 security fix: strip the entire video fieldset for users who lack
        appointments.view_slot_video_urls permission.

        get_readonly_fields() only controls editability — the field values are
        still rendered as plain text in the fieldset and readable by any is_staff
        admin user. Removing the fieldset entirely is the only way to prevent
        a host/moderator video join URL from being visible to unpermissioned staff.
        """
        fieldsets = super().get_fieldsets(request, obj)
        if not request.user.has_perm("appointments.view_slot_video_urls"):
            # Strip any fieldset that contains video URL or video provider fields.
            _video_fields = frozenset(
                ("video_join_url_citizen", "video_join_url_staff", "video_meeting_id", "video_provider")
            )
            fieldsets = [
                (title, opts)
                for title, opts in fieldsets
                if not any(f in opts.get("fields", ()) for f in _video_fields)
            ]
        return fieldsets

    @admin.display(description=_("ID"))
    def pk_short(self, obj: Slot) -> str:
        return str(obj.pk)[:8] + "…"

    @admin.display(description=_("Staff ID"))
    def staff_id_display(self, obj: Slot) -> str:
        return str(obj.staff_id)


# Add AvailabilityTemplate inline to StaffProfileAdmin
StaffProfileAdmin.inlines = list(StaffProfileAdmin.inlines) + [AvailabilityTemplateInline]

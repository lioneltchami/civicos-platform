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

from django import forms
from django.contrib import admin
from django.db import transaction
from django.utils import timezone as django_timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import (
    AppointmentType,
    AvailabilityTemplate,
    Booking,
    BookingAuditLog,
    ClientNoShowRecord,
    GovStackAffiliation,
    GovStackAlertSchedule,
    GovStackBBCredential,
    GovStackMessage,
    GovStackSubscriberProfile,
    Location,
    Organization,
    Resource,
    SchedulingPolicy,
    ServiceType,
    Slot,
    StaffException,
    StaffProfile,
    WaitlistEntry,
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
    # DEBT (HIGH — must fix before multi-tenant deployment):
    # filter_horizontal for appointment_types has no org-scoping. Any is_staff
    # admin can open StaffProfile records from their own org (get_queryset scopes
    # the list) but the filter_horizontal widget's autocomplete endpoint is not
    # scoped — it will enumerate AppointmentTypes from ALL organisations.
    #
    # Fix: override formfield_for_manytomany() to filter the AppointmentType
    # queryset by location__organization of the StaffProfile being edited.
    # This requires the Wave 3 fine-grained permission matrix to be in place.
    #
    # See CODEBASE_HANDOFF_FOR_AI.md §Known Technical Debt.
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


# ---------------------------------------------------------------------------
# Custom ModelForms for Wave 2 admin guards
# ---------------------------------------------------------------------------


class SlotAdminForm(forms.ModelForm):
    """
    ModelForm for SlotAdmin.

    M-1: Validates that capacity is not reduced below the current spaces_used
    count, converting what would be a raw DB IntegrityError (CHECK constraint
    violation) into a clean field-level ValidationError shown in the admin form.
    """

    class Meta:
        model = Slot
        fields = "__all__"

    def clean_capacity(self):
        capacity = self.cleaned_data.get("capacity")
        # Only validate on existing instances (not on slot creation).
        if self.instance and self.instance.pk:
            spaces_used = self.instance.spaces_used
            if capacity is not None and capacity < spaces_used:
                raise forms.ValidationError(
                    _(
                        "Capacity (%(cap)d) cannot be less than the number of spaces "
                        "already used (%(used)d). Cancel bookings first."
                    ),
                    params={"cap": capacity, "used": spaces_used},
                )
        return capacity


class StaffExceptionAdminForm(forms.ModelForm):
    """
    ModelForm for StaffExceptionAdmin.

    M-7: Makes internal_note append-only. The existing note history is
    displayed as a read-only field; a separate note_addition CharField
    lets admins append timestamped entries. The full internal_note field
    is excluded from direct editing to prevent silent overwrites.

    PIPEDA: timestamp entries record the actor's user PK only — no name
    or email is stored in the note text.
    """

    note_addition = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label=_("Add note (appended with timestamp)"),
        help_text=_(
            "Enter text to append to the note history. "
            "Previous notes are preserved — this field only adds new content."
        ),
    )

    class Meta:
        model = StaffException
        # Exclude internal_note from direct editing; it is shown as a
        # readonly display field and updated only via note_addition.
        exclude = ("internal_note",)


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

    def get_queryset(self, request):
        """
        H-3 security fix: scope queryset to the acting admin's own organization.

        Non-superuser admins see only templates whose staff member belongs to
        their own organization. Fail-closed: return empty queryset if the admin
        user has no staff_profile or their location is unset.
        """
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        try:
            admin_org = request.user.staff_profile.location.organization
            return qs.filter(staff__location__organization=admin_org)
        except AttributeError:
            return qs.none()

    @admin.display(description=_("Staff ID"))
    def staff_id_display(self, obj: AvailabilityTemplate) -> str:
        # PIPEDA: return PK only — no email or name.
        return str(obj.staff_id)


@admin.register(StaffException)
class StaffExceptionAdmin(admin.ModelAdmin):
    """
    Admin for date-level staff availability exceptions.
    PIPEDA: list view shows staff_id (PK), not name or email.
    internal_note is staff/admin-only — never shown to citizens.

    M-7: internal_note is append-only. The existing note history is shown as
    a read-only display field; new content is appended via the note_addition
    field with a UTC timestamp and actor PK (no PII name/email — PIPEDA).
    """

    form = StaffExceptionAdminForm

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
    readonly_fields = ("created_at", "updated_at", "internal_note")
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
                "Never display to citizens. "
                "Notes are append-only and include a UTC timestamp and actor ID."
            ),
            "fields": ("internal_note", "note_addition"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    def get_queryset(self, request):
        """
        H-3 security fix: scope queryset to the acting admin's own organization.

        Non-superuser admins see only exceptions whose staff member belongs to
        their own organization. Fail-closed: return empty queryset if the admin
        user has no staff_profile or their location is unset.
        """
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        try:
            admin_org = request.user.staff_profile.location.organization
            return qs.filter(staff__location__organization=admin_org)
        except AttributeError:
            return qs.none()

    def save_model(self, request, obj, form, change):
        """
        M-7: Append note_addition to internal_note with a UTC timestamp and
        the acting admin's user PK. No name or email is stored (PIPEDA).

        H-5: Re-fetch the instance under select_for_update() inside
        transaction.atomic() before appending, so concurrent submissions
        cannot silently overwrite each other's note entries.
        """
        # Save all fields except internal_note first.
        super().save_model(request, obj, form, change)

        addition = form.cleaned_data.get("note_addition", "").strip()
        if not addition:
            return

        ts = django_timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        actor_pk = request.user.pk
        entry = f"[{ts} — admin #{actor_pk}] {addition}"

        # Re-fetch under lock to prevent concurrent note overwrites (H-5).
        with transaction.atomic():
            fresh = StaffException.objects.select_for_update().get(pk=obj.pk)
            if fresh.internal_note:
                fresh.internal_note = f"{fresh.internal_note}\n{entry}"
            else:
                fresh.internal_note = entry
            fresh.save(update_fields=["internal_note"])

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

    M-1: SlotAdminForm.clean_capacity() prevents capacity from being reduced
    below spaces_used, surfacing a friendly ValidationError instead of a raw
    DB IntegrityError from the appt_slot_spaces_lte_capacity CheckConstraint.
    """

    form = SlotAdminForm
    list_select_related = ("appointment_type", "location", "staff")

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

    def save_model(self, request, obj, form, change):
        """
        M-1 / H-8: Call Slot.clean() before saving so that CheckConstraint
        violations produce a friendly ValidationError in the admin rather than
        a raw DB IntegrityError (500 error).

        We call obj.clean() — not obj.full_clean() — to avoid re-running the
        field-level validations already handled by SlotAdminForm.clean_capacity()
        before this method is reached. Django 5.2's _changeform_view() catches
        ValidationError raised from save_model() and displays it as a non-field
        form error, so this is the correct and idiomatic approach.
        """
        from django.core.exceptions import ValidationError
        obj.clean()  # Raises ValidationError if constraints are violated.
        super().save_model(request, obj, form, change)

    @admin.display(description=_("ID"))
    def pk_short(self, obj: Slot) -> str:
        return str(obj.pk)[:8] + "…"

    @admin.display(description=_("Staff ID"))
    def staff_id_display(self, obj: Slot) -> str:
        return str(obj.staff_id)


# ===========================================================================
# Wave 3: Booking, ClientNoShowRecord, WaitlistEntry
# ===========================================================================


class BookingAuditLogInline(admin.TabularInline):
    """Read-only inline showing the audit trail for a booking."""
    model = BookingAuditLog
    extra = 0
    can_delete = False
    readonly_fields = [
        "timestamp", "action_display", "actor_id", "actor_ip",
        "previous_status", "new_status", "detail",
    ]
    ordering = ["-timestamp"]

    @admin.display(description="Action")
    def action_display(self, obj):
        """
        Renders BookingAuditLog.action as its raw stored string, bypassing
        Django admin's default choices-lookup rendering for readonly fields
        with choices=. That default rendering silently blanks out any value
        not present in ACTION_CHOICES — but the GovStack Log endpoint
        (services.govstack_log.log_create) deliberately stores arbitrary
        caller-supplied log_category text into this field without
        constraining it to ACTION_CHOICES (GovStack's log_category is
        free-text by spec; see govstack_log.py's module docstring). Without
        this override, every GovStack-created audit entry whose category
        isn't one of CivicOS's own 16 internal action strings would render
        as a blank dash here, defeating the purpose of this audit-trail view.
        """
        return obj.action

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """
    Admin for Booking model.

    PIPEDA gates:
    - citizen PK shown (not email/name)
    - form_responses hidden unless appointments.view_booking_form_responses
    - internal_notes hidden unless appointments.view_internal_notes
    """
    list_display = [
        "pk_short",
        "citizen_pk",
        "slot_start",
        "status",
        "no_show",
        "appointment_mode",
        "booking_channel",
        "created_at",
    ]
    list_filter = ["status", "no_show", "appointment_mode", "booking_channel", "language"]
    search_fields = ["pk"]  # Only PK — no PII search
    ordering = ["-created_at"]
    inlines = [BookingAuditLogInline]
    readonly_fields = [
        "pk",
        "citizen_pk_display",
        "slot",
        "created_at",
        "updated_at",
        "rescheduled_from",
        "reschedule_count",
        "cancelled_at",
        "cancelled_by",
        "late_cancellation",
        "consent_recorded_at",
        "work_item_id",
        "service_request_id",
    ]
    fieldsets = (
        (None, {
            "fields": (
                "pk",
                ("citizen_pk_display", "slot"),
                ("status", "no_show"),
                ("appointment_mode", "booking_channel", "language"),
            ),
        }),
        (_("Rescheduling"), {
            "classes": ("collapse",),
            "fields": ("rescheduled_from", "reschedule_count"),
        }),
        (_("Cancellation"), {
            "classes": ("collapse",),
            "fields": (
                "cancelled_at",
                "cancelled_by",
                "cancellation_reason",
                "late_cancellation",
            ),
        }),
        (_("Intake (Protected B — permission gated)"), {
            "classes": ("collapse",),
            "fields": (
                "form_responses",
                "interpreter_needed",
                "interpreter_language",
                "accessibility_needs",
            ),
        }),
        (_("Staff Notes (permission gated)"), {
            "classes": ("collapse",),
            "fields": ("internal_notes",),
        }),
        (_("Consent & Compliance"), {
            "classes": ("collapse",),
            "fields": ("consent_recorded_at", "consent_version"),
        }),
        (_("System Links"), {
            "classes": ("collapse",),
            "fields": ("work_item_id", "service_request_id"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    def pk_short(self, obj):
        return str(obj.pk)[:8] + "…"
    pk_short.short_description = "PK (short)"

    def citizen_pk(self, obj):
        """Display citizen PK only — NEVER email or name (PIPEDA)."""
        return str(obj.citizen_id)
    citizen_pk.short_description = "Citizen PK"
    citizen_pk.admin_order_field = "citizen_id"

    def citizen_pk_display(self, obj):
        return str(obj.citizen_id)
    citizen_pk_display.short_description = "Citizen PK"

    def slot_start(self, obj):
        return obj.slot.start_datetime.isoformat() if obj.slot_id else "—"
    slot_start.short_description = "Slot start (UTC)"

    def get_readonly_fields(self, request, obj=None):
        """Gate Protected B fields behind explicit permission."""
        ro = list(self.readonly_fields)
        if not request.user.has_perm("appointments.view_booking_form_responses"):
            ro += ["form_responses", "accessibility_needs", "interpreter_language"]
        if not request.user.has_perm("appointments.view_internal_notes"):
            ro += ["internal_notes"]
        return ro

    def has_delete_permission(self, request, obj=None):
        """Bookings are audit-relevant records — no deletion via admin."""
        return False


@admin.register(ClientNoShowRecord)
class ClientNoShowRecordAdmin(admin.ModelAdmin):
    """
    Admin for ClientNoShowRecord. Access gated by manage_no_shows permission.
    PIPEDA: citizen referenced by PK only in list view.
    """
    list_display = [
        "citizen_pk",
        "no_show_count",
        "late_cancellation_count",
        "total_appointments",
        "is_flagged",
        "is_suspended",
        "updated_at",
    ]
    list_filter = ["is_flagged", "is_suspended"]
    ordering = ["-updated_at"]
    readonly_fields = [
        "citizen",
        "no_show_count",
        "late_cancellation_count",
        "total_appointments",
        "last_no_show_at",
        "flagged_at",
        "flagged_by",
        "created_at",
        "updated_at",
    ]
    actions = ["clear_flag_action", "clear_suspension_action"]

    def citizen_pk(self, obj):
        return str(obj.citizen_id)
    citizen_pk.short_description = "Citizen PK"

    def has_module_perms(self, request):
        return request.user.has_perm("appointments.manage_no_shows")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("appointments.manage_no_shows")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("appointments.manage_no_shows")

    def has_add_permission(self, request):
        return False  # Created only by the booking service

    def has_delete_permission(self, request, obj=None):
        return False  # Retention-protected

    @admin.action(description=_("Clear flag (unflag selected citizens)"))
    def clear_flag_action(self, request, queryset):
        if not request.user.has_perm("appointments.manage_no_shows"):
            self.message_user(request, _("Permission denied."), level="error")
            return
        updated = queryset.filter(is_flagged=True).update(is_flagged=False, flagged_at=None)
        self.message_user(request, _(f"{updated} record(s) unflagged."))

    @admin.action(description=_("Clear suspension (re-enable self-booking)"))
    def clear_suspension_action(self, request, queryset):
        if not request.user.has_perm("appointments.manage_no_shows"):
            self.message_user(request, _("Permission denied."), level="error")
            return
        updated = queryset.filter(is_suspended=True).update(
            is_suspended=False, suspension_note=""
        )
        self.message_user(request, _(f"{updated} record(s) unsuspended."))


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    """Read-only admin view for waitlist entries. Mutations via service layer only."""
    list_display = [
        "pk",
        "citizen_pk",
        "slot",
        "priority_class",
        "position",
        "status",
        "notification_sent_at",
        "joined_at",
    ]
    list_filter = ["status", "priority_class"]
    ordering = ["slot", "position"]
    readonly_fields = [
        "slot",
        "citizen_pk_display",
        "priority_class",
        "position",
        "status",
        "notification_sent_at",
        "acceptance_deadline",
        "notification_channel",
        "joined_at",
        "created_at",
        "updated_at",
    ]

    def citizen_pk(self, obj):
        return str(obj.citizen_id)
    citizen_pk.short_description = "Citizen PK"

    def citizen_pk_display(self, obj):
        return str(obj.citizen_id)
    citizen_pk_display.short_description = "Citizen PK"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Add AvailabilityTemplate inline to StaffProfileAdmin
StaffProfileAdmin.inlines = list(StaffProfileAdmin.inlines) + [AvailabilityTemplateInline]


# ===========================================================================
# GovStack Scheduler BB — supplementary models
# ===========================================================================


@admin.register(GovStackSubscriberProfile)
class GovStackSubscriberProfileAdmin(admin.ModelAdmin):
    """
    Admin for GovStack Subscriber extension.
    PIPEDA: list view shows user_id (PK) only — never email or name.
    alert_url/status_poll_url are operator-supplied callback endpoints
    (see the SSRF hardening in services/govstack_resource.py's sibling
    fields for the general pattern) and are display-only here.
    """

    list_display = (
        "id",
        "user_id_display",
        "category",
        "alert_preference",
        "created_at",
    )
    list_filter = ("category", "alert_preference")
    search_fields = ()  # No PII search fields — user email/name are on the User model.
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description=_("User ID"))
    def user_id_display(self, obj: GovStackSubscriberProfile) -> str:
        return str(obj.user_id)


@admin.register(GovStackMessage)
class GovStackMessageAdmin(admin.ModelAdmin):
    """Admin for GovStack reusable notification message templates."""

    list_display = ("id", "category", "entity", "created_at")
    list_filter = ("category", "entity")
    search_fields = ("category", "message_body", "entity__name_en")
    ordering = ("entity", "category")
    autocomplete_fields = ("entity",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(GovStackAffiliation)
class GovStackAffiliationAdmin(admin.ModelAdmin):
    """Admin for GovStack Resource↔Entity affiliation records."""

    list_display = ("id", "resource", "entity", "resource_category", "created_at")
    list_filter = ("resource_category", "entity")
    search_fields = ("resource__name_en", "entity__name_en", "resource_category")
    ordering = ("entity", "resource")
    autocomplete_fields = ("resource", "entity")
    readonly_fields = ("created_at", "updated_at")


@admin.register(GovStackAlertSchedule)
class GovStackAlertScheduleAdmin(admin.ModelAdmin):
    """Admin for scheduled GovStack push notifications."""

    list_display = (
        "id",
        "slot",
        "message",
        "target_category",
        "alert_datetime",
        "dispatched_icon",
    )
    list_filter = ("dispatched", "target_category")
    search_fields = ("celery_task_id",)
    ordering = ("alert_datetime",)
    date_hierarchy = "alert_datetime"
    autocomplete_fields = ("slot", "message")
    readonly_fields = ("created_at", "updated_at", "celery_task_id", "dispatched")

    @admin.display(description=_("Dispatched"), boolean=False)
    def dispatched_icon(self, obj: GovStackAlertSchedule) -> str:
        return _bool_icon(obj.dispatched)


@admin.register(GovStackBBCredential)
class GovStackBBCredentialAdmin(admin.ModelAdmin):
    """
    Admin for GovStack Scheduler BB authentication credentials.

    SECURITY: token_hash is NEVER displayed or editable here — only the
    non-secret 8-character token_prefix is shown, purely so an operator can
    visually distinguish rotated credentials from one another. There is no
    admin action to view or export the plaintext secret; it exists only at
    generation time via the govstack_generate_bb_credential management
    command and is never persisted anywhere in recoverable form.
    """

    list_display = ("id", "bb", "token_prefix", "last_used_at", "rotated_at")
    search_fields = ("bb__bb_id", "token_prefix")
    ordering = ("-rotated_at",)
    autocomplete_fields = ("bb",)
    readonly_fields = ("token_prefix", "last_used_at", "rotated_at", "created_at", "updated_at")
    # token_hash is deliberately excluded from all fieldsets — never rendered,
    # even as a readonly field, to keep the hash out of the admin HTML entirely.
    fields = ("bb", "token_prefix", "last_used_at", "rotated_at", "created_at", "updated_at")

    def has_add_permission(self, request):
        # Credentials are provisioned only via govstack_generate_bb_credential
        # (which handles plaintext generation + hashing). No add form here —
        # there is no safe way to let an admin type in a "secret" through a
        # plain HTML form field without it being logged/cached in transit.
        return False

    def has_change_permission(self, request, obj=None):
        # Rotation is a generate-new-secret operation, not a field edit —
        # done exclusively via the management command's --rotate flag.
        return False

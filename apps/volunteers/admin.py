"""
Volunteer Management BB — Django Admin.

Access control policy:
  VolunteerProfile    — no direct add (created via application flow); coordinators can change status.
  Honorarium          — no add via admin (use coordinator view); no delete (immutable financial record).
  ScreeningRecord     — no delete; no change once verified_clear is set.
  HoursLog            — read-only for approved records.
  RecognitionMilestone — read-only (system-created on hours approval).

Security / PIPEDA:
  - list_display never shows volunteer name, email, or any direct PII.
  - Volunteers are identified by profile.pk only.
  - accommodation_notes, emergency_contact_*, sin_last4 rendered only for users
    with volunteers.view_accommodation_notes permission (field-level gate in get_fieldsets).
  - sin_encrypted is NEVER exposed in any admin fieldset or list.
  - ScreeningRecord notes: admin help_text reminds staff not to record check results.

All admin actions are logged to AuditLogEntry via the standard Django admin
log (LogEntry), which is supplementary to the apps/audit AuditLogEntry.
Critical actions (accommodation_notes read, SIN access, export) are separately
written to AuditLogEntry in the service / view layer.
"""
from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    Certification,
    HoursLog,
    Honorarium,
    Opportunity,
    Program,
    RecognitionMilestone,
    ScreeningRecord,
    Shift,
    ShiftBooking,
    SkillTag,
    VolunteerApplication,
    VolunteerNote,
    VolunteerProfile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_accommodation_perm(request) -> bool:
    """True if the request user has the view_accommodation_notes permission."""
    return request.user.has_perm("volunteers.view_accommodation_notes")


# ---------------------------------------------------------------------------
# SkillTag
# ---------------------------------------------------------------------------

@admin.register(SkillTag)
class SkillTagAdmin(admin.ModelAdmin):
    list_display = ["name_en", "name_fr", "slug", "category", "is_active"]
    list_filter = ["is_active", "category"]
    search_fields = ["name_en", "name_fr", "slug"]
    prepopulated_fields = {"slug": ("name_en",)}
    ordering = ["name_en"]


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------

@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = [
        "name_en", "slug", "cra_category", "coordinator_pk", "is_active"
    ]
    list_filter = ["is_active", "cra_category"]
    search_fields = ["name_en", "name_fr", "slug"]
    prepopulated_fields = {"slug": ("name_en",)}
    ordering = ["name_en"]
    raw_id_fields = ["coordinator"]

    @admin.display(description=_("Coordinator PK"), ordering="coordinator_id")
    def coordinator_pk(self, obj):
        # PIPEDA: display PK, not username/email
        return obj.coordinator_id


# ---------------------------------------------------------------------------
# Opportunity
# ---------------------------------------------------------------------------

class ShiftInline(admin.TabularInline):
    model = Shift
    extra = 0
    fields = [
        "start_datetime", "end_datetime", "capacity",
        "waitlist_enabled", "is_cancelled",
    ]
    readonly_fields = ["is_cancelled", "cancelled_at"]
    ordering = ["start_datetime"]
    show_change_link = True

    def has_delete_permission(self, request, obj=None):
        # Shifts cannot be deleted from admin — cancel via coordinator view.
        return False


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = [
        "title_en", "program", "status", "volunteer_capacity",
        "requires_vulnerable_sector_check", "published_at",
    ]
    list_filter = ["status", "program", "requires_vulnerable_sector_check", "is_remote"]
    search_fields = ["title_en", "title_fr", "slug"]
    prepopulated_fields = {"slug": ("title_en",)}
    ordering = ["-published_at"]
    raw_id_fields = ["program", "volunteer_agreement"]
    inlines = [ShiftInline]
    readonly_fields = ["published_at"]

    fieldsets = [
        (None, {
            "fields": [
                "program", "title_en", "title_fr", "slug",
                "description_en", "description_fr",
                "responsibilities_en", "responsibilities_fr",
            ],
        }),
        (_("Location"), {
            "fields": ["location_name", "location_address", "is_remote"],
        }),
        (_("Requirements"), {
            "fields": [
                "required_skills", "minimum_age",
                "requires_vulnerable_sector_check",
                "requires_police_record_check",
                "requires_reference_check",
                "requires_own_vehicle",
                "required_profile_fields",
            ],
        }),
        (_("Capacity & publishing"), {
            "fields": [
                "volunteer_capacity", "status", "published_at", "closes_at",
            ],
        }),
        (_("Agreement & honorarium"), {
            "fields": ["volunteer_agreement", "honorarium_per_shift"],
        }),
    ]

    actions = ["publish_opportunities", "close_opportunities"]

    @admin.action(description=_("Publish selected opportunities"))
    def publish_opportunities(self, request, queryset):
        from django.utils import timezone
        count = queryset.filter(status=Opportunity.STATUS_DRAFT).update(
            status=Opportunity.STATUS_PUBLISHED,
            published_at=timezone.now(),
        )
        self.message_user(request, f"{count} opportunity/ies published.")

    @admin.action(description=_("Close selected opportunities"))
    def close_opportunities(self, request, queryset):
        count = queryset.exclude(status=Opportunity.STATUS_CLOSED).update(
            status=Opportunity.STATUS_CLOSED,
        )
        self.message_user(request, f"{count} opportunity/ies closed.")


# ---------------------------------------------------------------------------
# VolunteerProfile — field-level PII gate for sensitive fields
# ---------------------------------------------------------------------------

class CertificationInline(admin.TabularInline):
    model = Certification
    extra = 0
    fields = ["cert_type", "cert_type_other", "issued_date", "expires_date", "verified_at"]
    readonly_fields = ["verified_at", "verified_by"]
    ordering = ["-issued_date"]
    show_change_link = True


class ScreeningRecordInline(admin.TabularInline):
    model = ScreeningRecord
    extra = 0
    fields = ["check_type", "completed_date", "expires_date", "verified_clear", "verified_at"]
    readonly_fields = ["verified_at", "verified_by"]
    ordering = ["-completed_date"]
    show_change_link = True

    def has_delete_permission(self, request, obj=None):
        # Screening records cannot be deleted — PIPEDA audit trail.
        return False


@admin.register(VolunteerProfile)
class VolunteerProfileAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "preferred_name_display", "status",
        "total_hours_approved", "preferred_language",
    ]
    list_filter = ["status", "preferred_language", "available_weekdays",
                   "available_weekends", "available_evenings"]
    # PIPEDA: do NOT search by email — use pk or preferred_name only.
    search_fields = ["pk", "preferred_name"]
    ordering = ["-created_at"]
    raw_id_fields = ["user", "status_changed_by", "photo_consent"]
    inlines = [ScreeningRecordInline, CertificationInline]
    readonly_fields = [
        "pk", "user_pk", "total_hours_approved",
        "status_changed_at", "status_changed_by",
        "sin_encrypted",   # Never editable in admin; set via service layer only.
    ]

    def get_fieldsets(self, request, obj=None):
        """
        Return fieldsets with sensitive sections shown only to users with
        volunteers.view_accommodation_notes permission.
        """
        base_fieldsets = [
            (None, {
                "fields": [
                    "user_pk", "preferred_name", "preferred_language",
                    "phone_number", "status", "total_hours_approved",
                ],
            }),
            (_("Availability"), {
                "fields": [
                    "availability_notes",
                    "available_weekdays", "available_weekends", "available_evenings",
                ],
            }),
            (_("Skills"), {
                "fields": ["skills"],
            }),
        ]

        if _has_accommodation_perm(request):
            base_fieldsets += [
                (_("Emergency contact ⚠ SENSITIVE"), {
                    "classes": ["collapse"],
                    "fields": [
                        "emergency_contact_name",
                        "emergency_contact_phone",
                        "emergency_contact_relationship",
                    ],
                }),
                (_("Accommodation ⚠ SENSITIVE"), {
                    "classes": ["collapse"],
                    "fields": ["accommodation_notes"],
                }),
                (_("Age verification ⚠ SENSITIVE"), {
                    "classes": ["collapse"],
                    "fields": ["date_of_birth"],
                }),
                (_("SIN ⚠ SENSITIVE — last 4 only"), {
                    "classes": ["collapse"],
                    "description": _(
                        "SIN is stored Fernet-encrypted. Only the last 4 digits "
                        "are displayed here for confirmation. The encrypted value "
                        "is never shown."
                    ),
                    "fields": ["sin_last4"],
                }),
            ]

        return base_fieldsets

    @admin.display(description=_("Preferred name"), ordering="preferred_name")
    def preferred_name_display(self, obj):
        # Shows preferred_name if set; masks identity for list display.
        return obj.preferred_name or f"(not set)"

    @admin.display(description=_("User PK"))
    def user_pk(self, obj):
        return obj.user_id

    def has_add_permission(self, request):
        # Profiles are created via the application flow, not directly.
        return False

    def has_delete_permission(self, request, obj=None):
        # Profiles are deactivated (status=inactive), never hard-deleted via admin.
        return False


# ---------------------------------------------------------------------------
# VolunteerApplication
# ---------------------------------------------------------------------------

@admin.register(VolunteerApplication)
class VolunteerApplicationAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "opportunity", "volunteer_pk", "status", "reviewed_by_pk", "created_at"
    ]
    list_filter = ["status", "opportunity__program"]
    search_fields = ["pk", "opportunity__slug"]
    ordering = ["-created_at"]
    raw_id_fields = ["opportunity", "volunteer", "reviewed_by", "consent_record", "work_item"]
    readonly_fields = [
        "pk", "opportunity", "volunteer_pk", "created_at", "updated_at",
        "work_item",
    ]

    fieldsets = [
        (None, {
            "fields": ["pk", "opportunity", "volunteer_pk", "motivation", "status"],
        }),
        (_("Review"), {
            "fields": [
                "reviewed_by", "reviewed_at",
                "rejection_reason",
                "screening_notes",
                "declares_no_relevant_criminal_history",
            ],
        }),
        (_("Workflow"), {
            "fields": ["work_item", "consent_record"],
        }),
    ]

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk(self, obj):
        return obj.volunteer_id

    @admin.display(description=_("Reviewed by PK"), ordering="reviewed_by_id")
    def reviewed_by_pk(self, obj):
        return obj.reviewed_by_id

    def has_add_permission(self, request):
        # Applications are created via the volunteer portal only.
        return False

    def has_delete_permission(self, request, obj=None):
        # Application records are retained for audit trail.
        return False


# ---------------------------------------------------------------------------
# Shift / ShiftBooking
# ---------------------------------------------------------------------------

class ShiftBookingInline(admin.TabularInline):
    model = ShiftBooking
    extra = 0
    fields = ["volunteer_pk_display", "status", "waitlist_position", "cancelled_at"]
    readonly_fields = ["volunteer_pk_display", "cancelled_at"]
    ordering = ["status", "waitlist_position"]
    show_change_link = True

    @admin.display(description=_("Volunteer PK"))
    def volunteer_pk_display(self, obj):
        return obj.volunteer_id

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "opportunity", "start_datetime", "end_datetime",
        "duration_hours", "capacity", "is_cancelled",
    ]
    list_filter = ["is_cancelled", "opportunity__program"]
    search_fields = ["pk", "opportunity__slug"]
    ordering = ["-start_datetime"]
    raw_id_fields = ["opportunity", "coordinator", "cancelled_by"]
    inlines = [ShiftBookingInline]
    readonly_fields = ["is_cancelled", "cancelled_at", "cancelled_by", "duration_hours"]

    @admin.display(description=_("Duration (hours)"), ordering=None)
    def duration_hours(self, obj):
        return obj.duration_hours

    def has_delete_permission(self, request, obj=None):
        # Shifts are cancelled, not deleted.
        return False


@admin.register(ShiftBooking)
class ShiftBookingAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "shift", "volunteer_pk", "status", "waitlist_position", "created_at"
    ]
    list_filter = ["status"]
    search_fields = ["pk", "shift__pk"]
    ordering = ["-created_at"]
    raw_id_fields = ["shift", "volunteer"]
    readonly_fields = ["pk", "created_at", "updated_at", "reminder_24h_sent", "reminder_2h_sent"]

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk(self, obj):
        return obj.volunteer_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# HoursLog
# ---------------------------------------------------------------------------

@admin.register(HoursLog)
class HoursLogAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "volunteer_pk", "opportunity", "date", "hours", "status", "approved_by_pk"
    ]
    list_filter = ["status", "opportunity__program"]
    search_fields = ["pk", "opportunity__slug"]
    ordering = ["-date"]
    raw_id_fields = ["volunteer", "opportunity", "shift", "approved_by"]
    readonly_fields = ["pk", "created_at", "updated_at", "approved_at"]

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk(self, obj):
        return obj.volunteer_id

    @admin.display(description=_("Approved by PK"), ordering="approved_by_id")
    def approved_by_pk(self, obj):
        return obj.approved_by_id

    def has_add_permission(self, request):
        # Hours are logged via volunteer portal or coordinator view.
        return False

    def has_delete_permission(self, request, obj=None):
        # Hours records are immutable once approved.
        return False

    def has_change_permission(self, request, obj=None):
        # Approved records cannot be mutated via admin.
        if obj and obj.status == HoursLog.STATUS_APPROVED:
            return False
        return super().has_change_permission(request, obj)


# ---------------------------------------------------------------------------
# ScreeningRecord
# ---------------------------------------------------------------------------

@admin.register(ScreeningRecord)
class ScreeningRecordAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "volunteer_pk", "check_type", "completed_date",
        "expires_date", "verified_clear", "is_expired_display",
    ]
    list_filter = ["check_type", "verified_clear"]
    search_fields = ["pk"]
    ordering = ["-completed_date"]
    raw_id_fields = ["volunteer", "opportunity", "verified_by"]
    readonly_fields = ["pk", "created_at", "updated_at"]

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk(self, obj):
        return obj.volunteer_id

    @admin.display(description=_("Expired?"), boolean=True)
    def is_expired_display(self, obj):
        return obj.is_expired

    def has_add_permission(self, request):
        # Screening records created via coordinator view only.
        return False

    def has_delete_permission(self, request, obj=None):
        # Screening records are permanent — part of the due diligence audit trail.
        return False


# ---------------------------------------------------------------------------
# Certification
# ---------------------------------------------------------------------------

@admin.register(Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "volunteer_pk", "cert_type", "issued_date",
        "expires_date", "verified_at", "is_expired_display",
    ]
    list_filter = ["cert_type"]
    search_fields = ["pk"]
    ordering = ["-issued_date"]
    raw_id_fields = ["volunteer", "verified_by"]
    readonly_fields = ["pk", "created_at", "updated_at"]

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk(self, obj):
        return obj.volunteer_id

    @admin.display(description=_("Expired?"), boolean=True)
    def is_expired_display(self, obj):
        return obj.is_expired

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Honorarium
# ---------------------------------------------------------------------------

@admin.register(Honorarium)
class HonorariumAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "volunteer_pk", "payment_type", "amount", "currency",
        "calendar_year", "t4a_required", "t4a_issued", "payment_date",
    ]
    list_filter = ["payment_type", "calendar_year", "t4a_required", "t4a_issued"]
    search_fields = ["pk"]
    ordering = ["-payment_date"]
    raw_id_fields = ["volunteer", "created_by", "payment"]
    readonly_fields = [
        "pk", "created_at", "updated_at",
        "calendar_year",   # Derived from payment_date on save.
        "t4a_required",    # Set by service layer.
    ]

    fieldsets = [
        (None, {
            "fields": [
                "pk", "volunteer_pk_display", "payment_type",
                "amount", "currency", "description",
                "payment_date", "calendar_year",
            ],
        }),
        (_("T4A"), {
            "fields": ["t4a_required", "t4a_issued", "t4a_issued_at"],
        }),
        (_("Payments BB link"), {
            "fields": ["payment"],
            "classes": ["collapse"],
        }),
        (_("Metadata"), {
            "fields": ["created_by", "created_at", "updated_at"],
            "classes": ["collapse"],
        }),
    ]

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk(self, obj):
        return obj.volunteer_id

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk_display(self, obj):
        return obj.volunteer_id

    def has_add_permission(self, request):
        # Honoraria created via coordinator admin view with CRA threshold validation.
        return False

    def has_delete_permission(self, request, obj=None):
        # Immutable financial record — CRA audit trail.
        return False


# ---------------------------------------------------------------------------
# VolunteerNote
# ---------------------------------------------------------------------------

@admin.register(VolunteerNote)
class VolunteerNoteAdmin(admin.ModelAdmin):
    list_display = ["pk", "volunteer_pk", "author_pk", "created_at"]
    search_fields = ["pk"]
    ordering = ["-created_at"]
    raw_id_fields = ["volunteer", "author"]
    readonly_fields = ["pk", "created_at", "updated_at"]

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk(self, obj):
        return obj.volunteer_id

    @admin.display(description=_("Author PK"), ordering="author_id")
    def author_pk(self, obj):
        return obj.author_id

    def has_delete_permission(self, request, obj=None):
        # Notes are append-only; deletion via admin not permitted.
        return False


# ---------------------------------------------------------------------------
# RecognitionMilestone
# ---------------------------------------------------------------------------

@admin.register(RecognitionMilestone)
class RecognitionMilestoneAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "volunteer_pk", "hours_threshold", "achieved_at", "notification_sent"
    ]
    list_filter = ["notification_sent"]
    search_fields = ["pk"]
    ordering = ["-achieved_at"]
    raw_id_fields = ["volunteer"]
    readonly_fields = ["pk", "achieved_at"]

    @admin.display(description=_("Volunteer PK"), ordering="volunteer_id")
    def volunteer_pk(self, obj):
        return obj.volunteer_id

    def has_add_permission(self, request):
        # System-created only (via services/hours.py on approval).
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Read-only records.
        return False

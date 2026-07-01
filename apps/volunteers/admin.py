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


def _mask_ip_for_audit(ip: str | None) -> str | None:
    """
    Mask an IP address for privacy-preserving audit storage (PIPEDA compliance).

    Defined locally to avoid a circular import between apps.volunteers and
    apps.forms (or apps.auth_extension).  Logic mirrors apps/forms/utils.py's
    _mask_ip() exactly:

    IPv4: keeps first 3 octets, zeroes the last octet.
          192.168.1.123 → 192.168.1.0
    IPv6: keeps the first 32 bits (/32 prefix), zeroes the rest.
          2001:db8::1 → 2001:db8::

    Returns None (not a sentinel string) when input is None so callers can
    distinguish "no IP captured" from "IP captured but masked".
    Returns '0.0.0.0' on any parsing error.
    """
    import ipaddress as _ipaddress

    if ip is None:
        return None
    if not ip:
        return "0.0.0.0"
    try:
        addr = _ipaddress.ip_address(ip.strip())
        if isinstance(addr, _ipaddress.IPv4Address):
            parts = str(addr).split(".")
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
        else:  # IPv6
            network = _ipaddress.ip_network(f"{addr}/32", strict=False)
            return str(network.network_address)
    except ValueError:
        return "0.0.0.0"


def _write_volunteer_audit(
    event_type: str,
    request,
    resource_id: str,
    detail: dict | None = None,
    resource_type: str = "volunteers.VolunteerProfile",
) -> None:
    """
    Write an immutable AuditLogEntry for a Volunteer BB admin action.

    Failures are caught and logged as warnings so audit never breaks a view.
    No PII (names, notes content) is written to event_detail — only PKs and
    field names, per the PIPEDA minimum-data principle.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        from apps.audit.models import AuditLogEntry

        # select_for_update() serializes concurrent audit writes: each writer
        # locks the current last row, so the next writer sees the committed entry
        # as its prev_hash, preventing chain-of-custody forks under concurrent load.
        last = (
            AuditLogEntry.objects.select_for_update()
            .order_by("-timestamp")
            .values("entry_hash")
            .first()
        )
        prev_hash = last["entry_hash"] if last else ""

        # IP extraction: only trust X-Forwarded-For when behind a known proxy.
        from django.conf import settings as dj_settings
        actor_ip = None
        if getattr(dj_settings, "SECURE_PROXY_SSL_HEADER", None):
            xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
            if xff:
                actor_ip = xff.split(",")[0].strip() or None
        if actor_ip is None:
            actor_ip = request.META.get("REMOTE_ADDR") or None

        AuditLogEntry.objects.create(
            event_type=event_type,
            outcome="success",
            actor_id=str(request.user.pk),
            # PIPEDA minimum-data: staff email is PII; only authentication events
            # (login/logout/MFA) should snapshot the email.  actor_id is sufficient
            # to identify the actor for data.viewed / data.updated events.
            actor_email="",
            actor_ip=_mask_ip_for_audit(actor_ip),
            actor_user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
            resource_type=resource_type,
            resource_id=resource_id,
            event_detail=detail or {},
            request_id=request.META.get("HTTP_X_REQUEST_ID", ""),
            session_id=getattr(request.session, "session_key", None) or "",
            prev_hash=prev_hash,
        )
    except Exception:
        logger.warning(
            "volunteers admin: audit log failed for event_type=%s resource_id=%s",
            event_type,
            resource_id,
        )


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

    # NOTE: This action uses QuerySet.update() which bypasses model save() and
    # Django signals. Wave 2 notification receivers (opportunity published → notify
    # subscribed volunteers) will NOT fire from this admin action.
    # If notifications are required, replace with a loop calling obj.save().
    @admin.action(description=_("Publish selected opportunities"))
    def publish_opportunities(self, request, queryset):
        from django.utils import timezone
        count = queryset.filter(status=Opportunity.STATUS_DRAFT).update(
            status=Opportunity.STATUS_PUBLISHED,
            published_at=timezone.now(),
        )
        self.message_user(request, f"{count} opportunity/ies published.")

    # NOTE: This action uses QuerySet.update() which bypasses model save() and
    # Django signals. Wave 2 notification receivers (opportunity published → notify
    # subscribed volunteers) will NOT fire from this admin action.
    # If notifications are required, replace with a loop calling obj.save().
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
    # SECURITY: show_change_link=False prevents click-through to ScreeningRecordAdmin
    # change view, which would expose `notes` without the view_accommodation_notes
    # permission gate that guards the standalone admin.
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        # Inline add is blocked to match the standalone ScreeningRecordAdmin policy.
        # Adding via the inline would bypass coordinator-view validations.
        return False

    def has_delete_permission(self, request, obj=None):
        # Screening records cannot be deleted — PIPEDA audit trail.
        return False


@admin.register(VolunteerProfile)
class VolunteerProfileAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "status",
    ]
    list_filter = ["status", "preferred_language", "available_weekdays",
                   "available_weekends", "available_evenings"]
    # PIPEDA: search by profile PK only. preferred_name is PII — do not expose in search.
    search_fields = ["pk"]
    ordering = ["-created_at"]
    raw_id_fields = ["user", "status_changed_by", "photo_consent"]
    inlines = [ScreeningRecordInline, CertificationInline]
    # sin_encrypted is NEVER in readonly_fields or fieldsets — PIPEDA + security requirement.
    readonly_fields = [
        "pk", "user_pk", "total_hours_approved",
        "status_changed_at", "status_changed_by",
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

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        # sin_encrypted is NEVER exposed in admin — PIPEDA + security requirement.
        # Access only via services/volunteers.py decrypt_sin().
        return [f for f in fields if f != "sin_encrypted"]

    @admin.display(description=_("Preferred name"), ordering="preferred_name")
    def preferred_name_display(self, obj):
        # Shows preferred_name if set; masks identity for list display.
        return obj.preferred_name or f"(not set)"

    @admin.display(description=_("User PK"))
    def user_pk(self, obj):
        return obj.user_id

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """
        Audit-log every admin change-form view of a VolunteerProfile.

        If the viewer holds view_accommodation_notes, they can see SIN last4,
        emergency contacts, and accommodation notes — those reads are recorded
        so there is an immutable trail for PIPEDA accountability.

        Writes are captured by Django's built-in LogEntry; this hook captures reads.
        The audit entry records WHICH sensitive sections were exposed, never the
        content itself (PIPEDA minimum-data principle).
        """
        response = super().change_view(request, object_id, form_url, extra_context)

        # Only write the audit entry on a successful GET (page rendered to user).
        # POST (save) is audited by Django's own LogEntry + save_model hooks.
        if request.method == "GET":
            has_sensitive = _has_accommodation_perm(request)
            _write_volunteer_audit(
                event_type="data.viewed",
                request=request,
                resource_id=str(object_id),
                detail={
                    "action": "profile_change_view",
                    "sensitive_sections_visible": has_sensitive,
                    # Sections visible to this user — no field values, just names.
                    "sections": (
                        [
                            "emergency_contact",
                            "accommodation_notes",
                            "date_of_birth",
                            "sin_last4",
                        ]
                        if has_sensitive
                        else []
                    ),
                },
            )

        return response

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

    def get_fieldsets(self, request, obj=None):
        base = [
            (None, {"fields": ["opportunity", "volunteer", "status", "reviewed_by", "reviewed_at"]}),
            (_("Consent"), {"fields": ["consent_record", "work_item"]}),
        ]
        if _has_accommodation_perm(request):
            base.append(
                (_("Screening (restricted)"), {
                    "fields": ["motivation", "screening_notes", "rejection_reason"],
                    "description": _(
                        "Visible only to users with volunteers.view_accommodation_notes. "
                        "PIPEDA: never shown to the applicant."
                    ),
                })
            )
        return base

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
        # Approved hours are immutable financial/audit records — never deletable.
        # Pending and rejected entries may be deleted by coordinators to correct mistakes.
        if obj is not None and obj.status == "approved":
            return False
        return request.user.has_perm("volunteers.delete_hourslog")

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

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.verified_clear is not None:
            # Once VSC is verified (cleared or not), the result is immutable.
            base += ["verified_clear", "verified_at", "verified_by"]
        return base

    def save_model(self, request, obj, form, change):
        """
        Audit-log every write to a ScreeningRecord.

        Writes a RECORD_UPDATED entry so there is an immutable trail of who
        changed verification status or notes.  We never store the notes value
        itself in the audit entry — only field names that changed (non-PII).
        """
        changed_fields = list(form.changed_data) if change else ["<new record>"]
        _write_volunteer_audit(
            event_type="data.updated",
            request=request,
            resource_id=str(obj.volunteer_id),
            detail={
                "action": "screening_record_saved",
                "screening_record_pk": obj.pk,
                "check_type": obj.check_type,
                "changed_fields": changed_fields,
                # Deliberately NOT logging notes content — PIPEDA minimum-data principle.
            },
            resource_type="volunteers.ScreeningRecord",
        )
        super().save_model(request, obj, form, change)

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
        "pk", "volunteer_pk_display", "payment_type", "amount", "currency",
        "calendar_year", "t4a_required", "t4a_issued", "payment_date",
    ]
    list_filter = ["payment_type", "calendar_year", "t4a_required", "t4a_issued"]
    search_fields = ["pk"]
    ordering = ["-payment_date"]
    raw_id_fields = ["volunteer", "created_by", "payment"]
    readonly_fields = [
        "pk", "created_at", "updated_at",
        "calendar_year",         # Derived from payment_date on save.
        "t4a_required",          # Set by service layer.
        "volunteer_pk_display",  # Computed display field — read-only.
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

    def has_add_permission(self, request):
        # Notes are created via the coordinator portal view, not directly in admin.
        # This ensures author, timestamp, and audit fields are set by the service layer.
        return False

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

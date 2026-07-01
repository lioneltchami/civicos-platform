"""
Volunteer Management BB — Models.

13 models covering the full volunteer lifecycle.

PIPEDA design rules (enforced here, not just in views):
  - No PII (volunteer name, email) in __str__, list_display, or audit logs.
    Always reference volunteers by profile.pk.
  - Sensitive fields (SIN, accommodation_notes, emergency contacts) require
    the volunteers.view_accommodation_notes permission to access; gated in
    admin and views.
  - Criminal record check results are NEVER stored — only the verified-clear
    flag and check date (minimum data for due diligence).
  - date_of_birth stored only when role requires age verification
    (Opportunity.required_profile_fields controls this at application time).
  - Photo upload requires explicit consent via Consent BB (photo_consent FK).

CRA compliance:
  - Honorarium.calendar_year derived from payment_date on save.
  - Hard block at $1,000 cumulative honoraria per volunteer per calendar year
    (enforced in services/honoraria.py, not here — model stores the record).
  - t4a_required flag set by service layer when cumulative > $500.

Spec: docs/volunteer-management-bb-spec.md
"""
from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimestampedModel


# ---------------------------------------------------------------------------
# SkillTag
# ---------------------------------------------------------------------------

class SkillTag(models.Model):
    """
    Controlled vocabulary for volunteer skills and certification categories.
    Bilingual (EN/FR). Shared across Opportunity.required_skills and
    VolunteerProfile.skills M2M relations.
    """

    name_en = models.CharField(
        max_length=100, unique=True, verbose_name=_("Name (EN)")
    )
    name_fr = models.CharField(
        max_length=100, unique=True, verbose_name=_("Name (FR)")
    )
    slug = models.SlugField(unique=True)
    category = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Category"),
        help_text=_("e.g. Technical, Language, Health, Administrative"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Active"))

    class Meta:
        ordering = ["name_en"]
        verbose_name = _("Skill tag")
        verbose_name_plural = _("Skill tags")

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        """Return name in the currently active language."""
        from django.utils.translation import get_language
        lang = get_language()
        if lang and lang.startswith("fr"):
            return self.name_fr or self.name_en
        return self.name_en


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------

class Program(TimestampedModel):
    """
    Organizational program that Opportunities belong to.

    Maps to CRA T3010 charitable program categories (Line 5010).
    Hours aggregated by Program feed into T3010 volunteer time reporting.
    """

    CRA_CATEGORY_WELFARE   = "welfare"
    CRA_CATEGORY_EDUCATION = "education"
    CRA_CATEGORY_HEALTH    = "health"
    CRA_CATEGORY_RELIGION  = "religion"
    CRA_CATEGORY_OTHER     = "other"
    CRA_CATEGORY_CHOICES = [
        (CRA_CATEGORY_WELFARE,   _("Welfare of the general public")),
        (CRA_CATEGORY_EDUCATION, _("Education")),
        (CRA_CATEGORY_HEALTH,    _("Health")),
        (CRA_CATEGORY_RELIGION,  _("Religion")),
        (CRA_CATEGORY_OTHER,     _("Other")),
    ]

    name_en = models.CharField(max_length=200, verbose_name=_("Name (EN)"))
    name_fr = models.CharField(max_length=200, verbose_name=_("Name (FR)"))
    slug = models.SlugField(unique=True)
    description_en = models.TextField(blank=True, verbose_name=_("Description (EN)"))
    description_fr = models.TextField(blank=True, verbose_name=_("Description (FR)"))

    cra_category = models.CharField(
        max_length=20,
        choices=CRA_CATEGORY_CHOICES,
        default=CRA_CATEGORY_OTHER,
        verbose_name=_("CRA T3010 category"),
        help_text=_(
            "CRA charitable program category for T3010 reporting. "
            "Hours for this program contribute to the corresponding T3010 line."
        ),
    )

    is_active = models.BooleanField(default=True, verbose_name=_("Active"))
    coordinator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coordinated_programs",
        verbose_name=_("Program coordinator"),
        limit_choices_to={"is_staff": True},
    )

    class Meta:
        ordering = ["name_en"]
        verbose_name = _("Program")
        verbose_name_plural = _("Programs")

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        from django.utils.translation import get_language
        lang = get_language()
        if lang and lang.startswith("fr"):
            return self.name_fr or self.name_en
        return self.name_en


# ---------------------------------------------------------------------------
# Opportunity
# ---------------------------------------------------------------------------

class Opportunity(TimestampedModel):
    """
    A posted volunteer role with responsibilities, requirements, and capacity.

    Volunteers apply to an Opportunity; approved volunteers are assigned to
    Shifts within that Opportunity.

    PIPEDA minimum-collection principle:
    required_profile_fields controls which VolunteerProfile fields the
    application form prompts for. A role not requiring age verification
    must not include "date_of_birth" in this list.
    """

    STATUS_DRAFT     = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_CLOSED    = "closed"
    STATUS_ARCHIVED  = "archived"
    STATUS_CHOICES = [
        (STATUS_DRAFT,     _("Draft")),
        (STATUS_PUBLISHED, _("Published")),
        (STATUS_CLOSED,    _("Closed — not accepting applications")),
        (STATUS_ARCHIVED,  _("Archived")),
    ]

    program = models.ForeignKey(
        Program,
        on_delete=models.PROTECT,
        related_name="opportunities",
        verbose_name=_("Program"),
    )

    title_en = models.CharField(max_length=200, verbose_name=_("Title (EN)"))
    title_fr = models.CharField(max_length=200, verbose_name=_("Title (FR)"))
    slug = models.SlugField(unique=True)
    description_en = models.TextField(verbose_name=_("Description (EN)"))
    description_fr = models.TextField(verbose_name=_("Description (FR)"))
    responsibilities_en = models.TextField(
        blank=True, verbose_name=_("Responsibilities (EN)")
    )
    responsibilities_fr = models.TextField(
        blank=True, verbose_name=_("Responsibilities (FR)")
    )

    location_name = models.CharField(
        max_length=200, blank=True, verbose_name=_("Location name")
    )
    location_address = models.TextField(
        blank=True, verbose_name=_("Location address")
    )
    is_remote = models.BooleanField(
        default=False,
        verbose_name=_("Remote / virtual"),
        help_text=_("Volunteers can participate from any location."),
    )

    # --- Screening requirements ---
    required_skills = models.ManyToManyField(
        SkillTag,
        blank=True,
        related_name="required_by",
        verbose_name=_("Required skills"),
    )
    minimum_age = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Minimum age"),
        help_text=_("Leave blank for no age restriction."),
    )
    requires_vulnerable_sector_check = models.BooleanField(
        default=False,
        verbose_name=_("Requires Vulnerable Sector Check"),
        help_text=_(
            "Role involves unsupervised access to children or vulnerable adults. "
            "VSC must be obtained from local police detachment in person."
        ),
    )
    requires_police_record_check = models.BooleanField(
        default=False,
        verbose_name=_("Requires Police Record Check"),
    )
    requires_reference_check = models.BooleanField(
        default=False,
        verbose_name=_("Requires reference check"),
    )
    requires_own_vehicle = models.BooleanField(
        default=False,
        verbose_name=_("Requires own vehicle"),
    )

    # PIPEDA minimum-collection principle: only ask for profile fields the role needs.
    # JSON list — valid values: phone_number, date_of_birth, emergency_contact_name,
    # emergency_contact_phone, emergency_contact_relationship, accommodation_notes
    required_profile_fields = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Required profile fields"),
        help_text=_(
            "List of VolunteerProfile fields the application form will prompt for. "
            "Only include what the role actually requires (PIPEDA minimum-collection)."
        ),
    )

    volunteer_capacity = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Volunteer capacity"),
        help_text=_(
            "Maximum concurrent active volunteers. Leave blank for unlimited."
        ),
    )

    # --- Publishing ---
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name=_("Status"),
        db_index=True,
    )
    published_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Published at")
    )
    closes_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Closes at"),
        help_text=_("Applications accepted until this date/time. Blank = no close date."),
    )

    # Volunteer agreement template (Consent BB)
    volunteer_agreement = models.ForeignKey(
        "consent.ConsentCategory",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="opportunity_agreements",
        verbose_name=_("Volunteer agreement"),
        help_text=_(
            "Consent category representing the volunteer agreement. "
            "Volunteers must accept this before their application is submitted."
        ),
    )

    # Optional per-shift honorarium
    honorarium_per_shift = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Honorarium per shift (CAD)"),
        help_text=_(
            "Optional nominal honorarium per approved shift. "
            "Subject to CRA $500/year threshold — see docs/volunteer-management-bb-spec.md §3.2."
        ),
    )

    class Meta:
        ordering = ["-published_at", "title_en"]
        verbose_name = _("Opportunity")
        verbose_name_plural = _("Opportunities")
        indexes = [
            models.Index(
                fields=["status", "program"],
                name="vol_opp_status_prog_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.title_en

    def get_title(self) -> str:
        from django.utils.translation import get_language
        lang = get_language()
        if lang and lang.startswith("fr"):
            return self.title_fr or self.title_en
        return self.title_en

    @property
    def is_accepting_applications(self) -> bool:
        if self.status != self.STATUS_PUBLISHED:
            return False
        if self.closes_at and timezone.now() > self.closes_at:
            return False
        return True


# ---------------------------------------------------------------------------
# VolunteerProfile
# ---------------------------------------------------------------------------

class VolunteerProfile(TimestampedModel):
    """
    Extension of the User model for volunteer-specific data.
    Created on first application.

    PIPEDA design:
    - __str__ returns only pk (no name) — safe for logs and admin list.
    - accommodation_notes, emergency_contact_*, sin_encrypted, date_of_birth
      require the volunteers.view_accommodation_notes permission.
    - sin_encrypted stores Fernet-encrypted SIN as BinaryField; sin_last4
      stores the last 4 digits for display confirmation only.
    - photo upload requires photo_consent FK (Consent BB).
    - Criminal record check results are NEVER stored here (see ScreeningRecord).
    """

    STATUS_ACTIVE    = "active"
    STATUS_INACTIVE  = "inactive"
    STATUS_SUSPENDED = "suspended"
    STATUS_CHOICES = [
        (STATUS_ACTIVE,    _("Active")),
        (STATUS_INACTIVE,  _("Inactive")),
        (STATUS_SUSPENDED, _("Suspended — contact administrator")),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="volunteer_profile",
        verbose_name=_("User"),
    )

    # --- Contact & preferences ---
    preferred_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Preferred name"),
        help_text=_("How the volunteer prefers to be addressed."),
    )
    preferred_language = models.CharField(
        max_length=2,
        choices=[("en", _("English")), ("fr", _("Français"))],
        default="en",
        verbose_name=_("Preferred language"),
    )
    phone_number = models.CharField(
        max_length=20, blank=True, verbose_name=_("Phone number")
    )

    # --- Availability ---
    availability_notes = models.TextField(
        blank=True,
        verbose_name=_("Availability notes"),
        help_text=_("Free-text description of general availability."),
    )
    available_weekdays = models.BooleanField(
        default=False, verbose_name=_("Available weekdays")
    )
    available_weekends = models.BooleanField(
        default=False, verbose_name=_("Available weekends")
    )
    available_evenings = models.BooleanField(
        default=False, verbose_name=_("Available evenings")
    )

    # --- Skills (M2M to SkillTag) ---
    skills = models.ManyToManyField(
        SkillTag,
        blank=True,
        related_name="volunteers",
        verbose_name=_("Skills"),
    )

    # --- Emergency contact — SENSITIVE; requires view_accommodation_notes ---
    emergency_contact_name = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=_("Emergency contact name"),
    )
    emergency_contact_phone = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Emergency contact phone"),
    )
    emergency_contact_relationship = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Emergency contact relationship"),
    )

    # --- Accommodation — SENSITIVE; requires view_accommodation_notes ---
    accommodation_notes = models.TextField(
        blank=True,
        verbose_name=_("Accommodation / access needs"),
        help_text=_(
            "Dietary, mobility, or other accessibility needs. "
            "Visible to authorized coordinators only."
        ),
    )

    # --- SIN — SENSITIVE; populated only when T4A workflow triggered ---
    # Stored as Fernet-encrypted bytes; NEVER in plain text.
    # Use services/honoraria.py to set; never access directly.
    sin_encrypted = models.BinaryField(
        null=True,
        blank=True,
        verbose_name=_("SIN (encrypted)"),
        help_text=_(
            "Fernet-encrypted Social Insurance Number. "
            "Only collected when honorarium exceeds CRA $500 threshold. "
            "Never logged or displayed — use sin_last4 for confirmation."
        ),
    )
    sin_last4 = models.CharField(
        max_length=4,
        blank=True,
        verbose_name=_("SIN last 4 digits"),
        help_text=_("Last 4 digits of SIN for display confirmation only."),
    )

    # --- Age verification — only when role requires it ---
    date_of_birth = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Date of birth"),
        help_text=_(
            "Only collected when the Opportunity requires age verification "
            "(Opportunity.required_profile_fields includes 'date_of_birth')."
        ),
    )

    # --- Photo ---
    photo = models.ImageField(
        upload_to="volunteers/photos/",
        null=True,
        blank=True,
        verbose_name=_("Photo"),
        help_text=_("Requires photo_consent before upload."),
    )
    # Consent BB record confirming photo consent was given
    photo_consent = models.ForeignKey(
        "consent.ConsentRecord",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volunteer_photo_consents",
        verbose_name=_("Photo consent record"),
    )

    # --- Status ---
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
        verbose_name=_("Status"),
    )
    status_changed_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Status changed at")
    )
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volunteer_status_changes",
        verbose_name=_("Status changed by"),
    )

    # --- Denormalized totals (recomputed on hours approval) ---
    total_hours_approved = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name=_("Total approved hours"),
        help_text=_(
            "Denormalized sum of approved HoursLog rows. "
            "Authoritative source is HoursLog — recomputed on every approval."
        ),
    )

    class Meta:
        verbose_name = _("Volunteer profile")
        verbose_name_plural = _("Volunteer profiles")
        indexes = [
            models.Index(fields=["status"], name="vol_profile_status_idx"),
        ]
        permissions = [
            (
                "view_accommodation_notes",
                "Can view volunteer accommodation notes, emergency contacts, and SIN last4",
            ),
        ]

    def __str__(self) -> str:
        # PIPEDA: no PII (name, email) in __str__.
        return f"VolunteerProfile #{self.pk}"

    @property
    def display_name(self) -> str:
        """Preferred name if set; falls back to user's first name; then user PK."""
        if self.preferred_name:
            return self.preferred_name
        first = self.user.first_name
        return first if first else f"Volunteer #{self.pk}"


# ---------------------------------------------------------------------------
# VolunteerApplication
# ---------------------------------------------------------------------------

class VolunteerApplication(TimestampedModel):
    """
    A volunteer's application to an Opportunity.

    Status flow:
      pending → in_review → approved | rejected | waitlisted
      Any status → withdrawn (volunteer withdraws)

    PIPEDA: rejection_reason is internal only — never shown to the volunteer.
    The notification sent on rejection uses generic "not selected" language.
    """

    STATUS_PENDING    = "pending"
    STATUS_IN_REVIEW  = "in_review"
    STATUS_APPROVED   = "approved"
    STATUS_REJECTED   = "rejected"
    STATUS_WITHDRAWN  = "withdrawn"
    STATUS_WAITLISTED = "waitlisted"
    STATUS_CHOICES = [
        (STATUS_PENDING,    _("Pending review")),
        (STATUS_IN_REVIEW,  _("In review")),
        (STATUS_APPROVED,   _("Approved")),
        (STATUS_REJECTED,   _("Not selected")),
        (STATUS_WITHDRAWN,  _("Withdrawn by applicant")),
        (STATUS_WAITLISTED, _("Waitlisted")),
    ]

    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.PROTECT,
        related_name="applications",
        verbose_name=_("Opportunity"),
    )
    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.PROTECT,
        related_name="applications",
        verbose_name=_("Volunteer"),
    )

    motivation = models.TextField(
        blank=True,
        verbose_name=_("Motivation"),
        help_text=_("Volunteer's motivation / cover letter."),
    )

    # Consent BB — signed volunteer agreement
    consent_record = models.ForeignKey(
        "consent.ConsentRecord",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="volunteer_applications",
        verbose_name=_("Consent record"),
        help_text=_("Signed volunteer agreement via Consent BB."),
    )

    # Self-declaration at application time
    declares_no_relevant_criminal_history = models.BooleanField(
        default=False,
        verbose_name=_("Self-declares no relevant criminal history"),
    )
    screening_notes = models.TextField(
        blank=True,
        verbose_name=_("Screening notes"),
        help_text=_("Coordinator-only notes about this applicant's screening status."),
    )

    # --- Review ---
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_volunteer_applications",
        verbose_name=_("Reviewed by"),
    )
    reviewed_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Reviewed at")
    )
    rejection_reason = models.TextField(
        blank=True,
        verbose_name=_("Rejection reason"),
        help_text=_(
            "Internal coordinator notes. PIPEDA: never exposed to the volunteer — "
            "notification uses generic 'not selected' language only."
        ),
    )

    # Workflows BB link for approval routing
    work_item = models.OneToOneField(
        "workflows.WorkItem",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volunteer_application",
        verbose_name=_("Workflow work item"),
    )

    class Meta:
        verbose_name = _("Volunteer application")
        verbose_name_plural = _("Volunteer applications")
        unique_together = [("opportunity", "volunteer")]
        indexes = [
            models.Index(
                fields=["status", "opportunity"],
                name="vol_app_status_opp_idx",
            ),
            models.Index(
                fields=["volunteer", "status"],
                name="vol_app_vol_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Application #{self.pk} — Opportunity #{self.opportunity_id}"


# ---------------------------------------------------------------------------
# Shift
# ---------------------------------------------------------------------------

class Shift(TimestampedModel):
    """
    A specific scheduled instance of an Opportunity.

    Duration is always derived from (end_datetime - start_datetime); it is
    never stored to avoid drift. All datetimes stored in UTC; displayed in
    America/Toronto.

    Capacity: if shift.capacity is None, inherits Opportunity.volunteer_capacity
    (which may also be None = unlimited).
    """

    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="shifts",
        verbose_name=_("Opportunity"),
    )
    coordinator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coordinated_shifts",
        verbose_name=_("Shift coordinator"),
    )

    title_en = models.CharField(
        max_length=200, blank=True, verbose_name=_("Title (EN)")
    )
    title_fr = models.CharField(
        max_length=200, blank=True, verbose_name=_("Title (FR)")
    )
    description_en = models.TextField(blank=True, verbose_name=_("Description (EN)"))
    description_fr = models.TextField(blank=True, verbose_name=_("Description (FR)"))

    # Timing — stored as UTC; displayed in America/Toronto
    start_datetime = models.DateTimeField(verbose_name=_("Start (UTC)"))
    end_datetime = models.DateTimeField(verbose_name=_("End (UTC)"))

    location_override = models.CharField(
        max_length=300,
        blank=True,
        verbose_name=_("Location override"),
        help_text=_(
            "If set, overrides the Opportunity's location for this shift only."
        ),
    )
    is_remote = models.BooleanField(
        null=True,
        blank=True,
        verbose_name=_("Remote override"),
        help_text=_(
            "If set, overrides the Opportunity's remote flag for this shift only."
        ),
    )

    capacity = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Capacity"),
        help_text=_(
            "Max volunteers for this shift. "
            "Null = inherits Opportunity.volunteer_capacity."
        ),
    )
    waitlist_enabled = models.BooleanField(
        default=True, verbose_name=_("Waitlist enabled")
    )
    waitlist_cap = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Waitlist cap"),
        help_text=_("Max waitlist size. Null = unlimited."),
    )

    # --- Cancellation ---
    is_cancelled = models.BooleanField(
        default=False, db_index=True, verbose_name=_("Cancelled")
    )
    cancelled_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Cancelled at")
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_shifts",
        verbose_name=_("Cancelled by"),
    )
    cancellation_reason = models.TextField(
        blank=True, verbose_name=_("Cancellation reason")
    )

    class Meta:
        ordering = ["start_datetime"]
        verbose_name = _("Shift")
        verbose_name_plural = _("Shifts")
        indexes = [
            models.Index(
                fields=["opportunity", "start_datetime"],
                name="vol_shift_opp_start_idx",
            ),
            models.Index(
                fields=["start_datetime", "is_cancelled"],
                name="vol_shift_start_cancelled_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_datetime__gt=models.F("start_datetime")),
                name="vol_shift_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        from django.utils import timezone as tz
        local_start = tz.localtime(self.start_datetime)
        return f"Shift #{self.pk} @ {local_start:%Y-%m-%d %H:%M}"

    @property
    def effective_capacity(self) -> int | None:
        """Shift capacity, falling back to Opportunity capacity (may be None = unlimited)."""
        if self.capacity is not None:
            return self.capacity
        return self.opportunity.volunteer_capacity

    @property
    def duration_hours(self) -> float:
        """Derived shift duration in hours. Never stored to avoid drift."""
        delta = self.end_datetime - self.start_datetime
        return round(delta.total_seconds() / 3600, 2)


# ---------------------------------------------------------------------------
# ShiftBooking
# ---------------------------------------------------------------------------

class ShiftBooking(TimestampedModel):
    """
    A volunteer's confirmed (or waitlisted) booking to a specific Shift.

    Overbooking prevention:
    book_shift() in services/scheduling.py acquires SELECT FOR UPDATE on the
    Shift row within an atomic block before checking confirmed_count vs. capacity.

    Reminder deduplication:
    reminder_24h_sent / reminder_2h_sent flags are set atomically (via
    update_fields) before the Celery task fires — prevents duplicate sends
    on Celery retry.
    """

    STATUS_CONFIRMED  = "confirmed"
    STATUS_WAITLISTED = "waitlisted"
    STATUS_CANCELLED  = "cancelled"
    STATUS_NO_SHOW    = "no_show"
    STATUS_COMPLETED  = "completed"
    STATUS_CHOICES = [
        (STATUS_CONFIRMED,  _("Confirmed")),
        (STATUS_WAITLISTED, _("Waitlisted")),
        (STATUS_CANCELLED,  _("Cancelled")),
        (STATUS_NO_SHOW,    _("No show")),
        (STATUS_COMPLETED,  _("Completed")),
    ]

    shift = models.ForeignKey(
        Shift,
        on_delete=models.CASCADE,
        related_name="bookings",
        verbose_name=_("Shift"),
    )
    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.CASCADE,
        related_name="bookings",
        verbose_name=_("Volunteer"),
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_CONFIRMED,
        db_index=True,
        verbose_name=_("Status"),
    )
    waitlist_position = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Waitlist position"),
        help_text=_("1 = next to be promoted. Null if confirmed."),
    )

    # Reminder deduplication flags
    reminder_24h_sent = models.BooleanField(
        default=False, verbose_name=_("24h reminder sent")
    )
    reminder_2h_sent = models.BooleanField(
        default=False, verbose_name=_("2h reminder sent")
    )

    cancelled_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Cancelled at")
    )
    cancellation_reason = models.CharField(
        max_length=300, blank=True, verbose_name=_("Cancellation reason")
    )

    class Meta:
        verbose_name = _("Shift booking")
        verbose_name_plural = _("Shift bookings")
        unique_together = [("shift", "volunteer")]
        indexes = [
            models.Index(
                fields=["shift", "status"],
                name="vol_booking_shift_status_idx",
            ),
            models.Index(
                fields=["volunteer", "status"],
                name="vol_booking_vol_status_idx",
            ),
            models.Index(
                fields=["status", "waitlist_position"],
                name="vol_booking_waitlist_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Booking #{self.pk}: Vol #{self.volunteer_id} → Shift #{self.shift_id}"


# ---------------------------------------------------------------------------
# HoursLog
# ---------------------------------------------------------------------------

class HoursLog(TimestampedModel):
    """
    Authoritative record of volunteer hours for one day / shift.

    The denormalized total on VolunteerProfile.total_hours_approved is
    recomputed from approved HoursLog rows on every approval — the HoursLog
    table is always the source of truth.

    PIPEDA: volunteer identified by FK (PK) only — no name in this model.

    Constraints:
    - hours must be > 0 and ≤ 24 (checked at DB level).
    - date must not be in the future (enforced in services/hours.py).
    """

    STATUS_PENDING  = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING,  _("Pending coordinator approval")),
        (STATUS_APPROVED, _("Approved")),
        (STATUS_REJECTED, _("Rejected")),
    ]

    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.PROTECT,
        related_name="hours_logs",
        verbose_name=_("Volunteer"),
    )
    opportunity = models.ForeignKey(
        Opportunity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hours_logs",
        verbose_name=_("Opportunity"),
    )
    shift = models.ForeignKey(
        Shift,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hours_logs",
        verbose_name=_("Shift"),
    )

    date = models.DateField(verbose_name=_("Date"))
    hours = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        verbose_name=_("Hours"),
        help_text=_("Hours volunteered (0.01 – 24.00)."),
    )
    description = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Description"),
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_volunteer_hours",
        verbose_name=_("Approved by"),
    )
    approved_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Approved at")
    )
    rejection_reason = models.CharField(
        max_length=300,
        blank=True,
        verbose_name=_("Rejection reason"),
    )

    class Meta:
        ordering = ["-date"]
        verbose_name = _("Hours log")
        verbose_name_plural = _("Hours logs")
        indexes = [
            models.Index(
                fields=["volunteer", "status"],
                name="vol_hours_vol_status_idx",
            ),
            models.Index(
                fields=["opportunity", "date"],
                name="vol_hours_opp_date_idx",
            ),
            models.Index(
                fields=["status", "date"],
                name="vol_hours_status_date_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(hours__gt=0, hours__lte=24),
                name="vol_hourslog_range",
            ),
        ]

    def __str__(self) -> str:
        return f"HoursLog #{self.pk}: {self.hours}h on {self.date}"


# ---------------------------------------------------------------------------
# ScreeningRecord
# ---------------------------------------------------------------------------

class ScreeningRecord(TimestampedModel):
    """
    Tracks background checks and references completed by a volunteer.

    CRITICAL PIPEDA rule: The actual criminal record check RESULT is NEVER
    stored. Only the fact that a coordinator verified the result as clear
    (verified_clear=True) is recorded. This is the minimum data required for
    the organization's due diligence record.

    Vulnerable Sector Check (VSC) notes:
    - No online vendor is authorized to provide VSCs in Canada.
    - The volunteer must attend their local police detachment in person.
    - The VSC is issued to the volunteer, not the organization.
    - The volunteer presents the result to the coordinator.
    - Default expiry: 3 years from completed_date (configurable per policy).
    """

    CHECK_TYPE_VSC       = "vulnerable_sector_check"
    CHECK_TYPE_PRC       = "police_record_check"
    CHECK_TYPE_REFERENCE = "reference_check"
    CHECK_TYPE_DRIVERS   = "drivers_abstract"
    CHECK_TYPE_CHOICES = [
        (CHECK_TYPE_VSC,       _("Vulnerable Sector Check (VSC)")),
        (CHECK_TYPE_PRC,       _("Police Record Check")),
        (CHECK_TYPE_REFERENCE, _("Reference Check")),
        (CHECK_TYPE_DRIVERS,   _("Driver's Abstract")),
    ]

    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.PROTECT,
        related_name="screening_records",
        verbose_name=_("Volunteer"),
    )
    opportunity = models.ForeignKey(
        Opportunity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="screening_records",
        verbose_name=_("Opportunity"),
        help_text=_(
            "Opportunity this check was obtained for. "
            "Null = general / organization-wide."
        ),
    )

    check_type = models.CharField(
        max_length=30,
        choices=CHECK_TYPE_CHOICES,
        verbose_name=_("Check type"),
    )
    completed_date = models.DateField(
        verbose_name=_("Completed date"),
        help_text=_("Date the volunteer completed or obtained the check."),
    )
    expires_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Expiry date"),
        help_text=_(
            "VSC default = 3 years from completed_date. "
            "Null = no expiry (e.g. reference checks)."
        ),
    )

    # Coordinator verification — NEVER stores the check result.
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_volunteer_screenings",
        verbose_name=_("Verified by"),
    )
    verified_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Verified at")
    )
    verified_clear = models.BooleanField(
        null=True,
        verbose_name=_("Result: verified clear"),
        help_text=_(
            "True = coordinator confirmed the result is clear. "
            "False = result not clear (triggers workflow). "
            "Null = not yet verified by coordinator. "
            "The actual criminal record detail is NEVER stored here."
        ),
    )
    notes = models.CharField(
        max_length=300,
        blank=True,
        verbose_name=_("Admin notes"),
        help_text=_(
            "Coordinator admin notes. Must not contain criminal record details."
        ),
    )

    class Meta:
        verbose_name = _("Screening record")
        verbose_name_plural = _("Screening records")
        indexes = [
            models.Index(
                fields=["volunteer", "check_type"],
                name="vol_screen_vol_type_idx",
            ),
            models.Index(
                fields=["expires_date"],
                name="vol_screen_expires_idx",
            ),
            models.Index(
                fields=["verified_clear"],
                name="vol_screen_cleared_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"ScreeningRecord #{self.pk}: "
            f"{self.get_check_type_display()} "
            f"(Vol #{self.volunteer_id})"
        )

    @property
    def is_expired(self) -> bool:
        if not self.expires_date:
            return False
        return timezone.localtime(timezone.now()).date() > self.expires_date

    @property
    def expires_within_30_days(self) -> bool:
        if not self.expires_date:
            return False
        today = timezone.localtime(timezone.now()).date()
        delta = self.expires_date - today
        return 0 <= delta.days <= 30


# ---------------------------------------------------------------------------
# Certification
# ---------------------------------------------------------------------------

class Certification(TimestampedModel):
    """
    Formal qualifications and training records for a volunteer.
    Examples: First Aid, CPR, WHMIS, Food Handler Certificate.

    Uploaded certificate documents stored in a private S3 path —
    never publicly accessible.
    """

    CERT_TYPE_FIRST_AID    = "first_aid"
    CERT_TYPE_CPR          = "cpr"
    CERT_TYPE_WHMIS        = "whmis"
    CERT_TYPE_FOOD_HANDLER = "food_handler"
    CERT_TYPE_DRIVERS      = "drivers_licence"
    CERT_TYPE_OTHER        = "other"
    CERT_TYPE_CHOICES = [
        (CERT_TYPE_FIRST_AID,    _("First Aid")),
        (CERT_TYPE_CPR,          _("CPR")),
        (CERT_TYPE_WHMIS,        _("WHMIS")),
        (CERT_TYPE_FOOD_HANDLER, _("Food Handler Certificate")),
        (CERT_TYPE_DRIVERS,      _("Driver's Licence (class)")),
        (CERT_TYPE_OTHER,        _("Other")),
    ]

    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.CASCADE,
        related_name="certifications",
        verbose_name=_("Volunteer"),
    )
    cert_type = models.CharField(
        max_length=30,
        choices=CERT_TYPE_CHOICES,
        verbose_name=_("Certificate type"),
    )
    cert_type_other = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Other certificate type"),
        help_text=_("Required when cert_type = 'other'."),
    )
    issuing_body = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Issuing body"),
    )
    issued_date = models.DateField(verbose_name=_("Issued date"))
    expires_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Expiry date"),
        help_text=_("Null = no expiry."),
    )

    # Uploaded certificate scan — private S3 path
    document = models.FileField(
        upload_to="volunteers/certifications/",
        null=True,
        blank=True,
        verbose_name=_("Certificate document"),
        help_text=_(
            "Scanned certificate (PDF or image). "
            "Stored in private S3 bucket — not publicly accessible."
        ),
    )

    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_volunteer_certifications",
        verbose_name=_("Verified by"),
    )
    verified_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Verified at")
    )

    class Meta:
        ordering = ["-issued_date"]
        verbose_name = _("Certification")
        verbose_name_plural = _("Certifications")
        indexes = [
            models.Index(
                fields=["volunteer", "cert_type"],
                name="vol_cert_vol_type_idx",
            ),
            models.Index(
                fields=["expires_date"],
                name="vol_cert_expires_idx",
            ),
        ]

    def __str__(self) -> str:
        label = self.cert_type_other or self.get_cert_type_display()
        return f"Certification #{self.pk}: {label} (Vol #{self.volunteer_id})"

    @property
    def is_expired(self) -> bool:
        if not self.expires_date:
            return False
        return timezone.localtime(timezone.now()).date() > self.expires_date

    @property
    def expires_within_30_days(self) -> bool:
        if not self.expires_date:
            return False
        today = timezone.localtime(timezone.now()).date()
        delta = self.expires_date - today
        return 0 <= delta.days <= 30


# ---------------------------------------------------------------------------
# Honorarium
# ---------------------------------------------------------------------------

class Honorarium(TimestampedModel):
    """
    Records a payment to a volunteer — expense reimbursement or nominal honorarium.

    CRA compliance rules (enforced in services/honoraria.py):
    - EXPENSE_REIMBURSEMENT: no threshold tracking; not taxable.
    - HONORARIUM: cumulative tracking per volunteer per calendar year.
      Alert at $450 YTD (near threshold). Hard block at $1,000 YTD (service layer).
      T4A required when cumulative > $500 in a calendar year.

    This model is an immutable financial record: has_delete_permission = False
    in admin. Amounts in CAD.

    calendar_year is derived from payment_date on every save; do not set manually.
    """

    PAYMENT_TYPE_EXPENSE    = "expense_reimbursement"
    PAYMENT_TYPE_HONORARIUM = "honorarium"
    PAYMENT_TYPE_CHOICES = [
        (PAYMENT_TYPE_EXPENSE,    _("Expense Reimbursement")),
        (PAYMENT_TYPE_HONORARIUM, _("Honorarium")),
    ]

    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.PROTECT,
        related_name="honoraria",
        verbose_name=_("Volunteer"),
    )
    payment_type = models.CharField(
        max_length=30,
        choices=PAYMENT_TYPE_CHOICES,
        verbose_name=_("Payment type"),
    )
    amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        verbose_name=_("Amount (CAD)"),
    )
    currency = models.CharField(
        max_length=3,
        default="CAD",
        verbose_name=_("Currency"),
    )
    description = models.CharField(
        max_length=300,
        verbose_name=_("Description"),
    )
    payment_date = models.DateField(verbose_name=_("Payment date"))
    calendar_year = models.PositiveSmallIntegerField(
        verbose_name=_("Calendar year"),
        help_text=_(
            "CRA calendar year for threshold tracking. "
            "Derived from payment_date on save — do not set manually."
        ),
    )

    # T4A tracking
    t4a_required = models.BooleanField(
        default=False,
        verbose_name=_("T4A required"),
        help_text=_(
            "True when volunteer's cumulative honoraria exceed $500 in this calendar_year."
        ),
    )
    t4a_issued = models.BooleanField(
        default=False, verbose_name=_("T4A issued")
    )
    t4a_issued_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("T4A issued at")
    )

    # Payments BB link (optional — set when processed via Payments BB)
    payment = models.OneToOneField(
        "payments.Payment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="honorarium",
        verbose_name=_("Payment (Payments BB)"),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_honoraria",
        verbose_name=_("Created by"),
    )

    class Meta:
        verbose_name = _("Honorarium")
        verbose_name_plural = _("Honoraria")
        indexes = [
            models.Index(
                fields=["volunteer", "calendar_year", "payment_type"],
                name="vol_hon_vol_yr_type_idx",
            ),
            models.Index(
                fields=["t4a_required", "t4a_issued"],
                name="vol_hon_t4a_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="vol_honorarium_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Honorarium #{self.pk}: "
            f"${self.amount} ({self.get_payment_type_display()}) "
            f"Vol #{self.volunteer_id} — {self.calendar_year}"
        )

    def save(self, *args, **kwargs) -> None:
        # Always derive calendar_year from payment_date — never trust a caller-supplied value.
        self.calendar_year = self.payment_date.year
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# VolunteerNote
# ---------------------------------------------------------------------------

class VolunteerNote(TimestampedModel):
    """
    Internal coordinator notes on a volunteer.
    Never shown to the volunteer. Append-only in the UI.
    """

    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.CASCADE,
        related_name="notes",
        verbose_name=_("Volunteer"),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="volunteer_notes",
        verbose_name=_("Author"),
    )
    body = models.TextField(verbose_name=_("Note body"))

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Volunteer note")
        verbose_name_plural = _("Volunteer notes")

    def __str__(self) -> str:
        return f"Note #{self.pk} on VolunteerProfile #{self.volunteer_id}"


# ---------------------------------------------------------------------------
# RecognitionMilestone
# ---------------------------------------------------------------------------

class RecognitionMilestone(models.Model):
    """
    Records when a volunteer crosses a cumulative hours milestone.
    Idempotent — unique_together prevents duplicate milestone records.
    Created by services/hours.py when an hours approval pushes the volunteer
    past a configured threshold.

    Default thresholds: 25h, 50h, 100h, 250h, 500h, 1000h.
    Organizations can add custom thresholds via the Django admin.
    """

    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.CASCADE,
        related_name="milestones",
        verbose_name=_("Volunteer"),
    )
    hours_threshold = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        verbose_name=_("Hours threshold"),
        validators=[MinValueValidator(1)],
    )
    achieved_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Achieved at"),
    )
    notification_sent = models.BooleanField(
        default=False,
        verbose_name=_("Notification sent"),
    )

    class Meta:
        verbose_name = _("Recognition milestone")
        verbose_name_plural = _("Recognition milestones")
        unique_together = [("volunteer", "hours_threshold")]
        ordering = ["hours_threshold"]

    def __str__(self) -> str:
        return (
            f"Milestone #{self.pk}: "
            f"{self.hours_threshold}h — Vol #{self.volunteer_id}"
        )

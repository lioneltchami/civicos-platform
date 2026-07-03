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
  - CRA PC-025 thresholds enforced in Honorarium.clean() (called by save()):
      $450 alert threshold — non-blocking, coordinator must be notified.
      $500 T4A threshold — auto-sets t4a_required = True, non-blocking.
      $1,000 hard block — ValidationError raised, honorarium rejected.
  - Thresholds are cumulative YTD per volunteer per calendar year.
  - .update() and bulk_create() bypass clean(); callers must validate explicitly.

Spec: docs/volunteer-management-bb-spec.md
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import connection, models
from django.db.models import GeneratedField
from django.db.models.functions import ExtractYear
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
        """
        True if this Opportunity is published and has not passed its closing datetime.

        NOTE: Does NOT check volunteer_capacity — a fully-booked opportunity still
        returns True. Callers that need capacity awareness must also check
        application count vs volunteer_capacity separately.
        """
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

    @property
    def get_sin_bytes(self) -> bytes | None:
        """
        Return the encrypted SIN as bytes (not memoryview).
        Django's BinaryField returns memoryview in Python 3;
        Fernet.decrypt() requires bytes. Always use this property,
        never access sin_encrypted directly.
        """
        if self.sin_encrypted is None:
            return None
        return bytes(self.sin_encrypted)

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
        constraints = [
            models.CheckConstraint(
                check=models.Q(total_hours_approved__gte=0),
                name="vol_profile_total_hours_non_negative",
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

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.photo and not self.photo_consent_id:
            raise ValidationError(
                {"photo": _("A photo consent record is required before uploading a photo (PIPEDA).")}
            )

    def __setattr__(self, name: str, value) -> None:
        """
        Guard against accidental plaintext SIN storage.

        PIPEDA s.7 / security requirement: SIN is a sensitive personal identifier
        and must never be stored unencrypted. This guard fires at Python
        attribute-assignment time — before save() — so bugs are caught in tests,
        not in production.

        Only bytes that decode to a valid Fernet token (base64url, version byte
        0x80, minimum 9 raw bytes) are accepted. None clears the field.

        The service layer (apps.volunteers.services.sin) is responsible for
        encrypting the SIN before assignment. Do NOT bypass this guard by
        accessing the underlying DB field directly.
        """
        if name == "sin_encrypted" and value is not None:
            if isinstance(value, memoryview):
                value = bytes(value)
            if not isinstance(value, bytes):
                raise TypeError(
                    "sin_encrypted must be bytes (a Fernet token) or None, "
                    f"got {type(value).__name__}. "
                    "Use apps.volunteers.services.sin.encrypt_sin() to produce the token."
                )
            import base64 as _base64
            _raw = None
            try:
                _padded = value + b"=" * (-len(value) % 4)
                _raw = _base64.urlsafe_b64decode(_padded)
            except Exception:
                pass
            if _raw is None or len(_raw) < 73 or _raw[0] != 0x80:
                raise ValueError(
                    "sin_encrypted must be a valid Fernet token (base64url-encoded, "
                    "starting with version byte 0x80). "
                    "Use apps.volunteers.services.sin.encrypt_sin() to produce one. "
                    "Do NOT assign raw SIN digits or arbitrary bytes."
                )
        super().__setattr__(name, value)


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
    # PROTECT: ConsentRecords must never be hard-deleted (PIPEDA erasure anonymizes
    # their content in-place via the Consent BB). Changing to SET_NULL would silently
    # destroy proof of which agreement version was signed at application time.
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
        on_delete=models.PROTECT,
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
        on_delete=models.PROTECT,
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
        validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("24"))],
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
            models.UniqueConstraint(
                fields=["volunteer", "shift"],
                condition=models.Q(shift__isnull=False),
                name="vol_hourslog_unique_vol_shift",
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
        blank=True,  # None = pending (not yet verified by coordinator)
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
            "⚠ PIPEDA / CRA constraint: record only the screening outcome date, "
            "check-type, and logistical notes (e.g. 'submitted to RCMP 2024-03-01'). "
            "Do NOT record criminal record details, offence descriptions, charge history, "
            "or any result beyond the verified_clear flag. "
            "Access to this field is audit-logged. Maximum 300 characters."
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
        constraints = [
            models.UniqueConstraint(
                fields=["volunteer", "check_type"],
                condition=models.Q(opportunity__isnull=True),
                name="vol_screen_unique_vol_type_no_opp",
            ),
            models.UniqueConstraint(
                fields=["volunteer", "check_type", "opportunity"],
                condition=models.Q(opportunity__isnull=False),
                name="vol_screen_unique_vol_type_with_opp",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"ScreeningRecord #{self.pk}: "
            f"{self.get_check_type_display()} "
            f"(Vol #{self.volunteer_id})"
        )

    # Keyword fragments that suggest criminal-record detail rather than
    # logistical notes.  Checked only for VSC records with a recorded result.
    _PROHIBITED_NOTE_FRAGMENTS = (
        "charge",
        "offence",
        "offense",
        "conviction",
        "criminal record",
        "finding of guilt",
        "absolute discharge",
        "conditional discharge",
        "record suspension",
        "pardon",
        "acquit",
        "indictable",
        "summary conviction",
        "guilty",
    )

    def clean(self):
        from datetime import timedelta

        from django.core.exceptions import ValidationError

        # H8 fix: auto-compute a 3-year expiry for VSC records that have a
        # completed_date but no explicit expires_date set by the coordinator.
        # Canadian VSC clearances are valid for 3 years (RCMP policy).  We
        # only fill this in when the record is cleared (verified_clear=True)
        # so that unverified/failed records keep expires_date=None and never
        # show up in the expiry-alert task.
        if (
            self.check_type == self.CHECK_TYPE_VSC
            and self.verified_clear is True
            and self.completed_date
            and not self.expires_date
        ):
            self.expires_date = self.completed_date + timedelta(days=3 * 365)

        if not self.notes:
            return

        # For VSC records where a result has been recorded, enforce strict
        # data-minimisation: notes must be logistical only (PIPEDA / Criminal
        # Records Act Canada).  A 150-char cap forces brevity; prohibited
        # keywords detect outcome language that must never appear here.
        if (
            self.check_type == self.CHECK_TYPE_VSC
            and self.verified_clear is not None
        ):
            if len(self.notes) > 150:
                raise ValidationError(
                    {
                        "notes": _(
                            "Notes on a verified VSC record must be logistical only "
                            "(e.g. submission date, agency reference). "
                            "Maximum 150 characters — current entry has %(count)d."
                        )
                        % {"count": len(self.notes)}
                    }
                )

            lower = self.notes.lower()
            found = [kw for kw in self._PROHIBITED_NOTE_FRAGMENTS if kw in lower]
            if found:
                raise ValidationError(
                    {
                        "notes": _(
                            "Notes must not contain criminal-record detail. "
                            "Remove terms related to: %(terms)s. "
                            "Record only the submission date and agency reference — "
                            "the result is captured by the verified_clear flag."
                        )
                        % {"terms": ", ".join(f'"{t}"' for t in found)}
                    }
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

    # PROTECT: Certifications are compliance records (First Aid, CPR, VSC) and must
    # survive profile deletion. Erasure workflows must explicitly archive or redact
    # certifications before deleting the profile.
    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.PROTECT,
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

    CRA compliance rules (enforced in clean() / save()):
    - EXPENSE_REIMBURSEMENT: no threshold tracking; not taxable.
    - HONORARIUM: cumulative tracking per volunteer per calendar year.
      Alert at $450 YTD (near threshold) — non-blocking.
      T4A required ($500+ YTD) — auto-sets t4a_required = True; non-blocking.
      Hard block at $1,000 YTD — raises ValidationError; honorarium rejected.

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
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name=_("Amount (CAD)"),
    )
    currency = models.CharField(
        max_length=3,
        default="CAD",
        choices=[("CAD", "Canadian Dollar")],
        verbose_name=_("Currency"),
        help_text=_("Only CAD supported. CRA thresholds are denominated in CAD."),
    )
    description = models.CharField(
        max_length=300,
        verbose_name=_("Description"),
    )
    payment_date = models.DateField(verbose_name=_("Payment date"))
    calendar_year = GeneratedField(
        expression=ExtractYear("payment_date"),
        output_field=models.PositiveSmallIntegerField(),
        db_persist=True,   # STORED generated column — persisted to disk, indexable
        verbose_name=_("Calendar year"),
        help_text=_("Automatically derived from payment_date. Used for CRA threshold tracking."),
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
        # M2: Strip financial data (amount, payment_type) and volunteer identity from
        # __str__ — this string appears in Django admin logs, Sentry breadcrumbs,
        # and management command output. Year-only is safe aggregate data.
        return f"Honorarium #{self.pk} — {self.payment_date.year if self.payment_date else '?'}"

    def clean(self) -> None:
        """
        Enforce CRA PC-025 honorarium thresholds (per volunteer, per calendar year).

        Thresholds (from settings):
          VOLUNTEER_CRA_ALERT_THRESHOLD ($450): non-blocking — triggers coordinator alert.
              t4a_required is NOT automatically set at this level.
          VOLUNTEER_CRA_T4A_THRESHOLD  ($500): auto-sets t4a_required = True.
              A T4A slip must be issued to CRA. Non-blocking.
          VOLUNTEER_CRA_HARD_BLOCK    ($1000): raises ValidationError.
              The honorarium is rejected. Finance team must review.

        Thresholds are cumulative YTD (all honoraria for this volunteer in
        payment_date.year). Current instance is excluded from the YTD sum when
        self.pk is set (edit path).

        Note: .update() and bulk_create() bypass clean(). Celery tasks that update
        honoraria must call full_clean() explicitly or use the service layer.
        """
        from django.core.exceptions import ValidationError
        from django.db.models import Sum

        alert_threshold = getattr(settings, "VOLUNTEER_CRA_ALERT_THRESHOLD", 450)
        t4a_threshold = getattr(settings, "VOLUNTEER_CRA_T4A_THRESHOLD", 500)
        hard_block = getattr(settings, "VOLUNTEER_CRA_HARD_BLOCK", 1_000)

        if self.payment_type != self.PAYMENT_TYPE_HONORARIUM:
            return  # CRA PC-025 thresholds apply to honoraria only, not expense reimbursements

        if not self.payment_date or not self.amount:
            return  # incomplete data — let field validators handle it

        year = self.payment_date.year

        # Calculate YTD total for this volunteer in this calendar year,
        # excluding self (to allow edits without counting the current amount twice).
        # Filter to honorarium payment type only — expense reimbursements must not
        # count against CRA PC-025 thresholds (C-NEW-1 fix).
        # H2 (deadlock fix): Do NOT acquire select_for_update() here.
        # The service layer (create_honorarium) already holds a select_for_update()
        # lock on VolunteerProfile before calling full_clean(). That VolunteerProfile
        # lock is the sole serialisation point for concurrent honorarium creation.
        # Adding a second lock on Honorarium rows inside clean() creates an AB/BA
        # deadlock risk: if any other path acquires Honorarium first then tries to
        # lock VolunteerProfile, both transactions deadlock. A plain non-locking
        # read is safe here because we are already inside the same transaction that
        # holds the VolunteerProfile lock, so the Honorarium rows cannot change
        # under us (concurrent writes to the same volunteer would be blocked waiting
        # for the VolunteerProfile lock, not for a Honorarium lock).
        qs = Honorarium.objects.filter(
            volunteer=self.volunteer,
            payment_date__year=year,
            payment_type=self.PAYMENT_TYPE_HONORARIUM,
        )
        if self.pk:
            qs = qs.exclude(pk=self.pk)

        existing_total = qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        projected_total = existing_total + self.amount

        # Hard block: reject entirely.
        if projected_total >= Decimal(str(hard_block)):
            raise ValidationError(
                {
                    "amount": _(
                        "This honorarium would bring %(volunteer)s's %(year)s total to "
                        "$%(projected)s, exceeding the CRA hard block of $%(limit)s. "
                        "Contact your finance team. CRA PC-025."
                    ) % {
                        "volunteer": f"VolunteerProfile #{self.volunteer_id}",
                        "year": year,
                        "projected": f"{projected_total:.2f}",
                        "limit": f"{hard_block:.2f}",
                    }
                }
            )

        # T4A threshold: auto-set t4a_required.
        if projected_total >= Decimal(str(t4a_threshold)):
            self.t4a_required = True

        # Alert threshold ($450): non-blocking — does not prevent save.
        # The admin save_model() or service layer should check the YTD total
        # and emit a coordinator notification when >= $450 but < $500.
        # No action is taken here beyond setting t4a_required above if applicable.

    def save(self, *args, skip_clean: bool = False, **kwargs) -> None:
        """
        Save the Honorarium, running full_clean() (including CRA threshold checks)
        unless skip_clean=True is explicitly passed.

        Pass skip_clean=True only for bulk-load / fixture scenarios where the
        caller takes responsibility for running validations itself.

        H5 — update_fields guard:
        clean() may set self.t4a_required = True when the volunteer crosses the
        $500 T4A threshold. If a caller passes update_fields=["amount", ...],
        Django will only persist the listed fields and silently discard the
        t4a_required mutation — the CRA compliance flag is never written to the DB.

        When full_clean() runs (skip_clean=False) we inject "t4a_required" into
        update_fields automatically so the mutation is always persisted. We do NOT
        inject it when skip_clean=True because the caller is taking responsibility
        for validation and their targeted update should be honoured as-is.
        """
        if not skip_clean:
            self.full_clean()
            # H5: Ensure t4a_required is always persisted when full_clean() runs.
            # clean() may have mutated self.t4a_required = True — if the caller
            # passed update_fields, Django would otherwise silently drop that mutation.
            if "update_fields" in kwargs and kwargs["update_fields"] is not None:
                update_fields = list(kwargs["update_fields"])
                if "t4a_required" not in update_fields:
                    update_fields.append("t4a_required")
                kwargs["update_fields"] = update_fields
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
    body = models.TextField(max_length=2000, verbose_name=_("Note body"))

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
        constraints = [
            models.CheckConstraint(
                check=models.Q(hours_threshold__gte=1),
                name="vol_milestone_threshold_min",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Milestone #{self.pk}: "
            f"{self.hours_threshold}h — Vol #{self.volunteer_id}"
        )

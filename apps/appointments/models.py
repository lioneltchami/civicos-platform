"""
Appointments / Scheduling Building Block — Models.

Wave 1 covers the scheduling catalogue and configuration layer:
  Organization → Location → Resource
  ServiceType → AppointmentType → SchedulingPolicy
  StaffProfile (extends auth_extension.User for staff)

Later waves add:
  Wave 2: AvailabilityTemplate, StaffException, Slot
  Wave 3: Booking, Attendee, BookingAuditLog
  Wave 4: WaitlistEntry, QueueEntry, ClientNoShowRecord

Spec: SPEC_APPOINTMENTS_BB.md

PIPEDA / Privacy Act design rules:
  - No PII (citizen name, email) in __str__, list_display, or audit event_detail.
    Citizens identified by .pk (UUID) only; staff by .pk (int) only.
  - form_responses JSON field (Wave 3) may contain Protected B data — gated behind
    the appointments.view_booking_form_responses permission at admin and view layers.
  - video_join_url_citizen (Wave 2 Slot) NEVER in unauthenticated email.
    Delivered only inside an authenticated portal session.
  - Intake form answers collected only for the specific service type's requirements —
    PIPEDA data-minimisation principle.

Security invariants:
  - select_for_update() inside atomic() before capacity checks (Wave 3).
  - record_event() inside atomic() block (PIPEDA 4.5.3) (Wave 3).
  - Citizens receive 404 (not 403) for booking PKs they do not own (Wave 5).
  - LoginRequiredMixin ALWAYS before PermissionRequiredMixin in view MRO (Wave 5).
"""
from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimestampedModel


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

class Organization(TimestampedModel):
    """
    Top-level tenant boundary for the Appointments BB.

    Each organization owns one or more Locations. A single CivicOS deployment
    typically serves a single organization; multi-tenant deployments partition
    by organization at the view layer.

    No PII stored here — organization name only.
    """

    name_en = models.CharField(max_length=200, verbose_name=_("Name (EN)"))
    name_fr = models.CharField(max_length=200, verbose_name=_("Name (FR)"))
    slug = models.SlugField(
        max_length=80,
        unique=True,
        verbose_name=_("Slug"),
        help_text=_("Machine-readable identifier. Used in URL routing and service-layer lookups."),
    )
    description_en = models.TextField(blank=True, verbose_name=_("Description (EN)"))
    description_fr = models.TextField(blank=True, verbose_name=_("Description (FR)"))
    organization_type = models.CharField(
        max_length=25,  # "government_provincial" = 21 chars; 25 leaves headroom
        choices=[
            ("government_federal", _("Federal Government")),
            ("government_provincial", _("Provincial Government")),
            ("government_municipal", _("Municipal Government")),
            ("ngo", _("Non-Governmental Organization")),
            ("health", _("Health Authority / Clinic")),
            ("other", _("Other")),
        ],
        default="government_federal",
        verbose_name=_("Organization type"),
    )
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Active"))

    class Meta:
        ordering = ["name_en"]
        verbose_name = _("Organization")
        verbose_name_plural = _("Organizations")

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        """Return name in the currently active language."""
        from django.utils.translation import get_language
        lang = get_language() or "en"
        return self.name_fr if lang.startswith("fr") else self.name_en


# ---------------------------------------------------------------------------
# SchedulingPolicy
# ---------------------------------------------------------------------------

class SchedulingPolicy(TimestampedModel):
    """
    Configurable booking rules for a Location or AppointmentType.

    When both a Location and an AppointmentType have a policy attached, the
    AppointmentType policy takes precedence (more specific overrides general).
    If neither has a policy, defaults in settings.CIVICOS["APPOINTMENTS"] apply.

    The default policy is seeded by migration 0002_seed_default_policy.
    """

    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name=_("Policy name"),
        help_text=_("Human-readable name for this policy configuration."),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))

    # ── Booking window ───────────────────────────────────────────────────────

    min_lead_time_hours = models.PositiveIntegerField(
        default=1,
        verbose_name=_("Minimum lead time (hours)"),
        help_text=_(
            "Minimum hours before a slot that self-service booking is allowed. "
            "Enforced against wall-clock time, not business hours. "
            "Staff-assisted bookings bypass this limit."
        ),
    )
    max_advance_days = models.PositiveIntegerField(
        default=180,
        verbose_name=_("Maximum advance booking (days)"),
        help_text=_("Maximum days in the future a citizen can book. 0 = no limit."),
    )

    # ── Slot generation ──────────────────────────────────────────────────────

    slot_interval_minutes = models.PositiveIntegerField(
        default=15,
        verbose_name=_("Slot interval (minutes)"),
        help_text=_(
            "Step between slot start times. May differ from appointment duration. "
            "Example: 30-min appointments on a 15-min grid = every quarter-hour start."
        ),
        validators=[MinValueValidator(5), MaxValueValidator(240)],
    )
    buffer_before_minutes = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Buffer before appointment (minutes)"),
        help_text=_(
            "Setup time before the appointment. Expands the slot's effective busy-time "
            "footprint so back-to-back slots cannot be double-booked."
        ),
        validators=[MaxValueValidator(240)],
    )
    buffer_after_minutes = models.PositiveIntegerField(
        default=5,
        verbose_name=_("Buffer after appointment (minutes)"),
        help_text=_("Wrap-up time after the appointment. Same function as buffer_before."),
        validators=[MaxValueValidator(240)],
    )

    # ── Cancellation / rescheduling ──────────────────────────────────────────

    cancellation_notice_hours = models.PositiveIntegerField(
        default=24,
        verbose_name=_("Cancellation notice required (hours)"),
        help_text=_(
            "Minimum hours before the appointment that a citizen may self-cancel. "
            "Cancellations within this window are flagged as late_cancellation=True."
        ),
    )
    reschedule_notice_hours = models.PositiveIntegerField(
        default=24,
        verbose_name=_("Reschedule notice required (hours)"),
        help_text=_("Minimum hours before the appointment that a citizen may reschedule."),
    )
    max_reschedule_count = models.PositiveIntegerField(
        default=3,
        verbose_name=_("Maximum reschedule count"),
        help_text=_(
            "How many times a single booking may be rescheduled before the citizen "
            "must contact staff. 0 = rescheduling not allowed by citizens."
        ),
    )

    # ── Booking limits (anti-hoarding) ───────────────────────────────────────

    max_active_bookings_per_citizen = models.PositiveIntegerField(
        default=3,
        verbose_name=_("Max active bookings per citizen"),
        help_text=_(
            "Maximum simultaneous CONFIRMED bookings per citizen under this policy. "
            "Prevents slot hoarding. 0 = no limit."
        ),
    )
    booking_frequency_days = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Booking frequency limit (days)"),
        help_text=_(
            "If > 0, a citizen cannot book again within this many calendar days of their "
            "last CONFIRMED booking. Used by food banks and similar services where "
            "frequency is part of the eligibility model. 0 = no frequency limit."
        ),
    )

    # ── Waitlist ─────────────────────────────────────────────────────────────

    waitlist_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Waitlist enabled"),
        help_text=_("If True, citizens can join a waitlist when a slot is full."),
    )
    waitlist_acceptance_window_hours = models.PositiveIntegerField(
        default=2,
        verbose_name=_("Waitlist acceptance window (hours)"),
        help_text=_(
            "Hours a waitlisted citizen has to accept a freed slot before it moves "
            "to the next person on the waitlist."
        ),
    )
    max_waitlist_per_slot = models.PositiveIntegerField(
        default=10,
        verbose_name=_("Maximum waitlist entries per slot"),
        help_text=_("Hard cap on the waitlist queue length per slot. 0 = unlimited."),
    )
    waitlist_notify_batch_size = models.PositiveIntegerField(
        default=3,
        verbose_name=_("Waitlist notification batch size"),
        help_text=_(
            "How many waitlisted citizens to notify simultaneously when a slot opens. "
            "Batch of 3 raises fill rate from ~50% to 80%+ (industry evidence). "
            "The first to accept gets the slot; others are notified it was taken."
        ),
    )

    # ── No-show escalation ───────────────────────────────────────────────────

    no_show_warning_threshold = models.PositiveIntegerField(
        default=1,
        verbose_name=_("No-show warning threshold"),
        help_text=_(
            "After this many no-shows, flag the ClientNoShowRecord for staff review. "
            "0 = never warn."
        ),
    )
    no_show_suspension_threshold = models.PositiveIntegerField(
        default=3,
        verbose_name=_("No-show suspension threshold"),
        help_text=_(
            "After this many no-shows, set ClientNoShowRecord.is_suspended=True "
            "preventing further self-service booking. "
            "0 = non-punitive mode (recommended for health and social services)."
        ),
    )

    class Meta:
        ordering = ["name"]
        verbose_name = _("Scheduling policy")
        verbose_name_plural = _("Scheduling policies")

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# ServiceType
# ---------------------------------------------------------------------------

class ServiceType(TimestampedModel):
    """
    Catalogue of bookable services.

    Each ServiceType represents a category of service that can be offered at
    a location. One or more AppointmentTypes are defined under each ServiceType
    to represent specific schedulable variants (e.g., 30-min in-person vs.
    15-min virtual).

    Bilingual (EN/FR) following GC Design System Active Offer requirements.
    """

    slug = models.SlugField(
        max_length=80,
        unique=True,
        verbose_name=_("Slug"),
        help_text=_("Machine-readable identifier. Used in URL routing and service-layer lookups."),
    )
    name_en = models.CharField(max_length=200, verbose_name=_("Name (EN)"))
    name_fr = models.CharField(max_length=200, verbose_name=_("Name (FR)"))
    description_en = models.TextField(blank=True, verbose_name=_("Description (EN)"))
    description_fr = models.TextField(blank=True, verbose_name=_("Description (FR)"))
    category = models.CharField(
        max_length=40,
        choices=[
            ("government", _("Government Service")),
            ("health", _("Health Service")),
            ("legal", _("Legal Aid")),
            ("employment", _("Employment Service")),
            ("housing", _("Housing Service")),
            ("settlement", _("Settlement Service")),
            ("food", _("Food Security")),
            ("mental_health", _("Mental Health & Addictions")),
            ("other", _("Other")),
        ],
        default="government",
        verbose_name=_("Service category"),
    )
    sector = models.CharField(
        max_length=20,
        choices=[
            ("government", _("Government")),
            ("ngo", _("NGO")),
            ("both", _("Both")),
        ],
        default="both",
        verbose_name=_("Sector"),
        help_text=_("Which sector this service type is typically used in."),
    )

    # Privacy / compliance
    privacy_sensitivity = models.CharField(
        max_length=20,
        choices=[
            ("standard", _("Standard (unclassified)")),
            ("protected_a", _("Protected A")),
            ("protected_b", _("Protected B")),
        ],
        default="standard",
        verbose_name=_("Privacy sensitivity"),
        help_text=_(
            "Sensitivity classification following TBS SPIN 2023. "
            "Drives which additional consent and access controls apply."
        ),
    )

    # Eligibility
    requires_eligibility_screening = models.BooleanField(
        default=False,
        verbose_name=_("Requires eligibility screening"),
        help_text=_(
            "If True, citizens must complete an eligibility check before the booking "
            "flow is accessible (e.g., income threshold for legal aid, insurance "
            "status for CHC, By-Name-List for coordinated housing access)."
        ),
    )
    eligibility_description_en = models.TextField(
        blank=True,
        verbose_name=_("Eligibility description (EN)"),
        help_text=_("Shown to citizens before they begin the booking flow."),
    )
    eligibility_description_fr = models.TextField(
        blank=True,
        verbose_name=_("Eligibility description (FR)"),
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name=_("Active"),
    )
    sort_order = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_("Sort order"),
        help_text=_("Lower numbers appear first in listings. Tie-breaks alphabetically by name."),
    )

    class Meta:
        ordering = ["sort_order", "name_en"]
        verbose_name = _("Service type")
        verbose_name_plural = _("Service types")
        indexes = [
            models.Index(fields=["category", "is_active"], name="appt_servicetype_cat_active"),
        ]

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        """Return name in the currently active language."""
        from django.utils.translation import get_language
        lang = get_language() or "en"
        return self.name_fr if lang.startswith("fr") else self.name_en


# ---------------------------------------------------------------------------
# AppointmentType
# ---------------------------------------------------------------------------

class AppointmentType(TimestampedModel):
    """
    Schedulable variant of a ServiceType.

    Defines the concrete parameters for a bookable appointment: duration,
    mode (in-person / virtual / phone), capacity, gating requirements
    (document upload, payment, consent), and intake form schema.

    Multiple AppointmentTypes can share the same ServiceType, e.g.:
      - EI Application — 30 min in-person
      - EI Application — 45 min in-person (complex cases)
      - EI Application — 20 min virtual

    The SchedulingPolicy attached here overrides the Location-level policy
    for fine-grained control per appointment variant.
    """

    service_type = models.ForeignKey(
        ServiceType,
        on_delete=models.PROTECT,
        related_name="appointment_types",
        verbose_name=_("Service type"),
        help_text=_("Parent service category."),
    )
    slug = models.SlugField(
        max_length=80,
        unique=True,
        verbose_name=_("Slug"),
        help_text=_("Machine-readable identifier used in URL routing."),
    )
    name_en = models.CharField(max_length=200, verbose_name=_("Name (EN)"))
    name_fr = models.CharField(max_length=200, verbose_name=_("Name (FR)"))
    description_en = models.TextField(blank=True, verbose_name=_("Description (EN)"))
    description_fr = models.TextField(blank=True, verbose_name=_("Description (FR)"))
    duration_minutes = models.PositiveIntegerField(
        default=30,
        verbose_name=_("Duration (minutes)"),
        help_text=_(
            "Length of the appointment itself, not including buffers. "
            "Buffers are defined in the attached SchedulingPolicy."
        ),
        validators=[MinValueValidator(5), MaxValueValidator(480)],
    )
    mode = models.CharField(
        max_length=20,
        choices=[
            ("in_person", _("In-Person")),
            ("virtual", _("Virtual (Video)")),
            ("phone", _("Phone")),
            ("hybrid", _("Hybrid — Client Choice")),
        ],
        default="in_person",
        verbose_name=_("Appointment mode"),
    )

    # Capacity
    capacity_per_slot = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_("Capacity per slot"),
        help_text=_(
            "Number of clients that can be booked into a single slot. "
            "1 = one-to-one appointment. > 1 = group session."
        ),
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )

    # Scheduling policy — overrides Location-level policy
    scheduling_policy = models.ForeignKey(
        SchedulingPolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointment_types",
        verbose_name=_("Scheduling policy"),
        help_text=_(
            "Overrides the Location's scheduling policy for this appointment type. "
            "Leave blank to inherit the Location policy."
        ),
    )

    # Intake form (JSON Schema for pre-booking questions)
    intake_form_schema = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Intake form schema"),
        help_text=_(
            "JSON Schema (draft-07) for pre-booking intake questions. "
            "Answers are stored in Booking.form_responses at booking time. "
            "Collect only what the service actually requires (PIPEDA data-minimisation)."
        ),
    )

    # ── Gating requirements ──────────────────────────────────────────────────

    requires_document_upload = models.BooleanField(
        default=False,
        verbose_name=_("Requires document upload"),
        help_text=_(
            "If True, citizens must upload a supporting document before the booking is "
            "confirmed. Document category is specified in required_document_category_slug."
        ),
    )
    required_document_category_slug = models.CharField(
        max_length=80,
        blank=True,
        verbose_name=_("Required document category slug"),
        help_text=_("Slug of the DocumentCategory required. Only relevant if requires_document_upload=True."),
    )
    requires_payment = models.BooleanField(
        default=False,
        verbose_name=_("Requires payment"),
        help_text=_(
            "If True, the citizen must complete a fee payment before the slot is confirmed. "
            "Fee amount is configured via the Payments BB using fee_code."
        ),
    )
    fee_code = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Fee code"),
        help_text=_("Fee code in the Payments BB. Only relevant if requires_payment=True."),
    )
    requires_consent = models.BooleanField(
        default=False,
        verbose_name=_("Requires explicit consent"),
        help_text=_(
            "If True, the citizen must grant consent for data processing before booking. "
            "The Consent BB gate is checked using consent_category_slug. "
            "Always required under Quebec Law 25 for sensitive data."
        ),
    )
    consent_category_slug = models.CharField(
        max_length=80,
        blank=True,
        verbose_name=_("Consent category slug"),
        help_text=_(
            "Slug of the ConsentCategory to check. Default: 'appointment_data_processing'. "
            "Only relevant if requires_consent=True."
        ),
    )

    # ── Behaviour flags ──────────────────────────────────────────────────────

    requires_staff_confirmation = models.BooleanField(
        default=True,
        verbose_name=_("Requires staff confirmation"),
        help_text=_(
            "If True, new bookings start as PENDING and must be confirmed by staff "
            "before the citizen receives a confirmation email. "
            "If False, bookings are auto-confirmed immediately on submission."
        ),
    )
    allow_citizen_self_booking = models.BooleanField(
        default=True,
        verbose_name=_("Allow citizen self-booking"),
        help_text=_(
            "If False, only staff can create bookings (e.g., IRCC immigration interviews "
            "where eligibility is verified before scheduling)."
        ),
    )
    allow_walk_in = models.BooleanField(
        default=False,
        verbose_name=_("Allow walk-in"),
        help_text=_("If True, walk-in queue entries are accepted alongside advance bookings."),
    )
    non_punitive_no_show = models.BooleanField(
        default=False,
        verbose_name=_("Non-punitive no-show policy"),
        help_text=_(
            "If True, no-shows do not count against the citizen's ClientNoShowRecord "
            "and do not trigger suspension. Recommended for mental health, addictions, "
            "and other services where a punitive approach is clinically harmful."
        ),
    )
    allow_anonymous_booking = models.BooleanField(
        default=False,
        verbose_name=_("Allow anonymous booking"),
        help_text=_(
            "If True, citizens may book without creating a CivicOS account. "
            "Bookings are identified by email address only. "
            "NOT recommended for services collecting Protected B data — "
            "anonymous bookings cannot be linked to PIPEDA subject access requests."
        ),
    )

    # ── Interpreter / accommodation ──────────────────────────────────────────

    interpreter_required_option = models.CharField(
        max_length=20,
        choices=[
            ("none", _("Not applicable")),
            ("optional", _("Client may request")),
            ("required", _("Always required")),
        ],
        default="none",
        verbose_name=_("Interpreter option"),
        help_text=_(
            "Controls whether the booking flow asks about interpreter needs. "
            "Required for settlement services and some legal aid appointments."
        ),
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name=_("Active"),
    )
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name=_("Sort order"))

    # Link to Wagtail CMS page for public-facing description
    cms_page_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("CMS page ID"),
        help_text=_(
            "Optional: link to a Wagtail page describing this appointment type publicly. "
            "Not a FK to avoid a migration every time CMS pages change."
        ),
    )

    class Meta:
        ordering = ["sort_order", "name_en"]
        verbose_name = _("Appointment type")
        verbose_name_plural = _("Appointment types")
        indexes = [
            models.Index(fields=["service_type", "is_active"], name="appt_appttype_stype_active"),
            models.Index(fields=["mode", "is_active"], name="appt_appttype_mode_active"),
        ]

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        """Return name in the currently active language."""
        from django.utils.translation import get_language
        lang = get_language() or "en"
        return self.name_fr if lang.startswith("fr") else self.name_en

    def get_effective_policy(self) -> SchedulingPolicy | None:
        """
        Return this appointment type's own SchedulingPolicy, or None if unset.

        Callers are responsible for the two-level fallback:
          1. AppointmentType.get_effective_policy() — most specific
          2. Location.get_effective_policy()          — location default
          3. settings.CIVICOS["APPOINTMENTS"] defaults — global fallback
        This method only handles step 1.
        """
        return self.scheduling_policy


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class Location(TimestampedModel):
    """
    Physical office, clinic, or virtual meeting room pool.

    All Slot start/end datetimes are stored in UTC. The location timezone is
    used only for display and for the slot generation algorithm (converting
    staff availability templates from local time to UTC).

    Privacy regime field governs how data collected at this location is
    handled — different Canadian statutes apply depending on deployment context.
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="locations",
        verbose_name=_("Organization"),
    )
    name_en = models.CharField(max_length=200, verbose_name=_("Name (EN)"))
    name_fr = models.CharField(max_length=200, verbose_name=_("Name (FR)"))
    slug = models.SlugField(
        max_length=80,
        unique=True,
        verbose_name=_("Slug"),
        help_text=_("Machine-readable identifier. Used in URL routing."),
    )
    is_virtual = models.BooleanField(
        default=False,
        verbose_name=_("Virtual location"),
        help_text=_("If True, this location has no physical address — it is a virtual meeting room pool."),
    )

    # Physical address (blank for virtual locations)
    street_address = models.CharField(max_length=300, blank=True, verbose_name=_("Street address"))
    city = models.CharField(max_length=100, blank=True, verbose_name=_("City"))
    province = models.CharField(
        max_length=2,
        blank=True,
        verbose_name=_("Province / Territory"),
        help_text=_("ISO 3166-2:CA two-letter code (e.g. ON, QC, BC)."),
    )
    postal_code = models.CharField(max_length=7, blank=True, verbose_name=_("Postal code"))
    accessibility_features_en = models.TextField(
        blank=True,
        verbose_name=_("Accessibility features (EN)"),
        help_text=_("Describe physical accessibility: wheelchair access, elevator, TTY, parking. Shown to citizens."),
    )
    accessibility_features_fr = models.TextField(
        blank=True,
        verbose_name=_("Accessibility features (FR)"),
    )

    # Timezone — IANA identifier (e.g., "America/Toronto")
    timezone = models.CharField(
        max_length=64,
        default="America/Toronto",
        verbose_name=_("Timezone (IANA)"),
        help_text=_(
            "IANA timezone identifier for this location (e.g. 'America/Toronto', "
            "'America/Vancouver', 'America/Winnipeg'). "
            "All Slot datetimes are stored in UTC; this field is used for display "
            "and availability template conversion only. "
            "Canada spans 6 timezones — never assume Eastern time."
        ),
    )

    # Business hours JSON: [{"day_of_week": 1, "open": "09:00", "close": "17:00"}, ...]
    business_hours = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Business hours"),
        help_text=_(
            "JSON array of weekly business hours for display purposes. "
            "Format: [{\"day_of_week\": 1, \"open\": \"09:00\", \"close\": \"17:00\"}] "
            "where day_of_week follows ISO 8601 (1=Monday, 7=Sunday). "
            "This is for public display only — staff availability is configured "
            "via AvailabilityTemplate (Wave 2)."
        ),
    )

    scheduling_policy = models.ForeignKey(
        SchedulingPolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locations",
        verbose_name=_("Scheduling policy"),
        help_text=_(
            "Default policy for all appointment types at this location. "
            "Can be overridden per AppointmentType."
        ),
    )

    # Privacy regime governs how data at this location is treated
    privacy_regime = models.CharField(
        max_length=30,
        choices=[
            ("privacy_act", _("Federal Privacy Act")),
            ("pipeda", _("PIPEDA")),
            ("phipa", _("Ontario PHIPA")),
            ("law25", _("Quebec Law 25")),
            ("foippa_bc", _("BC FOIPPA")),
            ("fippa_on", _("Ontario FIPPA")),
            ("other", _("Other provincial statute")),
        ],
        default="pipeda",
        verbose_name=_("Privacy regime"),
        help_text=_(
            "Governing privacy law for this location. Affects consent requirements, "
            "retention periods, breach notification deadlines, and PIA obligations. "
            "Quebec Law 25 is the most stringent: requires mandatory PIA for booking "
            "systems and express consent for sensitive fields."
        ),
    )

    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Active"))

    # Contact
    phone_en = models.CharField(max_length=20, blank=True, verbose_name=_("Phone (EN)"))
    phone_fr = models.CharField(max_length=20, blank=True, verbose_name=_("Phone (FR)"))
    tty_phone = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("TTY/TDD phone"),
        help_text=_("Teletype number for Deaf and hard-of-hearing citizens."),
    )
    email = models.EmailField(blank=True, verbose_name=_("Contact email"))

    class Meta:
        ordering = ["name_en"]
        verbose_name = _("Location")
        verbose_name_plural = _("Locations")
        indexes = [
            models.Index(fields=["organization", "is_active"], name="appt_location_org_active"),
        ]

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        """Return name in the currently active language."""
        from django.utils.translation import get_language
        lang = get_language() or "en"
        return self.name_fr if lang.startswith("fr") else self.name_en

    def get_effective_policy(self) -> SchedulingPolicy | None:
        """Return the location-level scheduling policy (or None if unset)."""
        return self.scheduling_policy


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------

class Resource(TimestampedModel):
    """
    Physical or virtual resource that can be assigned to a Slot.

    Examples: meeting room, video conferencing room, TTY-equipped consultation
    room, phone line, piece of equipment.

    Resources are optional on Slots — they're used when physical rooms or
    specific equipment must be reserved alongside the staff member.

    The features JSON field uses controlled vocabulary tags to express resource
    capabilities that citizens can filter on (e.g. WHEELCHAIR_ACCESSIBLE).
    """

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="resources",
        verbose_name=_("Location"),
    )
    name_en = models.CharField(max_length=200, verbose_name=_("Name (EN)"))
    name_fr = models.CharField(max_length=200, verbose_name=_("Name (FR)"))
    resource_type = models.CharField(
        max_length=20,
        choices=[
            ("room", _("Meeting Room")),
            ("equipment", _("Equipment")),
            ("virtual", _("Virtual Meeting Room")),
            ("phone_line", _("Phone Line")),
            ("other", _("Other")),
        ],
        verbose_name=_("Resource type"),
    )
    capacity = models.PositiveIntegerField(
        default=1,
        verbose_name=_("Capacity"),
        help_text=_("Maximum number of people this resource can accommodate simultaneously."),
        validators=[MinValueValidator(1)],
    )
    features = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Features"),
        help_text=_(
            "List of feature tags describing this resource's capabilities. "
            "Controlled vocabulary: WHEELCHAIR_ACCESSIBLE, VIDEO_CONFERENCING, "
            "PROJECTOR, PRIVATE, INTERPRETER_PHONE, HEARING_LOOP, ADJUSTABLE_HEIGHT. "
            "Used by the slot availability service to filter resources for citizens "
            "with stated accessibility requirements."
        ),
    )

    # External calendar stub (future wave — calendar sync not in Wave 1)
    calendar_provider = models.CharField(
        max_length=20,
        choices=[
            ("none", _("None")),
            ("google", _("Google Calendar")),
            ("exchange", _("Microsoft Exchange / Outlook")),
        ],
        default="none",
        verbose_name=_("Calendar provider"),
        help_text=_("External calendar to sync this resource's availability. Wave 7+ only."),
    )
    external_calendar_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("External calendar ID"),
        help_text=_("Calendar ID in the external provider. Used only when calendar_provider is set."),
    )

    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Active"))

    class Meta:
        ordering = ["name_en"]
        verbose_name = _("Resource")
        verbose_name_plural = _("Resources")
        indexes = [
            models.Index(
                fields=["location", "resource_type", "is_active"],
                name="appt_resource_loc_type_active",
            ),
        ]

    def __str__(self) -> str:
        return self.name_en

    def get_name(self) -> str:
        """Return name in the currently active language."""
        from django.utils.translation import get_language
        lang = get_language() or "en"
        return self.name_fr if lang.startswith("fr") else self.name_en


# ---------------------------------------------------------------------------
# StaffProfile
# ---------------------------------------------------------------------------

class StaffProfile(TimestampedModel):
    """
    Scheduling-specific profile for staff members.

    Extends auth_extension.User (is_staff=True) without modifying the core
    User model. One StaffProfile per staff user.

    PIPEDA note: display_name fields are optional. The __str__ method returns
    only the user PK to prevent PII exposure in Django admin list views,
    Celery task logs, and audit log event_detail fields.
    Staff are identified by staff_profile.pk or user.pk in all logs.

    Video provider fields are stubs for Wave 7 (virtual appointments).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_profile",
        limit_choices_to={"is_staff": True},
        verbose_name=_("User account"),
        help_text=_(
            "The staff member's CivicOS user account. "
            "Must have is_staff=True. limit_choices_to is enforced at form level."
        ),
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff",
        verbose_name=_("Primary location"),
        help_text=_("The office or clinic where this staff member primarily works."),
    )
    appointment_types = models.ManyToManyField(
        AppointmentType,
        blank=True,
        related_name="staff_members",
        verbose_name=_("Appointment types"),
        help_text=_("Which appointment types this staff member is qualified to handle."),
    )

    # Display names — visible to citizens in the booking flow (optional)
    display_name_en = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Display name (EN)"),
        help_text=_(
            "Optional: public-facing name shown to citizens when selecting a staff member. "
            "Omit to hide staff identity (some services use anonymous assignment)."
        ),
    )
    display_name_fr = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Display name (FR)"),
    )

    # Scheduling controls
    max_daily_appointments = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Max daily appointments"),
        help_text=_(
            "Hard cap on appointments per calendar day for this staff member. "
            "Null = no cap (bounded only by availability template hours)."
        ),
    )
    accepts_walk_ins = models.BooleanField(
        default=False,
        verbose_name=_("Accepts walk-ins"),
        help_text=_("If True, this staff member appears in the walk-in queue assignment pool."),
    )
    is_accepting_bookings = models.BooleanField(
        default=True,
        verbose_name=_("Accepting bookings"),
        help_text=_(
            "Quick toggle to pause new bookings for this staff member without removing "
            "their availability templates. Useful during leave without advance notice."
        ),
    )

    # External calendar integration stubs (Wave 7)
    calendar_integration_provider = models.CharField(
        max_length=20,
        choices=[
            ("none", _("None")),
            ("google", _("Google Calendar")),
            ("exchange", _("Microsoft Exchange / Outlook")),
        ],
        default="none",
        verbose_name=_("Calendar provider"),
        help_text=_("External calendar for two-way availability sync. Wave 7+ only."),
    )
    external_calendar_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("External calendar ID"),
        help_text=_("Staff member's calendar ID in the external provider."),
    )

    # Video conference provider stubs (Wave 7)
    video_provider = models.CharField(
        max_length=20,
        choices=[
            ("none", _("None")),
            ("teams", _("Microsoft Teams")),
            ("zoom", _("Zoom")),
            ("jitsi", _("Jitsi Meet (Self-Hosted)")),
            ("phone", _("Phone Bridge")),
        ],
        default="none",
        verbose_name=_("Video provider"),
        help_text=_(
            "Video conference platform for virtual appointments. "
            "Requires corresponding CIVICOS['APPOINTMENTS'] credentials. Wave 7+ only."
        ),
    )
    video_external_user_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Video platform user ID"),
        help_text=_(
            "Staff member's user ID in the video platform "
            "(e.g. Teams UPN 'name@dept.gc.ca', Zoom userId UUID). "
            "Security: NEVER expose in templates or API responses — server-side only."
        ),
    )

    class Meta:
        ordering = ["user_id"]
        verbose_name = _("Staff profile")
        verbose_name_plural = _("Staff profiles")
        indexes = [
            models.Index(
                fields=["location", "is_accepting_bookings"],
                name="appt_staff_loc_accepting",
            ),
        ]

    def __str__(self) -> str:
        # PIPEDA: return PK only — no email, name, or any personal identifier.
        return f"StaffProfile #{self.pk} (user_id={self.user_id})"

    def get_display_name(self) -> str:
        """
        Return the public-facing display name for this staff member.

        Returns the language-appropriate display_name if set, otherwise an
        empty string (caller should show a generic label like "Staff" or
        hide the staff member identity per service policy).
        """
        from django.utils.translation import get_language
        lang = get_language() or "en"
        if lang.startswith("fr"):
            return self.display_name_fr or self.display_name_en
        return self.display_name_en or self.display_name_fr

    def clean(self) -> None:
        from django.core.exceptions import ValidationError
        super().clean()
        if self.user_id and not self.user.__class__.objects.filter(
            pk=self.user_id, is_staff=True
        ).exists():
            raise ValidationError(
                {"user": _("The selected user must have is_staff=True to be assigned as a staff profile.")}
            )

"""
Appointments / Scheduling Building Block — Models.

Wave 1 covers the scheduling catalogue and configuration layer:
  Organization → Location → Resource
  ServiceType → AppointmentType → SchedulingPolicy
  StaffProfile (extends auth_extension.User for staff)

Later waves add:
  Wave 2 (implemented): AvailabilityTemplate, StaffException, Slot
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

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimestampedModel


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

class Organization(TimestampedModel):
    """
    Represents a government department, NGO, or service-delivery org.

    .. migration-target::

        STRUCTURAL DEBT — tracked in CODEBASE_HANDOFF_FOR_AI.md §Known Technical Debt

        This model belongs in ``apps.core``, not ``apps.appointments``.
        The GovStack spec references ``core.Organization`` as the canonical
        cross-BB identity for any service-delivery organisation.

        Problem: ``Location.organization`` FK, ``StaffProfile → Location → Organization``
        chain, and any future BB that needs an org FK must currently reach into
        ``apps.appointments`` — violating app-layer boundaries.

        Migration path (execute before adding a second BB org FK):
          1. Add Organization to ``apps/core/models.py`` (keep same fields).
          2. Add a ``SeparateDatabaseAndState`` migration in ``apps/core`` to
             claim the existing ``appointments_organization`` table without
             recreating it.
          3. Add a ``SeparateDatabaseAndState`` migration in ``apps/appointments``
             to remove the model from its state without dropping the table.
          4. Update all imports and re-point FKs to ``core.Organization``.
          5. Update OrganizationAdmin registration to ``apps/core/admin.py``.

        Priority: HIGH before Wave 3 (when Booking model will add org-scoped
        queries and a second BB org FK becomes likely).

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
        """
        Return this location's own SchedulingPolicy, or None if unset.

        Callers are responsible for the three-level fallback chain:
          1. AppointmentType.get_effective_policy() — most specific
          2. Location.get_effective_policy()          — location default  (this method)
          3. settings.CIVICOS['APPOINTMENTS'] defaults — global fallback

        When this method returns None, callers must fall back to
        ``settings.CIVICOS['APPOINTMENTS']`` defaults. This method only handles step 2.
        """
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


# ---------------------------------------------------------------------------
# AvailabilityTemplate
# ---------------------------------------------------------------------------

class AvailabilityTemplateQuerySet(models.QuerySet):
    def active_on(self, date) -> "AvailabilityTemplateQuerySet":
        """
        Return templates whose date range covers `date`.

        A template is active on `date` if:
          - valid_from <= date
          - valid_until is NULL (open-ended) OR valid_until >= date
        """
        return self.filter(valid_from__lte=date).filter(
            models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=date)
        )


class AvailabilityTemplate(TimestampedModel):
    """
    Recurring weekly availability window for a staff member.

    Times are pure TimeField values in the staff's location timezone.
    Multiple rows per staff member — one per working day-of-week they are available.
    valid_from / valid_until allow seasonal schedule changes without deleting
    old templates (e.g., summer hours vs. winter hours).

    The slot generation algorithm reads these templates and converts them to UTC
    using staff.location.timezone (IANA identifier) before generating Slot records.

    day_of_week uses ISO 8601: 1=Monday, 7=Sunday — consistent with Python's
    date.isoweekday().
    """

    DAYS_OF_WEEK = [
        (1, _("Monday")),
        (2, _("Tuesday")),
        (3, _("Wednesday")),
        (4, _("Thursday")),
        (5, _("Friday")),
        (6, _("Saturday")),
        (7, _("Sunday")),
    ]

    objects = AvailabilityTemplateQuerySet.as_manager()

    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.PROTECT,  # PIPEDA 4.5.3: audit record — must decommission staff explicitly
        related_name="availability_templates",
        verbose_name=_("Staff profile"),
    )
    day_of_week = models.PositiveSmallIntegerField(
        choices=DAYS_OF_WEEK,
        verbose_name=_("Day of week"),
        help_text=_("ISO 8601 weekday: 1=Monday, 7=Sunday. Consistent with Python date.isoweekday()."),
    )
    start_time = models.TimeField(
        verbose_name=_("Start time"),
        help_text=_(
            "Start of availability window in the staff member's location timezone "
            "(see staff.location.timezone). The slot generation service converts this "
            "to UTC before creating Slot records."
        ),
    )
    end_time = models.TimeField(
        verbose_name=_("End time"),
        help_text=_(
            "End of availability window (exclusive) in the staff's location timezone. "
            "Must be strictly after start_time."
        ),
    )
    valid_from = models.DateField(
        verbose_name=_("Valid from"),
        help_text=_("First calendar date this template is active (inclusive)."),
    )
    valid_until = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Valid until"),
        help_text=_(
            "Last calendar date this template is active (inclusive). "
            "None = open-ended (no expiry). "
            "Must be on or after valid_from when set."
        ),
    )

    class Meta:
        ordering = ["day_of_week", "start_time"]
        verbose_name = _("Availability template")
        verbose_name_plural = _("Availability templates")
        indexes = [
            models.Index(
                fields=["staff", "day_of_week", "valid_from"],
                name="appt_avail_staff_dow_from",
            ),
            models.Index(fields=["created_at"], name="appt_availtpl_created_at_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="appt_avail_end_after_start",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_until__gte=models.F("valid_from"))
                ),
                name="appt_avail_until_gte_from",
            ),
            models.CheckConstraint(
                condition=models.Q(day_of_week__gte=1) & models.Q(day_of_week__lte=7),
                name="appt_avail_dow_1_to_7",
            ),
        ]

    def __str__(self) -> str:
        # PIPEDA: no PII — use staff PK only.
        return (
            f"StaffProfile #{self.staff_id} — "
            f"{self.get_day_of_week_display()} "
            f"{self.start_time}–{self.end_time}"
        )

    def clean(self) -> None:
        from django.core.exceptions import ValidationError
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError(
                {"end_time": _("End time must be after start time.")}
            )
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError(
                {"valid_until": _("Valid until must be on or after valid from.")}
            )

        # H-5: Prevent overlapping date ranges for the same (staff, day_of_week).
        # Two templates overlap if: self.valid_from <= other.valid_until AND
        # other.valid_from <= self.valid_until. None valid_until means open-ended.
        # Skip when staff is not yet assigned (unsaved object without a staff FK).
        if self.staff_id and self.valid_from:
            qs = AvailabilityTemplate.objects.filter(
                staff=self.staff_id,
                day_of_week=self.day_of_week,
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)

            for other in qs:
                other_end = other.valid_until  # None = open-ended (infinity)
                self_end = self.valid_until    # None = open-ended (infinity)

                # Overlap: Start A <= End B  AND  Start B <= End A (None = infinity)
                a_start_lte_b_end = (other_end is None) or (self.valid_from <= other_end)
                b_start_lte_a_end = (self_end is None) or (other.valid_from <= self_end)

                if a_start_lte_b_end and b_start_lte_a_end:
                    raise ValidationError(
                        {
                            "valid_from": _(
                                "This template's date range overlaps with an existing template "
                                "for the same staff member and day of week (%(other_from)s – %(other_until)s)."
                            ) % {
                                "other_from": other.valid_from,
                                "other_until": other.valid_until or _("open-ended"),
                            }
                        }
                    )


# ---------------------------------------------------------------------------
# StaffException
# ---------------------------------------------------------------------------

class StaffException(TimestampedModel):
    """
    Date-level override for a staff member's availability.

    Overrides the AvailabilityTemplate for a specific calendar date.

    exception_type semantics:
      holiday  — staff is unavailable (public holiday, scheduled day off).
                 No slots generated for this date.
      leave    — sick or personal leave, possibly last-minute.
                 No slots generated.
      override — custom hours for this date (partial day, different start/end).
                 Slots generated using override_start_time / override_end_time.
      training — staff is at training or conference.
                 No slots generated.

    override_start_time / override_end_time are only set for exception_type='override'.

    PRIVACY: internal_note is staff/admin-only and MUST NEVER be shown to citizens.
    """

    EXCEPTION_TYPE_CHOICES = [
        ("holiday", _("Public Holiday / Day Off")),
        ("leave", _("Sick / Personal Leave")),
        ("override", _("Override Hours")),
        ("training", _("Training / Conference")),
    ]

    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.PROTECT,  # PIPEDA 4.5.3: audit record — must decommission staff explicitly
        related_name="exceptions",
        verbose_name=_("Staff profile"),
    )
    exception_date = models.DateField(
        db_index=True,
        verbose_name=_("Exception date"),
        help_text=_("Calendar date this exception applies to."),
    )
    exception_type = models.CharField(
        max_length=20,
        choices=EXCEPTION_TYPE_CHOICES,
        verbose_name=_("Exception type"),
        help_text=_(
            "holiday/leave/training → no slots generated for this date. "
            "override → slots generated using override_start_time / override_end_time."
        ),
    )
    override_start_time = models.TimeField(
        null=True,
        blank=True,
        verbose_name=_("Override start time"),
        help_text=_(
            "Custom start time in the staff's location timezone. "
            "Only required when exception_type='override'."
        ),
    )
    override_end_time = models.TimeField(
        null=True,
        blank=True,
        verbose_name=_("Override end time"),
        help_text=_(
            "Custom end time in the staff's location timezone. "
            "Must be after override_start_time. "
            "Only required when exception_type='override'."
        ),
    )
    internal_note = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Internal note"),
        help_text=_(
            "Staff/admin-facing note about this exception. "
            "MUST NOT be shown to citizens or included in any citizen-facing communication."
        ),
    )

    class Meta:
        ordering = ["exception_date"]
        verbose_name = _("Staff exception")
        verbose_name_plural = _("Staff exceptions")
        indexes = [
            models.Index(fields=["created_at"], name="appt_staffexc_created_at_idx"),
        ]
        constraints = [
            # H-2: DB-level guarantee that override exceptions always have both times set.
            # Mirrors the Python-layer check in clean(). Prevents direct SQL from creating
            # a broken override record that would crash the slot generator on None access.
            models.CheckConstraint(
                condition=(
                    ~models.Q(exception_type="override")
                    | (
                        models.Q(override_start_time__isnull=False)
                        & models.Q(override_end_time__isnull=False)
                    )
                ),
                name="appt_staffexc_override_requires_times",
            ),
            models.UniqueConstraint(
                fields=["staff", "exception_date"],
                name="appt_staffexc_staff_date_uniq",
            ),
        ]

    def __str__(self) -> str:
        # PIPEDA: no PII — use staff PK only.
        return (
            f"StaffProfile #{self.staff_id} — "
            f"{self.exception_date} ({self.get_exception_type_display()})"
        )

    def clean(self) -> None:
        from django.core.exceptions import ValidationError
        super().clean()
        if self.exception_type == "override":
            if self.override_start_time is None or self.override_end_time is None:
                raise ValidationError(
                    {"override_start_time": _("Both override_start_time and override_end_time are required when exception_type is 'override'.")}
                )
            if self.override_end_time <= self.override_start_time:
                raise ValidationError(
                    {"override_end_time": _("Override end time must be after override start time.")}
                )


# ---------------------------------------------------------------------------
# Slot
# ---------------------------------------------------------------------------


class Slot(TimestampedModel):
    """
    Concrete bookable occurrence of an AppointmentType.

    Created ahead of time (pre-stored) by the generate_slots_for_range() service
    function or its Celery task. The SlotAvailabilityService can also compute
    virtual (non-persisted) slots for display before they are written to the DB.

    All datetime fields are stored in UTC. Display uses location.timezone (IANA).

    Status state machine:
      available → partial  (first booking created)
      partial   → full     (capacity reached)
      full      → partial  (booking cancelled, space freed)
      partial   → available (all bookings cancelled)
      any       → blocked  (admin action; prevents new bookings)
      any       → cancelled (admin action; cascades cancellation to all Bookings)
      any       → completed (end-of-day batch; slot is in the past)

    Concurrency: capacity checks use SELECT FOR UPDATE inside atomic() in the
    booking service layer (Wave 3). This model does not enforce atomicity —
    the service layer does.

    SECURITY: video_join_url_citizen MUST NEVER appear in unauthenticated email
    or any API response that is not behind an authenticated session. Deliver
    only inside an authenticated portal session at /appointments/booking/<uuid>/join/.

    spaces_used ≤ capacity is enforced by DB CheckConstraint.
    """

    SLOT_STATUS_CHOICES = [
        ("available", _("Available")),
        ("partial", _("Partially Booked")),
        ("full", _("Fully Booked")),
        ("blocked", _("Blocked")),
        ("cancelled", _("Cancelled")),
        ("completed", _("Completed")),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Slot ID"),
    )
    appointment_type = models.ForeignKey(
        AppointmentType,
        on_delete=models.PROTECT,
        related_name="slots",
        verbose_name=_("Appointment type"),
        help_text=_("The schedulable appointment variant this slot provides."),
    )
    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.PROTECT,
        related_name="slots",
        verbose_name=_("Staff member"),
        help_text=_("The staff member assigned to conduct this appointment."),
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="slots",
        verbose_name=_("Location"),
        help_text=_("Physical or virtual location where this appointment takes place."),
    )
    resource = models.ForeignKey(
        Resource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slots",
        verbose_name=_("Resource"),
        help_text=_(
            "Optional physical resource (meeting room, equipment) reserved for this slot. "
            "Used when the resource must be booked alongside the staff member."
        ),
    )

    # ── Datetime fields (all UTC) ─────────────────────────────────────────────

    start_datetime = models.DateTimeField(
        db_index=True,
        verbose_name=_("Start (UTC)"),
        help_text=_(
            "Appointment start time in UTC. "
            "Convert to location.timezone for citizen-facing display."
        ),
    )
    end_datetime = models.DateTimeField(
        db_index=True,
        verbose_name=_("End (UTC)"),
        help_text=_("Appointment end time in UTC. Must be after start_datetime."),
    )
    effective_start = models.DateTimeField(
        verbose_name=_("Effective start (UTC)"),
        help_text=_(
            "start_datetime minus buffer_before_minutes from the effective SchedulingPolicy. "
            "Used for busy-time collision detection — ensures back-to-back slots cannot overlap "
            "considering setup time."
        ),
    )
    effective_end = models.DateTimeField(
        verbose_name=_("Effective end (UTC)"),
        help_text=_(
            "end_datetime plus buffer_after_minutes. "
            "Used for busy-time collision detection — ensures wrap-up time is reserved."
        ),
    )

    # ── Capacity ─────────────────────────────────────────────────────────────

    capacity = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_("Capacity"),
        help_text=_(
            "Maximum number of clients that can be booked into this slot. "
            "Matches appointment_type.capacity_per_slot at generation time. "
            "May be reduced by admin for specific slots (e.g., staff illness mid-day)."
        ),
    )
    spaces_used = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_("Spaces used"),
        help_text=_(
            "Count of confirmed bookings occupying this slot. "
            "Incremented by create_booking(); decremented by cancel_booking(). "
            "Always ≤ capacity (enforced by DB CheckConstraint). "
            "Updated with SELECT FOR UPDATE inside atomic() in the booking service."
        ),
    )

    # ── Status ───────────────────────────────────────────────────────────────

    status = models.CharField(
        max_length=20,
        choices=SLOT_STATUS_CHOICES,
        default="available",
        db_index=True,
        verbose_name=_("Status"),
        help_text=_(
            "Slot availability status. Transitions: available→partial→full (as bookings are added); "
            "full/partial→available/partial (as bookings are cancelled); "
            "any→blocked (admin action); any→cancelled (admin action); "
            "any→completed (end-of-day batch for past slots)."
        ),
    )
    is_walk_in_slot = models.BooleanField(
        default=False,
        verbose_name=_("Walk-in slot"),
        help_text=_(
            "If True, this slot is reserved for walk-in clients and does not appear "
            "in the advance booking flow. Used when appointment_type.allow_walk_in=True."
        ),
    )

    # ── Virtual appointment (Wave 7) ─────────────────────────────────────────

    # SECURITY: video_join_url_citizen MUST NEVER be delivered in unauthenticated email.
    # Serve only inside authenticated portal session at /appointments/booking/<uuid>/join/.
    video_join_url_citizen = models.URLField(
        blank=True,
        verbose_name=_("Citizen video join URL"),
        help_text=_(
            "Pre-generated video conference join link for the citizen. "
            "SECURITY: NEVER include in unauthenticated email. "
            "Deliver only inside authenticated portal session. Wave 7+."
        ),
    )
    video_join_url_staff = models.URLField(
        blank=True,
        verbose_name=_("Staff video join URL"),
        help_text=_(
            "Pre-generated host/moderator link for the staff member. "
            "SECURITY: NEVER expose this field in citizen-facing API responses, "
            "email templates, or public serializers. This URL grants host/moderator "
            "access to the video session. Wave 7+."
        ),
    )
    video_meeting_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Video meeting ID"),
        help_text=_("Platform-specific meeting ID (e.g., Zoom meeting ID, Teams thread ID). Wave 7+."),
    )
    video_provider = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Video provider"),
        help_text=_("Video platform for this slot: 'teams', 'zoom', 'jitsi'. Wave 7+."),
    )

    internal_note = models.TextField(
        blank=True,
        verbose_name=_("Internal note"),
        help_text=_(
            "Staff/admin notes about this slot. "
            "MUST NOT be shown to citizens or included in citizen-facing communications."
        ),
    )

    class Meta:
        ordering = ["start_datetime"]
        verbose_name = _("Slot")
        verbose_name_plural = _("Slots")
        indexes = [
            models.Index(
                fields=["appointment_type", "start_datetime", "status"],
                name="appt_slot_appttype_dt_status",
            ),
            models.Index(
                fields=["staff", "start_datetime"],
                name="appt_slot_staff_dt",
            ),
            models.Index(
                fields=["location", "start_datetime", "status"],
                name="appt_slot_loc_dt_status",
            ),
            models.Index(
                fields=["effective_start", "effective_end"],
                name="appt_slot_eff_start_end",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_datetime__gt=models.F("start_datetime")),
                name="appt_slot_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(spaces_used__lte=models.F("capacity")),
                name="appt_slot_spaces_lte_capacity",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_start__lte=models.F("start_datetime")),
                name="appt_slot_eff_start_lte_start",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_end__gte=models.F("end_datetime")),
                name="appt_slot_eff_end_gte_end",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1),
                name="appt_slot_capacity_gte_1",
            ),
        ]
        permissions = [
            # H-6: Custom permission to view restricted video join URLs.
            # Without this permission, the video fieldset is hidden in the admin
            # and the URL is never serialized into citizen/staff-facing responses.
            ("view_slot_video_urls", "Can view slot video join URLs"),
        ]

    def __str__(self) -> str:
        # PIPEDA: use PK (UUID) and status only — no scheduling metadata in logs.
        return f"Slot {self.pk} ({self.status})"

    def clean(self) -> None:
        """Python-layer validation mirroring DB CheckConstraints for friendly admin errors."""
        super().clean()
        from django.core.exceptions import ValidationError
        errors = {}
        if (
            self.end_datetime is not None
            and self.start_datetime is not None
            and self.end_datetime <= self.start_datetime
        ):
            errors["end_datetime"] = _(
                "End datetime must be after start_datetime."
            )
        if self.capacity is not None and self.capacity < 1:
            errors["capacity"] = _("Capacity must be at least 1.")
        if (
            self.capacity is not None
            and self.spaces_used is not None
            and self.spaces_used > self.capacity
        ):
            errors["spaces_used"] = _(
                "Spaces used (%(used)d) cannot exceed capacity (%(cap)d)."
            ) % {"used": self.spaces_used, "cap": self.capacity}
        if (
            self.effective_start is not None
            and self.start_datetime is not None
            and self.effective_start > self.start_datetime
        ):
            errors["effective_start"] = _(
                "Effective start must be before or equal to start_datetime."
            )
        if (
            self.effective_end is not None
            and self.end_datetime is not None
            and self.effective_end < self.end_datetime
        ):
            errors["effective_end"] = _(
                "Effective end must be after or equal to end_datetime."
            )
        if errors:
            raise ValidationError(errors)

    @property
    def available_spaces(self) -> int:
        """Remaining bookable spaces in this slot."""
        return self.capacity - self.spaces_used

    @property
    def is_available(self) -> bool:
        """True if the slot can accept new bookings."""
        return self.status in ("available", "partial") and self.available_spaces > 0


# ===========================================================================
# Wave 3: Booking Engine
# ===========================================================================


class Booking(TimestampedModel):
    """
    Client reservation against a Slot. UUID primary key.

    Rescheduling creates a NEW Booking; old one is CANCELLED with rescheduled=True.
    Full audit history preserved via BookingAuditLog.

    PIPEDA invariants:
    - form_responses may contain Protected B data — permission-gated in admin.
    - No PII in __str__ (citizen referenced by pk only).
    - video_join_url_citizen NEVER in unauthenticated email.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    slot = models.ForeignKey(
        "Slot",
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name=_("Slot"),
    )
    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="appointment_bookings",
        limit_choices_to={"is_staff": False},
        verbose_name=_("Citizen"),
    )

    # Status state machine (§6.1)
    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending Staff Confirmation")),
        (STATUS_CONFIRMED, _("Confirmed")),
        (STATUS_REJECTED, _("Rejected")),
        (STATUS_CANCELLED, _("Cancelled")),
        (STATUS_COMPLETED, _("Completed")),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )

    # No-show: separate Boolean (confirmed + no_show=True is valid)
    no_show = models.BooleanField(
        default=False,
        help_text=_("Staff marks True if client did not attend. NOT a status value."),
        verbose_name=_("No-show"),
    )

    # Rescheduling chain
    rescheduled = models.BooleanField(
        default=False,
        verbose_name=_("Rescheduled"),
        help_text=_("True if this booking was replaced by a newer rescheduled booking."),
    )
    rescheduled_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rescheduled_to_set",
        verbose_name=_("Rescheduled from"),
        help_text=_("The original booking this was rescheduled from."),
    )
    reschedule_count = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_("Reschedule count"),
    )

    # Cancellation
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Cancelled at"))
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_bookings",
        verbose_name=_("Cancelled by"),
    )
    cancellation_reason = models.TextField(blank=True, verbose_name=_("Cancellation reason"))
    late_cancellation = models.BooleanField(
        default=False,
        help_text=_(
            "True if cancelled within the cancellation_notice_hours window. "
            "Used for no-show policy escalation."
        ),
        verbose_name=_("Late cancellation"),
    )

    # Booking channel
    CHANNEL_ONLINE = "online"
    CHANNEL_PHONE = "phone"
    CHANNEL_WALK_IN = "walk_in"
    CHANNEL_STAFF_PORTAL = "staff_portal"
    CHANNEL_API = "api"
    CHANNEL_CHOICES = [
        (CHANNEL_ONLINE, _("Online Self-Service")),
        (CHANNEL_PHONE, _("Phone (Staff-Assisted)")),
        (CHANNEL_WALK_IN, _("Walk-In")),
        (CHANNEL_STAFF_PORTAL, _("Staff Portal")),
        (CHANNEL_API, _("API")),
    ]
    booking_channel = models.CharField(
        max_length=20,
        choices=CHANNEL_CHOICES,
        default=CHANNEL_ONLINE,
        verbose_name=_("Booking channel"),
    )
    language = models.CharField(
        max_length=5,
        choices=[("en", _("English")), ("fr", _("French"))],
        default="en",
        verbose_name=_("Language"),
    )
    appointment_mode = models.CharField(
        max_length=20,
        choices=[
            ("in_person", _("In-Person")),
            ("virtual", _("Virtual (Video)")),
            ("phone", _("Phone")),
        ],
        default="in_person",
        verbose_name=_("Appointment mode"),
    )

    # Intake form answers
    # PIPEDA: may contain Protected B data — permission-gated in admin + views
    form_responses = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Form responses"),
        help_text=_("Intake form answers. May contain Protected B data — access gated by permission."),
    )

    # Interpreter
    interpreter_needed = models.BooleanField(default=False, verbose_name=_("Interpreter needed"))
    interpreter_language = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Interpreter language"),
        help_text=_("Quebec Law 25: sensitive personal information — express consent required."),
    )

    # Accessibility
    accessibility_needs = models.TextField(
        blank=True,
        verbose_name=_("Accessibility needs"),
        help_text=_("Quebec Law 25: sensitive personal information — express consent required."),
    )

    # Staff notes (NEVER shown to citizen)
    internal_notes = models.TextField(
        blank=True,
        verbose_name=_("Internal notes"),
        help_text=_("Staff/admin notes. MUST NOT be shown to citizens."),
    )

    # Confirmation / reminder tracking
    confirmation_sent_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Confirmation sent at"))
    reminder_72h_sent = models.BooleanField(default=False, verbose_name=_("72h reminder sent"))
    reminder_24h_sent = models.BooleanField(default=False, verbose_name=_("24h reminder sent"))
    reminder_2h_sent = models.BooleanField(default=False, verbose_name=_("2h reminder sent"))

    # Celery task IDs for revocation
    reminder_72h_task_id = models.CharField(max_length=100, blank=True, verbose_name=_("72h reminder task ID"))
    reminder_24h_task_id = models.CharField(max_length=100, blank=True, verbose_name=_("24h reminder task ID"))
    reminder_2h_task_id = models.CharField(max_length=100, blank=True, verbose_name=_("2h reminder task ID"))

    # Plain UUID references (no FK — avoids circular import)
    work_item_id = models.UUIDField(
        null=True,
        blank=True,
        verbose_name=_("Work item ID"),
        help_text=_("UUID of the related WorkItem (apps.workflows). Plain UUID, not FK."),
    )
    service_request_id = models.UUIDField(
        null=True,
        blank=True,
        verbose_name=_("Service request ID"),
    )

    # Consent
    consent_recorded_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Consent recorded at"))
    consent_version = models.CharField(max_length=20, blank=True, verbose_name=_("Consent version"))

    # Document attachments (GenericRelation — no schema change to documents app)
    document_attachments = GenericRelation(
        "documents.DocumentAttachment",
        related_query_name="booking",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Booking")
        verbose_name_plural = _("Bookings")
        indexes = [
            models.Index(fields=["citizen", "status"], name="appt_booking_citizen_status"),
            models.Index(fields=["slot", "status"], name="appt_booking_slot_status"),
            models.Index(fields=["status", "no_show"], name="appt_booking_status_noshow"),
            models.Index(fields=["citizen", "created_at"], name="appt_booking_citizen_created"),
        ]
        permissions = [
            ("view_booking_form_responses", "Can view booking intake form responses (Protected B gate)"),
            ("manage_no_shows", "Can manage citizen no-show records"),
            ("view_internal_notes", "Can view staff-only internal booking notes"),
        ]

    def __str__(self) -> str:
        # PIPEDA: citizen referenced by pk only — no name/email
        return f"Booking {self.pk} (citizen_id={self.citizen_id}, {self.status})"

    @property
    def is_active(self) -> bool:
        """True if the booking can still be managed (not in a terminal state)."""
        return self.status in (self.STATUS_PENDING, self.STATUS_CONFIRMED)


class Attendee(TimestampedModel):
    """
    Additional attendees beyond the primary citizen.
    PIPEDA: Contains PII (name, email, phone) — restricted access.
    """

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="attendees",
        verbose_name=_("Booking"),
    )
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    email = models.EmailField(blank=True, verbose_name=_("Email"))
    phone = models.CharField(max_length=20, blank=True, verbose_name=_("Phone"))
    role = models.CharField(
        max_length=20,
        choices=[
            ("family_member", _("Family Member")),
            ("legal_guardian", _("Legal Guardian")),
            ("support_person", _("Support Person")),
            ("interpreter", _("Interpreter")),
            ("advocate", _("Advocate / Representative")),
            ("other", _("Other")),
        ],
        default="family_member",
        verbose_name=_("Role"),
    )
    no_show = models.BooleanField(default=False, verbose_name=_("No-show"))
    timezone = models.CharField(max_length=64, blank=True, verbose_name=_("Timezone"))
    preferred_language = models.CharField(max_length=5, blank=True, verbose_name=_("Preferred language"))

    class Meta:
        ordering = ["name"]
        verbose_name = _("Attendee")
        verbose_name_plural = _("Attendees")

    def __str__(self) -> str:
        return f"Attendee {self.pk} (role={self.role}, booking={self.booking_id})"


class BookingAuditLog(models.Model):
    """
    Immutable append-only audit trail for every booking lifecycle event.

    Uses models.Model (not TimestampedModel) because:
    - No updated_at (immutable records never update)
    - Uses 'timestamp' field (not created_at) per audit convention

    Security invariants:
    - save() raises ValueError if PK already exists (immutability guard)
    - delete() always raises ValueError
    - 'detail' MUST NOT contain PII (slugs and UUIDs only)
    - 'actor_id' is CharField (not FK) — PII safety
    """

    ACTION_CREATED = "created"
    ACTION_CONFIRMED = "confirmed"
    ACTION_REJECTED = "rejected"
    ACTION_CANCELLED_CITIZEN = "cancelled_citizen"
    ACTION_CANCELLED_STAFF = "cancelled_staff"
    ACTION_CANCELLED_SYSTEM = "cancelled_system"
    ACTION_RESCHEDULED = "rescheduled"
    ACTION_COMPLETED = "completed"
    ACTION_NO_SHOW_MARKED = "no_show_marked"
    ACTION_REMINDER_SENT = "reminder_sent"
    ACTION_WAITLIST_JOINED = "waitlist_joined"
    ACTION_WAITLIST_PROMOTED = "waitlist_promoted"
    ACTION_DOCUMENT_ATTACHED = "document_attached"
    ACTION_PAYMENT_RECEIVED = "payment_received"
    ACTION_CONSENT_RECORDED = "consent_recorded"
    ACTION_DATA_PURGED = "data_purged"

    ACTION_CHOICES = [
        (ACTION_CREATED, _("Booking Created")),
        (ACTION_CONFIRMED, _("Confirmed")),
        (ACTION_REJECTED, _("Rejected")),
        (ACTION_CANCELLED_CITIZEN, _("Cancelled by Citizen")),
        (ACTION_CANCELLED_STAFF, _("Cancelled by Staff")),
        (ACTION_CANCELLED_SYSTEM, _("Cancelled by System")),
        (ACTION_RESCHEDULED, _("Rescheduled")),
        (ACTION_COMPLETED, _("Completed")),
        (ACTION_NO_SHOW_MARKED, _("No Show Marked")),
        (ACTION_REMINDER_SENT, _("Reminder Sent")),
        (ACTION_WAITLIST_JOINED, _("Joined Waitlist")),
        (ACTION_WAITLIST_PROMOTED, _("Promoted from Waitlist")),
        (ACTION_DOCUMENT_ATTACHED, _("Document Attached")),
        (ACTION_PAYMENT_RECEIVED, _("Payment Received")),
        (ACTION_CONSENT_RECORDED, _("Consent Recorded")),
        (ACTION_DATA_PURGED, _("Data Purged")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="audit_log",
        verbose_name=_("Booking"),
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Timestamp"))
    action = models.CharField(max_length=30, choices=ACTION_CHOICES, verbose_name=_("Action"))
    # CharField (not FK) — decouples from User model; stores str(user.pk) or "system"
    actor_id = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Actor ID"),
        help_text=_("String pk of the acting user, or 'system'. NOT a FK (PII safety)."),
    )
    actor_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("Actor IP"),
    )
    previous_status = models.CharField(max_length=20, blank=True, verbose_name=_("Previous status"))
    new_status = models.CharField(max_length=20, blank=True, verbose_name=_("New status"))
    detail = models.JSONField(
        default=dict,
        verbose_name=_("Detail"),
        help_text=_("Additional context. MUST NOT contain PII — slugs and UUIDs only."),
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = _("Booking Audit Log Entry")
        verbose_name_plural = _("Booking Audit Log Entries")
        indexes = [
            models.Index(fields=["booking", "timestamp"], name="appt_bookingaudit_booking_ts"),
        ]

    def __str__(self) -> str:
        return f"BookingAuditLog {self.pk} ({self.action}, booking={self.booking_id})"

    def save(self, *args, **kwargs) -> None:
        """Immutability guard — audit records are append-only."""
        if self.pk and BookingAuditLog.objects.filter(pk=self.pk).exists():
            raise ValueError(
                f"BookingAuditLog {self.pk} is immutable. Audit records cannot be modified."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        """Audit records can never be deleted — ATIA retention requirement."""
        raise ValueError(
            f"BookingAuditLog {self.pk} cannot be deleted. "
            "Audit records are retained for 7 years per ATIA requirements."
        )


# ===========================================================================
# Wave 4 Structural Models (schema created in Wave 3 migration for FK consistency)
# ===========================================================================

# Priority ordering map (for query annotation — alphabetical doesn't match priority)
_WAITLIST_PRIORITY_ORDER = {
    "emergency": 1,
    "bumped": 2,
    "high_need": 3,
    "recurring": 4,
    "standard": 5,
}


class WaitlistEntry(TimestampedModel):
    """
    Per-slot waitlist. Citizens join when a slot is full.

    Note on ordering: Meta.ordering uses ['position'] for stability.
    Service-layer queries MUST use annotated integer priority ordering
    (see PRIORITY_ORDER) for correct priority sequencing in Wave 4.
    """

    PRIORITY_ORDER = _WAITLIST_PRIORITY_ORDER

    slot = models.ForeignKey(
        Slot,
        on_delete=models.CASCADE,
        related_name="waitlist",
        verbose_name=_("Slot"),
    )
    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
        verbose_name=_("Citizen"),
    )
    priority_class = models.CharField(
        max_length=20,
        choices=[
            ("emergency", _("Emergency / Crisis")),
            ("bumped", _("Bumped by Provider")),
            ("high_need", _("High Need / Vulnerable")),
            ("recurring", _("Recurring Regular")),
            ("standard", _("Standard")),
        ],
        default="standard",
        verbose_name=_("Priority class"),
    )
    position = models.PositiveIntegerField(db_index=True, verbose_name=_("Position"))
    status = models.CharField(
        max_length=20,
        choices=[
            ("waiting", _("Waiting")),
            ("notified", _("Notified — Awaiting Response")),
            ("accepted", _("Accepted — Booking Created")),
            ("expired", _("Notification Expired")),
            ("withdrawn", _("Withdrawn by Client")),
        ],
        default="waiting",
        verbose_name=_("Status"),
    )
    notification_sent_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Notification sent at"))
    acceptance_deadline = models.DateTimeField(null=True, blank=True, verbose_name=_("Acceptance deadline"))
    notification_channel = models.CharField(
        max_length=10,
        choices=[
            ("email", _("Email")),
            ("sms", _("SMS")),
            ("phone", _("Phone Call")),
        ],
        default="email",
        verbose_name=_("Notification channel"),
    )
    joined_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Joined at"))

    class Meta:
        ordering = ["position"]
        verbose_name = _("Waitlist Entry")
        verbose_name_plural = _("Waitlist Entries")
        constraints = [
            models.UniqueConstraint(
                fields=["slot", "citizen"],
                name="appt_waitlist_slot_citizen_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["slot", "status", "position"], name="appt_waitlist_slot_status_pos"),
            models.Index(fields=["citizen", "status"], name="appt_waitlist_citizen_status"),
        ]

    def __str__(self) -> str:
        return (
            f"WaitlistEntry {self.pk} "
            f"(slot={self.slot_id}, citizen_id={self.citizen_id}, pos={self.position})"
        )


class QueueEntry(TimestampedModel):
    """
    Real-time queue for walk-in + booked clients on the day of service.
    One entry per citizen per service day; linked to a Booking (optional for walk-ins).
    """

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="queue_entries",
        verbose_name=_("Location"),
    )
    queue_date = models.DateField(db_index=True, verbose_name=_("Queue date"))
    queue_number = models.CharField(max_length=10, db_index=True, verbose_name=_("Queue number"))
    booking = models.OneToOneField(
        Booking,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_entry",
        verbose_name=_("Booking"),
    )
    citizen_display_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Citizen display name"),
        help_text=_("Display name for queue board. Not stored for anonymous bookings."),
    )
    queue_type = models.CharField(
        max_length=20,
        choices=[
            ("emergency", _("Emergency")),
            ("booked", _("Pre-Booked")),
            ("walk_in", _("Walk-In")),
        ],
        default="booked",
        verbose_name=_("Queue type"),
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ("waiting", _("Waiting")),
            ("called", _("Called")),
            ("in_service", _("In Service")),
            ("completed", _("Completed")),
            ("no_show", _("No Show")),
            ("cancelled", _("Left / Cancelled")),
        ],
        default="waiting",
        db_index=True,
        verbose_name=_("Status"),
    )
    assigned_staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_assignments",
        verbose_name=_("Assigned staff"),
    )
    called_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Called at"))
    service_started_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Service started at"))
    service_completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Service completed at"))
    wait_time_minutes = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("Wait time (minutes)"))

    class Meta:
        ordering = ["queue_type", "created_at"]
        verbose_name = _("Queue Entry")
        verbose_name_plural = _("Queue Entries")
        constraints = [
            models.UniqueConstraint(
                fields=["location", "queue_date", "queue_number"],
                name="appt_queue_location_date_number_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["location", "queue_date", "status"],
                name="appt_queue_loc_date_status",
            ),
        ]

    def __str__(self) -> str:
        return f"QueueEntry #{self.queue_number} ({self.status}, {self.queue_date})"


class ClientNoShowRecord(TimestampedModel):
    """
    Aggregate no-show statistics per citizen. One record per citizen.
    Used for policy escalation (warning → suspension).

    Access-gated behind appointments.manage_no_shows permission.
    PIPEDA: is_suspended is a behavioural profile — minimal retention (2 years).
    """

    citizen = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="no_show_record",
        limit_choices_to={"is_staff": False},
        verbose_name=_("Citizen"),
    )
    no_show_count = models.PositiveIntegerField(default=0, verbose_name=_("No-show count"))
    late_cancellation_count = models.PositiveIntegerField(default=0, verbose_name=_("Late cancellation count"))
    total_appointments = models.PositiveIntegerField(default=0, verbose_name=_("Total appointments"))
    last_no_show_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Last no-show at"))

    # Escalation flags
    is_flagged = models.BooleanField(default=False, verbose_name=_("Is flagged"))
    flagged_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Flagged at"))
    flagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="flagged_no_show_records",
        verbose_name=_("Flagged by"),
    )
    is_suspended = models.BooleanField(
        default=False,
        verbose_name=_("Is suspended"),
        help_text=_("If True, citizen cannot self-book until staff reviews and clears."),
    )
    suspension_note = models.TextField(blank=True, verbose_name=_("Suspension note"))

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = _("Client No-Show Record")
        verbose_name_plural = _("Client No-Show Records")
        indexes = [
            models.Index(fields=["is_flagged"], name="appt_noshowrec_flagged"),
            models.Index(fields=["is_suspended"], name="appt_noshowrec_suspended"),
        ]

    def __str__(self) -> str:
        return f"ClientNoShowRecord (citizen_id={self.citizen_id}, no_shows={self.no_show_count})"

    @property
    def no_show_rate(self) -> float:
        """Percentage of appointments that resulted in a no-show."""
        if self.total_appointments == 0:
            return 0.0
        return round(self.no_show_count / self.total_appointments * 100, 1)

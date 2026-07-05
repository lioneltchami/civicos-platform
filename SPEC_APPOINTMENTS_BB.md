# Appointments / Scheduling Building Block — Specification

**Version:** 1.0.0  
**Date:** 2026-07-05  
**Status:** Approved for Implementation  
**Author:** CivicOS Architecture Team  
**Predecessor BBs:** Portal, Workflows, Notifications, Volunteers, Documents, Payments, Consent  

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Who Needs This Building Block](#2-who-needs-this-building-block)
3. [Standards Alignment](#3-standards-alignment)
4. [Conceptual Model](#4-conceptual-model)
5. [Domain Model — Data Structures](#5-domain-model--data-structures)
6. [State Machines](#6-state-machines)
7. [Slot Availability Algorithm](#7-slot-availability-algorithm)
8. [Service Layer Architecture](#8-service-layer-architecture)
9. [Integration with Existing Building Blocks](#9-integration-with-existing-building-blocks)
10. [Notification Architecture](#10-notification-architecture)
11. [Virtual Appointment Architecture](#11-virtual-appointment-architecture)
12. [Celery Tasks and Beat Schedule](#12-celery-tasks-and-beat-schedule)
13. [Views and URL Structure](#13-views-and-url-structure)
14. [Template Architecture](#14-template-architecture)
15. [Admin Interface](#15-admin-interface)
16. [REST API (DRF)](#16-rest-api-drf)
17. [Accessibility — WCAG 2.1 AA](#17-accessibility--wcag-21-aa)
18. [Privacy and Compliance](#18-privacy-and-compliance)
19. [Security Constraints](#19-security-constraints)
20. [Configuration (CIVICOS settings)](#20-configuration-civicos-settings)
21. [Migrations](#21-migrations)
22. [Test Requirements](#22-test-requirements)
23. [File Structure](#23-file-structure)
24. [Implementation Waves](#24-implementation-waves)

---

## 1. Purpose and Scope

The Appointments / Scheduling Building Block enables citizens and clients to book time-slots with staff for government services and NGO programs — either in-person at a physical location or virtually via video call. It integrates natively with the WorkItem queue, Notifications, Documents, Payments, and Consent BBs.

### 1.1 Core Capabilities

- **Service type catalogue** — configurable appointment categories with duration, capacity, and booking policies
- **Staff availability management** — weekly schedule templates with date-level exception overrides
- **Slot generation** — on-demand computation of available time slots considering availability, existing bookings, buffers, and booking limits
- **Citizen self-booking** — online booking flow with confirmation, rescheduling, and cancellation
- **Staff-assisted booking** — staff create/manage bookings on behalf of clients (phone channel, walk-in)
- **Waitlist management** — priority-ordered waitlist with automated slot promotion
- **Multi-modal appointments** — in-person, virtual (video), and phone appointment types
- **Automated reminders** — email (with iCal attachment) + SMS at T-72h, T-24h, T-2h
- **No-show tracking** — per-appointment recording with configurable escalation policies
- **WorkItem integration** — every appointment creates or links to a workflow WorkItem
- **Walk-in queue** — real-time queue management for drop-in services alongside booked appointments
- **Audit trail** — immutable log of every lifecycle event (PIPEDA 4.5.3, Privacy Act s. 10–11)
- **Document gating** — optional: require document upload before slot is confirmed
- **Fee gating** — optional: require payment before slot is confirmed
- **Consent gating** — PIPEDA-compliant consent collection at booking time

### 1.2 Out of Scope (This Version)

- Full iCal/CalDAV server (calendar sync is export-only — `.ics` file attachments in emails)
- Native Google Calendar or Outlook two-way sync (stubs only; future wave)
- Video conference platform hosting (link generation only; platform is external: Teams, Jitsi, Zoom)
- Complex multi-resource scheduling (e.g., room + interpreter simultaneously) — modelled but not UI-exposed in Wave 1
- Recurring appointment series (e.g., weekly check-ins) — modelled with `recurrence_rule` JSON but generation deferred
- SMS two-way confirmation (reply CONFIRM/CANCEL) — one-way SMS only in Wave 1

---

## 2. Who Needs This Building Block

### 2.1 Government Use Cases

| Service | Appointment Type | Mode | Key Constraints |
|---------|-----------------|------|-----------------|
| Passport / SIN / EI applications | In-person intake | In-person | Protected-B data; 30-min default slot |
| IRCC biometrics collection | Biometrics | In-person | Requires Biometrics Instruction Letter number |
| Immigration interviews (IRCC IFS) | Officer interview | Virtual (controlled) | Identity verified by third party; no join link emailed |
| ServiceOntario health card renewal | Counter service | In-person or Virtual | 18+ for virtual |
| Municipal licensing / permits | Counter service | In-person | Multiple services per appointment |
| Housing assessment (social services) | Needs assessment | In-person or Phone | By-Name-List priority integration |
| Employment assistance (OW) | Caseworker meeting | In-person or Phone | Mandatory attendance; non-compliance flagged |
| Social assistance / income support | Financial review | In-person | Protected-B; eligibility verification |
| Public health clinic | Medical consult | In-person | Health card required; uninsured accepted |
| Bylaw / complaint investigation | Inspector visit | In-person (location-out) | Staff travels to citizen; reverse booking |

### 2.2 NGO Use Cases

| Sector | Use Case | Key Constraints |
|--------|----------|-----------------|
| Food banks | Distribution / intake assessment | Frequency controls; proxy attendance; dignity-first |
| Legal aid clinics | Lawyer consultation | Eligibility screening before slot; conflict-of-interest checks |
| Settlement services (IRCC-funded) | NAARS intake; case management sessions | UCI/COPR fields; Francophone pathway routing |
| Mental health / addictions | Intake; counselling sessions | 30–40% no-show rate; risk flag escalation; non-punitive policy |
| Community health centres (CHC) | Primary care; nurse practitioner | Uninsured accepted; OHIP/non-OHIP paths |
| Housing / shelter | Coordinated access assessment | By-Name-List integration; VI-SPDAT score fields |
| Employment / skills training | Job coaching; resume help | OW referral integration; 3-contact-attempt protocol |
| Youth / child services | Intake; supervision visits | Third-party consent (guardian); worker accompaniment |

### 2.3 Decision: Government or NGO?

**Both.** Appointment booking is a universal need across the entire CivicOS target market. The same building block serves both, with configuration flags (`is_government_service`, `sector`, `requires_eligibility_screening`) distinguishing behaviour. Privacy obligations differ (Privacy Act vs. PIPEDA vs. Quebec Law 25) and are handled by `privacy_regime` field on the Location model.

---

## 3. Standards Alignment

### 3.1 GovStack Scheduler Building Block

This implementation follows the **GovStack Scheduler BB v1.0.1** (Apache 2.0), co-authored by ITU, UN DESA, UNDP, GIZ, and Estonia. The GovStack spec defines 9 API resource groups: `event`, `appointment`, `entity`, `resource`, `subscribers`, `message`, `alert_schedule`, `affiliation`, and `log`.

Our Django implementation maps GovStack concepts as:

| GovStack Term | CivicOS Model |
|---------------|--------------|
| Entity | `Organization` (existing) / `Location` |
| Resource | `Staff` (human) / `Resource` (physical) |
| EventList | `AppointmentType` (template) + `Slot` (occurrence) |
| AppointmentList | `Booking` |
| AffiliationList | `AvailabilityTemplate` |
| FreeResources | computed by `SlotAvailabilityService` |
| AlertScheduleList | `ReminderSchedule` (Celery eta tasks) |
| LogList | `BookingAuditLog` |

### 3.2 TMF646 Appointment API

The GovStack Scheduler BB is based on **TM Forum TMF646 Appointment API R19.0.0**. Status values from TMF646 map to our `Booking.status` state machine.

### 3.3 iCalendar (RFC 5545)

All appointment confirmation emails include a `text/calendar` MIME attachment (`.ics`) conforming to RFC 5545:
- `UID`: `appt-{uuid}@civicos.ca` — persisted forever; same across all updates
- `SEQUENCE`: starts at 0; incremented on every modification
- `STATUS:CANCELLED` + `METHOD:CANCEL` + SEQUENCE increment on cancellation
- `DTSTART`/`DTEND` always include `TZID=` IANA timezone parameter

### 3.4 CPSV-AP v3.2.0

For public service directory integration, appointment channels are described using **CPSV-AP v3.2.0** vocabulary:
- `cv:Channel` with `dct:type` (in-person/online/phone)
- `cv:openingHours` (schema.org OpeningHoursSpecification)
- `cv:processingTime` (ISO 8601 Duration)

### 3.5 Privacy Law Applicability

| Deployment Context | Governing Law | Key Obligations |
|--------------------|---------------|-----------------|
| Federal government | Privacy Act (RSC 1985, c. P-21) | PIA, PIB, Section 5 notice, TBS retention schedules |
| Provincial government (QC) | Quebec Law 25 (Act 25) | PIA mandatory, express consent for sensitive data, 72h breach notification to CAI |
| Provincial government (other) | Applicable provincial statute | Varies; Freedom of Information acts |
| Federally-regulated NGO / private sector | PIPEDA (until C-36 enacted) | 10 Fair Information Principles; breach notification if RRSH |
| Health-sector NGO (ON) | PHIPA | Custodian obligations; consent for collection/use/disclosure |
| All Quebec-based orgs | Quebec Law 25 | Overlays all others |

---

## 4. Conceptual Model

```
Organization (top-level tenant boundary)
  └── Location (physical office, CHC, virtual room pool)
       ├── SchedulingPolicy (min lead time, cancellation rules, waitlist config)
       ├── Resource (room, equipment, virtual meeting room)
       └── StaffProfile (FK→User, is_staff=True)
            └── AvailabilityTemplate (weekly schedule, IANA timezone)
                 └── StaffException (date overrides: holiday, leave, custom hours)

ServiceType (service catalogue — what can be booked)
  └── AppointmentType (schedulable variant: duration, capacity, mode, policy)
       └── Slot (concrete occurrence: datetime + staff + resource + capacity)
            ├── Booking (client reservation — state machine)
            │    ├── Attendee[] (each: no_show Bool, timezone, language pref)
            │    ├── BookingFormResponse (intake form answers)
            │    ├── DocumentAttachment[] (via GenericRelation)
            │    └── BookingAuditLog (immutable; every state change)
            └── WaitlistEntry[] (per slot; priority + FIFO tie-break)

QueueEntry (walk-in / real-time queue, separate from advance bookings)
ClientNoShowRecord (aggregate per citizen)
ReminderTask (Celery task registry for scheduled reminders)
```

---

## 5. Domain Model — Data Structures

### 5.1 ServiceType

```python
class ServiceType(TimeStampedModel):
    """Catalogue of bookable services. Each type has schedulable AppointmentTypes."""
    slug = models.SlugField(max_length=80, unique=True)
    name_en = models.CharField(max_length=200)
    name_fr = models.CharField(max_length=200)
    description_en = models.TextField(blank=True)
    description_fr = models.TextField(blank=True)
    category = models.CharField(
        max_length=40,
        choices=[
            ("government", "Government Service"),
            ("health", "Health Service"),
            ("legal", "Legal Aid"),
            ("employment", "Employment Service"),
            ("housing", "Housing Service"),
            ("settlement", "Settlement Service"),
            ("food", "Food Security"),
            ("mental_health", "Mental Health & Addictions"),
            ("other", "Other"),
        ],
    )
    sector = models.CharField(
        max_length=20,
        choices=[("government", "Government"), ("ngo", "NGO"), ("both", "Both")],
        default="both",
    )
    # Privacy / compliance
    privacy_sensitivity = models.CharField(
        max_length=20,
        choices=[
            ("standard", "Standard (unclassified)"),
            ("protected_a", "Protected A"),
            ("protected_b", "Protected B"),
        ],
        default="standard",
    )
    requires_eligibility_screening = models.BooleanField(default=False)
    eligibility_description_en = models.TextField(blank=True)
    eligibility_description_fr = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name_en"]
        indexes = [models.Index(fields=["category", "is_active"])]
```

### 5.2 AppointmentType

```python
class AppointmentType(TimeStampedModel):
    """Schedulable variant of a ServiceType (e.g., '30-min in-person EI intake')."""
    service_type = models.ForeignKey(
        ServiceType, on_delete=models.PROTECT, related_name="appointment_types"
    )
    slug = models.SlugField(max_length=80, unique=True)
    name_en = models.CharField(max_length=200)
    name_fr = models.CharField(max_length=200)
    description_en = models.TextField(blank=True)
    description_fr = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField(
        default=30,
        help_text="Length of the appointment itself (not including buffers).",
    )
    mode = models.CharField(
        max_length=20,
        choices=[
            ("in_person", "In-Person"),
            ("virtual", "Virtual (Video)"),
            ("phone", "Phone"),
            ("hybrid", "Hybrid (Client Choice)"),
        ],
        default="in_person",
    )
    # Capacity — how many clients per slot (1 = one-to-one; >1 = group)
    capacity_per_slot = models.PositiveSmallIntegerField(default=1)
    # Scheduling policy (overrides Location policy if set)
    scheduling_policy = models.ForeignKey(
        "SchedulingPolicy",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointment_types",
    )
    # Intake form (optional JSON schema for pre-booking questions)
    intake_form_schema = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON Schema for pre-booking intake form questions.",
    )
    # Requirements before slot confirmation
    requires_document_upload = models.BooleanField(default=False)
    required_document_category_slug = models.CharField(max_length=80, blank=True)
    requires_payment = models.BooleanField(default=False)
    fee_code = models.CharField(max_length=50, blank=True)
    requires_consent = models.BooleanField(default=False)
    consent_category_slug = models.CharField(max_length=80, blank=True)
    # Behaviour flags
    requires_staff_confirmation = models.BooleanField(
        default=True,
        help_text="If False, booking is auto-confirmed immediately.",
    )
    allow_citizen_self_booking = models.BooleanField(default=True)
    allow_walk_in = models.BooleanField(default=False)
    non_punitive_no_show = models.BooleanField(
        default=False,
        help_text="If True, no-shows do not count against the client record (CHC/NGO pattern).",
    )
    # Interpreter / interpreter resource required
    interpreter_required_option = models.CharField(
        max_length=20,
        choices=[
            ("none", "Not Applicable"),
            ("optional", "Client May Request"),
            ("required", "Always Required"),
        ],
        default="none",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    # Link to Wagtail CMS page for public description
    cms_page_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "name_en"]
        indexes = [
            models.Index(fields=["service_type", "is_active"]),
            models.Index(fields=["mode", "is_active"]),
        ]
```

### 5.3 SchedulingPolicy

```python
class SchedulingPolicy(TimeStampedModel):
    """Configurable booking rules. Attachable to Location or AppointmentType."""
    name = models.CharField(max_length=100, unique=True)
    # Booking window
    min_lead_time_hours = models.PositiveIntegerField(
        default=1,
        help_text=(
            "Minimum hours before a slot that booking is allowed. "
            "Enforced against wall-clock time, not business hours."
        ),
    )
    max_advance_days = models.PositiveIntegerField(
        default=180,
        help_text="Maximum days in the future a citizen can book.",
    )
    # Slot generation
    slot_interval_minutes = models.PositiveIntegerField(
        default=15,
        help_text="Step between slot start times (may differ from appointment duration).",
    )
    buffer_before_minutes = models.PositiveIntegerField(default=0)
    buffer_after_minutes = models.PositiveIntegerField(default=5)
    # Cancellation / rescheduling
    cancellation_notice_hours = models.PositiveIntegerField(
        default=24,
        help_text="Minimum hours before appointment that cancellation is allowed by citizen.",
    )
    reschedule_notice_hours = models.PositiveIntegerField(default=24)
    max_reschedule_count = models.PositiveIntegerField(
        default=3,
        help_text="How many times a citizen may reschedule a single appointment.",
    )
    # Booking limits (anti-hoarding)
    max_active_bookings_per_citizen = models.PositiveIntegerField(
        default=3,
        help_text="Max simultaneous CONFIRMED bookings per citizen across this policy.",
    )
    booking_frequency_days = models.PositiveIntegerField(
        default=0,
        help_text="If >0, citizen cannot book again within this many days (food bank pattern).",
    )
    # Waitlist
    waitlist_enabled = models.BooleanField(default=True)
    waitlist_acceptance_window_hours = models.PositiveIntegerField(
        default=2,
        help_text="Hours a waitlisted client has to accept a freed slot before it goes to next.",
    )
    max_waitlist_per_slot = models.PositiveIntegerField(default=10)
    waitlist_notify_batch_size = models.PositiveIntegerField(
        default=3,
        help_text=(
            "Notify this many waitlisted clients simultaneously to raise fill rate. "
            "Industry evidence: batch of 3 raises fill rate from ~50%% to 80%%+."
        ),
    )
    # No-show escalation thresholds
    no_show_warning_threshold = models.PositiveIntegerField(default=1)
    no_show_suspension_threshold = models.PositiveIntegerField(
        default=3,
        help_text=(
            "After this many no-shows the citizen is flagged for staff review. "
            "Set to 0 to disable (non-punitive mode)."
        ),
    )

    class Meta:
        verbose_name_plural = "scheduling policies"
```

### 5.4 Location

```python
class Location(TimeStampedModel):
    """Physical office, clinic, or virtual meeting room pool."""
    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="locations"
    )
    name_en = models.CharField(max_length=200)
    name_fr = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    is_virtual = models.BooleanField(default=False)
    # Physical address (blank if virtual)
    street_address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=2, blank=True)  # ISO 3166-2:CA code
    postal_code = models.CharField(max_length=7, blank=True)
    accessibility_features_en = models.TextField(blank=True)
    accessibility_features_fr = models.TextField(blank=True)
    # Timezone — IANA identifier (e.g., "America/Toronto")
    timezone = models.CharField(
        max_length=64,
        default="America/Toronto",
        help_text="IANA timezone for this location. All slot times are stored in UTC.",
    )
    # Business hours JSON: [{day_of_week: 1, open: "09:00", close: "17:00"}, ...]
    business_hours = models.JSONField(default=list)
    scheduling_policy = models.ForeignKey(
        SchedulingPolicy, on_delete=models.SET_NULL, null=True, blank=True
    )
    # Privacy regime governs how data at this location is treated
    privacy_regime = models.CharField(
        max_length=30,
        choices=[
            ("privacy_act", "Federal Privacy Act"),
            ("pipeda", "PIPEDA"),
            ("phipa", "Ontario PHIPA"),
            ("law25", "Quebec Law 25"),
            ("foippa_bc", "BC FOIPPA"),
            ("fippa_on", "Ontario FIPPA"),
        ],
        default="pipeda",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    phone_en = models.CharField(max_length=20, blank=True)
    phone_fr = models.CharField(max_length=20, blank=True)
    tty_phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

    class Meta:
        ordering = ["name_en"]
        indexes = [models.Index(fields=["organization", "is_active"])]
```

### 5.5 Resource

```python
class Resource(TimeStampedModel):
    """Physical or virtual resource that can be assigned to a slot (room, equipment)."""
    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, related_name="resources"
    )
    name_en = models.CharField(max_length=200)
    name_fr = models.CharField(max_length=200)
    resource_type = models.CharField(
        max_length=20,
        choices=[
            ("room", "Meeting Room"),
            ("equipment", "Equipment"),
            ("virtual", "Virtual Meeting Room"),
            ("phone_line", "Phone Line"),
            ("other", "Other"),
        ],
    )
    capacity = models.PositiveIntegerField(
        default=1,
        help_text="How many people this resource can accommodate simultaneously.",
    )
    features = models.JSONField(
        default=list,
        help_text=(
            "List of feature tags, e.g. ['WHEELCHAIR_ACCESSIBLE', "
            "'VIDEO_CONFERENCING', 'PROJECTOR', 'PRIVATE', 'INTERPRETER_PHONE']."
        ),
    )
    # External calendar integration (stub — future wave)
    calendar_provider = models.CharField(
        max_length=20,
        choices=[("none", "None"), ("google", "Google Calendar"), ("exchange", "Exchange/Outlook")],
        default="none",
    )
    external_calendar_id = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name_en"]
        indexes = [models.Index(fields=["location", "resource_type", "is_active"])]
```

### 5.6 StaffProfile

```python
class StaffProfile(TimeStampedModel):
    """
    Scheduling-specific profile for staff. Extends auth_extension.User
    (is_staff=True) without modifying the User model.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_profile",
        limit_choices_to={"is_staff": True},
    )
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, related_name="staff"
    )
    appointment_types = models.ManyToManyField(
        AppointmentType,
        blank=True,
        help_text="Which appointment types this staff member can handle.",
    )
    display_name_en = models.CharField(max_length=200, blank=True)
    display_name_fr = models.CharField(max_length=200, blank=True)
    max_daily_appointments = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Hard cap on appointments per day. None = no cap.",
    )
    accepts_walk_ins = models.BooleanField(default=False)
    is_accepting_bookings = models.BooleanField(
        default=True,
        help_text="Quick toggle to pause new bookings without removing availability.",
    )
    # iCal / video stubs
    calendar_integration_provider = models.CharField(
        max_length=20,
        choices=[("none", "None"), ("google", "Google Calendar"), ("exchange", "Exchange")],
        default="none",
    )
    external_calendar_id = models.CharField(max_length=200, blank=True)
    video_provider = models.CharField(
        max_length=20,
        choices=[
            ("none", "None"),
            ("teams", "Microsoft Teams"),
            ("zoom", "Zoom"),
            ("jitsi", "Jitsi Meet (Self-Hosted)"),
            ("phone", "Phone Bridge"),
        ],
        default="none",
    )
    video_external_user_id = models.CharField(
        max_length=200,
        blank=True,
        help_text="Staff's user ID in the video platform (e.g., Teams UPN, Zoom userId).",
    )

    class Meta:
        indexes = [models.Index(fields=["location", "is_accepting_bookings"])]
```

### 5.7 AvailabilityTemplate

```python
class AvailabilityTemplate(TimeStampedModel):
    """
    Recurring weekly availability window for a staff member.
    Times are pure TimeField values; timezone comes from staff.location.timezone.
    Multiple rows per staff member, one per day-of-week they work.
    valid_from / valid_until allow seasonal schedule changes.
    """
    staff = models.ForeignKey(
        StaffProfile, on_delete=models.CASCADE, related_name="availability_templates"
    )
    day_of_week = models.PositiveSmallIntegerField(
        help_text="ISO weekday: 1=Monday, 7=Sunday.",
        choices=[(i, name) for i, name in enumerate(
            ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"], 1
        )],
    )
    start_time = models.TimeField(help_text="In staff.location.timezone.")
    end_time = models.TimeField()
    valid_from = models.DateField(help_text="First date this template applies.")
    valid_until = models.DateField(
        null=True, blank=True, help_text="Inclusive last date. None = open-ended."
    )

    class Meta:
        ordering = ["day_of_week", "start_time"]
        indexes = [models.Index(fields=["staff", "day_of_week", "valid_from"])]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F("start_time")),
                name="appt_avail_end_after_start",
            )
        ]
```

### 5.8 StaffException

```python
class StaffException(TimeStampedModel):
    """Date-level override for a staff member (holiday, leave, different hours)."""
    staff = models.ForeignKey(
        StaffProfile, on_delete=models.CASCADE, related_name="exceptions"
    )
    exception_date = models.DateField(db_index=True)
    exception_type = models.CharField(
        max_length=20,
        choices=[
            ("holiday", "Public Holiday / Day Off"),
            ("leave", "Sick / Personal Leave"),
            ("override", "Override Hours"),
            ("training", "Training / Conference"),
        ],
    )
    # Only non-null for OVERRIDE type
    override_start_time = models.TimeField(null=True, blank=True)
    override_end_time = models.TimeField(null=True, blank=True)
    note_internal = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("staff", "exception_date")]
        ordering = ["exception_date"]
```

### 5.9 Slot

```python
class Slot(TimeStampedModel):
    """
    Concrete bookable occurrence of an AppointmentType.
    Created ahead of time (pre-stored) or on-demand via SlotGenerator service.
    stored in UTC; display using location.timezone.
    """
    appointment_type = models.ForeignKey(
        AppointmentType, on_delete=models.PROTECT, related_name="slots"
    )
    staff = models.ForeignKey(
        StaffProfile, on_delete=models.PROTECT, related_name="slots"
    )
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="slots"
    )
    resource = models.ForeignKey(
        Resource, on_delete=models.SET_NULL, null=True, blank=True, related_name="slots"
    )
    start_datetime = models.DateTimeField(db_index=True)  # UTC
    end_datetime = models.DateTimeField(db_index=True)    # UTC
    # Effective times including buffers (for busy-time calculations)
    effective_start = models.DateTimeField()  # = start_datetime - buffer_before
    effective_end = models.DateTimeField()    # = end_datetime + buffer_after
    capacity = models.PositiveSmallIntegerField(default=1)
    spaces_used = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=[
            ("available", "Available"),
            ("partial", "Partially Booked"),
            ("full", "Fully Booked"),
            ("blocked", "Blocked"),
            ("cancelled", "Cancelled"),
            ("completed", "Completed"),
        ],
        default="available",
        db_index=True,
    )
    is_walk_in_slot = models.BooleanField(
        default=False,
        help_text="If True, this slot is reserved for walk-in clients.",
    )
    # Virtual appointment: pre-generated join URL (if mode=virtual)
    video_join_url_citizen = models.URLField(blank=True)
    video_join_url_staff = models.URLField(blank=True)
    video_meeting_id = models.CharField(max_length=200, blank=True)
    video_provider = models.CharField(max_length=20, blank=True)
    # Notes
    internal_note = models.TextField(blank=True)

    class Meta:
        ordering = ["start_datetime"]
        indexes = [
            models.Index(fields=["appointment_type", "start_datetime", "status"]),
            models.Index(fields=["staff", "start_datetime"]),
            models.Index(fields=["location", "start_datetime", "status"]),
            models.Index(fields=["effective_start", "effective_end"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_datetime__gt=models.F("start_datetime")),
                name="appt_slot_end_after_start",
            ),
            models.CheckConstraint(
                check=models.Q(spaces_used__lte=models.F("capacity")),
                name="appt_slot_spaces_lte_capacity",
            ),
        ]
```

### 5.10 Booking

```python
class Booking(BaseModel):
    """
    Client reservation against a Slot. The primary citizen-facing record.
    UUID primary key (inherited from BaseModel).
    Rescheduling creates a NEW Booking record; the old one is CANCELLED
    with rescheduled=True and rescheduled_to FK set. Full history preserved.
    """
    slot = models.ForeignKey(Slot, on_delete=models.PROTECT, related_name="bookings")
    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="appointment_bookings",
        limit_choices_to={"is_staff": False},
    )
    # Status state machine — see §6.1
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending Staff Confirmation"),
            ("confirmed", "Confirmed"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
            ("completed", "Completed"),
        ],
        default="pending",
        db_index=True,
    )
    # No-show: separate Boolean per Cal.com pattern (allows CONFIRMED + no_show=True)
    no_show = models.BooleanField(
        default=False,
        help_text="Staff marks True if client did not attend. NOT a status value.",
    )
    # Rescheduling chain
    rescheduled = models.BooleanField(default=False)
    rescheduled_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rescheduled_to_set",
    )
    reschedule_count = models.PositiveSmallIntegerField(default=0)
    # Cancellation
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_bookings",
    )
    cancellation_reason = models.TextField(blank=True)
    late_cancellation = models.BooleanField(
        default=False,
        help_text=(
            "True if cancelled within the cancellation_notice_hours window. "
            "Used for no-show policy escalation."
        ),
    )
    # Booking metadata
    booking_channel = models.CharField(
        max_length=20,
        choices=[
            ("online", "Online Self-Service"),
            ("phone", "Phone (Staff-Assisted)"),
            ("walk_in", "Walk-In"),
            ("staff_portal", "Staff Portal"),
            ("api", "API"),
        ],
        default="online",
    )
    language = models.CharField(
        max_length=5,
        choices=[("en", "English"), ("fr", "French")],
        default="en",
    )
    appointment_mode = models.CharField(
        max_length=20,
        choices=[
            ("in_person", "In-Person"),
            ("virtual", "Virtual (Video)"),
            ("phone", "Phone"),
        ],
        default="in_person",
    )
    # Intake form answers (JSON matching appointment_type.intake_form_schema)
    form_responses = models.JSONField(default=dict, blank=True)
    # Interpreter request
    interpreter_needed = models.BooleanField(default=False)
    interpreter_language = models.CharField(max_length=50, blank=True)
    # Accessibility / accommodation
    accessibility_needs = models.TextField(blank=True)
    # Staff internal notes (never shown to citizen)
    internal_notes = models.TextField(blank=True)
    # Confirmation / reminder tracking
    confirmation_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_72h_sent = models.BooleanField(default=False)
    reminder_24h_sent = models.BooleanField(default=False)
    reminder_2h_sent = models.BooleanField(default=False)
    # Linked WorkItem (plain UUID — avoids circular import per ServiceFeePayment pattern)
    work_item_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="UUID of the related WorkItem (apps.workflows). Plain UUID, not FK.",
    )
    # Linked ServiceRequest (plain UUID)
    service_request_id = models.UUIDField(null=True, blank=True)
    # Consent
    consent_recorded_at = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=20, blank=True)
    # Document attachments (GenericRelation — no schema change to documents app)
    document_attachments = GenericRelation(
        "documents.DocumentAttachment", related_query_name="booking"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["citizen", "status"]),
            models.Index(fields=["slot", "status"]),
            models.Index(fields=["status", "no_show"]),
            models.Index(fields=["citizen", "created_at"]),
        ]
```

### 5.11 Attendee

```python
class Attendee(TimeStampedModel):
    """
    Additional attendees beyond the primary citizen (e.g., family member, interpreter).
    The primary citizen is on the Booking; extras go here.
    """
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="attendees")
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(
        max_length=20,
        choices=[
            ("family_member", "Family Member"),
            ("legal_guardian", "Legal Guardian"),
            ("support_person", "Support Person"),
            ("interpreter", "Interpreter"),
            ("advocate", "Advocate / Representative"),
            ("other", "Other"),
        ],
        default="family_member",
    )
    no_show = models.BooleanField(default=False)
    timezone = models.CharField(max_length=64, blank=True)
    preferred_language = models.CharField(max_length=5, blank=True)
```

### 5.12 WaitlistEntry

```python
class WaitlistEntry(TimeStampedModel):
    """Per-slot waitlist. Priority class + joined_at provide ordering."""
    slot = models.ForeignKey(Slot, on_delete=models.CASCADE, related_name="waitlist")
    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="waitlist_entries"
    )
    priority_class = models.CharField(
        max_length=20,
        choices=[
            ("emergency", "Emergency / Crisis"),      # 1 — highest
            ("bumped", "Bumped by Provider"),          # 2 — not their fault
            ("high_need", "High Need / Vulnerable"),   # 3
            ("recurring", "Recurring Regular"),        # 4
            ("standard", "Standard"),                  # 5 — lowest
        ],
        default="standard",
    )
    # Denormalized position (1 = next to be offered slot); recalculated on changes
    position = models.PositiveIntegerField(db_index=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("waiting", "Waiting"),
            ("notified", "Notified — Awaiting Response"),
            ("accepted", "Accepted — Booking Created"),
            ("expired", "Notification Expired"),
            ("withdrawn", "Withdrawn by Client"),
        ],
        default="waiting",
    )
    notification_sent_at = models.DateTimeField(null=True, blank=True)
    acceptance_deadline = models.DateTimeField(null=True, blank=True)
    notification_channel = models.CharField(
        max_length=10,
        choices=[("email", "Email"), ("sms", "SMS"), ("phone", "Phone Call")],
        default="email",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("slot", "citizen")]
        ordering = ["priority_class", "joined_at"]
        indexes = [
            models.Index(fields=["slot", "status", "position"]),
            models.Index(fields=["citizen", "status"]),
        ]
```

### 5.13 ClientNoShowRecord

```python
class ClientNoShowRecord(TimeStampedModel):
    """Aggregate no-show statistics per citizen. Used for policy escalation."""
    citizen = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="no_show_record",
        limit_choices_to={"is_staff": False},
    )
    no_show_count = models.PositiveIntegerField(default=0)
    late_cancellation_count = models.PositiveIntegerField(default=0)
    total_appointments = models.PositiveIntegerField(default=0)
    last_no_show_at = models.DateTimeField(null=True, blank=True)
    # Escalation
    is_flagged = models.BooleanField(default=False)
    flagged_at = models.DateTimeField(null=True, blank=True)
    flagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="flagged_no_show_records",
    )
    is_suspended = models.BooleanField(
        default=False,
        help_text="If True, citizen cannot self-book until staff reviews and clears.",
    )
    suspension_note = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["is_flagged"]), models.Index(fields=["is_suspended"])]
```

### 5.14 QueueEntry

```python
class QueueEntry(TimeStampedModel):
    """Real-time queue for walk-in + booked clients on the day of service."""
    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, related_name="queue_entries"
    )
    queue_date = models.DateField(db_index=True)
    queue_number = models.CharField(max_length=10, db_index=True)
    booking = models.OneToOneField(
        Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_entry"
    )
    citizen_name = models.CharField(
        max_length=200,
        help_text="Display name for queue board (not stored if anonymous).",
    )
    queue_type = models.CharField(
        max_length=20,
        choices=[
            ("booked", "Pre-Booked"),        # priority 2
            ("walk_in", "Walk-In"),           # priority 3
            ("emergency", "Emergency"),       # priority 1
        ],
        default="booked",
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ("waiting", "Waiting"),
            ("called", "Called"),
            ("in_service", "In Service"),
            ("completed", "Completed"),
            ("no_show", "No Show"),
            ("cancelled", "Left / Cancelled"),
        ],
        default="waiting",
        db_index=True,
    )
    assigned_staff = models.ForeignKey(
        StaffProfile, on_delete=models.SET_NULL, null=True, blank=True
    )
    called_at = models.DateTimeField(null=True, blank=True)
    service_started_at = models.DateTimeField(null=True, blank=True)
    service_completed_at = models.DateTimeField(null=True, blank=True)
    wait_time_minutes = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = [("location", "queue_date", "queue_number")]
        ordering = ["queue_type", "created_at"]
        indexes = [
            models.Index(fields=["location", "queue_date", "status"]),
        ]
```

### 5.15 BookingAuditLog

```python
class BookingAuditLog(models.Model):
    """
    Immutable append-only audit trail for every booking lifecycle event.
    Separate from the system-wide AuditLogEntry to provide booking-specific
    context fields. record_event() in apps.audit is ALSO called for system-wide
    compliance — this model is for booking-domain detail.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="audit_log"
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(
        max_length=30,
        choices=[
            ("created", "Booking Created"),
            ("confirmed", "Confirmed"),
            ("rejected", "Rejected"),
            ("cancelled_citizen", "Cancelled by Citizen"),
            ("cancelled_staff", "Cancelled by Staff"),
            ("cancelled_system", "Cancelled by System"),
            ("rescheduled", "Rescheduled"),
            ("completed", "Completed"),
            ("no_show_marked", "No Show Marked"),
            ("reminder_sent", "Reminder Sent"),
            ("waitlist_joined", "Joined Waitlist"),
            ("waitlist_promoted", "Promoted from Waitlist"),
            ("document_attached", "Document Attached"),
            ("payment_received", "Payment Received"),
            ("consent_recorded", "Consent Recorded"),
        ],
    )
    actor_id = models.CharField(max_length=50, blank=True)
    actor_ip = models.GenericIPAddressField(null=True)
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20, blank=True)
    detail = models.JSONField(
        default=dict,
        help_text="Additional context. MUST NOT contain PII.",
    )

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["booking", "timestamp"])]

    def save(self, *args, **kwargs):
        if self.pk and BookingAuditLog.objects.filter(pk=self.pk).exists():
            raise ValueError("BookingAuditLog records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("BookingAuditLog records cannot be deleted.")
```

---

## 6. State Machines

### 6.1 Booking Status State Machine

```
PENDING ──────────────► CONFIRMED ──────────────► COMPLETED
   │                        │    \                     ▲
   │                        │     └──── (no_show=True) │
   │                        │                 CONFIRMED + no_show=True
   ├──► REJECTED             │
   │    (staff declines)     │
   └──► CANCELLED            └──────────────────────► CANCELLED
        (citizen or system,          (citizen, staff, or system;
         before confirmation)         within cancellation_notice window
                                      = late_cancellation=True)
                             CONFIRMED ──────────────► RESCHEDULED*
                             * Rescheduling creates a NEW Booking record.
                               Old booking: status=CANCELLED, rescheduled=True,
                               rescheduled_from=<old pk on new>, rescheduled_to_set fills.

Side effects by transition:
PENDING → CONFIRMED:    • Emit appt_confirmed signal
                        • Generate video join URL if mode=virtual
                        • Send confirmation email with iCal attachment
                        • Schedule reminder Celery tasks
                        • Increment Slot.spaces_used
                        • Create WorkItem(due_at=slot.start_datetime)

CONFIRMED → CANCELLED:  • Free slot (decrement spaces_used)
                        • Cancel pending reminder tasks
                        • Send cancellation email with iCal CANCEL
                        • Trigger waitlist promotion on slot

CONFIRMED → COMPLETED:  • Record in BookingAuditLog
                        • Optionally create HoursLog / ServiceRecord

CONFIRMED → NO_SHOW:    • no_show=True (separate Boolean, NOT status change)
                        • Increment ClientNoShowRecord.no_show_count
                        • Check threshold; if ≥ suspension_threshold → flag record
                        • Emit appt_no_show signal

PENDING → CANCELLED:    • Do NOT decrement spaces_used (never incremented)
                        • Release any SelectedSlot lock
```

### 6.2 Slot Status State Machine

```
AVAILABLE ──► PARTIAL  ──► FULL ──► AVAILABLE  (when booking cancelled)
AVAILABLE ──► BLOCKED              (admin blocks slot)
AVAILABLE ──► CANCELLED            (admin cancels slot; cascades to all Bookings)
{any} ──► COMPLETED                (end of day batch; slot is in the past)
```

### 6.3 WaitlistEntry Status State Machine

```
WAITING ──► NOTIFIED ──► ACCEPTED ──► (Booking created; slot → PARTIAL/FULL)
                    └──► EXPIRED  ──► (next batch notified)
WAITING ──► WITHDRAWN               (client cancels own waitlist position)
```

---

## 7. Slot Availability Algorithm

```python
class SlotAvailabilityService:
    """
    Computes available slots for a given AppointmentType, date range, and
    optional staff/location filter. Steps mirror Cal.com's verified algorithm.
    """

    def get_available_slots(
        self,
        appointment_type: AppointmentType,
        date_from: date,
        date_to: date,
        location: Location | None = None,
        staff: StaffProfile | None = None,
    ) -> list[dict]:
        """
        1. Resolve eligible staff list (filtered by appointment_type, is_accepting_bookings)
        2. For each target date in [date_from, date_to]:
           a. Find AvailabilityTemplate rows matching date's isoweekday and valid_from/until range
           b. Check StaffException for this date:
              - HOLIDAY / LEAVE → skip date entirely
              - OVERRIDE → use override_start_time/override_end_time instead
           c. Snap first slot start to slot_interval_minutes grid (ceiling)
           d. Generate candidate slots:
                while (current + duration + buffer_after) <= avail_end:
                    yield (current, current + duration)
                    current += stride (= slot_interval_minutes)
           e. Apply min_lead_time: filter out slots where start < now + min_lead_time
           f. Apply max_advance_days: filter out slots beyond now + max_advance_days
           g. Collect busy times:
                - Existing Slot rows for this staff on this date:
                  busy = [effective_start, effective_end] (pre-computed, includes buffers)
                - Existing confirmed/pending Bookings
           h. Subtract busy from candidates (sweep algorithm):
                slot OK ↔ [start - buffer_before, end + buffer_after] ∩ busy = ∅
           i. Apply max_daily_appointments: count confirmed bookings today for staff;
                if ≥ cap → discard remaining slots
           j. Apply booking_frequency_days: if citizen has a booking within the
                frequency window → discard all slots (food bank pattern)
        3. Aggregate results; annotate each slot with available_spaces and waitlist_count
        4. Return sorted by start_datetime
        """
```

**Key invariants:**
- All computation is performed in UTC; results annotated with `display_time_local` (location's IANA timezone)
- `min_lead_time_hours` is enforced against wall-clock time, NOT business hours (Microsoft verified pattern)
- Buffer expansion: `effective_start = start - buffer_before`; `effective_end = end + buffer_after`
- Race condition guard: slot capacity check uses `SELECT FOR UPDATE` inside `atomic()` at booking time (not during availability check — availability is eventually consistent, booking is strictly consistent)

---

## 8. Service Layer Architecture

### 8.1 Service Functions

```
apps/appointments/services/
├── availability.py     # SlotAvailabilityService.get_available_slots()
├── booking.py          # create_booking(), confirm_booking(), cancel_booking(),
│                       # reschedule_booking(), mark_no_show(), complete_booking()
├── slots.py            # generate_slots_for_range(), block_slot(), cancel_slot()
├── waitlist.py         # join_waitlist(), promote_waitlist(), expire_waitlist_notifications()
├── queue.py            # add_to_queue(), call_next(), complete_queue_entry()
└── video.py            # generate_join_url() — dispatches to Teams/Zoom/Jitsi
```

### 8.2 `create_booking()` — Core Service Function

```python
def create_booking(
    *,
    slot: Slot,
    citizen: User,
    appointment_mode: str,
    form_responses: dict,
    actor: User,
    booking_channel: str = "online",
    interpreter_needed: bool = False,
    interpreter_language: str = "",
    accessibility_needs: str = "",
    request=None,
) -> Booking:
    """
    PIPEDA 4.5.3: entire function runs inside transaction.atomic().
    Concurrency: SELECT FOR UPDATE on Slot row.

    Algorithm:
    1. SELECT FOR UPDATE on slot.
    2. Check slot.status in (available, partial); check spaces_used < capacity.
       If full → raise SlotFullError (caller should offer waitlist).
    3. Verify citizen is not suspended (ClientNoShowRecord.is_suspended).
    4. Verify booking frequency policy (booking_frequency_days).
    5. Verify max_active_bookings_per_citizen not exceeded.
    6. Check consent if appointment_type.requires_consent:
         ConsentService.has_consent(citizen, appt_type.consent_category_slug)
    7. Create Booking(status='pending' or 'confirmed' per requires_staff_confirmation).
    8. Increment slot.spaces_used; update slot.status.
    9. Write BookingAuditLog(action='created').
    10. Call record_event(event_type='appt.booked', ...) — inside same atomic().
    11. If status='confirmed' directly (auto-confirm):
        a. Generate video join URL if mode='virtual'.
        b. Schedule reminder Celery tasks.
    12. Emit appt_booking_created signal via transaction.on_commit().
    13. Create WorkItem via on_commit (avoids import cycle; plain UUID reference).
    Return booking.
    """
```

### 8.3 `reschedule_booking()` — Rescheduling Pattern

```python
def reschedule_booking(*, booking: Booking, new_slot: Slot, actor: User, reason: str = "") -> Booking:
    """
    Rescheduling creates a NEW Booking record; old is CANCELLED with rescheduled=True.
    Full history is preserved in the audit chain.
    Mirrors Cal.com's verified rescheduling pattern.

    Algorithm:
    1. Verify booking.status == 'confirmed' and not booking.no_show.
    2. Check reschedule_count < policy.max_reschedule_count.
    3. Check policy.reschedule_notice_hours: now + notice < slot.start_datetime.
    4. SELECT FOR UPDATE on both old_slot and new_slot (consistent lock order: min PK first).
    5. Check new_slot capacity.
    6. Set booking.status='cancelled', rescheduled=True, cancelled_at=now.
    7. Decrement old_slot.spaces_used.
    8. Create new_booking(rescheduled_from=booking, reschedule_count=booking.reschedule_count+1).
    9. Increment new_slot.spaces_used.
    10. Cancel all pending reminder tasks for old booking (revoke Celery task IDs).
    11. Schedule new reminder tasks for new_booking.
    12. Write BookingAuditLog on both old and new bookings.
    13. Emit appt_rescheduled signal on commit.
    Return new_booking.
    """
```

### 8.4 `promote_waitlist()` — Atomic Batch Promotion

```python
def promote_waitlist(*, slot: Slot) -> int:
    """
    Called whenever a booking is cancelled or a slot's capacity increases.
    Notifies up to policy.waitlist_notify_batch_size clients simultaneously
    (raises fill rate from ~50%% to 80%%+).

    Algorithm:
    1. SELECT FOR UPDATE on slot.
    2. Compute available_spaces = slot.capacity - slot.spaces_used.
    3. If available_spaces == 0: return 0.
    4. batch = WaitlistEntry.objects.select_for_update().filter(
           slot=slot, status='waiting'
       ).order_by('priority_class', 'joined_at')[:policy.waitlist_notify_batch_size]
    5. For each entry in batch:
       a. entry.status = 'notified'
       b. entry.notification_sent_at = now
       c. entry.acceptance_deadline = now + timedelta(hours=policy.waitlist_acceptance_window_hours)
       d. Schedule Celery task: expire_waitlist_notification(entry.pk, eta=entry.acceptance_deadline)
    6. Bulk update.
    7. Dispatch notification to each notified citizen (email/SMS).
    8. Return len(batch).
    """
```

---

## 9. Integration with Existing Building Blocks

### 9.1 Portal BB Integration

**Signal:** `service_request_submitted` → `on_service_request_submitted` receiver in `appointments/receivers.py`.
If the ServiceRequest's `service_name` matches a `ServiceType.slug`, create a pending Booking record linked via `booking.service_request_id` (plain UUID, not FK — matches `ServiceFeePayment` pattern).

Optionally, booking confirmation advances the linked ServiceRequest status via `update_request_status()`.

### 9.2 Workflows BB Integration

Every confirmed booking creates a `WorkItem` via `create_work_item()`:
```python
# In on_commit handler:
from apps.workflows.services import create_work_item
work_item = create_work_item(
    content_object=booking,          # GFK accepts Booking with no schema change
    title=f"Appointment: {booking.slot.appointment_type.name_en}",
    actor=booking.citizen,
    priority=2,                      # HIGH by default; configurable per AppointmentType
    due_at=booking.slot.start_datetime,   # Overrides SLA with actual appointment time
)
booking.work_item_id = work_item.pk  # Store plain UUID reference
booking.save(update_fields=["work_item_id"])
```

Staff can transition the WorkItem status through the existing workflows UI. Appointment status changes also update the WorkItem via signal.

### 9.3 Notifications BB Integration

New Celery tasks in `appointments/tasks.py` call `send_email_notification()`. New templates (copy volunteer shift template structure):

| Template key | Trigger | Context |
|---|---|---|
| `appointment_booked` | Booking created (any status) | booking, slot, location, appointment_type, citizen |
| `appointment_confirmed` | Booking confirmed | + video_join_url (if virtual) |
| `appointment_rejected` | Booking rejected | + rejection_reason |
| `appointment_cancelled` | Booking cancelled | + cancellation_reason, rebooking_url |
| `appointment_rescheduled` | Rescheduled | + new slot details, iCal attachment |
| `appointment_reminder_72h` | T-72h | + preparation_checklist, location map link |
| `appointment_reminder_24h` | T-24h | + cancel/reschedule deep link |
| `appointment_reminder_2h` | T-2h | + video join URL (if virtual), parking/transit |
| `appointment_no_show` | No-show detected | + rebooking link |
| `waitlist_slot_available` | Waitlist promotion | + acceptance_deadline, slot details |
| `waitlist_expired` | Acceptance deadline passed | + rejoin waitlist link |

All templates: bilingual (EN/FR) following volunteer BB pattern. All emails: include `.ics` attachment (`text/calendar` MIME part, `METHOD:REQUEST` for new/update, `METHOD:CANCEL` for cancellation).

### 9.4 Consent BB Integration

For `AppointmentType.requires_consent=True`:
1. Booking flow gates on `ConsentService.has_consent(citizen, appointment_type.consent_category_slug)`
2. If no consent: redirect to consent collection page with `next=` param
3. On consent grant: resume booking
4. Record in `booking.consent_recorded_at`, `booking.consent_version`

Required fixture: `ConsentCategory(slug="appointment_data_processing", ...)`.

### 9.5 Payments BB Integration

For `AppointmentType.requires_payment=True`:
1. `create_booking()` returns status='pending' with a generated `PaymentIntent`
2. Citizen completes payment flow
3. `payment_completed` signal receiver calls `confirm_booking()`
4. `ServiceFeePayment(service_request_id=booking.pk, fee_code=appointment_type.fee_code, ...)`

### 9.6 Documents BB Integration

For `AppointmentType.requires_document_upload=True`:
1. Booking flow shows document upload step after form responses
2. `DocumentAttachment(content_object=booking, attachment_role="id_verification")` is created
3. `document_scan_clean` signal → if booking is pending_documents status → advance to pending confirmation

`Booking.document_attachments` is a `GenericRelation` — no changes to the documents app.

### 9.7 Audit BB Integration

Every lifecycle event calls `record_event()` inside `transaction.atomic()`:

```python
record_event(
    event_type="appt.booked",           # New event types added to AuditEventType
    outcome="success",
    actor_id=str(citizen.pk),
    actor_email="",                      # NEVER log email — use pk only
    actor_ip=ip,
    resource_type="appointments.Booking",
    resource_id=str(booking.pk),
    before_state={},
    after_state={"status": "pending", "slot_id": str(slot.pk)},
    event_detail={"appointment_type": appt_type.slug, "location": location.slug},
    request_id=request_id,
    session_id=session_id,
)
```

**New AuditEventType values:**
`appt.booked`, `appt.confirmed`, `appt.rejected`, `appt.cancelled`, `appt.rescheduled`, `appt.completed`, `appt.no_show`, `appt.waitlist.joined`, `appt.waitlist.promoted`, `appt.reminder.sent`

---

## 10. Notification Architecture

### 10.1 Reminder Scheduling Pattern

Mirror `send_shift_reminders_24h` from volunteers BB — idempotent atomic update:

```python
@shared_task(
    bind=True,
    queue="appointments",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
)
def send_appointment_reminders_24h(self):
    """Hourly beat task. Sends 24h reminders for appointments starting in 23-25h."""
    window_start = timezone.now() + timedelta(hours=23)
    window_end = timezone.now() + timedelta(hours=25)
    bookings = (
        Booking.objects.filter(
            status="confirmed",
            reminder_24h_sent=False,
            slot__start_datetime__gte=window_start,
            slot__start_datetime__lte=window_end,
        )
        .select_related("citizen", "slot__appointment_type", "slot__location", "slot__staff")
    )
    for booking in bookings:
        # Idempotency: atomic update; skip if already sent or status changed
        updated = Booking.objects.filter(
            pk=booking.pk, reminder_24h_sent=False, status="confirmed"
        ).update(reminder_24h_sent=True)
        if updated == 0:
            continue
        send_email_notification.delay(
            recipient_id=str(booking.citizen.pk),
            subject_key="appointment_reminder_24h",
            context=_build_reminder_context(booking),
        )
```

### 10.2 Reminder Task IDs (Revocable on Reschedule/Cancel)

When scheduling reminder tasks, store Celery task IDs so they can be revoked:

```python
booking.reminder_72h_task_id = send_appointment_reminders_72h.apply_async(
    args=[str(booking.pk)], eta=booking.slot.start_datetime - timedelta(hours=72)
).id
```

On cancellation/reschedule:
```python
from celery.app.control import revoke
for task_id in [booking.reminder_72h_task_id, booking.reminder_24h_task_id, ...]:
    if task_id:
        revoke(task_id, terminate=False)  # best-effort; guard in task body also
```

### 10.3 iCal Attachment Generation

```python
from icalendar import Calendar, Event, vDatetime, vText

def build_ical(booking: Booking, method: str = "REQUEST") -> bytes:
    cal = Calendar()
    cal.add("PRODID", "-//CivicOS//Appointments//EN")
    cal.add("VERSION", "2.0")
    cal.add("METHOD", method)  # REQUEST (new/update) or CANCEL
    event = Event()
    event.add("UID", f"appt-{booking.pk}@civicos.ca")  # stable forever
    event.add("DTSTAMP", datetime.utcnow())
    event.add("SEQUENCE", booking.reschedule_count)    # increment on reschedule
    event.add("DTSTART", booking.slot.start_datetime.astimezone(
        pytz.timezone(booking.slot.location.timezone)
    ))
    event.add("DTEND", booking.slot.end_datetime.astimezone(
        pytz.timezone(booking.slot.location.timezone)
    ))
    if method == "CANCEL":
        event.add("STATUS", "CANCELLED")
    else:
        event.add("STATUS", "CONFIRMED")
    event.add("SUMMARY", booking.slot.appointment_type.name_en)
    event.add("LOCATION", booking.slot.location.street_address or "Virtual")
    if booking.appointment_mode == "virtual" and booking.slot.video_join_url_citizen:
        event.add("DESCRIPTION", f"Join link: {booking.slot.video_join_url_citizen}")
    cal.add_component(event)
    return cal.to_ical()
```

---

## 11. Virtual Appointment Architecture

### 11.1 Video Provider Dispatch

```python
# services/video.py
def generate_join_url(*, slot: Slot, booking: Booking) -> dict:
    """
    Returns {"citizen_url": str, "staff_url": str, "meeting_id": str}.
    Called inside transaction.atomic() when booking is confirmed.
    """
    provider = slot.staff.video_provider
    if provider == "teams":
        return _generate_teams_meeting(slot, booking)
    elif provider == "zoom":
        return _generate_zoom_meeting(slot, booking)
    elif provider == "jitsi":
        return _generate_jitsi_url(slot, booking)
    elif provider == "phone":
        return {"citizen_url": "", "staff_url": "", "meeting_id": slot.staff.video_external_user_id}
    return {}
```

### 11.2 Microsoft Teams (MS Graph `createOrGet`)

```python
def _generate_teams_meeting(slot: Slot, booking: Booking) -> dict:
    """
    Uses createOrGet with booking UUID as externalId — idempotent.
    Idempotent: 201 = new meeting; 200 = already exists. Safe to retry.
    Requires: Azure App Registration with OnlineMeetings.ReadWrite.All scope.
    Token: OAuth2 Client Credentials (tenant-level).
    """
    token = _get_teams_token()
    response = requests.post(
        "https://graph.microsoft.com/v1.0/me/onlineMeetings/createOrGet",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "externalId": str(booking.pk),
            "startDateTime": slot.start_datetime.isoformat(),
            "endDateTime": slot.end_datetime.isoformat(),
            "subject": slot.appointment_type.name_en,
        },
    )
    response.raise_for_status()
    data = response.json()
    return {
        "citizen_url": data["joinWebUrl"],
        "staff_url": data["joinWebUrl"],
        "meeting_id": data["id"],
    }
    # Security: join link delivered INSIDE authenticated portal session only.
    # Never included in unauthenticated email (IRCC IFS precedent).
    # Waiting room MANDATORY — staff controls admission.
```

### 11.3 Jitsi Meet (Self-Hosted JWT)

```python
import jwt, time

def _generate_jitsi_url(slot: Slot, booking: Booking) -> dict:
    """Room name = appointment UUID → prevents room squatting."""
    room = f"appt-{booking.pk}"
    domain = settings.CIVICOS.get("JITSI_DOMAIN", "meet.civicos.ca")
    secret = settings.CIVICOS["JITSI_SECRET"]

    def make_token(is_moderator: bool) -> str:
        payload = {
            "iss": "civicos",
            "sub": domain,
            "aud": "jitsi",
            "room": room,
            "exp": int(time.time()) + 7200,
            "context": {"user": {"moderator": is_moderator}},
        }
        return jwt.encode(payload, secret, algorithm="HS256")

    return {
        "citizen_url": f"https://{domain}/{room}?jwt={make_token(False)}",
        "staff_url": f"https://{domain}/{room}?jwt={make_token(True)}",
        "meeting_id": room,
    }
```

---

## 12. Celery Tasks and Beat Schedule

### 12.1 Tasks (`apps/appointments/tasks.py`)

| Task | Queue | Trigger | Description |
|------|-------|---------|-------------|
| `send_appointment_reminders_72h` | appointments | Hourly beat | 72h email reminders (window: 71–73h) |
| `send_appointment_reminders_24h` | appointments | Hourly beat | 24h email + SMS reminders (window: 23–25h) |
| `send_appointment_reminders_2h` | appointments | Hourly beat | 2h SMS reminder (window: 1h50m–2h10m) |
| `expire_waitlist_notifications` | appointments | Per-entry ETA task | Expires a single WaitlistEntry and triggers next batch |
| `mark_past_slots_completed` | appointments | Daily beat (23:00) | Sets status=COMPLETED on past Slots |
| `detect_no_shows` | appointments | Hourly beat | Marks CONFIRMED bookings past end_datetime as potential no-show |
| `generate_slots_for_period` | appointments | Weekly beat | Pre-generates Slot rows for the next 60 days |
| `send_daily_queue_summary` | appointments | Daily beat (08:00) | Emails staff their day's bookings |
| `cleanup_expired_pending_bookings` | appointments | Hourly beat | Cancels PENDING bookings older than policy timeout |

### 12.2 Beat Schedule Additions to `settings/base.py`

```python
CIVICOS_APPOINTMENT_BEAT = {
    "appointments-reminders-72h": {
        "task": "apps.appointments.tasks.send_appointment_reminders_72h",
        "schedule": crontab(minute=0),  # hourly
    },
    "appointments-reminders-24h": {
        "task": "apps.appointments.tasks.send_appointment_reminders_24h",
        "schedule": crontab(minute=15),  # offset to distribute load
    },
    "appointments-reminders-2h": {
        "task": "apps.appointments.tasks.send_appointment_reminders_2h",
        "schedule": crontab(minute=30),
    },
    "appointments-mark-completed": {
        "task": "apps.appointments.tasks.mark_past_slots_completed",
        "schedule": crontab(hour=23, minute=0),
    },
    "appointments-detect-noshows": {
        "task": "apps.appointments.tasks.detect_no_shows",
        "schedule": crontab(minute=45),
    },
    "appointments-generate-slots": {
        "task": "apps.appointments.tasks.generate_slots_for_period",
        "schedule": crontab(day_of_week=0, hour=2),  # Sunday 02:00
    },
    "appointments-cleanup-pending": {
        "task": "apps.appointments.tasks.cleanup_expired_pending_bookings",
        "schedule": crontab(minute=5),
    },
}
```

---

## 13. Views and URL Structure

### 13.1 Citizen Views

```
/appointments/                                    citizen:dashboard
/appointments/services/                           citizen:service-list
/appointments/services/<slug>/                    citizen:service-detail
/appointments/book/<appt_type_slug>/              citizen:book-step-1  (date/location selection)
/appointments/book/<appt_type_slug>/slot/         citizen:book-step-2  (slot selection)
/appointments/book/<appt_type_slug>/details/      citizen:book-step-3  (intake form + consent)
/appointments/book/<appt_type_slug>/confirm/      citizen:book-step-4  (review + submit)
/appointments/book/<appt_type_slug>/payment/      citizen:book-payment  (if fee required)
/appointments/booking/<uuid:pk>/                  citizen:booking-detail
/appointments/booking/<uuid:pk>/cancel/           citizen:booking-cancel  (POST)
/appointments/booking/<uuid:pk>/reschedule/       citizen:booking-reschedule
/appointments/booking/<uuid:pk>/join/             citizen:booking-join  (virtual join link — auth-gated)
/appointments/waitlist/<uuid:pk>/accept/          citizen:waitlist-accept  (POST)
/appointments/waitlist/<uuid:pk>/withdraw/        citizen:waitlist-withdraw  (POST)
```

### 13.2 Staff Views

```
/appointments/staff/                              staff:dashboard
/appointments/staff/calendar/                     staff:calendar-view  (month/week/day)
/appointments/staff/bookings/                     staff:booking-list  (filterable)
/appointments/staff/bookings/<uuid:pk>/           staff:booking-detail
/appointments/staff/bookings/<uuid:pk>/confirm/   staff:booking-confirm  (POST)
/appointments/staff/bookings/<uuid:pk>/reject/    staff:booking-reject   (POST)
/appointments/staff/bookings/<uuid:pk>/complete/  staff:booking-complete (POST)
/appointments/staff/bookings/<uuid:pk>/no-show/   staff:booking-no-show  (POST)
/appointments/staff/bookings/<uuid:pk>/notes/     staff:booking-notes    (POST)
/appointments/staff/slots/create/                 staff:slot-create
/appointments/staff/slots/<uuid:pk>/block/        staff:slot-block       (POST)
/appointments/staff/slots/<uuid:pk>/cancel/       staff:slot-cancel      (POST)
/appointments/staff/availability/                 staff:availability-manage
/appointments/staff/queue/                        staff:queue-view  (real-time, SSE)
/appointments/staff/queue/call/<uuid:pk>/         staff:queue-call   (POST)
/appointments/staff/walkin/                       staff:walkin-create
```

### 13.3 Admin Views

```
/appointments/admin/service-types/                admin:service-type-list
/appointments/admin/appointment-types/            admin:appointment-type-list
/appointments/admin/locations/                    admin:location-list
/appointments/admin/policies/                     admin:policy-list
/appointments/admin/reports/                      admin:reports
/appointments/admin/no-show-records/              admin:no-show-records  (review flagged citizens)
```

### 13.4 API Endpoints (DRF)

```
/api/v1/appointments/slots/                       GET  — available slots
/api/v1/appointments/bookings/                    GET, POST
/api/v1/appointments/bookings/<uuid>/             GET, PUT, DELETE
/api/v1/appointments/bookings/<uuid>/confirm/     POST  (staff)
/api/v1/appointments/bookings/<uuid>/cancel/      POST
/api/v1/appointments/waitlist/                    GET, POST
/api/v1/appointments/waitlist/<uuid>/accept/      POST
```

---

## 14. Template Architecture

```
apps/appointments/templates/appointments/
├── base.html                          # extends documents/base.html
├── citizen/
│   ├── dashboard.html                 # upcoming bookings, past, waitlist
│   ├── service_list.html
│   ├── service_detail.html
│   ├── book_step1_datetime.html       # WCAG: accessible date picker (ARIA grid pattern)
│   ├── book_step2_slots.html          # slot cards with time zone display
│   ├── book_step3_details.html        # intake form + consent
│   ├── book_step4_confirm.html        # review summary
│   ├── booking_detail.html            # single booking with cancel/reschedule actions
│   ├── booking_cancel_confirm.html
│   ├── booking_reschedule.html
│   └── booking_join.html              # virtual join page (auth-gated; shows join link)
├── staff/
│   ├── dashboard.html                 # today's bookings, pending confirmations
│   ├── calendar.html                  # week view
│   ├── booking_list.html
│   ├── booking_detail.html
│   ├── booking_confirm.html
│   ├── availability_manage.html
│   ├── queue_view.html                # real-time queue (SSE or polling)
│   └── walkin_create.html
├── partials/
│   ├── _booking_card.html
│   ├── _slot_picker.html              # JS-enhanced slot time grid
│   ├── _date_picker.html              # ARIA grid date picker
│   ├── _booking_status_badge.html     # role="status" on all span elements
│   ├── _waitlist_position.html
│   └── _queue_number.html
└── email/
    ├── appointment_booked/
    │   ├── subject_en.txt, subject_fr.txt
    │   ├── body_en.html, body_fr.html
    │   └── body_en.txt, body_fr.txt
    ├── appointment_confirmed/          (same structure × 10 templates)
    ├── appointment_cancelled/
    ├── appointment_reminder_72h/
    ├── appointment_reminder_24h/
    ├── appointment_reminder_2h/
    ├── appointment_no_show/
    ├── appointment_rescheduled/
    ├── waitlist_slot_available/
    └── waitlist_expired/
```

---

## 15. Admin Interface

```python
# apps/appointments/admin.py

@admin.register(ServiceType)
class ServiceTypeAdmin(admin.ModelAdmin):
    list_display = ["name_en", "category", "sector", "privacy_sensitivity", "is_active"]
    list_filter = ["category", "sector", "is_active"]
    prepopulated_fields = {"slug": ["name_en"]}

@admin.register(AppointmentType)
class AppointmentTypeAdmin(admin.ModelAdmin):
    list_display = ["name_en", "service_type", "duration_minutes", "mode", "capacity_per_slot", "is_active"]
    list_filter = ["mode", "requires_payment", "requires_document_upload", "is_active"]

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["pk", "citizen_pk", "slot_start", "status", "no_show", "booking_channel"]
    list_filter = ["status", "no_show", "appointment_mode", "booking_channel"]
    readonly_fields = ["pk", "citizen", "slot", "form_responses", "created_at", "updated_at",
                       "rescheduled_from", "reschedule_count", "consent_recorded_at"]
    # PII GATE: never display citizen email directly in list view
    def citizen_pk(self, obj): return str(obj.citizen_id)
    def slot_start(self, obj): return obj.slot.start_datetime
    # Booking.form_responses may contain PII — render behind permission check
    def get_readonly_fields(self, request, obj=None):
        if not request.user.has_perm("appointments.view_booking_form_responses"):
            return self.readonly_fields + ["form_responses", "accessibility_needs",
                                           "interpreter_language", "internal_notes"]
        return self.readonly_fields

@admin.register(ClientNoShowRecord)
class ClientNoShowRecordAdmin(admin.ModelAdmin):
    list_display = ["citizen_pk", "no_show_count", "late_cancellation_count",
                    "is_flagged", "is_suspended"]
    list_filter = ["is_flagged", "is_suspended"]
    actions = ["clear_flag", "clear_suspension"]
    # Staff must have explicit permission to view or modify
    def has_module_perms(self, request): return request.user.has_perm("appointments.manage_no_shows")
```

---

## 16. REST API (DRF)

All endpoints follow the existing CivicOS API conventions (GovStack error envelope, JWT auth, throttling):

```python
# Slot availability
GET /api/v1/appointments/slots/?appointment_type=<slug>&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
Response: {
    "results": [
        {
            "slot_id": "uuid",
            "start_datetime": "2026-07-15T14:00:00Z",
            "start_local": "2026-07-15T10:00:00-04:00",
            "timezone": "America/Toronto",
            "timezone_abbr": "EDT",
            "end_datetime": "2026-07-15T14:30:00Z",
            "available_spaces": 1,
            "waitlist_count": 0,
            "staff_display_name": "...",
            "location_name": "...",
            "mode": "in_person"
        }
    ]
}

# Create booking
POST /api/v1/appointments/bookings/
Body: {slot_id, appointment_mode, form_responses, interpreter_needed, accessibility_needs}
Response: {booking_id, status, ...}

# Cancel booking
POST /api/v1/appointments/bookings/<uuid>/cancel/
Body: {reason}
```

---

## 17. Accessibility — WCAG 2.1 AA

### 17.1 Date Picker ARIA Pattern

The booking date picker uses the ARIA grid pattern (mandatory, not optional):

```html
<!-- Date picker popup -->
<div role="dialog" aria-modal="true" aria-label="{% trans 'Choose appointment date' %}">
  <div role="grid" aria-label="{{ month_name }}">
    <div role="row">
      {% for day_abbr in day_headers %}
        <div role="columnheader" aria-label="{{ day_abbr.full }}">{{ day_abbr.short }}</div>
      {% endfor %}
    </div>
    {% for week in calendar_weeks %}
    <div role="row">
      {% for day in week %}
        <div role="gridcell">
          <button
            aria-label="{{ day.aria_label }}"  {# "Tuesday, July 14, 2026" #}
            aria-selected="{{ day.is_selected|yesno:'true,false' }}"
            aria-disabled="{{ day.is_unavailable|yesno:'true,false' }}"
            {% if day.is_unavailable %}disabled{% endif %}
            tabindex="{{ day.is_focused|yesno:'0,-1' }}"
          >{{ day.number }}</button>
        </div>
      {% endfor %}
    </div>
    {% endfor %}
  </div>
</div>
```

Keyboard navigation: Arrow keys move between dates; Enter selects; Escape closes; Home/End go to start/end of week; PageUp/PageDown change month.

### 17.2 Time Zone Display

Every slot display MUST show:
```html
<!-- Good: explicit timezone -->
<time datetime="{{ slot.start_datetime|date:'c' }}">
  {{ slot.start_local|time:"g:i A" }} {{ slot.timezone_abbr }}
  <small>(UTC{{ slot.utc_offset }})</small>
</time>
```

Never show times without timezone. Canada spans 6 time zones (NST through PDT).

### 17.3 Status Badge

```html
<!-- All booking status badges -->
<span role="status" class="badge badge-{{ booking.status }}">
  {% trans booking.get_status_display %}
</span>
```

### 17.4 Bilingual Requirements

All booking flow strings: translated via Django `gettext`. All `aria-label`, `aria-describedby`, `placeholder` attributes must have translations. Confirmation emails sent in `booking.language` (EN or FR, chosen at booking time). Language toggle in header per GC Design System — `aria-label` in the **target language** (not current).

### 17.5 Forms — `aria-describedby` Wiring

Every form input has a corresponding `aria-describedby` pointing to its hint text and error message:
```html
<input id="id_{{ field.name }}" aria-describedby="hint_{{ field.name }} error_{{ field.name }}" ...>
<div id="hint_{{ field.name }}" class="field-hint">{{ field.help_text }}</div>
{% if field.errors %}
<div id="error_{{ field.name }}" role="alert" class="field-error">{{ field.errors.0 }}</div>
{% endif %}
```

---

## 18. Privacy and Compliance

### 18.1 PII in Booking Records

| Field | Sensitivity | Handling |
|-------|-------------|---------|
| `citizen` FK | Personal identity | Required; access-gated |
| `form_responses` | May contain Protected B (health, immigration) | Permission-gated in admin; field-level encryption for high-classification types |
| `accessibility_needs` | Sensitive (disability) | Permission-gated; Quebec Law 25 requires express consent |
| `interpreter_language` | May reveal ethnicity | Permission-gated; minimal retention |
| `internal_notes` | Staff-only | Never shown to citizen |
| `video_join_url_citizen` | Security-sensitive | Delivered inside authenticated session only; never in plain email |
| `no_show_count` | Behavioural profile | Access-gated; non-punitive flag per service |

### 18.2 Data Collection Minimization

At booking time, collect **only**:
- Contact info (phone/email — already on User record)
- Preferred language (already on User record)
- Appointment type and slot
- Intake form responses (only what the specific service requires)
- Accessibility accommodation flag (yes/no at booking; details collected at appointment)

**Do NOT collect** at booking time:
- Government ID numbers / UCI / SIN / health card numbers (collect at appointment)
- Date of birth (already on User record if applicable)
- Biometrics data
- Medical history (except for scheduling-essential flags)

### 18.3 Privacy Notice (Section 5 / PIPEDA Principle 3)

Each booking page displays a Privacy Notice before data collection begins, including:
- Purpose of collection
- Legal authority (government deployments) or voluntary/contractual basis (NGO)
- ATIP / access request coordinator contact
- Retention period
- Link to full Privacy Policy

### 18.4 Retention and Auto-Purge

| Data Type | Retention Period | Action |
|-----------|-----------------|--------|
| Active bookings | Duration of service + 2 years | Retain |
| Completed bookings | 7 years (Privacy Act s. 6 / TBS) | Retain |
| Cancelled bookings | 1 year | Retain then anonymize |
| BookingAuditLog | 7 years | Retain (ATIA) |
| Video join URLs | Until appointment end + 1 hour | Auto-null |
| form_responses (health data) | Per PHIPA / provincial statute | Configurable per ServiceType |
| ClientNoShowRecord | 2 years from last no-show | Auto-purge |
| QueueEntry | 90 days | Auto-purge |

Implemented via Celery beat task `purge_expired_booking_data` (monthly) that anonymizes or deletes records past their retention window, writes `BookingAuditLog(action='data_purged')`.

### 18.5 Anonymous Booking Mode

For harm-reduction, legal aid, and mental health services where identification creates a barrier:

```python
# AppointmentType flag
allow_anonymous_booking = models.BooleanField(default=False)
```

When enabled: citizen can book with only an email or phone (no account required). `booking.citizen` is nullable. An `AnonymousBookingToken` (UUID + expiry) is generated and emailed. The citizen uses the token to manage their booking without login.

### 18.6 Quebec Law 25 — Express Consent for Sensitive Fields

`accessibility_needs` and `interpreter_language` are sensitive personal information under Quebec Law 25. For Quebec-deployed locations (`privacy_regime='law25'`):
- A separate, granular consent checkbox is displayed for each sensitive field
- Pre-ticked boxes are not permitted
- Consent is stored as a `ConsentRecord`

---

## 19. Security Constraints

All constraints from prior BBs carry forward. Additional constraints:

1. **Video join URL NEVER in unauthenticated email.** Delivered only inside authenticated portal session. If citizen is not logged in when clicking notification, redirect to login then back to `/appointments/booking/<uuid>/join/`.
2. **IDOR prevention:** Citizens receive 404 (not 403) for booking PKs they do not own. Staff views restrict to bookings within their location.
3. **Slot capacity race condition:** `SELECT FOR UPDATE` on Slot row inside `atomic()` before incrementing `spaces_used`. Rejection returns HTTP 409 Conflict with offer to join waitlist.
4. **Intake form XSS:** `form_responses` rendered via `{{ response|force_escape }}` in all templates. Staff notes rendered via `{{ note|linebreaks|force_escape }}`.
5. **No PII in audit `event_detail`:** booking audit detail contains only slugs and UUIDs, not names, emails, or form contents.
6. **No PII in logs:** use `booking.citizen_id` (UUID), not `citizen.email`, in all log messages.
7. **`LoginRequiredMixin` before `PermissionRequiredMixin`** in all class-based view MRO.
8. **`raise_exception = True`** on all staff views with `PermissionRequiredMixin`.
9. **`record_event()` inside `atomic()`** for every booking lifecycle event (PIPEDA 4.5.3).
10. **`storage_key` never in templates, API responses, or logs** (inherited from Documents BB).
11. **Consent gate enforced server-side** — not just in JavaScript.
12. **Suspension check before booking** — `ClientNoShowRecord.is_suspended` checked inside `create_booking()` before slot lock.
13. **iCal attachment: `TZID` parameter required** — bare UTC-only DTSTART is insufficient for Canadian multi-timezone deployments.
14. **Jitsi JWT `exp` max 2h** — never issue a non-expiring video token.
15. **Teams `createOrGet` with `externalId=booking.pk`** — idempotent; safe to retry on transient failure.

---

## 20. Configuration (CIVICOS settings)

### 20.1 New CIVICOS Keys

```python
# config/settings/base.py — additions to CIVICOS dict
"APPOINTMENTS": {
    # Booking defaults
    "DEFAULT_SLOT_DURATION_MINUTES": 30,
    "DEFAULT_MIN_LEAD_TIME_HOURS": 1,
    "DEFAULT_MAX_ADVANCE_DAYS": 180,
    "DEFAULT_CANCELLATION_NOTICE_HOURS": 24,
    "DEFAULT_MAX_RESCHEDULE_COUNT": 3,
    "DEFAULT_WAITLIST_ACCEPTANCE_WINDOW_HOURS": 2,
    "DEFAULT_WAITLIST_BATCH_SIZE": 3,

    # Slot generation
    "SLOT_GENERATION_HORIZON_DAYS": 60,  # Generate slots this far ahead

    # Pending booking timeout (minutes) — if no payment/document received
    "PENDING_BOOKING_TIMEOUT_MINUTES": 30,

    # Video conference
    "TEAMS_TENANT_ID": env("TEAMS_TENANT_ID", default=""),
    "TEAMS_CLIENT_ID": env("TEAMS_CLIENT_ID", default=""),
    "TEAMS_CLIENT_SECRET": env("TEAMS_CLIENT_SECRET", default=""),
    "ZOOM_ACCOUNT_ID": env("ZOOM_ACCOUNT_ID", default=""),
    "ZOOM_CLIENT_ID": env("ZOOM_CLIENT_ID", default=""),
    "ZOOM_CLIENT_SECRET": env("ZOOM_CLIENT_SECRET", default=""),
    "JITSI_DOMAIN": env("JITSI_DOMAIN", default="meet.civicos.ca"),
    "JITSI_SECRET": env("JITSI_SECRET", default=""),

    # Retention (days)
    "BOOKING_RETENTION_DAYS": 2555,         # 7 years (completed)
    "CANCELLED_BOOKING_RETENTION_DAYS": 365, # 1 year then anonymize
    "NO_SHOW_RECORD_RETENTION_DAYS": 730,    # 2 years
    "QUEUE_ENTRY_RETENTION_DAYS": 90,
    "VIDEO_URL_EXPIRY_MINUTES": 60,          # null video_join_url 60min after slot end

    # SMS (Twilio)
    "TWILIO_ACCOUNT_SID": env("TWILIO_ACCOUNT_SID", default=""),
    "TWILIO_AUTH_TOKEN": env("TWILIO_AUTH_TOKEN", default=""),
    "TWILIO_FROM_NUMBER": env("TWILIO_FROM_NUMBER", default=""),
    "SMS_ENABLED": env.bool("APPOINTMENTS_SMS_ENABLED", default=False),
    "SMS_QUIET_HOURS_START": 21,             # 9 PM
    "SMS_QUIET_HOURS_END": 8,               # 8 AM (recipient's local timezone)
},
```

### 20.2 New Celery Route

```python
"apps.appointments.tasks.*": {"queue": "appointments"},
```

### 20.3 New Consent Category Fixture

```python
# apps/appointments/fixtures/consent_categories.json
[
    {
        "model": "consent.consentcategory",
        "fields": {
            "slug": "appointment_data_processing",
            "name_en": "Appointment Data Processing",
            "name_fr": "Traitement des données de rendez-vous",
            "purpose_en": "Collection and use of your personal information to book, manage, and deliver the appointment you have requested.",
            "purpose_fr": "Collecte et utilisation de vos renseignements personnels pour réserver, gérer et fournir le rendez-vous que vous avez demandé.",
            "lawful_basis": "consent",
            "is_required": true,
            "is_active": true
        }
    }
]
```

---

## 21. Migrations

| Migration | Description |
|-----------|-------------|
| `0001_initial` | `ServiceType`, `AppointmentType`, `SchedulingPolicy`, `Location`, `Resource`, `StaffProfile` |
| `0002_availability` | `AvailabilityTemplate`, `StaffException` |
| `0003_slots` | `Slot` with all indexes and CheckConstraints |
| `0004_bookings` | `Booking`, `Attendee`, `BookingAuditLog` |
| `0005_waitlist_queue` | `WaitlistEntry`, `QueueEntry`, `ClientNoShowRecord` |
| `0006_seed_default_policy` | Data migration: creates `SchedulingPolicy(name="default")` with sensible defaults |
| `0007_seed_consent_category` | Data migration: creates `ConsentCategory(slug="appointment_data_processing")` |
| `0008_add_audit_event_types` | Data migration: registers new AuditEventType strings in the audit app |

---

## 22. Test Requirements

### 22.1 Coverage Targets

| Module | Target |
|--------|--------|
| `services/availability.py` | ≥ 95% |
| `services/booking.py` | ≥ 95% |
| `services/waitlist.py` | ≥ 95% |
| `services/video.py` | ≥ 90% (mocked external calls) |
| `tasks.py` | ≥ 92% |
| `views/citizen.py` | ≥ 90% |
| `views/staff.py` | ≥ 85% |
| `models.py` | ≥ 95% |

### 22.2 Required Test Classes (minimum)

```
test_models.py
  SlotConstraintTests           — spaces_used ≤ capacity; end > start
  BookingAuditLogImmutabilityTests — save() raises on existing pk; delete() always raises
  ClientNoShowRecordTests

test_services_booking.py
  CreateBookingHappyPathTests
  CreateBookingCapacityTests    — race condition: concurrent bookings on last space
  CreateBookingSuspendedCitizenTests
  CreateBookingFrequencyControlTests
  RescheduleBookingTests        — new record created; old cancelled; chain preserved
  CancelBookingLateCancellationTests
  MarkNoShowTests
  WaitlistJoinAndPromoteTests
  WaitlistBatchPromoteTests     — batch of 3, fill rate
  WaitlistAutoPromoteOnCancelTests

test_services_availability.py
  SlotGenerationTests           — weekday template, buffers, interval grid
  StaffExceptionTests           — HOLIDAY blocks; OVERRIDE applies custom hours
  BookingWindowTests            — min_lead_time; max_advance_days wall-clock enforcement
  FrequencyControlTests         — booking_frequency_days (food bank pattern)
  DSTHandlingTests              — ensure UTC slot times correct across DST boundary
  MultiTimezoneTests            — location in Vancouver; citizen in Toronto

test_signals.py
  BookingCreatedSignalTests     — WorkItem created via on_commit
  BookingConfirmedSignalTests   — notification dispatched; reminders scheduled
  BookingCancelledSignalTests   — reminder tasks revoked; waitlist promoted

test_tasks.py
  ReminderIdempotencyTests      — atomic filter/update guard; not sent twice
  DetectNoShowTests
  GenerateSlotsTests
  CleanupExpiredPendingTests

test_views_citizen.py
  BookingFlowTests              — 4-step flow; consent gate; fee gate; document gate
  BookingDetailIDORTests        — citizen gets 404 for other citizen's booking
  VirtualJoinAuthGatedTests     — join link not accessible unauthenticated
  CancelWithinWindowTests
  CancelOutsideWindowTests      — late_cancellation=True
  RescheduleCountLimitTests

test_views_staff.py
  StaffDashboardScopeTests      — staff only sees bookings at their location
  ConfirmBookingTests
  RejectBookingTests
  NoShowMarkingTests
  QueueManagementTests

test_pipeda.py
  NoEmailInLogsTests
  NoStorageKeyInResponseTests
  ConsentGateTests
  FormResponsePIIGateTests      — admin cannot see form_responses without permission
  VideoURLAuthGateTests

test_wcag.py
  DatePickerARIATests           — role=gridcell; aria-selected; aria-disabled present
  StatusBadgeRoleTests          — all status badges have role="status"
  TimeZoneDisplayTests          — timezone abbreviation present on all time displays
  BilingualTests                — all ARIA labels translateable

test_integration.py
  PortalToAppointmentFlowTests  — service_request_submitted → booking created
  BookingToWorkItemTests        — confirmed booking → WorkItem with due_at=slot.start
  PaymentGatedBookingTests      — payment_completed → booking confirmed
  DocumentGatedBookingTests     — scan_clean → booking advanced
```

---

## 23. File Structure

```
apps/appointments/
├── __init__.py
├── apps.py
├── admin.py
├── forms.py
├── models.py
├── signals.py
├── receivers.py
├── tasks.py
├── urls.py
├── services/
│   ├── __init__.py
│   ├── availability.py
│   ├── booking.py
│   ├── slots.py
│   ├── waitlist.py
│   ├── queue.py
│   └── video.py
├── views/
│   ├── __init__.py
│   ├── citizen.py
│   └── staff.py
├── serializers.py           (DRF)
├── api_views.py             (DRF)
├── migrations/
│   ├── 0001_initial.py
│   ├── 0002_availability.py
│   ├── 0003_slots.py
│   ├── 0004_bookings.py
│   ├── 0005_waitlist_queue.py
│   ├── 0006_seed_default_policy.py
│   ├── 0007_seed_consent_category.py
│   └── 0008_add_audit_event_types.py
├── fixtures/
│   └── consent_categories.json
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_services_booking.py
│   ├── test_services_availability.py
│   ├── test_signals.py
│   ├── test_tasks.py
│   ├── test_views_citizen.py
│   ├── test_views_staff.py
│   ├── test_pipeda.py
│   ├── test_wcag.py
│   └── test_integration.py
├── templates/appointments/
│   ├── base.html
│   ├── citizen/           (9 templates)
│   ├── staff/             (10 templates)
│   ├── partials/          (6 partials)
│   └── email/             (10 template groups × 6 files each)
└── templatetags/
    ├── __init__.py
    └── appointment_tags.py   # |local_time, |timezone_abbr, |booking_status_class
```

---

## 24. Implementation Waves

### Wave 1 — Foundation (Models + Config)

- Models: `ServiceType`, `AppointmentType`, `SchedulingPolicy`, `Location`, `Resource`, `StaffProfile`
- Migrations 0001
- Settings wiring (`CIVICOS["APPOINTMENTS"]`, Celery queue)
- Admin (read-only display)
- Test: `test_models.py` (model constraints, field defaults)

### Wave 2 — Availability Engine

- Models: `AvailabilityTemplate`, `StaffException`, `Slot`
- Migrations 0002–0003
- `services/availability.py` — `SlotAvailabilityService`
- `services/slots.py` — `generate_slots_for_range()`
- `tasks.py` — `generate_slots_for_period`, `mark_past_slots_completed`
- Test: `test_services_availability.py` (slot generation, DST, frequency controls)

### Wave 3 — Core Booking Engine

- Models: `Booking`, `Attendee`, `BookingAuditLog`, `ClientNoShowRecord`
- Migrations 0004–0005
- `services/booking.py` — all booking lifecycle functions
- `signals.py` + `receivers.py` — WorkItem creation, notification dispatch
- Migrations 0006–0008 (seed data)
- Test: `test_services_booking.py`, `test_signals.py`

### Wave 4 — Waitlist + Queue

- `WaitlistEntry`, `QueueEntry` (already in migration 0005)
- `services/waitlist.py` — join, promote, expire
- `services/queue.py` — walk-in queue management
- `tasks.py` — `expire_waitlist_notifications`, `detect_no_shows`, `cleanup_expired_pending_bookings`
- Test: `test_tasks.py` + waitlist/queue coverage in `test_services_booking.py`

### Wave 5 — Views + Templates

- `views/citizen.py` — 4-step booking flow, booking management
- `views/staff.py` — staff dashboard, confirmation, no-show marking
- `forms.py`
- `urls.py`
- All templates (citizen + staff + partials)
- Test: `test_views_citizen.py`, `test_views_staff.py`

### Wave 6 — Notifications + iCal + Reminders

- Email templates (10 groups × EN/FR)
- iCal attachment generation
- `tasks.py` — all reminder tasks (72h, 24h, 2h)
- SMS integration (Twilio) if `SMS_ENABLED`
- Beat schedule wiring
- Test: `test_tasks.py` reminder idempotency

### Wave 7 — Virtual Appointments

- `services/video.py` — Teams, Zoom, Jitsi dispatch
- `/appointments/booking/<uuid>/join/` view (auth-gated)
- Video join URL null-out task
- Test: `test_services_video.py` (all mocked)

### Wave 8 — DRF API + Integration Tests

- `serializers.py`, `api_views.py`
- API URL wiring
- Test: `test_pipeda.py`, `test_wcag.py`, `test_integration.py`

### Wave 9 — Review, Hardening, Tag

- 4-parallel adversarial review agents
- Fix findings
- Full test suite run (target: 300+ new tests; overall suite: 1,100+)
- Git tag `v0.9.0`
- Update `PROJECT_OVERVIEW_AND_STATUS.md` and `CODEBASE_HANDOFF_FOR_AI.md`

---

## References

- GovStack Scheduler BB v1.0.1: https://scheduler.govstack.global/
- TM Forum TMF646 Appointment API R19.0.0
- RFC 5545 iCalendar: https://datatracker.ietf.org/doc/html/rfc5545
- Service BC Appointment System: https://appointments.servicebc.gov.bc.ca/
- IRCC Interview Facilitation Service PIA: https://www.canada.ca/en/immigration-refugees-citizenship/corporate/transparency/access-information-privacy/privacy-impact-assessment/interview-facilitation-service.html
- RDV Service Public (France): https://github.com/betagouv/rdv-service-public
- eAppointment (Munich): https://github.com/it-at-m/eappointment
- Cal.com Architecture: https://deepwiki.com/calcom/cal.com
- WCAG 2.1 AA: https://www.w3.org/TR/WCAG21/
- GC Design System: https://design-system.canada.ca/en/
- TBS Policy on Service and Digital: https://www.tbs-sct.canada.ca/pol/doc-eng.aspx?id=32603
- Privacy Act (RSC 1985, c. P-21): https://laws-lois.justice.gc.ca/eng/acts/P-21/
- PIPEDA: https://laws-lois.justice.gc.ca/eng/acts/P-8.6/
- Quebec Law 25 (Bill 64): https://www.cai.gouv.qc.ca/
- NHS DNA Rate Reduction Study (2024): https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12242212/

# Volunteer Management Building Block — Specification

**Status:** Draft — ready for implementation  
**Last updated:** 2026-07-01  
**Author:** CivicOS core team  
**Conformance:** CivicOS custom BB. Aligned with:
- [Volunteer Canada — Canadian Code for Volunteer Involvement (CCVI), 2017 edition](https://volunteer.ca/canadian-code-for-volunteer-involvement/)
- [Public Safety Canada — Best Practice Guidelines for Screening Volunteers](https://www.publicsafety.gc.ca/cnt/rsrcs/pblctns/bpg-scrng-vls/index-en.aspx)
- [RCMP — Vulnerable Sector Checks](https://rcmp.ca/en/criminal-records/criminal-record-checks/vulnerable-sector-checks)
- [CRA — Policy Commentary PC-025: Expenses Incurred by Volunteers](https://www.canada.ca/en/revenue-agency/services/charities-giving/charities/policies-guidance/policy-commentary-025-expenses-incurred-volunteers.html)
- [Office of the Privacy Commissioner — PIPEDA Fair Information Principles](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/p_principle/)

---

## 1. Purpose

Enable nonprofit and government organizations on CivicOS to recruit, onboard, schedule, and recognize volunteers in a PIPEDA-aligned, WCAG 2.1 AA-accessible, bilingual (EN/FR) system. The building block covers the full volunteer lifecycle:

```
Recruitment → Application → Screening → Onboarding → Scheduling → Hours Logging → Recognition → Offboarding
```

The BB integrates with:
- **Payments BB** — honoraria and expense reimbursements
- **Analytics & Reporting BB** — volunteer hours by program, impact metrics for funder reporting
- **Workflows BB** — application approval and onboarding workflow routing
- **Notifications BB** — shift reminders, application status emails, certification expiry alerts
- **Consent BB** — volunteer agreement, waiver, and photo-consent capture
- **Auth BB** — volunteer portal login; role-based access for coordinators

---

## 2. Scope

### In scope

- Volunteer profile management (contact info, skills, availability, emergency contacts)
- Opportunity creation and management (roles, locations, required skills, age restrictions)
- Shift scheduling (recurring shifts, sign-up, waitlists, capacity enforcement)
- Application and approval workflow (multi-step; integrates with Workflows BB)
- Background check / screening tracking (Vulnerable Sector Check, police record check, references)
- Hours logging and coordinator approval
- Certification and training record tracking with expiry alerts
- Honoraria and expense reimbursement integration with Payments BB
- Impact reporting (hours × provincial minimum wage, in-kind value estimates)
- Volunteer self-service portal (sign up, manage schedule, view hours, download letters)
- Bilingual volunteer-facing UI (EN/FR via Django i18n + Wagtail locale system)
- CRA T3010-relevant volunteer metrics (total hours, program breakdown)
- WCAG 2.1 AA throughout

### Out of scope (V1)

- Native mobile app (responsive web only)
- External volunteer marketplace / job-board syndication
- Automated reference check integrations (flag required; manual follow-up)
- RCMP direct API integration for Vulnerable Sector Checks (VSC must be obtained by volunteer from local police; system tracks receipt date, expiry, and verification status)
- Payroll processing for stipends above CRA honorarium threshold (refer to HR system)
- Multi-tenancy beyond single-organization deployment

---

## 3. Canadian Compliance Context

### 3.1 Canadian Code for Volunteer Involvement (CCVI)

The [CCVI](https://volunteer.ca/canadian-code-for-volunteer-involvement/) defines 10 Standards of Practice that CivicOS Volunteer Management supports:

| CCVI Standard | CivicOS Support |
|---|---|
| Mission-Based Approach | `Opportunity.program` links volunteering to organizational programs |
| HR & Program Planning | Role descriptions, capacity limits, and skill requirements on `Opportunity` |
| Policies & Procedures | Policy acknowledgement via Consent BB (volunteer agreement field) |
| Volunteer Assignments | Structured role descriptions with EN/FR descriptions on `Opportunity` |
| Recruitment | Opportunity posting on public-facing portal; application form |
| Screening | `ScreeningRecord` model; Vulnerable Sector Check, reference tracking |
| Orientation & Training | Onboarding workflow via Workflows BB; `Certification` records |
| Supervision | `Shift` coordinator field; hours require coordinator approval |
| Retention | Recognition milestones; anniversary notifications |
| Evaluation | Impact reports; program-level hours aggregation |

### 3.2 CRA: Volunteer vs. Employee Distinction

This is the most critical compliance risk. CRA guidance on [PC-025](https://www.canada.ca/en/revenue-agency/services/charities-giving/charities/policies-guidance/policy-commentary-025-expenses-incurred-volunteers.html) and related guidance establishes:

**Volunteers** receive no compensation or only nominal compensation. Expense reimbursement for out-of-pocket costs is not taxable. Honoraria ≤ $500/year (calendar year, across all payers) are generally non-taxable. Amounts above $500/year must be reported on a T4A slip and may attract source deductions if the relationship is found to be employment.

| Payment type | CRA treatment | CivicOS action |
|---|---|---|
| Expense reimbursement (receipts) | Not taxable; not income | `Honorarium.payment_type = EXPENSE_REIMBURSEMENT` — no T4A threshold tracking required |
| Honorarium ≤ $500 cumulative in calendar year | Generally non-taxable; issued T4A is recipient's responsibility | `Honorarium` model tracks cumulative per volunteer per year; alert at $450 threshold |
| Honorarium > $500 cumulative in calendar year | Taxable; T4A required; potential employment relationship risk | System flags for payroll review; **CivicOS does not remit source deductions** — coordinator must escalate to HR/payroll |
| Stipend (regular scheduled payment) | Employment income — source deductions apply | Out of scope; system warns coordinator and refuses to process |

**Design rule:** The `Honorarium` model enforces a hard maximum of `$1,000` per volunteer per calendar year. Above that threshold the system refuses to create the record and instructs the coordinator to route through payroll.

### 3.3 Vulnerable Sector Check (VSC)

The [RCMP VSC](https://rcmp.ca/en/criminal-records/criminal-record-checks/vulnerable-sector-checks) applies to positions of trust or authority over children or vulnerable persons. Key facts:

- No online vendor can perform a VSC — the volunteer must attend their local police detachment in person.
- The check is granted to the volunteer, not the organization. The volunteer presents the result to the organization.
- Organizations must determine whether a role requires a VSC based on the nature of contact (unsupervised access to children or vulnerable adults → VSC required).
- Check results have a practical shelf life (typically 1–3 years depending on organizational policy; CivicOS defaults to 3 years).

**CivicOS approach:** `ScreeningRecord.check_type = VULNERABLE_SECTOR_CHECK`. The system stores the check date, expiry, and verification status (verified by coordinator yes/no). The actual criminal record result is **never stored** — only the fact of a verified clear check. This is the minimum data required for the organization's due diligence record.

### 3.4 PIPEDA and Volunteer Privacy

Per the [Office of the Privacy Commissioner](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/r_o_p/02_05_d_19/), nonprofits are generally exempt from PIPEDA for non-commercial activities, but the PIPEDA fair information principles represent the standard a reasonable organization should meet.

Volunteer data includes sensitive categories:

| Data element | Sensitivity | Storage rule |
|---|---|---|
| Name, email, phone | Standard PII | Encrypted at rest (field-level if required by org policy); never in logs |
| Date of birth | Moderate (age verification) | Stored only if role requires age verification (e.g., 18+ only) |
| Emergency contact name + phone | Moderate | Stored; accessible to coordinators only |
| SIN / social insurance number | **Sensitive** — required for T4A issuance if honorarium > $500 | Stored **only** after coordinator triggers T4A workflow; encrypted at rest; masked in UI |
| Health/accommodation notes | **Sensitive** | Stored in `VolunteerProfile.accommodation_notes`; role-gated; audit logged on every read |
| Criminal record check result | **Sensitive** | **Never stored** — only check date, verified-by-coordinator flag, and expiry |
| Photo | Standard PII | Requires explicit photo-consent via Consent BB before upload |
| Emergency contact relationship | Low | Stored |

**Minimum collection principle:** the application form only collects what is required for the role. `Opportunity` has a field `required_profile_fields` (JSON list) that the form builder respects. A role not requiring age verification cannot prompt for date of birth.

---

## 4. Consumers & Permissions

| Role | Django group | Key permissions |
|---|---|---|
| Volunteer | `volunteer` | View public opportunities; manage own application, shifts, hours; view own profile |
| Volunteer Coordinator | `volunteer_coordinator` | Manage opportunities, shifts, applications for assigned programs; approve hours; view screening status |
| Volunteer Administrator | `volunteer_admin` | Full access to all programs; manage certifications; export reports; access accommodation notes |
| Backoffice Staff | `staff` | Read-only view of volunteer hours for service request context |
| Superuser | — | All of the above |

Permission codenames follow the pattern `volunteers.<action>_<model>`:

- `volunteers.view_volunteerprofile`
- `volunteers.change_volunteerprofile`
- `volunteers.view_accommodation_notes` (separate permission — read-only field gate)
- `volunteers.add_opportunity`, `volunteers.change_opportunity`, `volunteers.delete_opportunity`
- `volunteers.add_shift`, `volunteers.change_shift`
- `volunteers.approve_hourslog`
- `volunteers.view_screeningrecord`, `volunteers.add_screeningrecord`
- `volunteers.view_honorarium`, `volunteers.add_honorarium`
- `volunteers.export_volunteer_report`

> **MRO rule (global invariant):** `LoginRequiredMixin` always before `PermissionRequiredMixin` in all class-based views.

---

## 5. Architecture

### 5.1 New app: `apps/volunteers`

```
apps/volunteers/
├── __init__.py
├── apps.py
├── models.py              # All data models (see §6)
├── admin.py               # Coordinator-facing admin with field-level PII gates
├── forms.py               # Application form, shift sign-up form, hours log form
├── services/
│   ├── __init__.py
│   ├── applications.py    # apply(), withdraw(), approve_application(), reject_application()
│   ├── scheduling.py      # create_shift(), book_shift(), cancel_booking(), fill_waitlist()
│   ├── hours.py           # log_hours(), approve_hours(), reject_hours(), monthly_summary()
│   ├── screening.py       # record_check(), check_expiring_soon(), get_overdue_checks()
│   ├── honoraria.py       # create_honorarium(), validate_cra_threshold(), cumulative_ytd()
│   └── reporting.py       # hours_by_program(), impact_value(), t3010_volunteer_metrics()
├── signals.py             # application_submitted, hours_approved, shift_cancelled, etc.
├── receivers.py           # Wire signals to Notifications BB
├── tasks.py               # Celery tasks (see §10)
├── views/
│   ├── __init__.py
│   ├── portal.py          # Volunteer self-service views
│   ├── coordinator.py     # Coordinator management views
│   └── admin_views.py     # Admin-only views (exports, screening, honoraria)
├── serializers.py         # DRF serializers for API BB
├── urls.py
├── wagtail_hooks.py       # Inject volunteer sidebar into backoffice
├── templates/
│   └── volunteers/
│       ├── portal/
│       │   ├── dashboard.html
│       │   ├── opportunity_list.html
│       │   ├── opportunity_detail.html
│       │   ├── application_form.html
│       │   ├── application_status.html
│       │   ├── my_schedule.html
│       │   ├── shift_signup.html
│       │   ├── my_hours.html
│       │   ├── log_hours_form.html
│       │   └── profile_edit.html
│       ├── coordinator/
│       │   ├── dashboard.html
│       │   ├── opportunity_form.html
│       │   ├── shift_manage.html
│       │   ├── application_review.html
│       │   ├── hours_approval.html
│       │   └── volunteer_detail.html
│       └── admin/
│           ├── screening_dashboard.html
│           ├── honorarium_form.html
│           └── impact_report.html
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_services_applications.py
│   ├── test_services_scheduling.py
│   ├── test_services_hours.py
│   ├── test_services_screening.py
│   ├── test_services_honoraria.py
│   ├── test_services_reporting.py
│   ├── test_views_portal.py
│   ├── test_views_coordinator.py
│   ├── test_tasks.py
│   └── test_api.py
└── migrations/
    └── 0001_initial.py
```

### 5.2 URL namespace

```
/volunteers/                                → Volunteer portal dashboard
/volunteers/opportunities/                  → Opportunity list (public or login-gated per org config)
/volunteers/opportunities/<slug>/           → Opportunity detail + apply CTA
/volunteers/apply/<opportunity_slug>/       → Application form
/volunteers/my/applications/               → My applications list
/volunteers/my/schedule/                   → My upcoming shifts
/volunteers/my/shifts/<shift_id>/cancel/   → Cancel booking
/volunteers/my/hours/                      → My hours log
/volunteers/my/hours/log/                  → Log hours form
/volunteers/my/profile/                    → Edit profile
/volunteers/my/letters/                    → Download volunteer reference/hours letters

/volunteers/coordinator/                   → Coordinator dashboard
/volunteers/coordinator/opportunities/     → Manage opportunities
/volunteers/coordinator/opportunities/create/       → Create opportunity
/volunteers/coordinator/opportunities/<slug>/edit/  → Edit opportunity
/volunteers/coordinator/shifts/<shift_id>/          → Shift roster
/volunteers/coordinator/applications/              → Review applications
/volunteers/coordinator/applications/<id>/review/  → Approve/reject application
/volunteers/coordinator/hours/                     → Pending hours approvals
/volunteers/coordinator/volunteers/<id>/           → Volunteer detail (coordinator view)

/volunteers/admin/screening/               → Screening dashboard
/volunteers/admin/honoraria/               → Honoraria management
/volunteers/admin/honoraria/create/        → Issue honorarium (triggers Payments BB)
/volunteers/admin/reports/hours/           → Hours by program export
/volunteers/admin/reports/impact/          → Impact value report
```

### 5.3 Integration diagram

```
apps/volunteers/
    │
    ├── → apps/workflows/      (application approval WorkItem creation)
    ├── → apps/notifications/  (shift reminders, status emails, expiry alerts)
    ├── → apps/payments/       (honorarium via PaymentIntent / manual payment record)
    ├── → apps/consent/        (volunteer agreement, waiver, photo consent)
    ├── → apps/reports/        (contributes volunteer hours to ReportSnapshot)
    ├── → apps/audit/          (audit log all coordinator/admin actions)
    └── → apps/api/            (DRF serializers exposed under /api/v1/volunteers/)
```

---

## 6. Data Model

### 6.1 `VolunteerProfile`

One-to-one extension of the `User` model. Created automatically on first volunteer application.

```python
class VolunteerProfile(TimestampedModel):
    """
    PIPEDA note: no PII in __str__. Use profile.pk in all logs and audit records.
    Accommodation notes and emergency contacts are role-gated (volunteers.view_accommodation_notes).
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="volunteer_profile",
    )

    # Contact — duplicated from User for volunteer-specific overrides (e.g., preferred contact)
    preferred_name = models.CharField(max_length=100, blank=True)
    preferred_language = models.CharField(
        max_length=2, choices=[("en", "English"), ("fr", "Français")], default="en"
    )
    phone_number = models.CharField(max_length=20, blank=True)

    # Availability
    availability_notes = models.TextField(blank=True)         # Free text; EN or FR
    available_weekdays = models.BooleanField(default=False)
    available_weekends = models.BooleanField(default=False)
    available_evenings  = models.BooleanField(default=False)

    # Skills and interests (M2M to SkillTag)
    skills = models.ManyToManyField("SkillTag", blank=True, related_name="volunteers")

    # Emergency contact — access requires volunteers.view_accommodation_notes
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_relationship = models.CharField(max_length=50, blank=True)

    # Accommodation — SENSITIVE; access requires volunteers.view_accommodation_notes
    accommodation_notes = models.TextField(
        blank=True,
        help_text="Dietary, mobility, or other accessibility needs. Visible to coordinators only.",
    )

    # SIN — SENSITIVE; populated only when T4A workflow is triggered
    # Encrypted at rest via field-level encryption (cryptography.fernet)
    # Stored as base64-encoded Fernet ciphertext; never logged
    sin_encrypted = models.BinaryField(null=True, blank=True)
    sin_last4 = models.CharField(max_length=4, blank=True)    # For display confirmation only

    # Age verification — only populated when role requires it
    date_of_birth = models.DateField(null=True, blank=True)

    # Photo — requires photo_consent via Consent BB before upload
    photo = models.ImageField(upload_to="volunteers/photos/", null=True, blank=True)
    photo_consent = models.ForeignKey(
        "consent.ConsentRecord",
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name="volunteer_photo_consents",
    )

    # Status
    STATUS_ACTIVE      = "active"
    STATUS_INACTIVE    = "inactive"
    STATUS_SUSPENDED   = "suspended"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_SUSPENDED, "Suspended — contact administrator"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="volunteer_status_changes",
    )

    # Totals — denormalized for quick display; authoritative source is HoursLog
    total_hours_approved = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Volunteer Profile"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        # No PII in __str__ — used in admin list display
        return f"VolunteerProfile #{self.pk}"
```

### 6.2 `SkillTag`

Controlled vocabulary for skills and certifications categories.

```python
class SkillTag(models.Model):
    name_en = models.CharField(max_length=100, unique=True)
    name_fr = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    category = models.CharField(max_length=50, blank=True)  # e.g., "Technical", "Language", "Health"
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name_en"]
```

### 6.3 `Program`

Organizational programs that opportunities belong to. Maps to CRA T3010 program categories.

```python
class Program(TimestampedModel):
    """
    Maps to CRA T3010 charitable program categories.
    Opportunities belong to exactly one Program.
    Hours are aggregated by Program for T3010 volunteer time reporting.
    """
    name_en = models.CharField(max_length=200)
    name_fr = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description_en = models.TextField(blank=True)
    description_fr = models.TextField(blank=True)

    # CRA T3010 charitable program category (Line 5010 categories)
    CRA_CATEGORY_CHOICES = [
        ("welfare", "Welfare of the general public"),
        ("education", "Education"),
        ("health", "Health"),
        ("religion", "Religion"),
        ("other", "Other"),
    ]
    cra_category = models.CharField(max_length=20, choices=CRA_CATEGORY_CHOICES, default="other")

    is_active = models.BooleanField(default=True)
    coordinator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="coordinated_programs",
        limit_choices_to={"groups__name": "volunteer_coordinator"},
    )

    class Meta:
        ordering = ["name_en"]
```

### 6.4 `Opportunity`

A volunteer role with defined responsibilities, requirements, and capacity.

```python
class Opportunity(TimestampedModel):
    """
    A posted volunteer role. Volunteers apply to an Opportunity;
    approved volunteers are then assigned to Shifts within that Opportunity.
    """
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="opportunities")

    title_en = models.CharField(max_length=200)
    title_fr = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description_en = models.TextField()
    description_fr = models.TextField()
    responsibilities_en = models.TextField(blank=True)
    responsibilities_fr = models.TextField(blank=True)

    location_name = models.CharField(max_length=200, blank=True)
    location_address = models.TextField(blank=True)
    is_remote = models.BooleanField(default=False)

    # Requirements
    required_skills = models.ManyToManyField(SkillTag, blank=True, related_name="required_by")
    minimum_age = models.PositiveSmallIntegerField(null=True, blank=True)  # None = no restriction
    requires_vulnerable_sector_check = models.BooleanField(default=False)
    requires_police_record_check = models.BooleanField(default=False)
    requires_reference_check = models.BooleanField(default=False)
    requires_own_vehicle = models.BooleanField(default=False)

    # Profile fields required at application (enforces minimum-collection principle)
    # JSON list: e.g. ["phone_number", "date_of_birth", "emergency_contact_name"]
    required_profile_fields = models.JSONField(default=list, blank=True)

    # Capacity
    volunteer_capacity = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Maximum concurrent active volunteers. Null = unlimited.",
    )

    # Publishing
    STATUS_DRAFT       = "draft"
    STATUS_PUBLISHED   = "published"
    STATUS_CLOSED      = "closed"
    STATUS_ARCHIVED    = "archived"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_CLOSED, "Closed — not accepting applications"),
        (STATUS_ARCHIVED, "Archived"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)

    # Application form: links to Consent BB volunteer agreement template
    volunteer_agreement = models.ForeignKey(
        "consent.ConsentTemplate",
        null=True, blank=True, on_delete=models.PROTECT,
        related_name="opportunity_agreements",
    )

    # Honorarium policy (optional)
    honorarium_per_shift = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Optional honorarium per approved shift (CAD). Subject to CRA $500/year threshold.",
    )

    class Meta:
        ordering = ["-published_at"]
        indexes = [
            models.Index(fields=["status", "program"]),
            models.Index(fields=["slug"]),
        ]

    def __str__(self):
        return self.title_en
```

### 6.5 `VolunteerApplication`

A volunteer's application to an Opportunity.

```python
class VolunteerApplication(TimestampedModel):
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.PROTECT, related_name="applications"
    )
    volunteer = models.ForeignKey(
        VolunteerProfile, on_delete=models.PROTECT, related_name="applications"
    )

    # Motivation / cover letter
    motivation = models.TextField(blank=True)

    # Consent — link to the signed volunteer agreement ConsentRecord
    consent_record = models.ForeignKey(
        "consent.ConsentRecord",
        null=True, blank=True, on_delete=models.PROTECT,
        related_name="volunteer_applications",
    )

    # Screening attestations at application time
    declares_no_relevant_criminal_history = models.BooleanField(default=False)
    screening_notes = models.TextField(blank=True)

    # Application status
    STATUS_PENDING    = "pending"
    STATUS_IN_REVIEW  = "in_review"
    STATUS_APPROVED   = "approved"
    STATUS_REJECTED   = "rejected"
    STATUS_WITHDRAWN  = "withdrawn"
    STATUS_WAITLISTED = "waitlisted"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending review"),
        (STATUS_IN_REVIEW, "In review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Not selected"),
        (STATUS_WITHDRAWN, "Withdrawn by applicant"),
        (STATUS_WAITLISTED, "Waitlisted"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviewed_applications",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)  # Internal; never shown to volunteer

    # Link to Workflows BB WorkItem for approval routing
    work_item = models.OneToOneField(
        "workflows.WorkItem",
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name="volunteer_application",
    )

    class Meta:
        unique_together = [("opportunity", "volunteer")]
        indexes = [
            models.Index(fields=["status", "opportunity"]),
            models.Index(fields=["volunteer", "status"]),
        ]

    def __str__(self):
        return f"Application #{self.pk} — Opportunity #{self.opportunity_id}"
```

### 6.6 `Shift`

A specific scheduled instance of an Opportunity (date, time, location, capacity).

```python
class Shift(TimestampedModel):
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.CASCADE, related_name="shifts"
    )
    coordinator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="coordinated_shifts",
    )

    title_en = models.CharField(max_length=200, blank=True)
    title_fr = models.CharField(max_length=200, blank=True)
    description_en = models.TextField(blank=True)
    description_fr = models.TextField(blank=True)

    # Timing — all stored as UTC; displayed in America/Toronto
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    # Computed: (end_datetime - start_datetime).total_seconds() / 3600
    # DO NOT store duration — derive from start/end to avoid drift

    location_override = models.CharField(max_length=300, blank=True)  # Override opportunity location
    is_remote = models.BooleanField(null=True, blank=True)             # Override opportunity remote flag

    capacity = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Maximum volunteers for this shift. Null = inherits Opportunity.volunteer_capacity.",
    )
    waitlist_enabled = models.BooleanField(default=True)
    waitlist_cap = models.PositiveSmallIntegerField(null=True, blank=True)

    # Cancellation
    is_cancelled = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="cancelled_shifts",
    )
    cancellation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["start_datetime"]
        indexes = [
            models.Index(fields=["opportunity", "start_datetime"]),
            models.Index(fields=["start_datetime", "is_cancelled"]),
        ]

    @property
    def effective_capacity(self):
        if self.capacity is not None:
            return self.capacity
        return self.opportunity.volunteer_capacity  # May be None = unlimited

    @property
    def duration_hours(self):
        delta = self.end_datetime - self.start_datetime
        return round(delta.total_seconds() / 3600, 2)

    def __str__(self):
        return f"Shift #{self.pk} @ {self.start_datetime:%Y-%m-%d %H:%M}"
```

### 6.7 `ShiftBooking`

A volunteer's confirmed booking to a specific Shift.

```python
class ShiftBooking(TimestampedModel):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="bookings")
    volunteer = models.ForeignKey(
        VolunteerProfile, on_delete=models.CASCADE, related_name="bookings"
    )

    STATUS_CONFIRMED  = "confirmed"
    STATUS_WAITLISTED = "waitlisted"
    STATUS_CANCELLED  = "cancelled"
    STATUS_NO_SHOW    = "no_show"
    STATUS_COMPLETED  = "completed"   # Set when HoursLog is approved
    STATUS_CHOICES = [
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_WAITLISTED, "Waitlisted"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_NO_SHOW, "No show"),
        (STATUS_COMPLETED, "Completed"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CONFIRMED)

    waitlist_position = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Waitlist position (1 = next up). Null if confirmed.",
    )

    # Reminder sent flags — prevent duplicate Celery sends
    reminder_24h_sent = models.BooleanField(default=False)
    reminder_2h_sent  = models.BooleanField(default=False)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = [("shift", "volunteer")]
        indexes = [
            models.Index(fields=["shift", "status"]),
            models.Index(fields=["volunteer", "status"]),
            models.Index(fields=["status", "waitlist_position"]),
        ]

    def __str__(self):
        return f"Booking #{self.pk}: Vol #{self.volunteer_id} → Shift #{self.shift_id}"
```

### 6.8 `HoursLog`

Individual hours log entry per shift (or ad-hoc, for non-shift volunteer time).

```python
class HoursLog(TimestampedModel):
    """
    Authoritative record of volunteer hours. Denormalized total on VolunteerProfile
    is recomputed from approved HoursLog rows, not the other way around.

    PIPEDA: volunteer PK only. No name in log or audit records.
    """
    volunteer = models.ForeignKey(
        VolunteerProfile, on_delete=models.PROTECT, related_name="hours_logs"
    )
    opportunity = models.ForeignKey(
        Opportunity, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="hours_logs",
    )
    shift = models.ForeignKey(
        Shift, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="hours_logs",
    )

    date = models.DateField()
    hours = models.DecimalField(max_digits=6, decimal_places=2)  # Max 24.00 per entry
    description = models.CharField(max_length=500, blank=True)

    STATUS_PENDING  = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending coordinator approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="approved_hours",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["volunteer", "status"]),
            models.Index(fields=["opportunity", "date"]),
            models.Index(fields=["shift"]),
            models.Index(fields=["status", "date"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(hours__gt=0) & models.Q(hours__lte=24),
                name="hourslog_hours_range",
            )
        ]

    def __str__(self):
        return f"HoursLog #{self.pk}: {self.hours}h on {self.date}"
```

### 6.9 `ScreeningRecord`

Tracks background checks and references for a volunteer in the context of an Opportunity.

```python
class ScreeningRecord(TimestampedModel):
    """
    Records that a check was completed and verified. The actual criminal record
    result is NEVER stored — only the date of verification and coordinator confirmation.

    PIPEDA sensitive data handling:
    - coordinator who verified is stored by PK only
    - check_type and verified_at are the minimum required for due diligence
    """
    volunteer = models.ForeignKey(
        VolunteerProfile, on_delete=models.PROTECT, related_name="screening_records"
    )
    opportunity = models.ForeignKey(
        Opportunity, null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Opportunity this check was obtained for. Null = general / organization-wide.",
    )

    CHECK_TYPE_VSC       = "vulnerable_sector_check"
    CHECK_TYPE_PRC       = "police_record_check"
    CHECK_TYPE_REFERENCE = "reference_check"
    CHECK_TYPE_DRIVERS   = "drivers_abstract"
    CHECK_TYPE_CHOICES = [
        (CHECK_TYPE_VSC, "Vulnerable Sector Check (VSC)"),
        (CHECK_TYPE_PRC, "Police Record Check"),
        (CHECK_TYPE_REFERENCE, "Reference Check"),
        (CHECK_TYPE_DRIVERS, "Driver's Abstract"),
    ]
    check_type = models.CharField(max_length=30, choices=CHECK_TYPE_CHOICES)

    completed_date = models.DateField(
        help_text="Date the volunteer completed or obtained the check."
    )
    expires_date = models.DateField(
        null=True, blank=True,
        help_text="Expiry date. VSC default = 3 years from completed_date. Null = no expiry.",
    )

    # Verification by coordinator — NEVER stores check result
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="verified_screenings",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_clear = models.BooleanField(
        null=True,
        help_text=(
            "True = coordinator confirmed result is clear. "
            "False = result not clear (triggers workflow). "
            "Null = not yet verified."
        ),
    )

    notes = models.CharField(
        max_length=300, blank=True,
        help_text="Admin notes only. Must not contain criminal record details.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["volunteer", "check_type"]),
            models.Index(fields=["expires_date"]),
            models.Index(fields=["verified_clear"]),
        ]

    @property
    def is_expired(self):
        if not self.expires_date:
            return False
        return timezone.localtime(timezone.now()).date() > self.expires_date

    @property
    def expires_within_30_days(self):
        if not self.expires_date:
            return False
        delta = self.expires_date - timezone.localtime(timezone.now()).date()
        return 0 <= delta.days <= 30
```

### 6.10 `Certification`

Formal qualifications and training completed by a volunteer (First Aid, WHMIS, Food Handler, etc.).

```python
class Certification(TimestampedModel):
    volunteer = models.ForeignKey(
        VolunteerProfile, on_delete=models.CASCADE, related_name="certifications"
    )

    CERT_TYPE_FIRST_AID     = "first_aid"
    CERT_TYPE_CPR           = "cpr"
    CERT_TYPE_WHMIS         = "whmis"
    CERT_TYPE_FOOD_HANDLER  = "food_handler"
    CERT_TYPE_DRIVERS       = "drivers_licence"
    CERT_TYPE_OTHER         = "other"
    CERT_TYPE_CHOICES = [
        (CERT_TYPE_FIRST_AID, "First Aid"),
        (CERT_TYPE_CPR, "CPR"),
        (CERT_TYPE_WHMIS, "WHMIS"),
        (CERT_TYPE_FOOD_HANDLER, "Food Handler Certificate"),
        (CERT_TYPE_DRIVERS, "Driver's Licence (class)"),
        (CERT_TYPE_OTHER, "Other"),
    ]
    cert_type = models.CharField(max_length=30, choices=CERT_TYPE_CHOICES)
    cert_type_other = models.CharField(max_length=100, blank=True)  # If cert_type = OTHER
    issuing_body = models.CharField(max_length=200, blank=True)
    issued_date = models.DateField()
    expires_date = models.DateField(null=True, blank=True)

    # Uploaded certificate document — stored in S3; not public
    document = models.FileField(
        upload_to="volunteers/certifications/",
        null=True, blank=True,
        help_text="Scanned certificate (PDF or image). Stored in private S3 bucket.",
    )

    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="verified_certifications",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_date"]
        indexes = [
            models.Index(fields=["volunteer", "cert_type"]),
            models.Index(fields=["expires_date"]),
        ]

    @property
    def is_expired(self):
        if not self.expires_date:
            return False
        return timezone.localtime(timezone.now()).date() > self.expires_date
```

### 6.11 `Honorarium`

Records a one-time non-employment payment to a volunteer (expense reimbursement or nominal honorarium). Integrates with Payments BB.

```python
class Honorarium(TimestampedModel):
    """
    CRA compliance:
    - EXPENSE_REIMBURSEMENT: no threshold tracking; not taxable
    - HONORARIUM: cumulative tracking per volunteer per calendar year
      - Alert at $450 cumulative
      - Hard block at $1,000 cumulative (coordinator must escalate to payroll)
    - Amounts > $500/year require T4A; system generates T4A data but does NOT remit taxes
    """
    PAYMENT_TYPE_EXPENSE      = "expense_reimbursement"
    PAYMENT_TYPE_HONORARIUM   = "honorarium"
    PAYMENT_TYPE_CHOICES = [
        (PAYMENT_TYPE_EXPENSE, "Expense Reimbursement"),
        (PAYMENT_TYPE_HONORARIUM, "Honorarium"),
    ]

    volunteer = models.ForeignKey(
        VolunteerProfile, on_delete=models.PROTECT, related_name="honoraria"
    )
    payment_type = models.CharField(max_length=30, choices=PAYMENT_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=3, default="CAD")
    description = models.CharField(max_length=300)
    payment_date = models.DateField()
    calendar_year = models.PositiveSmallIntegerField(
        help_text="CRA calendar year for threshold tracking (derived from payment_date on save).",
    )

    # T4A tracking
    t4a_required = models.BooleanField(
        default=False,
        help_text="Set True when volunteer's cumulative honoraria exceed $500 in calendar_year.",
    )
    t4a_issued = models.BooleanField(default=False)
    t4a_issued_at = models.DateTimeField(null=True, blank=True)

    # Payments BB link — set when payment is made via Payments BB
    payment = models.OneToOneField(
        "payments.Payment", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="honorarium",
    )

    # Created by
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_honoraria",
    )

    class Meta:
        indexes = [
            models.Index(fields=["volunteer", "calendar_year", "payment_type"]),
            models.Index(fields=["t4a_required", "t4a_issued"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="honorarium_amount_positive",
            ),
        ]

    def save(self, *args, **kwargs):
        self.calendar_year = self.payment_date.year
        super().save(*args, **kwargs)
```

### 6.12 `VolunteerNote`

Internal coordinator notes on a volunteer. Never shown to the volunteer.

```python
class VolunteerNote(TimestampedModel):
    volunteer = models.ForeignKey(
        VolunteerProfile, on_delete=models.CASCADE, related_name="notes"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="volunteer_notes"
    )
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note #{self.pk} on VolunteerProfile #{self.volunteer_id}"
```

### 6.13 `RecognitionMilestone`

Tracks cumulative hours milestones for recognition purposes (email/certificate triggers).

```python
class RecognitionMilestone(models.Model):
    """
    Milestones are organization-configurable. Default set:
    25h, 50h, 100h, 250h, 500h, 1000h.
    When a volunteer crosses a threshold, a notification is triggered
    and this record is created (idempotent).
    """
    volunteer = models.ForeignKey(
        VolunteerProfile, on_delete=models.CASCADE, related_name="milestones"
    )
    hours_threshold = models.DecimalField(max_digits=8, decimal_places=2)
    achieved_at = models.DateTimeField(auto_now_add=True)
    notification_sent = models.BooleanField(default=False)

    class Meta:
        unique_together = [("volunteer", "hours_threshold")]
        ordering = ["hours_threshold"]
```

---

## 7. Services Layer

All business logic lives in `apps/volunteers/services/`. Views call services; services import from other BBs' service layers.

### 7.1 `services/applications.py`

```python
def apply(
    opportunity: Opportunity,
    volunteer_profile: VolunteerProfile,
    motivation: str,
    consent_record_id: int | None,
) -> VolunteerApplication:
    """
    Create a VolunteerApplication and trigger the approval workflow.
    Raises ValidationError if:
    - volunteer already has a non-withdrawn application for this opportunity
    - opportunity is not published
    - opportunity.closes_at is in the past
    - volunteer profile is missing required_profile_fields
    All DB writes inside atomic(). WorkItem creation on_commit().
    """

def withdraw(application: VolunteerApplication, reason: str = "") -> VolunteerApplication:
    """Volunteer withdraws own application. Sets status=WITHDRAWN."""

def approve_application(
    application: VolunteerApplication,
    reviewed_by: User,
    notes: str = "",
) -> VolunteerApplication:
    """
    Coordinator approves. Sets status=APPROVED.
    Fires application_approved signal → Notifications BB sends email.
    If opportunity.requires_vulnerable_sector_check and volunteer has no valid VSC,
    creates a ScreeningRecord placeholder (status=pending) and alerts coordinator.
    """

def reject_application(
    application: VolunteerApplication,
    reviewed_by: User,
    rejection_reason: str,
) -> VolunteerApplication:
    """
    Coordinator rejects. Sets status=REJECTED.
    rejection_reason stored internally; not exposed to volunteer.
    Fires application_rejected signal → Notifications BB sends email (generic 'not selected' text).
    """
```

### 7.2 `services/scheduling.py`

```python
def create_shift(
    opportunity: Opportunity,
    coordinator: User,
    start_datetime: datetime,
    end_datetime: datetime,
    **kwargs,
) -> Shift:
    """
    Validate start < end. Validate end - start <= 24 hours.
    Create Shift. Notify all approved volunteers for this Opportunity.
    """

def book_shift(shift: Shift, volunteer_profile: VolunteerProfile) -> ShiftBooking:
    """
    Atomic. Checks:
    - shift.is_cancelled → ValidationError
    - volunteer has approved application for this opportunity
    - no overlapping confirmed booking (same volunteer, overlapping time window)
    - shift not in past
    - capacity check with SELECT FOR UPDATE to prevent overbooking race condition
    If at capacity and waitlist_enabled → create waitlisted booking.
    Schedules reminder tasks on_commit().
    """

def cancel_booking(
    booking: ShiftBooking,
    reason: str = "",
    cancelled_by: User | None = None,
) -> ShiftBooking:
    """
    Cancel a booking. If waitlist exists, promote first waitlisted volunteer
    and send them a confirmation notification. Cancellation within 2h of shift
    logs a warning note.
    """

def fill_waitlist(shift: Shift) -> list[ShiftBooking]:
    """
    Called after cancellation. Promotes waitlist_position=1 volunteer to confirmed.
    Idempotent — safe to call multiple times.
    """

def cancel_shift(
    shift: Shift,
    reason: str,
    cancelled_by: User,
) -> Shift:
    """
    Cancel entire shift. Sets is_cancelled=True. Notifies all confirmed bookings.
    Cancels pending reminder tasks (revoke by task ID stored on ShiftBooking).
    """
```

### 7.3 `services/hours.py`

```python
def log_hours(
    volunteer_profile: VolunteerProfile,
    date: date,
    hours: Decimal,
    opportunity: Opportunity | None = None,
    shift: Shift | None = None,
    description: str = "",
) -> HoursLog:
    """
    Validate:
    - hours > 0 and hours <= 24
    - date not in future (> today in America/Toronto)
    - if shift provided, date must match shift.start_datetime.date()
    - volunteer has approved application for this opportunity
    Create HoursLog in STATUS_PENDING.
    """

def approve_hours(
    hours_log: HoursLog,
    approved_by: User,
) -> HoursLog:
    """
    Atomic. Set status=APPROVED, approved_by, approved_at.
    Increment VolunteerProfile.total_hours_approved.
    Check RecognitionMilestone thresholds → create milestone + queue notification on_commit().
    If opportunity.honorarium_per_shift set → call honoraria.create_honorarium() on_commit().
    """

def reject_hours(
    hours_log: HoursLog,
    rejected_by: User,
    reason: str,
) -> HoursLog:
    """Set status=REJECTED. Notify volunteer."""

def monthly_summary(
    volunteer_profile: VolunteerProfile,
    year: int,
    month: int,
) -> dict:
    """
    Return {'total_hours': Decimal, 'by_opportunity': [{'opportunity_id': ..., 'hours': ...}]}
    """
```

### 7.4 `services/honoraria.py`

```python
def cumulative_ytd(
    volunteer_profile: VolunteerProfile,
    calendar_year: int,
) -> Decimal:
    """
    Sum of all Honorarium records (payment_type=HONORARIUM) for this volunteer
    in this calendar year. Used for CRA threshold check.
    """

def validate_cra_threshold(
    volunteer_profile: VolunteerProfile,
    amount: Decimal,
    calendar_year: int,
) -> None:
    """
    Raises HonorariumThresholdError if cumulative_ytd + amount > 1000.
    Raises HonorariumWarning if cumulative_ytd + amount > 450 (non-blocking, logged).
    """

def create_honorarium(
    volunteer_profile: VolunteerProfile,
    payment_type: str,
    amount: Decimal,
    description: str,
    payment_date: date,
    created_by: User,
) -> Honorarium:
    """
    For HONORARIUM type: calls validate_cra_threshold before creating.
    Sets t4a_required=True if cumulative > 500.
    All writes atomic. Does NOT call Payments BB (payment is manual / external).
    Returns Honorarium for coordinator to process payment separately.
    """
```

### 7.5 `services/reporting.py`

```python
def hours_by_program(
    year: int,
    month: int | None = None,
    program: Program | None = None,
) -> QuerySet:
    """
    Aggregate approved HoursLog by program. Returns:
    [{'program_id': ..., 'program_name': ..., 'total_hours': ..., 'volunteer_count': ...}]
    Used by Analytics & Reporting BB.
    """

def impact_value(
    year: int,
    month: int | None = None,
    province: str = "ON",
) -> dict:
    """
    Compute in-kind value: total_hours × provincial_minimum_wage.
    Returns {'total_hours': Decimal, 'hourly_rate': Decimal, 'in_kind_value': Decimal}
    Provincial minimum wages stored in a config dict (updated annually).
    """

def t3010_volunteer_metrics(year: int) -> dict:
    """
    Returns metrics needed for CRA T3010 Registered Charity Information Return:
    - Total volunteer count (distinct VolunteerProfile PKs with approved hours in year)
    - Total volunteer hours (sum of approved HoursLog.hours for year)
    - Breakdown by CRA program category (via Opportunity → Program → cra_category)
    Note: T3010 does not require volunteer names — counts and hours only.
    """
```

---

## 8. Signals

```python
# apps/volunteers/signals.py

application_submitted  = Signal()   # sender=VolunteerApplication
application_approved   = Signal()   # sender=VolunteerApplication
application_rejected   = Signal()   # sender=VolunteerApplication
application_withdrawn  = Signal()   # sender=VolunteerApplication

shift_booked           = Signal()   # sender=ShiftBooking
shift_booking_cancelled = Signal()  # sender=ShiftBooking
shift_cancelled        = Signal()   # sender=Shift

hours_logged           = Signal()   # sender=HoursLog
hours_approved         = Signal()   # sender=HoursLog
hours_rejected         = Signal()   # sender=HoursLog

screening_expiring     = Signal()   # sender=ScreeningRecord
certification_expiring = Signal()   # sender=Certification
milestone_achieved     = Signal()   # sender=RecognitionMilestone
```

All signal dispatches use `.send_robust()` to prevent a failing receiver from rolling back the originating transaction.

`apps/volunteers/receivers.py` connects these signals to `apps/notifications/services.py` to queue notification sends inside `transaction.on_commit()`.

---

## 9. Celery Tasks

All tasks in `apps/volunteers/tasks.py`. Routed to `volunteers` queue (new queue added to `CELERY_TASK_ROUTES`).

```python
@shared_task(bind=True, max_retries=3, queue="volunteers")
def send_shift_reminder(self, booking_id: int, reminder_type: str):
    """
    reminder_type: "24h" | "2h"
    Checks ShiftBooking.reminder_{type}_sent to prevent duplicates.
    Sets flag before sending (TOCTOU-safe via atomic update).
    Sends notification via Notifications BB.
    """

@shared_task(bind=True, queue="volunteers")
def fill_waitlist_after_cancellation(self, shift_id: int):
    """
    Called on_commit() when a booking is cancelled.
    Promotes first waitlisted booking to confirmed. Sends notification.
    """

@shared_task(bind=True, queue="volunteers")
def check_expiring_screenings():
    """
    Celery Beat — daily at 08:00 America/Toronto.
    Finds ScreeningRecord rows expiring within 30 days with no renewal.
    Sends alert to volunteer (action required) and coordinator (FYI).
    """

@shared_task(bind=True, queue="volunteers")
def check_expiring_certifications():
    """
    Celery Beat — daily at 08:00 America/Toronto.
    Finds Certification rows expiring within 30 days.
    Notifies volunteer and coordinator.
    """

@shared_task(bind=True, queue="volunteers")
def send_monthly_hours_summary():
    """
    Celery Beat — 1st of each month at 09:00 America/Toronto.
    Sends each active volunteer their prior-month approved hours summary.
    Uses .iterator(chunk_size=500) for memory safety.
    """

@shared_task(bind=True, queue="volunteers")
def compute_volunteer_impact_snapshot():
    """
    Celery Beat — 2nd of month at 03:00 America/Toronto (runs after ReportSnapshot).
    Computes volunteer hours and in-kind value for prior month.
    Writes result into ReportSnapshot (report_type="volunteers") via reports BB service.
    """
```

**Beat task schedule additions** (added to `seed_periodic_tasks`):

| Task | Crontab | Queue |
|---|---|---|
| `check_expiring_screenings` | Daily 08:00 Toronto | `volunteers` |
| `check_expiring_certifications` | Daily 08:00 Toronto | `volunteers` |
| `send_monthly_hours_summary` | 1st of month 09:00 Toronto | `volunteers` |
| `compute_volunteer_impact_snapshot` | 2nd of month 03:00 Toronto | `volunteers` |

---

## 10. Notifications

All notifications delivered through the Notifications BB. Template keys:

| Template key | Trigger | Recipients |
|---|---|---|
| `volunteer_application_received` | `application_submitted` | Coordinator |
| `volunteer_application_approved` | `application_approved` | Volunteer |
| `volunteer_application_rejected` | `application_rejected` | Volunteer (generic text; no reason) |
| `volunteer_shift_reminder_24h` | Celery task 24h pre-shift | Volunteer |
| `volunteer_shift_reminder_2h` | Celery task 2h pre-shift | Volunteer |
| `volunteer_shift_cancelled` | `shift_cancelled` | All confirmed bookings |
| `volunteer_waitlist_promoted` | `fill_waitlist_after_cancellation` | Promoted volunteer |
| `volunteer_hours_approved` | `hours_approved` | Volunteer |
| `volunteer_hours_rejected` | `hours_rejected` | Volunteer |
| `volunteer_milestone_achieved` | `milestone_achieved` | Volunteer |
| `volunteer_screening_expiring` | `check_expiring_screenings` | Volunteer + Coordinator |
| `volunteer_certification_expiring` | `check_expiring_certifications` | Volunteer + Coordinator |
| `volunteer_monthly_hours_summary` | `send_monthly_hours_summary` | Volunteer |
| `volunteer_honorarium_t4a_threshold` | `create_honorarium` (>$450 YTD) | Coordinator |

All templates have EN and FR variants. The Notifications BB selects based on `VolunteerProfile.preferred_language`.

---

## 11. REST API (API BB extension)

New serializers and views registered under `/api/v1/volunteers/`. All endpoints require JWT Bearer token authentication (RS256). Throttle: `UserRateThrottle` 1000/hour.

### Endpoints

```
GET  /api/v1/volunteers/opportunities/           → OpportunityListView
GET  /api/v1/volunteers/opportunities/<slug>/    → OpportunityDetailView
POST /api/v1/volunteers/applications/            → SubmitApplicationView
GET  /api/v1/volunteers/applications/            → MyApplicationsView (own only)
GET  /api/v1/volunteers/applications/<id>/       → ApplicationDetailView (own only)
DELETE /api/v1/volunteers/applications/<id>/     → WithdrawApplicationView (sets WITHDRAWN)

GET  /api/v1/volunteers/shifts/?opportunity=<slug>  → ShiftListView
POST /api/v1/volunteers/shifts/<id>/book/           → BookShiftView
POST /api/v1/volunteers/shifts/<id>/cancel-booking/ → CancelBookingView

GET  /api/v1/volunteers/hours/               → MyHoursView
POST /api/v1/volunteers/hours/               → LogHoursView
GET  /api/v1/volunteers/hours/summary/       → MyHoursSummaryView

GET  /api/v1/volunteers/profile/             → MyProfileView (GET + PATCH)
PATCH /api/v1/volunteers/profile/            → UpdateProfileView

# Coordinator endpoints (requires volunteer_coordinator or volunteer_admin group)
GET  /api/v1/volunteers/admin/applications/                  → AllApplicationsView
PATCH /api/v1/volunteers/admin/applications/<id>/approve/    → ApproveApplicationView
PATCH /api/v1/volunteers/admin/applications/<id>/reject/     → RejectApplicationView
GET  /api/v1/volunteers/admin/hours/pending/                 → PendingHoursView
PATCH /api/v1/volunteers/admin/hours/<id>/approve/           → ApproveHoursView
PATCH /api/v1/volunteers/admin/hours/<id>/reject/            → RejectHoursView
GET  /api/v1/volunteers/admin/reports/hours/                 → HoursReportView
GET  /api/v1/volunteers/admin/reports/impact/                → ImpactReportView
```

### Key serializer rules

- `VolunteerProfileSerializer`: excludes `sin_encrypted`, `accommodation_notes` unless caller has `volunteers.view_accommodation_notes`; includes `sin_last4` only
- `ApplicationSerializer`: excludes `rejection_reason` — never exposed to volunteer
- `HoursLogSerializer`: volunteer sees own records; coordinator sees all for their program
- All monetary amounts: `Decimal` serialized as string (avoid float precision loss)

---

## 12. Reporting Integration (Analytics & Reporting BB)

### 12.1 New `ReportSnapshot` report type

Extend `ReportSnapshot.REPORT_TYPE_CHOICES` to include:

```python
REPORT_TYPE_VOLUNTEERS = "volunteers"
```

`data` schema for `report_type = "volunteers"`:

```json
{
  "volunteer_count": 87,
  "new_volunteer_count": 12,
  "active_volunteer_count": 63,
  "total_hours_approved": "1243.50",
  "hours_by_program": {
    "food-bank": {"hours": "456.00", "volunteer_count": 28},
    "youth-mentorship": {"hours": "312.50", "volunteer_count": 19},
    "admin-support": {"hours": "475.00", "volunteer_count": 24}
  },
  "hours_by_cra_category": {
    "welfare": "912.00",
    "education": "312.50",
    "other": "19.00"
  },
  "in_kind_value_cad": "24870.00",
  "provincial_hourly_rate_used": "16.55",
  "applications_received": 34,
  "applications_approved": 28,
  "applications_rejected": 4,
  "milestones_achieved": 7,
  "screenings_expiring_30d": 3,
  "certifications_expiring_30d": 5
}
```

### 12.2 Volunteer Hours Export

New export type added to `ExportRecord.EXPORT_TYPE_CHOICES`:

```python
EXPORT_TYPE_VOLUNTEER_HOURS = "volunteer_hours"
EXPORT_TYPE_VOLUNTEER_T3010 = "volunteer_t3010"
```

`volunteer_hours` CSV columns: `date`, `opportunity_slug`, `program_slug`, `hours`, `status`  
(Volunteer identity is excluded — reports are aggregate by default; per-volunteer export available to `volunteer_admin` only with `ExportRecord` audit trail)

`volunteer_t3010` CSV columns: `cra_category`, `program_slug`, `volunteer_count`, `total_hours`  
Used to populate CRA T3010 Schedule 6 (Compensation and Fundraising).

---

## 13. Templates and UI

### 13.1 Volunteer Portal (authenticated)

- `portal/dashboard.html` — upcoming shifts, pending hours, recent notifications, total approved hours counter
- `portal/opportunity_list.html` — filterable grid of published opportunities; bilingual titles; skill tags
- `portal/opportunity_detail.html` — full role description (EN/FR); screening requirements list; apply CTA; accessibility: `role="main"`, skip nav
- `portal/application_form.html` — dynamic fields driven by `Opportunity.required_profile_fields`; Consent BB widget for volunteer agreement signature
- `portal/my_schedule.html` — calendar view (HTMX-enhanced); list fallback for screen readers
- `portal/my_hours.html` — paginated table; pending/approved/rejected status badges; totals
- `portal/log_hours_form.html` — date picker, hours field (decimal), opportunity selector, description; WCAG: `<label>` for every field, `aria-describedby` on inputs
- `portal/profile_edit.html` — standard fields + skills M2M selector; emergency contact section with appropriate privacy notice

### 13.2 Coordinator Views

- `coordinator/dashboard.html` — action-required widgets: pending applications, pending hours, expiring screenings, unfilled shifts
- `coordinator/application_review.html` — volunteer's motivation, profile summary (no SIN/accommodation unless permissioned), screening checklist; approve/reject buttons with CSRF
- `coordinator/shift_manage.html` — shift roster table; booking status; add/remove volunteers; waitlist management
- `coordinator/hours_approval.html` — bulk-approve action; individual reject with reason field
- `coordinator/volunteer_detail.html` — full profile (permission-gated sections); screening records; certification status; hours history; notes (append-only AJAX)

### 13.3 Accessibility requirements (WCAG 2.1 AA)

All templates must satisfy:

- **1.3.1 Info and Relationships** — data tables use `<th scope>` and `<caption>`; form groups use `<fieldset>` + `<legend>`
- **1.4.3 Contrast** — status badges: minimum 4.5:1 ratio; tested against CivicOS design tokens
- **2.1.1 Keyboard** — all actions operable by keyboard; no click-only interactions
- **2.4.1 Bypass Blocks** — skip navigation link on all portal pages
- **3.3.1 Error Identification** — server-side validation errors linked via `aria-describedby` to the triggering input
- **4.1.2 Name, Role, Value** — all custom widgets (shift calendar, skill selector) expose proper ARIA roles
- **Bilingual** — every user-visible string wrapped in `{% trans %}` or `{% blocktrans %}`; all PO/MO files located in `locale/en/LC_MESSAGES/` and `locale/fr/LC_MESSAGES/`
- **Language selector** — volunteer can set `preferred_language` on profile; sets Django's `LANGUAGE_COOKIE_NAME` on save

---

## 14. Admin Configuration

`apps/volunteers/admin.py` follows the backoffice admin patterns established in `apps/payments/admin.py`:

- `VolunteerProfileAdmin` — list display: PK, preferred name, status, total hours, program count; search: PK only (no email in list columns — PIPEDA)
- Field-level permission gate: `accommodation_notes`, `emergency_contact_*`, `sin_last4` only visible to users with `volunteers.view_accommodation_notes`
- `ScreeningRecordInline` on `VolunteerProfileAdmin` — shows check_type, completed_date, expires_date, verified_clear (no result details)
- `CertificationInline` — shows cert_type, expires_date, verified_at
- `HonorariumAdmin` — `has_delete_permission = False` (immutable financial record); shows YTD cumulative via `@admin.display`
- `OpportunityAdmin` — list_display: title_en, program, status, volunteer_capacity; publish action
- `ShiftAdmin` — list_display: opportunity, start_datetime, duration_hours, confirmed_count, waitlist_count
- All admin actions are audit logged via `AuditLogEntry` (existing pattern from `apps/audit`)

---

## 15. PIPEDA Audit Log Requirements

Every action on sensitive volunteer data must create an `AuditLogEntry`:

| Action | Event type | PII rule |
|---|---|---|
| Application submitted | `volunteer_application_submitted` | Log opportunity_id, volunteer_profile_id — no name |
| Application approved/rejected | `volunteer_application_reviewed` | Log application_id, reviewer_pk — no rejection_reason |
| Hours approved/rejected | `volunteer_hours_reviewed` | Log hours_log_id, reviewer_pk |
| Accommodation notes read | `volunteer_accommodation_read` | Log profile_id, reader_pk, timestamp |
| SIN collected/updated | `volunteer_sin_updated` | Log profile_id, updater_pk — never log SIN digits |
| Screening record created | `volunteer_screening_recorded` | Log profile_id, check_type, coordinator_pk — no result |
| Honorarium created | `volunteer_honorarium_created` | Log honorarium_id, amount, payment_type — no volunteer name |
| Profile exported | `volunteer_profile_exported` | Log profile_id, exporter_pk, timestamp |

---

## 16. Implementation Wave Plan

Following the wave pattern established for Payments BB and Reports BB.

### Wave 1 — Foundation (data model, admin, migrations)

**Goal:** Database schema established; models importable; Django admin functional for all models.

Deliverables:
- `apps/volunteers/` scaffold (apps.py, models.py, admin.py, migrations/0001_initial.py)
- All 13 models with full field definitions, indexes, constraints
- `INSTALLED_APPS` updated; `urls.py` wired; `CELERY_TASK_ROUTES` updated with `volunteers` queue
- `seed_periodic_tasks` updated with 4 new Beat tasks
- Django admin for all models (field-level PII gates on `VolunteerProfileAdmin`)
- `SkillTag` data fixtures (`management/commands/seed_skill_tags.py`)
- **Adversarial review** of models and admin
- Full migration run; test suite for models (~60 tests)

### Wave 2 — Application and Screening workflows

**Goal:** Volunteers can apply; coordinators can approve/reject; screening records tracked.

Deliverables:
- `services/applications.py` (apply, withdraw, approve_application, reject_application)
- `services/screening.py` (record_check, check_expiring_soon)
- Signals: `application_submitted`, `application_approved`, `application_rejected`, `screening_expiring`
- Receivers: wire signals to Notifications BB
- Views: `portal/opportunity_list`, `portal/opportunity_detail`, `portal/application_form`, `portal/application_status`
- Views: `coordinator/application_review`
- Templates: all portal application templates + coordinator review template
- Workflows BB integration: create WorkItem on application submission
- **Adversarial review** of application workflow (focus: authorization, PIPEDA, race conditions)
- Test suite: `test_services_applications.py`, `test_views_portal.py` (~80 tests)

### Wave 3 — Scheduling and Hours

**Goal:** Shifts can be created; volunteers can book; hours can be logged and approved.

Deliverables:
- `services/scheduling.py` (create_shift, book_shift, cancel_booking, fill_waitlist, cancel_shift)
- `services/hours.py` (log_hours, approve_hours, reject_hours, monthly_summary)
- Signals: `shift_booked`, `shift_booking_cancelled`, `shift_cancelled`, `hours_logged`, `hours_approved`, `hours_rejected`
- Celery tasks: `send_shift_reminder`, `fill_waitlist_after_cancellation`
- Views: `portal/my_schedule`, `portal/shift_signup`, `portal/my_hours`, `portal/log_hours_form`
- Views: `coordinator/shift_manage`, `coordinator/hours_approval`
- Templates: all scheduling and hours templates
- **Adversarial review** (focus: overbooking race condition, booking validation, hours range check)
- Test suite: `test_services_scheduling.py`, `test_services_hours.py` (~90 tests)

### Wave 4 — Certifications, Honoraria, and Recognition

**Goal:** Certifications tracked; honoraria issued with CRA threshold enforcement; milestones celebrated.

Deliverables:
- `services/honoraria.py` (cumulative_ytd, validate_cra_threshold, create_honorarium)
- `RecognitionMilestone` milestone check in `approve_hours`
- Celery tasks: `check_expiring_screenings`, `check_expiring_certifications`, `send_monthly_hours_summary`
- Views: `admin/screening_dashboard`, `admin/honorarium_form`
- Templates: admin views
- **Adversarial review** (focus: CRA threshold enforcement, SIN handling, concurrent honorarium creation)
- Test suite: `test_services_screening.py`, `test_services_honoraria.py` (~60 tests)

### Wave 5 — Reporting and API

**Goal:** Impact reporting complete; REST API endpoints live; Analytics BB integration done.

Deliverables:
- `services/reporting.py` (hours_by_program, impact_value, t3010_volunteer_metrics)
- Celery task: `compute_volunteer_impact_snapshot`
- `ReportSnapshot` extension: new `volunteers` report type
- `ExportRecord` extension: new `volunteer_hours` and `volunteer_t3010` export types
- CSV export: `apps/reports/exports/volunteer_export.py` (streaming, PIPEDA column whitelist)
- Impact report view: `admin/impact_report.html`
- DRF serializers: `apps/volunteers/serializers.py`
- API views: all endpoints in §11
- Wagtail hook: volunteer section in backoffice sidebar
- Volunteer reference letter PDF (WeasyPrint — hours letter for volunteer to present to third parties)
- **Adversarial review** (focus: IDOR in API, export PIPEDA compliance, T3010 accuracy)
- Test suite: `test_services_reporting.py`, `test_api.py` (~70 tests)

### Wave 6 — Hardening and close-out

**Goal:** All review findings fixed; full test suite green; documentation updated.

Deliverables:
- Address all CRITICAL and HIGH findings from Waves 1–5 adversarial reviews
- Bilingual QA pass (all user-facing strings in both EN and FR)
- WCAG review of all templates (keyboard navigation, ARIA, contrast)
- `docs/PROJECT_OVERVIEW_AND_STATUS.md` updated with volunteer BB test count
- `docs/CODEBASE_HANDOFF_FOR_AI.md` updated with `apps/volunteers/` app reference
- Full test suite run (target: 360+ new tests across all waves)
- Commit and tag

---

## 17. Test Strategy

### Coverage targets

| Module | Target |
|---|---|
| Models (constraints, properties, __str__) | 100% of models and properties |
| `services/applications.py` | All paths including edge cases (duplicate apply, closed opportunity, missing profile fields) |
| `services/scheduling.py` | **Overbooking race condition via `TransactionTestCase`**; waitlist promotion; past-shift validation |
| `services/hours.py` | Milestone threshold boundary tests; approval denormalization correctness |
| `services/honoraria.py` | CRA $450/$500/$1,000 thresholds; concurrent creation; T4A flag |
| `services/reporting.py` | Aggregate correctness; T3010 category mapping |
| Celery tasks | Synchronous via `task.apply()`; no `time.sleep()`; `patch(timezone.now)` for deterministic dates |
| API views | IDOR: volunteer A cannot see volunteer B's hours/profile; coordinator scoping |
| Signal/receiver integration | Use `TransactionTestCase` for `on_commit` receivers |

### Key test invariants

- **No `time.sleep()`** — all time-dependent tests use `patch("django.utils.timezone.now", return_value=datetime(...))`
- **Overbooking** — `test_concurrent_booking_race_condition` must use `TransactionTestCase` and simulate two concurrent `book_shift()` calls; assert only one succeeds
- **PIPEDA** — assert no PII appears in `AuditLogEntry.extra_data` for any tested action
- **CRA threshold** — test at exactly $449.99, $450.00, $499.99, $500.00, $999.99, $1,000.00 cumulative YTD
- **VSC expiry** — test `is_expired` on check at, before, and after `expires_date`; test `expires_within_30_days` at 31, 30, 1, 0 days
- **Bilingual** — `assertContains(response, opportunity.title_fr)` when `Accept-Language: fr` set
- **MRO** — assert all CBVs have `LoginRequiredMixin` before `PermissionRequiredMixin` (static introspection test)

---

## 18. Provincial Minimum Wage Reference

Used by `services/reporting.impact_value()` for in-kind value computation. Updated annually.

| Province / Territory | Rate (2025) | Effective date |
|---|---|---|
| Ontario | $17.20 | Oct 1, 2024 |
| British Columbia | $17.40 | Jun 1, 2024 |
| Alberta | $15.00 | Oct 1, 2018 |
| Quebec | $16.10 | May 1, 2024 |
| Nova Scotia | $15.70 | Apr 1, 2024 |
| New Brunswick | $15.30 | Apr 1, 2024 |
| Manitoba | $15.80 | Oct 1, 2024 |
| Saskatchewan | $15.00 | Oct 1, 2024 |
| PEI | $16.00 | Apr 1, 2024 |
| Newfoundland & Labrador | $16.00 | Oct 1, 2024 |
| Northwest Territories | $16.05 | Sep 1, 2023 |
| Nunavut | $19.00 | Jan 1, 2024 |
| Yukon | $17.59 | Apr 1, 2024 |

Default: Ontario rate unless `province` param provided. Store rates in `VOLUNTEER_MINIMUM_WAGES` dict in `config/settings/base.py`; override per-deployment if needed.

---

## References

- [Volunteer Canada — Canadian Code for Volunteer Involvement](https://volunteer.ca/canadian-code-for-volunteer-involvement/)
- [Volunteer Management Professionals of Canada — Standards of Practice](https://www.vmpc.ca/standards_parctice.html)
- [Public Safety Canada — Best Practice Guidelines for Screening Volunteers](https://www.publicsafety.gc.ca/cnt/rsrcs/pblctns/bpg-scrng-vls/index-en.aspx)
- [RCMP — Vulnerable Sector Checks](https://rcmp.ca/en/criminal-records/criminal-record-checks/vulnerable-sector-checks)
- [CRA — PC-025: Expenses Incurred by Volunteers](https://www.canada.ca/en/revenue-agency/services/charities-giving/charities/policies-guidance/policy-commentary-025-expenses-incurred-volunteers.html)
- [CRA — PC-012: Out-of-pocket Expenses](https://www.canada.ca/en/revenue-agency/services/charities-giving/charities/policies-guidance/policy-commentary-012-pocket-expenses.html)
- [LawNow — Volunteering and Income Tax](https://www.lawnow.org/volunteering-and-income-tax/)
- [Certn — Non-Profit Background Checks: Screening Volunteers](https://certn.co/blog/non-profit-background-checks-screening-volunteers/)
- [Rosterfy — Essential Guide to Volunteer Police Records Checks in Canada](https://www.rosterfy.com/blog/volunteer-police-records-check-canada)
- [Office of the Privacy Commissioner — How PIPEDA Applies to Charitable and Non-Profit Organizations](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/r_o_p/02_05_d_19/)
- [Office of the Privacy Commissioner — Sensitive Information](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/pipeda-compliance-help/pipeda-interpretation-bulletins/interpretations_10_sensible/)
- [CRA — T3010 Registered Charity Information Return](https://www.canada.ca/en/revenue-agency/services/forms-publications/forms/t3010.html)

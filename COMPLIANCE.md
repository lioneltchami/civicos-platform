# Compliance Requirements

This document defines the non-negotiable compliance requirements for all Govstack deployments serving Canadian municipal and public sector clients. All features must be built to satisfy these requirements by default — compliance is not optional and must not be bolted on after the fact.

---

## 1. Accessibility — WCAG 2.1 Level AA

**Standard:** Web Content Accessibility Guidelines (WCAG) 2.1, Level AA  
**Basis:** *Accessible Canada Act* (S.C. 2019, c. 10); Ontario *Accessibility for Ontarians with Disabilities Act* (AODA); Quebec *Act to ensure persons with a disability have equal opportunities*

### Requirements

- All public-facing pages and forms must meet WCAG 2.1 AA success criteria
- Keyboard navigation: every interactive element reachable and operable by keyboard alone
- Focus management: visible focus indicators, logical tab order, focus trapping in modals
- Colour contrast: minimum 4.5:1 for normal text, 3:1 for large text and UI components
- Images: all meaningful images have descriptive `alt` text; decorative images have `alt=""`
- Forms: every input has a programmatically associated `<label>`; errors are described in text, not colour alone; inline error messages reference the field by name
- ARIA: use native HTML semantics first; ARIA only to supplement where HTML is insufficient
- Video/audio: captions for all prerecorded media; transcripts for audio-only content
- Reading level: plain language target of Grade 8 or below for citizen-facing copy
- No content that flashes more than 3 times per second

### Enforcement

- Automated accessibility scanning in CI (axe-core or pa11y) blocks PRs with new violations
- Manual keyboard and screen-reader testing (NVDA + Firefox; VoiceOver + Safari) before each major release
- Accessibility audit by a qualified evaluator before initial production launch and annually thereafter
- Accessibility statement published on every deployment listing known limitations and contact for assistance

---

## 2. Privacy by Design — PIPEDA & Provincial Privacy Laws

**Standard:** *Personal Information Protection and Electronic Documents Act* (PIPEDA); Quebec *Law 25* (Bill 64); applicable provincial privacy legislation  
**Principle:** Collect only what is necessary; protect what you collect; give individuals control over their data.

### Requirements

**Data minimization**
- Each form field and model attribute must be justified against a stated purpose
- Fields that collect PII must be tagged with `pii=True` in model metadata (custom field kwarg)
- Default retention periods must be defined at the model level; automated purge jobs run on schedule

**Consent**
- Explicit consent must be obtained before collecting personal information
- Consent must be granular (separate consent for separate purposes), informed, and revocable
- Consent records must be stored with timestamp, IP address, and the exact text presented

**Access and portability**
- Citizens must be able to request an export of all personal data held about them (DSAR — Data Subject Access Request)
- Portal provides self-serve access to submitted forms and service history
- Staff can generate a redacted export for DSAR fulfilment; export is logged in the audit trail

**Retention and deletion**
- Records are not held longer than their documented retention period
- Deletion is implemented as hard delete for PII after retention expiry (not soft delete)
- Backups containing expired PII are rotated within the backup retention window

**Privacy Impact Assessment (PIA)**
- A PIA must be completed and approved before any new data collection feature goes to production
- PIAs are stored in `docs/compliance/pia/`

**Quebec Law 25 specifics**
- A Privacy Officer must be named for each deployment
- Serious privacy incidents must be reported to the *Commission d'accès à l'information* within 72 hours
- Privacy policy must be published in plain language in both official languages

---

## 3. Bilingual Support — Official Languages

**Standard:** *Official Languages Act* (R.S.C. 1985, c. 31); applicable municipal bylaws  
**Requirement:** All citizen-facing content and interfaces must be available in both English and French.

### Requirements

- All UI strings translated with Django's `gettext` / `gettext_lazy`; `.po`/`.mo` files maintained for `en` and `fr`
- Wagtail locale system used for bilingual page trees; each page type has an EN and FR variant
- Language switcher visible on every page; language preference saved for authenticated users
- Wagtail admin available in French for francophone editors
- Form validation error messages translated
- Email and SMS notification templates maintained in both languages; language selected based on recipient preference
- Date, number, and currency formatting follows locale conventions
- No hard-coded English strings in templates or Python code — all user-visible text goes through `{% trans %}` or `gettext`

---

## 4. Audit Logging & Accountability

**Standard:** Treasury Board *Directive on Security Management*; *Access to Information Act*; municipal records management bylaws

### What Must Be Logged

Every audit record includes: `timestamp`, `actor_id`, `actor_ip`, `actor_user_agent`, `event_type`, `resource_type`, `resource_id`, `before_state` (JSON), `after_state` (JSON), `outcome` (success/failure), `session_id`.

| Event Category | Examples |
|---|---|
| Authentication | Login, logout, failed login, MFA challenge, password reset, account lockout |
| Authorization | Permission denied, role change, privilege escalation |
| Data access | View PII record, download submission, export report |
| Data modification | Create, update, delete any record containing PII |
| Admin actions | Configuration change, user creation, role assignment |
| Workflow events | Submission received, status changed, assigned, approved, rejected |
| System events | Scheduled job run, integration call, error |

### Storage Requirements

- Audit log is **append-only**: no update or delete operations permitted on audit records
- Audit log is stored in a dedicated database table (or separate schema) with no cascade deletes from referenced objects
- Log entries are retained for a minimum of **7 years** (aligned with municipal records schedules)
- Indexes on `timestamp`, `actor_id`, `resource_type + resource_id` for query performance
- Log export available to administrators as CSV; export itself is logged

### Integrity

- Each log entry includes a `prev_hash` linking to the previous entry's hash (chain of custody)
- Daily digest hashes can be published to a transparency log for tamper evidence

---

## 5. Security

**Standard:** Government of Canada *IT Security Risk Management* framework; NIST SP 800-53; OWASP Top 10

### Authentication & Session Management

- Minimum password complexity: 12 characters, complexity configurable per deployment
- Account lockout after 5 consecutive failed login attempts; lockout duration configurable
- MFA required for all staff accounts; optional (strongly encouraged) for citizens
- Session timeout: 30 minutes inactivity for authenticated sessions; configurable
- Secure session cookies: `HttpOnly`, `Secure`, `SameSite=Lax`
- Password hashing: `argon2` (via `django-argon2`)

### Transport Security

- TLS 1.2 minimum; TLS 1.3 preferred
- HSTS with `max-age=31536000; includeSubDomains; preload`
- No mixed content; all assets served over HTTPS

### Application Security

- Django's CSRF protection enabled on all state-changing endpoints
- Content Security Policy headers on all responses
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- SQL injection: Django ORM used exclusively; raw SQL only via `connection.execute()` with parameterized queries
- File uploads: type validation (magic bytes, not extension); virus scanning before storage; stored outside web root
- Dependencies: `pip-audit` and Dependabot run on CI; critical CVEs block deployments

### Infrastructure

- Secrets managed via environment variables or a secrets manager (Vault, AWS Secrets Manager); never in source code
- Database access restricted to application user; no direct public internet access to database
- Principle of least privilege: application database user has only the permissions it needs
- Regular backups with tested restore procedures; backup encryption at rest

---

## 6. Records Management

- All citizen-submitted data is treated as a public record subject to applicable municipal records retention schedules
- Retention periods configured per form/service type; automated expiry enforced
- Records cannot be deleted during a legal hold; hold mechanism must be implemented before launch of any transactional service
- ATIP (Access to Information and Privacy) request workflow included in the `portal` module

---

## Compliance Matrix

| Requirement | Module(s) Responsible | Status |
|---|---|---|
| WCAG 2.1 AA | `cms`, `forms`, `portal` + all templates | 🔲 Pending |
| PIPEDA / Law 25 | `core`, `forms`, `portal`, `audit` | 🔲 Pending |
| Official Languages (EN/FR) | All modules | 🔲 Pending |
| Audit logging | `audit` + signal hooks in all modules | 🔲 Pending |
| MFA for staff | `auth` | 🔲 Pending |
| Password policy | `auth` | 🔲 Pending |
| Session security | `auth`, `core` middleware | 🔲 Pending |
| Data retention & purge | `core`, `forms`, `portal` | 🔲 Pending |
| DSAR export | `portal` | 🔲 Pending |
| Secure file uploads | `forms`, `portal` | 🔲 Pending |
| CSP / security headers | `core` middleware | 🔲 Pending |
| TLS enforcement | Infrastructure / deployment | 🔲 Pending |

# Architecture

## Philosophy: Modular Building Blocks

CivicOS is designed around the principle of **composable, independently deployable building blocks** — inspired by the [CivicOS initiative](https://civicos.global/), which defines a standard set of reusable digital government capabilities.

Each building block:
- Has a **single, well-defined responsibility**
- Exposes a **stable internal API** (Python service layer) that other blocks consume
- Can be **enabled or disabled** per deployment without breaking other modules
- Is **independently testable** with no hard dependencies on sibling modules
- Follows a consistent internal structure: `models → services → views → serializers/forms → templates`

This means a small municipality can deploy just `cms` + `forms` to get a content-managed website with accessible web forms, while a larger client can layer on `portal` + `workflows` + `notifications` to deliver full transactional services.

---

## High-Level Module Map

```
┌─────────────────────────────────────────────────────────────┐
│                        Citizen Browser                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS
┌──────────────────────────▼──────────────────────────────────┐
│                    Django / Wagtail App                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │   cms    │  │  forms   │  │  portal  │  │ workflows │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬─────┘  │
│       │             │              │               │         │
│  ┌────▼─────────────▼──────────────▼───────────────▼──────┐ │
│  │                        core                            │ │
│  │  (base models, helpers, settings, middleware, signals) │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌─────────┐  ┌───────────────┐  ┌────────────────────────┐ │
│  │  auth   │  │ notifications │  │        audit           │ │
│  └─────────┘  └───────────────┘  └────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
         │                              │
┌────────▼──────────┐      ┌────────────▼──────────┐
│    PostgreSQL      │      │    Redis / Celery      │
│  (primary store)  │      │  (cache + task queue)  │
└───────────────────┘      └───────────────────────┘
```

---

## Module Responsibilities

### `core`
The foundation. Every other module depends on `core`. It provides:
- **Base abstract models**: `TimestampedModel`, `UUIDModel`, `SoftDeleteModel`
- **Settings management**: environment-driven config via `django-environ`
- **Shared middleware**: request ID injection, language negotiation, security headers
- **Signal infrastructure**: publish/subscribe hooks other modules listen to
- **Utility library**: pagination helpers, date formatting, translation utilities
- **Django checks**: custom system checks that enforce compliance requirements at startup

`core` must never import from sibling modules.

### `cms`
Content management powered by Wagtail. Responsible for:
- All public-facing page types (HomePage, GenericPage, ServiceIndexPage, ServicePage, NewsIndexPage, NewsPage)
- StreamField block library (rich text, call-to-action, accordion, media, table, etc.)
- Snippet models for reusable content (menus, footer, alerts, contact info)
- Site settings (via `wagtail.contrib.settings`)
- Multilingual page trees (Wagtail's built-in locale system for EN/FR)
- Image and document management with access control

The `cms` module is always present. All other modules inject their front-end entry points as Wagtail pages or via `wagtail_hooks`.

### `forms`
Dynamic form builder and submission manager. Responsible for:
- Configurable form pages (via `wagtail.contrib.forms`) extended with accessibility metadata
- Field types: text, textarea, email, phone, date, file upload, select, checkbox group, signature
- Server-side validation with WCAG-compliant inline error messages
- Submission storage with retention policies and consent tracking
- CSV/Excel export with PII redaction options
- Spam protection (honeypot + configurable CAPTCHA)
- Confirmation emails to submitters and notifications to staff

### `portal`
Authenticated citizen-facing self-service. Responsible for:
- Account registration, login, and profile management
- Service request submission, status tracking, and history
- Document upload and download
- Secure messaging between citizen and staff
- Accessibility: operable as a logged-in experience without JavaScript dependency

The portal consumes `workflows` to determine request status and `notifications` to deliver updates.

### `workflows`
Internal staff workflow engine. Responsible for:
- Configurable workflow definitions (states, transitions, role assignments)
- Claim, assign, approve, reject, and escalate actions on work items
- SLA tracking and overdue alerting
- Integration with Django's permission system (role-based access to workflow steps)
- Workflow history and notes (fully audited)

Workflows are triggered by `forms` submissions and `portal` service requests, and fire events consumed by `notifications` and `audit`.

### `auth`
Authentication and authorization. Responsible for:
- Django `AbstractUser` extension with profile fields and language preference
- Role-based permission groups (citizen, staff, supervisor, admin)
- MFA (TOTP via `django-otp`)
- SSO integration (SAML 2.0 and OIDC via `python3-saml` / `mozilla-django-oidc`)
- Session security: timeouts, concurrent session control, IP binding
- Password policy enforcement

### `notifications`
Outbound messaging. Responsible for:
- Email delivery via SMTP or transactional API (SendGrid, SES, GC Notify)
- SMS delivery (optional, via GC Notify or Twilio)
- In-app notification inbox for portal users
- Template management (Wagtail-managed, bilingual)
- Delivery tracking and bounce handling
- Unsubscribe / preference management

### `audit`
Immutable event log. Responsible for:
- Recording all significant events: logins, logouts, data access, modifications, exports, admin actions
- Tamper-evident storage (append-only model, optional blockchain anchoring)
- Queryable audit log for administrators with filtering and export
- Retention policies aligned with records management requirements
- Integration with Django signals — other modules fire signals, audit subscribes

---

## Inter-Module Communication

Modules communicate via:

1. **Django signals** — loose coupling for cross-cutting concerns. Example: `portal` fires `service_request_submitted`; `notifications` and `audit` subscribe independently.
2. **Service layer imports** — when a direct dependency is acceptable (e.g., `portal` imports `workflows.services.create_work_item`). Always import from the service layer, never directly from models.
3. **Wagtail hooks** — modules register `wagtail_hooks` to inject UI elements into the Wagtail admin or front-end without coupling to `cms`.

**Never** import from a sibling module's `models.py` directly from a view or template — always go through the service layer.

---

## Directory Structure

```
civicos/
├── apps/
│   ├── core/
│   │   ├── models.py          # Abstract base models
│   │   ├── middleware.py
│   │   ├── signals.py
│   │   ├── checks.py          # Django system checks
│   │   └── utils/
│   ├── cms/
│   │   ├── models.py          # Page, snippet, and media models
│   │   ├── blocks.py          # StreamField blocks
│   │   ├── snippets.py
│   │   └── wagtail_hooks.py
│   ├── forms/
│   │   ├── models.py
│   │   ├── services.py
│   │   ├── views.py
│   │   └── wagtail_hooks.py
│   ├── portal/
│   ├── workflows/
│   ├── auth_extension/
│   ├── notifications/
│   └── audit/
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   ├── production.py
│   │   └── test.py
│   ├── urls.py
│   └── wsgi.py
├── templates/
│   ├── base.html
│   └── includes/
├── static/
├── docs/
├── context/
├── README.md
├── ARCHITECTURE.md
└── COMPLIANCE.md
```

---

## Design Principles

- **Privacy by design**: collect the minimum data necessary; PII is tagged at the model level
- **Security by default**: CSRF, XSS, clickjacking, and SQL injection protections on by default; no security headers opt-in
- **Accessibility first**: all HTML is written to WCAG 2.1 AA; templates are validated in CI
- **Auditability**: every state change to citizen data or workflow items is recorded
- **Testability**: business logic lives in service functions, not views; views stay thin
- **12-factor app**: configuration via environment variables; no secrets in code

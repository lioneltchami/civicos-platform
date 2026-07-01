# Technology Stack

Documented technology decisions for CivicOS. Each decision includes a rationale. Revisit this file when a major version upgrade or architectural change is considered.

---

## Core Framework

### Python 3.12+
- Latest stable release with improved performance and error messages
- Type hint syntax improvements (`list[str]`, `X | Y` union types) used throughout
- Long-term support through 2028

### Django 5.x (currently 5.2 LTS)
- Django 5.2 is an LTS release supported until April 2028 — appropriate for government clients who need stability
- Use `django-environ` for 12-factor environment configuration
- `INSTALLED_APPS` ordered: Django internals → third-party → Wagtail → civicos apps
- Split settings: `config/settings/base.py`, `development.py`, `production.py`, `test.py`

### Wagtail 6.x (currently 6.3+)
- Best-in-class Django CMS with excellent editor UX — critical for municipal staff adoption
- StreamField for flexible, structured page content (editors compose pages from blocks)
- Built-in image optimization and rendition system
- Wagtail locale system for bilingual (EN/FR) content trees
- Wagtail Snippets for reusable global content (menus, alerts, contact info)
- `wagtail.contrib.settings` for site-wide configuration managed by editors
- `wagtail.contrib.forms` as the base for the `forms` building block (extended heavily)
- `wagtail.contrib.redirects` for managing URL changes without developer involvement

---

## Database

### PostgreSQL 15+
- Only supported database. No SQLite in production; no MySQL.
- Rationale: full-text search, JSONB for flexible metadata, row-level security, excellent Django ORM support
- Use `psycopg[binary]` (psycopg3) — the modern async-compatible driver
- Connection pooling: `PgBouncer` in transaction mode for production deployments
- Migrations: Django migrations only; no Alembic or manual SQL migrations

---

## Authentication & Security

### `django-allauth`
- Handles registration, login, email verification, and social/SSO authentication
- Configured in headless mode for clean integration with our custom templates
- Providers: username/password, SAML 2.0 (via `python3-saml`), OIDC (via `mozilla-django-oidc`)

### `django-otp` + `django-two-factor-auth`
- TOTP-based MFA for staff accounts
- Required for all `is_staff` users; optional for citizens

### `argon2-cffi` (via `django[argon2]`)
- Password hashing algorithm; more resistant to GPU cracking than PBKDF2
- Set as `PASSWORD_HASHERS[0]` in settings

### `django-csp`
- Content Security Policy header management
- Configured to disallow inline scripts and restrict resource origins

---

## Task Queue & Caching

### Celery 5.x + Redis
- Celery for asynchronous tasks: sending emails, processing file uploads, scheduled jobs (retention purges, report generation)
- Redis as the message broker and result backend
- `django-celery-beat` for periodic tasks (cron-style scheduling managed in the database)

### Django Cache (Redis)
- Django's built-in Redis cache backend
- Session storage: database by default, with Redis as an optional deployment choice
- Cache timeout conventions: short-lived (5 min) for public pages, no cache for authenticated views with PII

---

## Frontend

### Progressive enhancement approach
The frontend is built on standard Django templates with progressive enhancement — the site works without JavaScript, then enhances with JS where it adds genuine value. This is a government requirement: forms and core services must be accessible to users on older browsers or with JS disabled.

### HTMX
- For partial page updates (form submission feedback, filtering, status polling) without a full JS framework
- Avoids the complexity of a SPA while enabling responsive, modern UX
- All HTMX interactions must degrade gracefully to full page loads

### Alpine.js
- For lightweight client-side interactivity (accordions, tabs, disclosure widgets, date pickers)
- Used sparingly; preference for CSS-only solutions where possible

### Stimulus (Wagtail admin)
- Wagtail 6.x ships with Stimulus for admin UI interactions
- Custom Stimulus controllers for any admin enhancements

### CSS
- Custom design system built on CSS custom properties (no utility-class framework in public templates)
- Follows the GC Design System principles for government-appropriate visual language
- SCSS compiled via `django-compressor` or `whitenoise` + build step
- Mobile-first responsive layout

---

## Email & Notifications

### GC Notify (primary for Canadian deployments)
- Canada's federal transactional notification service (email + SMS)
- Free for government organizations; built-in bilingual template support
- Python client: `notifications-python-client`

### Fallback: SMTP / SendGrid / AWS SES
- Configurable via environment variable `NOTIFICATION_BACKEND`
- `django-anymail` for unified interface across email providers

---

## File Storage

### Development: local filesystem
- `MEDIA_ROOT` for uploaded files during development

### Production: object storage
- AWS S3 / compatible (MinIO for on-prem) via `django-storages`
- Private bucket: files not publicly accessible; served via pre-signed URLs
- Virus scanning: ClamAV via `django-clamd` or integration with cloud scanning service
- File type validation: check magic bytes, not file extension

---

## Static Files

### `whitenoise`
- Serves static files efficiently from the Django process in production
- No separate static file server required for small-to-medium deployments
- Compressed and cached with long-lived cache headers

---

## Search

### Wagtail search (initially)
- `wagtail.search` with PostgreSQL backend for full-text search across CMS pages
- No separate search service required initially

### Elasticsearch / OpenSearch (future, large deployments)
- When search volume or complexity warrants it, switch to Elasticsearch backend
- Wagtail's search API is backend-agnostic — no code changes in page models

---

## Deployment

### Docker + Docker Compose (development)
```
services:
  web:      # Django/Wagtail
  db:       # PostgreSQL
  redis:    # Cache + Celery broker
  worker:   # Celery worker
  beat:     # Celery Beat (scheduler)
```

### Production: Kubernetes-ready
- Each service as a separate Deployment
- `gunicorn` + `uvicorn` workers as the WSGI/ASGI server
- Health checks: `/health/` endpoint (Django check framework)
- Horizontal scaling: stateless application tier; session in DB or Redis

### Infrastructure as Code
- Terraform modules for AWS and Azure deployments (future)
- `helm` chart for Kubernetes deployment (future)

---

## Developer Tooling

| Tool | Purpose |
|---|---|
| `ruff` | Linting + formatting (replaces flake8, black, isort) |
| `mypy` | Static type checking |
| `pytest` + `pytest-django` | Test runner |
| `factory_boy` | Test fixtures |
| `coverage.py` | Test coverage reporting |
| `pip-audit` | Dependency vulnerability scanning |
| `pre-commit` | Git hooks for linting, secrets detection |
| `django-debug-toolbar` | Development query and performance inspection |
| `pa11y` / `axe-core` | Automated accessibility testing in CI |

---

## Key Third-Party Packages

| Package | Purpose |
|---|---|
| `django-environ` | Environment variable configuration |
| `django-allauth` | Authentication |
| `django-otp` | MFA (TOTP) |
| `django-extensions` | Development utilities (shell_plus, graph_models) |
| `django-storages` | Cloud file storage |
| `django-compressor` | Static asset compression |
| `django-csp` | Content Security Policy headers |
| `django-ratelimit` | Rate limiting for auth endpoints |
| `celery` + `redis` | Async task queue |
| `django-celery-beat` | Periodic tasks |
| `psycopg[binary]` | PostgreSQL driver (psycopg3) |
| `Pillow` | Image processing |
| `openpyxl` | Excel export for form submissions |
| `notifications-python-client` | GC Notify integration |

---

## Version Pinning Policy

- Pin all production dependencies to exact versions in `requirements/base.txt`
- Use `pip-compile` (from `pip-tools`) to generate pinned files from `requirements/*.in` source files
- Dependabot configured to open PRs for security updates automatically
- Minor/patch updates reviewed weekly; major updates planned deliberately

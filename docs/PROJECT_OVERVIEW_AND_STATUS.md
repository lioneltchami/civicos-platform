# Govstack Project Overview and Status

This document is a current-state summary of the Govstack repository: what the project is for, how it is structured, what is already in the codebase, and what work has been completed so far.

It is intentionally practical. The goal is to help a new contributor, stakeholder, or future maintainer understand the project without having to reconstruct the story from source files.

---

## 1. What Govstack Is

Govstack is a modular digital government services platform built with Django and Wagtail.

The platform is designed for municipal and public-sector use cases where the site needs to do more than publish static content. It is intended to support:

- A public-facing CMS for pages, announcements, and service information
- Dynamic forms for citizen intake
- A logged-in citizen portal
- Internal staff workflows for reviewing and routing requests
- Notifications for email, SMS, or in-app updates
- Audit logging for accountability and records management
- Authentication with email-based accounts and MFA support

The core idea is that each capability lives in its own Django app and can evolve as a reusable building block rather than as a monolithic one-off implementation.

---

## 2. Who The Project Is For

The repository and its docs describe Govstack as a fit for:

- Municipal governments
- Public-sector organizations
- Bilingual service environments
- Organizations that need accessibility, privacy, and auditability by default

The intended audiences include:

- Citizens using public services online
- Staff publishing site content
- Staff processing service requests
- IT administrators deploying and maintaining the stack
- Developers extending the platform

---

## 3. Core Design Principles

The repository consistently emphasizes these themes:

- Modular building blocks instead of one-off features
- Accessibility-first public UI
- Bilingual English/French support
- Privacy and records retention awareness
- Immutable audit trails for important actions
- Environment-driven configuration
- Docker-based local development

The repo docs also frame the project as aligned with government-grade requirements, especially around WCAG, privacy, and multilingual delivery.

---

## 4. Current Repository Structure

The main Django project is organized around these top-level pieces:

- `config/` for Django settings, URL routing, WSGI, and ASGI
- `apps/core/` for shared base models, middleware, logging, and signals
- `apps/cms/` for Wagtail page types, blocks, and snippets
- `apps/forms/` for Wagtail form-builder extensions
- `apps/portal/` for the citizen portal
- `apps/workflows/` for request routing and staff workflow models
- `apps/auth_extension/` for the custom user model and auth-related extensions
- `apps/notifications/` for delivery models, handlers, services, and tasks
- `apps/audit/` for immutable audit logging
- `templates/` for frontend templates
- `static/` for frontend assets
- `requirements/` for pinned dependency files
- `context/` for product, standards, and technical background

The repo is already laid out as a production-oriented Django project, not a starter template.

---

## 5. What Is Already Implemented

### 5.1 Django and Wagtail foundation

The project is already set up with:

- Django 5.2
- Wagtail 6.4
- PostgreSQL
- Redis
- Celery
- Docker Compose for local orchestration

The project settings are split into base, development, production, and test modules.

### 5.2 Custom authentication foundation

The repo includes a custom user model in `apps/auth_extension` that:

- Uses email as the login identifier
- Removes the username field
- Stores language preference
- Stores a phone number
- Stores security-related profile fields

This is already wired into `AUTH_USER_MODEL` in settings.

### 5.3 CMS foundation

The `apps/cms` app includes:

- Custom page models
- Wagtail block definitions
- Custom image and document models
- Snippet models for reusable site-wide content
- Wagtail hooks

The templates include base layout support and page templates for the main CMS experience.

### 5.4 Forms foundation

The `apps/forms` app extends Wagtail’s form builder with:

- Custom form fields
- A custom submission model
- Consent tracking
- Retention tracking
- A custom form page type

### 5.5 Citizen portal foundation

The `apps/portal` app includes:

- A service request model
- Status update tracking
- Portal URLs
- Portal views and templates

### 5.6 Workflow foundation

The `apps/workflows` app includes:

- Work item modeling
- Work item history tracking
- Status and assignment fields

### 5.7 Notifications foundation

The `apps/notifications` app includes:

- Notification models
- Delivery service/task scaffolding
- Notification handlers

### 5.8 Audit foundation

The `apps/audit` app includes:

- Immutable audit log entries
- A service layer
- Handler scaffolding

### 5.9 Base UI and templates

There is already a frontend skeleton:

- `templates/base.html`
- CMS page templates
- Portal templates
- Shared partials for breadcrumbs and metadata
- A base stylesheet in `static/css/main.css`

---

## 6. What Has Been Done So Far In This Session

This is the practical implementation work that has already been completed in the current repo state.

### 6.1 Fixed `make migrate`

`make migrate` previously failed during Django startup and system checks.

The main issues that were fixed:

- A `two_factor` URL compatibility issue where the installed package exposed `urlpatterns` as a tuple rather than a plain list
- A reverse relation clash between Wagtail’s built-in `FormSubmission` model and the project’s custom `forms.FormSubmission`
- A missing initial migration for `apps.auth_extension`
- The local database already contained Wagtail tables, so `migrate` needed to be able to reconcile existing schema state with the migration graph

The Makefile now runs:

- `python manage.py migrate --fake-initial`

That allows the local database to be brought into a consistent migration state without failing on tables that already exist.

### 6.2 Fixed `make superuser`

`make superuser` originally failed because Django’s interactive `createsuperuser` command is not reliable in a non-TTY container run.

The repo now includes a custom management command:

- `python manage.py bootstrap_superuser`

This command:

- Creates or updates a superuser by email
- Works noninteractively
- Uses `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD` when provided
- Generates a temporary password if none is supplied
- Is idempotent, so rerunning it does not break the account

The Makefile now points `make superuser` at that command.

### 6.3 Added a baseline auth migration

A new initial migration was generated for the custom auth app:

- `apps/auth_extension/migrations/0001_initial.py`

That was required so Django could resolve the migration graph correctly.

### 6.4 Added bootstrap documentation support

The example environment file now mentions the superuser bootstrap variables:

- `DJANGO_SUPERUSER_EMAIL`
- `DJANGO_SUPERUSER_PASSWORD`

That makes the new setup path discoverable for anyone bootstrapping the repo locally.

---

## 7. Current Operational Status

At the time this document was written:

- `make migrate` completes successfully
- `make superuser` completes successfully
- The local bootstrap superuser account exists at `admin@govstack.local`

There are still non-blocking warnings during Django checks, including:

- `WAGTAILADMIN_BASE_URL` not being set
- Notes from Django that several local apps still have model changes not yet captured in migrations

Those warnings do not currently block the Make targets above, but they should be addressed as the project moves toward fuller migration coverage.

---

## 8. What The Codebase Still Appears To Be Working Toward

This repository is not a finished public service platform yet. It is a strong scaffold with several core paths in place, but there are still areas that look intentionally unfinished or still under active development.

Likely next phases include:

- Filling out the service request lifecycle end to end
- Completing the workflow layer and linking it to portal actions
- Expanding notification delivery behavior
- Adding migration coverage for the remaining apps
- Hardening templates and accessibility details
- Adding test coverage around the implemented service flows
- Completing deployment and environment-specific configuration

In short, the platform is already structurally coherent, but it is still in the build-out stage rather than the “feature-complete” stage.

---

## 9. How To Think About The Code Today

The best way to read this repository is:

- `config/` defines the project and how it starts
- `apps/core/` provides the shared foundation
- `apps/cms/` is the public content layer
- `apps/forms/` handles structured intake
- `apps/portal/` is where authenticated citizen interactions live
- `apps/workflows/` is the staff processing engine
- `apps/notifications/` carries updates outward
- `apps/audit/` preserves accountability
- `apps/auth_extension/` customizes identity and login

That is the intended architecture, and the current codebase already reflects it.

---

## 10. Useful Files For Orientation

- [README.md](/Users/lionel/builders/govstack/README.md)
- [ARCHITECTURE.md](/Users/lionel/builders/govstack/ARCHITECTURE.md)
- [COMPLIANCE.md](/Users/lionel/builders/govstack/COMPLIANCE.md)
- [config/settings/base.py](/Users/lionel/builders/govstack/config/settings/base.py)
- [config/urls.py](/Users/lionel/builders/govstack/config/urls.py)
- [Makefile](/Users/lionel/builders/govstack/Makefile)
- [apps/auth_extension/models.py](/Users/lionel/builders/govstack/apps/auth_extension/models.py)
- [apps/forms/models.py](/Users/lionel/builders/govstack/apps/forms/models.py)
- [apps/portal/models.py](/Users/lionel/builders/govstack/apps/portal/models.py)
- [apps/workflows/models.py](/Users/lionel/builders/govstack/apps/workflows/models.py)
- [apps/notifications/models.py](/Users/lionel/builders/govstack/apps/notifications/models.py)
- [apps/audit/models.py](/Users/lionel/builders/govstack/apps/audit/models.py)

---

## 11. Summary

Govstack is a Django + Wagtail municipal services platform built around modular blocks: CMS, forms, portal, workflows, notifications, audit, and auth.

The repo already contains the main structural pieces for that architecture, plus templates and supporting config. In this session, the critical bootstrap path was brought into a working state:

- migrations now run in the current local database state
- superuser creation now works noninteractively
- the project has a base auth migration
- the URL and form submission issues that broke startup were resolved

This means the repository is now in a much better state for real feature work, testing, and deeper product build-out.

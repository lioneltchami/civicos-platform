# Govstack Code Handoff for AI

This document is a technical snapshot of the code that exists in the repository right now.

It is written for another AI tool or developer that needs to understand the codebase quickly and continue working without first reconstructing the repo from scratch.

---

## 1. What This Repository Contains

Govstack is a Django + Wagtail application structured as a set of modular apps for public-sector digital services.

The codebase currently includes:

- Core Django/Wagtail configuration
- A custom user model and auth extension
- CMS page and snippet scaffolding
- Wagtail form builder extensions
- A citizen portal model layer and views
- Workflow models for internal staff processing
- Notification models and delivery scaffolding
- Audit logging models and handlers
- Docker Compose and Makefile-based developer workflow

The code is already beyond a scaffold. There are concrete models, templates, and operational commands in place.

---

## 2. Top-Level Project Wiring

### 2.1 Django startup

`manage.py` sets the default settings module to:

- `config.settings.development`

That means local commands run against the development settings unless overridden.

### 2.2 Settings layout

The settings package is split into:

- `config/settings/base.py`
- `config/settings/development.py`
- `config/settings/production.py`
- `config/settings/test.py`

The base settings file already defines the major app composition:

- Django built-ins
- Wagtail apps
- Third-party auth / OTP / Celery / storage apps
- Local Govstack apps

The configured custom user model is:

- `AUTH_USER_MODEL = "auth_extension.User"`

### 2.3 URL configuration

The main URL router is `config/urls.py`.

It currently maps:

- `/cms/` to Wagtail admin
- `/django-admin/` to Django admin
- `/documents/` to Wagtail documents
- `/account/two-factor/` to two-factor auth views
- `/account/` to django-allauth
- `/health/` to health check URLs
- `/portal/` to the citizen portal
- `/` to Wagtail public pages
- `__debug__/` to debug toolbar in development

There is also a compatibility shim in `config/urls.py` for `two_factor.urls`, because the installed package exposes `urlpatterns` as a tuple instead of a plain list.

---

## 3. Implemented Apps

## 3.1 `apps/core`

This app provides shared primitives.

Files present include:

- `models.py`
- `middleware.py`
- `signals.py`
- `logging.py`
- `context_processors.py`

The important model classes are:

- `UUIDModel`
- `TimestampedModel`
- `SoftDeleteModel`
- `BaseModel`

These are the shared base classes used by the portal, notifications, workflows, and audit apps.

### Notable behavior

- `UUIDModel` gives models a UUID primary key
- `TimestampedModel` adds `created_at` and `updated_at`
- `SoftDeleteModel` adds a `deleted_at` marker
- `BaseModel` combines UUID + timestamps

This is the project’s shared data foundation.

---

## 3.2 `apps/auth_extension`

This app customizes Django authentication.

### Main model

`apps/auth_extension/models.py` defines:

- `GovstackUserManager`
- `User`

### User model details

The custom `User` model:

- Removes `username`
- Uses `email` as the login field
- Adds `preferred_language`
- Adds `last_login_ip`
- Adds `terms_accepted_at`
- Adds `phone_number`
- Sets `USERNAME_FIELD = "email"`
- Leaves `REQUIRED_FIELDS = []`

### Manager details

`GovstackUserManager` implements:

- `_create_user`
- `create_user`
- `create_superuser`

This keeps auth compatible with email-based login.

### Migration status

There is now an initial migration:

- `apps/auth_extension/migrations/0001_initial.py`

This migration is required for Django to build the migration graph correctly.

### Bootstrap command

A custom management command exists at:

- `apps/auth_extension/management/commands/bootstrap_superuser.py`

This command:

- Creates or updates a superuser by email
- Uses `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD` when present
- Generates a temporary password when one is not supplied
- Is idempotent
- Works in non-TTY Docker runs

The Makefile now points `make superuser` at this command.

---

## 3.3 `apps/cms`

This is the Wagtail CMS layer.

Files currently present include:

- `models.py`
- `blocks.py`
- `wagtail_hooks.py`

The codebase includes custom media models and page/snippet scaffolding.

### Notable implemented pieces

- `CustomImage`
- `CustomRendition`
- `CustomDocument`
- `SiteAlert`
- `NavigationMenu`
- `HomePage`

The CMS models are already structured around Wagtail conventions:

- `Page` subclasses for page types
- snippet-style reusable models
- image/document extensions

The templates directory also includes CMS templates, including a base page layout.

---

## 3.4 `apps/forms`

This app extends Wagtail forms.

### Main classes

`apps/forms/models.py` currently defines:

- `FormField`
- `FormSubmission`
- `FormPage`

### FormField

`FormField` extends Wagtail’s `AbstractFormField` and adds:

- `help_text_long`
- `is_pii`
- a `ParentalKey` to `forms.FormPage`

### FormSubmission

`FormSubmission` extends Wagtail’s `AbstractFormSubmission` and adds:

- `consent_given`
- `consent_text_shown`
- `submitter_ip`
- `expires_at`

It also overrides the inherited `page` relation with:

- `related_name="govstack_form_submissions"`

That avoids a reverse accessor clash with Wagtail’s built-in form submission model.

### FormPage

`FormPage` extends `AbstractEmailForm` and adds:

- `intro`
- `thank_you_text`
- `consent_text`
- `retention_days`

It binds:

- `form_field = FormField`
- `submission_class = FormSubmission`

The page also defines admin panels and templates:

- `forms/form_page.html`
- `forms/form_page_landing.html`

### Important implementation note

The recent runtime fix in this app was to prevent `Page.formsubmission_set` from colliding with Wagtail’s built-in model relation.

---

## 3.5 `apps/portal`

This app represents the authenticated citizen portal.

### Models

`apps/portal/models.py` defines:

- `ServiceRequestStatus`
- `ServiceRequest`
- `StatusUpdate`

### ServiceRequest

`ServiceRequest` stores:

- `citizen`
- `service_page_id`
- `service_name`
- `status`
- `reference_number`
- `submission_data`
- `internal_notes`
- `expires_at`

It uses `BaseModel`, so it gets UUID + timestamps.

### StatusUpdate

`StatusUpdate` stores the history of changes to a request:

- `service_request`
- `old_status`
- `new_status`
- `changed_by`
- `public_note`

### Routing and templates

Portal routes live in:

- `apps/portal/urls.py`

Portal templates currently include:

- `templates/portal/dashboard.html`
- `templates/portal/request_list.html`
- `templates/portal/request_detail.html`

The portal is present, but likely still thin compared to the overall product vision.

---

## 3.6 `apps/workflows`

This app models internal staff work.

### Models

`apps/workflows/models.py` defines:

- `WorkItemStatus`
- `WorkItem`
- `WorkItemHistory`

### WorkItem

Stores:

- `content_type`
- `object_id`
- `title`
- `status`
- `assigned_to`
- `due_at`
- `priority`

This is generic enough to attach to portal requests or form submissions.

### WorkItemHistory

Stores the history of actions on a work item:

- `work_item`
- `action`
- `old_status`
- `new_status`
- `actor`
- `notes`

The workflow layer is currently modeled, but the more advanced service/action logic still appears to be in progress.

---

## 3.7 `apps/notifications`

This app is the outbound messaging layer.

### Models

`apps/notifications/models.py` defines:

- `NotificationChannel`
- `NotificationStatus`
- `Notification`

### Notification

Stores:

- `recipient`
- `channel`
- `subject`
- `body`
- `language`
- `status`
- `sent_at`
- `external_id`
- `read_at`

The app also includes:

- `handlers.py`
- `services.py`
- `tasks.py`

This suggests a separation between model storage, business logic, and background delivery.

---

## 3.8 `apps/audit`

This app is for immutable audit logging.

### Main model

`apps/audit/models.py` defines:

- `AuditEventType`
- `AuditLogEntry`

### AuditLogEntry fields

Stores:

- timestamp
- event type
- outcome
- actor metadata
- resource metadata
- state snapshots
- request/session correlation
- hash chain fields

### Immutable behavior

The model overrides:

- `save()` to prevent updates after creation
- `delete()` to prevent deletion

It also computes a SHA-256 hash over the event payload.

This is a strong starting point for tamper-evident logging.

The app also contains:

- `handlers.py`
- `services.py`

---

## 4. Developer Workflow Code

### 4.1 Makefile

The `Makefile` is already a central command surface for the repo.

Important commands include:

- `make up`
- `make down`
- `make build`
- `make logs`
- `make migrate`
- `make migrations`
- `make superuser`
- `make check`
- `make test`
- `make lint`
- `make format`

### Current key changes

`make migrate` now runs:

- `docker compose run --rm web python manage.py migrate --fake-initial`

`make superuser` now runs:

- `docker compose run --rm web python manage.py bootstrap_superuser`

This was necessary to make the repo usable in the current local database/container setup.

### 4.2 Environment example

`.env.example` documents the common settings and now also mentions:

- `DJANGO_SUPERUSER_EMAIL`
- `DJANGO_SUPERUSER_PASSWORD`

Those variables are optional inputs to the bootstrap command.

---

## 5. Templates And Static Assets

The current template set includes:

- `templates/base.html`
- `templates/includes/meta.html`
- `templates/includes/breadcrumb.html`
- `templates/forms/form_page.html`
- `templates/forms/form_page_landing.html`
- `templates/portal/dashboard.html`
- `templates/portal/request_list.html`
- `templates/portal/request_detail.html`

Static assets currently include:

- `static/css/main.css`

This means the public UI is present, but the design system and final content polish may still be evolving.

---

## 6. What Was Fixed Recently

These are the concrete fixes already applied to the repo.

### 6.1 two_factor URL compatibility

The installed `django-two-factor-auth` package exposes `urlpatterns` as a tuple in this environment.

That caused Django URL resolution to fail during system checks.

The fix in `config/urls.py` normalizes that tuple into:

- a list of URL patterns
- a namespace value

This prevents the URL resolver from crashing at startup.

### 6.2 FormSubmission reverse relation clash

The custom `forms.FormSubmission` model originally inherited the default reverse relation name from Wagtail’s `AbstractFormSubmission`.

That clashed with Wagtail’s own `Page.formsubmission_set` relation.

The fix was to override the `page` field in `FormSubmission` with:

- `related_name="govstack_form_submissions"`

### 6.3 Missing auth migration

The `auth_extension` app had no migration history even though it is part of `INSTALLED_APPS` and provides the custom `AUTH_USER_MODEL`.

That prevented Django from building the migration graph.

The fix was to add:

- `apps/auth_extension/migrations/0001_initial.py`

### 6.4 Bootstrap superuser flow

The normal interactive `createsuperuser` command is not reliable in this repo’s Dockerized, non-TTY execution flow.

The fix was to add a dedicated management command:

- `bootstrap_superuser`

This is now the canonical `make superuser` path.

### 6.5 Local DB reconciliation

The current development database already contains Wagtail tables and a partial migration history.

To avoid failing on existing schema objects, the Makefile migration target now uses:

- `--fake-initial`

That makes the migration path idempotent for the current local setup.

---

## 7. Current State of the Codebase

### Working now

- Django app startup in the dev container
- Database migration command
- Superuser bootstrap command
- Custom auth model and migration graph
- Core CMS / portal / workflow / notifications / audit model scaffolding

### Still in-progress or scaffolded

- Full form submission workflow
- Portal service request lifecycle completion
- End-to-end workflow actions and assignment flows
- Notification delivery implementation depth
- Migration coverage for all local apps
- Test coverage across the implemented models/services
- Final UI polishing and accessibility validation

---

## 8. Practical Reading Order For Another AI Tool

If another AI needs to continue work here, the best order is:

1. `config/settings/base.py`
2. `config/urls.py`
3. `apps/auth_extension/models.py`
4. `apps/auth_extension/management/commands/bootstrap_superuser.py`
5. `apps/forms/models.py`
6. `apps/portal/models.py`
7. `apps/workflows/models.py`
8. `apps/notifications/models.py`
9. `apps/audit/models.py`
10. `Makefile`

That sequence covers startup, identity, intake, portal, work routing, messaging, audit, and developer commands.

---

## 9. Short Summary

Govstack already has real code in place for:

- the platform foundation
- the custom user model
- CMS scaffolding
- form handling
- citizen portal models
- workflow models
- notifications
- audit logging
- developer bootstrap commands

The repo is not just a plan. It is a functioning Django/Wagtail codebase with a modular design, and the recent fixes brought the local bootstrap path into a working state.

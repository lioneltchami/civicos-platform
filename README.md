# Govstack

**Modular, production-grade digital government services platform for municipal and public sector clients.**

Built on [Wagtail](https://wagtail.org/) + [Django](https://www.djangoproject.com/), Govstack gives municipalities a composable set of building blocks to deliver citizen-facing digital services — without rebuilding from scratch every time.

---

## Vision

Governments at every level are under pressure to modernize service delivery while meeting strict requirements around accessibility, privacy, security, and accountability. Most off-the-shelf solutions either lock clients into rigid workflows or require expensive customization.

Govstack takes a different approach: a **modular building-block architecture** where each capability (CMS, forms, citizen portal, workflow engine, notifications, audit logging) is a self-contained, reusable module that can be deployed independently or combined into a full platform.

The result is a platform that municipal IT teams can own, extend, and maintain — and that editors and service designers can operate without developer support.

---

## Who It's For

| User | Role |
|---|---|
| **Municipal staff / editors** | Publish content, manage forms, triage service requests |
| **Service designers** | Configure workflows and forms without code |
| **IT administrators** | Deploy, configure, and maintain the platform |
| **Citizens / residents** | Access services, submit requests, track status |
| **Developers / integrators** | Extend and integrate with existing municipal systems |

---

## Key Differentiators

- **Modular building blocks** — adopt one module or all of them; each stands alone
- **Government-grade compliance** — WCAG 2.1 AA, PIPEDA/Quebec Law 25, bilingual (EN/FR), full audit trails
- **Editor-friendly** — Wagtail's best-in-class CMS with structured content via StreamField
- **Security by default** — role-based access, MFA support, encrypted at rest and in transit
- **GovStack-inspired** — architecture aligned with the [GovStack initiative](https://govstack.global/) building block specifications
- **Open source** — no vendor lock-in; deployable on any infrastructure (cloud, on-prem, hybrid)

---

## Core Modules

| Module | Description |
|---|---|
| `core` | Shared utilities, base models, middleware, settings management |
| `cms` | Wagtail-powered content management with structured page types |
| `forms` | Dynamic form builder with submission management and export |
| `portal` | Authenticated citizen portal — accounts, service requests, status tracking |
| `workflows` | Configurable approval and routing workflows for internal staff |
| `auth` | Authentication, authorization, MFA, SSO (SAML/OIDC) |
| `notifications` | Email, SMS, and in-app notifications with template management |
| `audit` | Immutable audit logging for all significant system events |

---

## Tech Stack

- **Backend:** Python 3.12+, Django 5.x, Wagtail 6.x
- **Database:** PostgreSQL 15+
- **Task queue:** Celery + Redis
- **Frontend:** HTMX + Alpine.js for progressive enhancement; Wagtail Stimulus for admin
- **Deployment:** Docker + Docker Compose (dev), Kubernetes-ready for production
- **CI/CD:** GitHub Actions

See [`context/tech-stack.md`](context/tech-stack.md) for detailed decisions.

---

## Getting Started

> Full setup instructions coming in `docs/setup.md` once the project is initialized.

```bash
git clone https://github.com/your-org/govstack.git
cd govstack
cp .env.example .env
docker compose up
```

---

## Compliance & Standards

This platform is built to meet Canadian public sector requirements. See [`COMPLIANCE.md`](COMPLIANCE.md) for the full compliance matrix.

---

## Architecture

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the modular building block design and module interaction patterns.

## Project Status

See [`docs/PROJECT_OVERVIEW_AND_STATUS.md`](docs/PROJECT_OVERVIEW_AND_STATUS.md) for a current-state summary of what the project is, what is implemented, and what has been completed so far.

---

## License

[MIT License](LICENSE) — free to use, modify, and deploy.

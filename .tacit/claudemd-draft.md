# CLAUDE.md — govstack

> Source of truth for AI agents. Read completely before writing code.

**Sections:** [Identity](#identity) | [Quick Start](#quick-start) | [Structure](#structure) | [Data Model](#data-model) | [API Surface](#api-surface) | [Build & Test](#build--test) | [Boundaries](#boundaries) | [When In Doubt](#when-in-doubt)

## Identity

| Attribute | Value |
|-----------|-------|
| **Name** | govstack |
| **Description** | **Modular, production-grade digital government services platform for municipal and public sector clients.** |
| **Type** | web-app |
| **Languages** | JavaScript, Python |
| **Frameworks** | GitHub Actions, React, Tailwind CSS |
| **Package Manager** | pip |
| **Non-code files** | 26 markdown, 6 YAML, 5 JSON, 5 docker, 1 CI configs, 12 images |

## Quick Start

```bash
pip install -r requirements.txt
npm run build                       # build
pytest                              # test
npm run dev                         # dev server
```

## Structure

| Layer | Path | Responsibility |
|-------|------|---------------|
| Domain (pure logic) | `+ 8 more packages` | — |
|  | `apps/api/{api,documents,notifications,portal,tests,volunteers,workflows}/` | — |
|  | `apps/appointments/{appointments,services,tests}/` | — |
|  | `apps/audit/management/commands` | — |
|  | `apps/backoffice/{backoffice,forms,tests,views}/` | — |
|  | `apps/cms` | — |
|  | `apps/consent/management/commands` | — |
|  | `apps/consent/{consent,tests}/` | — |
|  | `apps/core/{core,tests}/` | — |
|  | `apps/documents/management/commands` | — |
|  | `apps/documents/{documents,services,tests,views}/` | — |
|  | `apps/payments/management/commands` | — |
|  | `apps/payments/{payments,gateways,services,tests,views}/` | — |
|  | `apps/reports/{reports,exports,services,tests,views}/` | — |
|  | `apps/volunteers/management/commands` | — |
| Infrastructure | `apps/appointments/migrations` | — |
|  | `apps/audit/migrations` | — |
|  | `apps/auth_extension/migrations` | — |
|  | `apps/cms/migrations` | — |
|  | `apps/consent/migrations` | — |
|  | `apps/core/migrations` | — |
|  | `apps/documents/migrations` | — |
|  | `apps/forms/migrations` | — |
|  | `apps/notifications/migrations` | — |
|  | `apps/payments/migrations` | — |
|  | `apps/portal/migrations` | — |
|  | `apps/reports/migrations` | — |
|  | `apps/volunteers/migrations` | — |
|  | `apps/workflows/migrations` | — |
| Libraries | `apps/audit/management` | — |
|  | `apps/auth_extension/management` | — |
|  | `apps/auth_extension/management/commands` | — |
|  | `apps/cms/tests` | — |
|  | `apps/consent/management` | — |
|  | `apps/core/management/commands` | — |
|  | `apps/core/{management,urls}/` | — |
|  | `apps/documents/management` | — |
|  | `apps/forms/templatetags` | — |

### Import Rules

- apps/ always imports config at root level
- config layer imports and coordinates all apps/
- API handlers isolated in apps/api/{domain}/ with dedicated URL routers
- Each app has self-contained tests/ subdirectory—never cross-app test imports
- Management commands in apps/{app}/management/commands—never import from views or models directly

Backend: Django apps in /apps organized by domain, each with models, views, tests, and migrations. Frontend: React + Tailwind CSS (separate build). API layer: centralized routing in /apps/api/{documents,notifications,portal,volunteers}/ with dedicated URL handlers. Root config imports all apps; apps

## Data Model

| File | Key Types |
|------|-----------|
| `apps/appointments/models.py` |  |
| `apps/audit/models.py` |  |
| `apps/cms/models.py` |  |
| `apps/consent/models.py` |  |
| `apps/core/models.py` |  |

## API Surface

| File | Role | Key Methods |
|------|------|-------------|
| `apps/api/documents/urls.py` | api_handler |  |
| `apps/api/notifications/urls.py` | api_handler |  |
| `apps/api/portal/urls.py` | api_handler |  |
| `apps/api/urls.py` | api_handler |  |
| `apps/api/volunteers/urls.py` | api_handler |  |

## Build & Test

```bash
pytest                              # run tests
npm run build                       # build
npm run dev                         # dev server
```

## Boundaries

**Always:**
- Run pytest before committing—each app has isolated test coverage
- Review Django migrations for schema correctness—never auto-migrate production
- Test API endpoints after routing changes in apps/api/

**Ask first:**
- Modifying apps/core or apps/config—these affect all dependent apps
- Database schema changes (models.py) and related migrations
- Adding new apps or API routes—requires coordination with URL structure

**Never:**
- Commit API keys, secrets, or credentials—use environment variables only
- Skip Django migrations in production deployments
- Force-push to main—uses GitHub Actions CI/CD pipeline

## When In Doubt

1. Identify which app(s) your change touches—run that app's tests/ suite locally first (pytest)
2. Django migrations are auto-generated but must be reviewed for schema safety before deploy
3. API changes require testing both backend (pytest api/) and frontend (npm run build)
4. Frontend and backend are separate build processes—coordinate timing for API surface changes


---

*Generated by Tacit on 2026-07-24*

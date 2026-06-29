# Working Rules

These are the operating rules for how Claude should work on this project. They apply to every task, every file, every suggestion.

---

## Code Quality

### Style and conventions
- Follow PEP 8 strictly; use `ruff` for linting and formatting
- Maximum line length: 100 characters
- Use type hints on all function signatures (Python 3.12+ style: `list[str]` not `List[str]`)
- Prefer explicit over implicit — clarity beats cleverness
- Name things accurately: a function that creates a user should be called `create_user`, not `make_user` or `new_user`

### Django conventions
- Fat service layer, thin views: business logic lives in `services.py`, not in views, models, or signals
- Models are for data structure and basic validation only — no business logic in model methods beyond `clean()` and `save()` overrides
- Use `select_related` and `prefetch_related` proactively; never let N+1 queries into production
- Use `get_object_or_404` for public views; raise proper exceptions in service functions
- Class-based views preferred for consistency; function-based views acceptable for simple endpoints
- Always use `reverse()` or `{% url %}` — never hard-code URLs
- Write model `__str__` methods that return something human-readable

### Wagtail conventions
- Define StreamField blocks in `blocks.py`; keep `models.py` focused on page structure
- Use `StructBlock` over raw `StreamBlock` for typed, reusable content structures
- Register Wagtail hooks in `wagtail_hooks.py`, not in `models.py`
- Use `Page.get_context()` for passing data to templates; avoid template tags for data fetching
- Keep page models lean — delegate rendering logic to template tags or context processors when needed

### Security (non-negotiable)
- Never disable CSRF protection
- Never use `mark_safe()` on user-supplied input
- Never construct SQL with string formatting — always use ORM or parameterized queries
- Never log PII (names, emails, SINs, addresses) in application logs
- All file uploads must be validated (type + size) and stored outside the web root
- Never commit secrets — use environment variables; check with `git-secrets` pre-commit hook

### Accessibility (non-negotiable)
- Every HTML template must pass axe-core with zero violations
- Forms: every `<input>` has a `<label>` with matching `for`/`id`; error messages reference the field name
- Interactive elements: all focusable via keyboard, with visible focus style
- Don't add `role` or `aria-*` attributes unless native HTML semantics are insufficient
- Test with keyboard navigation before marking any UI work complete

---

## When to Ask Clarifying Questions

Ask before starting if:
- The requirement touches PII collection or processing (data model decisions are hard to reverse)
- There are two or more reasonable architectural approaches with meaningfully different trade-offs
- The task could affect the audit log schema (adding new event types needs thought)
- The task involves a new dependency (assess security, maintenance status, licence)
- The scope is ambiguous and getting it wrong would waste significant effort

Don't ask about:
- Style choices that are covered by the conventions above
- Django/Wagtail patterns that have a clear best practice
- Minor implementation details where a reasonable default exists

When asking, ask one focused question — not a list of five. Identify the most important decision and ask that.

---

## Approach to Security & Compliance

- Treat WCAG, privacy, bilingual, and audit requirements as **first-class acceptance criteria** — a feature is not done until it meets them
- When adding any new model with user data, ask: what is the retention period? Is this PII? Does it need an audit log entry?
- When writing any view that returns user data, ask: is this access logged? Is it scoped to the correct user?
- When adding a new form field, ask: is this field necessary? What happens to the data?
- Security issues found during development should be flagged immediately, not deferred

---

## How to Structure Changes

### Commits
Each commit should represent one logical change. Commit messages follow Conventional Commits:

```
feat(forms): add file upload field type with type validation
fix(portal): correct French translation for status labels
chore(deps): upgrade django to 5.2.1
docs(compliance): add DSAR workflow documentation
```

Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `style`, `perf`

### Pull requests / change sets
- One feature or fix per PR
- PR description explains the *why*, not just the *what*
- Tests must pass; new features require new tests
- Accessibility and compliance checklist reviewed before merge

### File organization
When adding a new feature to a module, follow this order:
1. Model (if new data is needed)
2. Migration
3. Service function
4. Form or serializer
5. View
6. URL
7. Template
8. Tests (for each layer above)
9. Translation strings

---

## Testing Expectations

- **Unit tests** for all service functions — mock external dependencies
- **Integration tests** for views — use Django test client, not mocks
- **Form validation tests** — test both valid and invalid inputs, including edge cases
- **Accessibility tests** — axe-core in CI for all new templates
- **Factory Boy** for test fixtures; never use fixtures files
- Aim for 90%+ coverage on `services.py` files; views and models need coverage too
- Tests live in `tests/` within each app, mirroring the module structure

---

## Dependency Policy

Before adding a new dependency:
1. Check if Django or the standard library already provides it
2. Check the package's maintenance status (last release, open issues, active maintainers)
3. Check the licence (must be MIT, BSD, Apache 2.0, or equivalent permissive licence)
4. Check for known CVEs via `pip-audit`
5. Add it to `requirements/base.txt` with a pinned version and a comment explaining why it's needed

Avoid packages that wrap Django internals in ways that make upgrades difficult.

---

## Working with Translations

- All user-visible strings in Python code: `from django.utils.translation import gettext_lazy as _; _("string")`
- All user-visible strings in templates: `{% load i18n %}` then `{% trans "string" %}`
- After adding new strings, run `django-admin makemessages -l fr` and add French translations before the PR is merged — do not leave `msgstr ""` entries
- Translation files live in `locale/en/LC_MESSAGES/django.po` and `locale/fr/LC_MESSAGES/django.po`

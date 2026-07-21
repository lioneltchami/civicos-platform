# GovStack BB Re-Scan: Progress & Certification Readiness Check
## Reusable Prompt — Run this any time to get an updated status

**Purpose:** Paste this file as a prompt whenever you want a fresh, evidence-based scan
of how CivicOS has progressed toward GovStack BB certification. It compares the current
codebase state against the last known baseline recorded in
`GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md` and tells you what has moved, what is
closer to certifiable, and what the correct next action is.

**Author:** CivicOS project
**Last updated:** 2026-07-17
**Applies to codebase at:** `/Users/lionel/builders/govstack`
**Baseline document:** `GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md` (root of repo)

---

## INSTRUCTIONS FOR THE SCANNER

You are running a **progress re-scan** of GovStack Building Block certification readiness
for the CivicOS codebase. This is not a first-time assessment — a baseline already
exists. Your job is to:

1. **Re-check the hard evidence** that the baseline relied on (the filesystem check,
   test counts, govstack_views.py presence) and see what has changed.
2. **Identify what is closer to certifiable** than it was at the baseline date.
3. **Flag any regression** — things that were at a certain tier and have dropped.
4. **Update the status table** with the current date and current tiers.
5. **Give a concrete next action** for each BB that is not yet certified.

### Non-Negotiable Rules

- **Never trust comments, file names, or previous assessments as evidence.** Read the
  actual code. A `govstack_views.py` that exists but only has stub methods (`pass` or
  `raise NotImplementedError`) does NOT count as "In Progress".
- **Count only passing tests.** Run `python manage.py test --settings=config.settings.test`
  and use the actual output. Do not reuse old test counts.
- **Check the live spec, not your memory.** GovStack specs evolve. Fetch each relevant
  spec before comparing.
- **Separate CivicOS internal readiness from GovStack certifiability.** These are
  different metrics. Track them in separate columns. 1,000 internal tests do not count
  toward GovStack status.
- **Use only these four GovStack tiers — no rounding up:**

  | Tier | Symbol | Hard criteria (ALL must be true) |
  |------|--------|----------------------------------|
  | Not Started | 🔴 | No `govstack_views.py` or it only has stubs |
  | In Progress | 🟠 | `govstack_views.py` exists with real logic, but not all spec endpoints covered or harness not yet submitted |
  | Harness Submitted | 🔵 | Submitted to `testing.govstack.global` and awaiting result |
  | Certified | 🟢 | Has a documented passing harness run — actual certificate or passing CI badge in the repo |

---

## STEP 1 — Baseline Snapshot (do not skip)

Before scanning anything, read the current baseline so you can compare deltas:

```
Read file: /Users/lionel/builders/govstack/GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md
```

Note from the baseline:
- **Date of last scan:** 2026-07-17
- **Only BB with a GovStack API layer at baseline:** `apps/consent/`
- **Baseline GovStack tiers:**
  - Consent: 🔵 Harness submitted
  - All others: 🔴 Not started
- **Known pending issues at baseline:** H-03 (serializedSnapshot type mismatch), M-08 (pagination envelopes)

---

## STEP 2 — Hard Evidence Checks

Run each of the following checks and record the actual output. Do not interpolate or
estimate — use the real results.

### 2a. GovStack API Layer Presence

Check which apps now have a GovStack API layer:

```bash
find apps -name "govstack_views.py" -o -name "govstack_urls.py" | sort
```

**Baseline result (2026-07-17):**
```
apps/consent/govstack_views.py
apps/consent/govstack_urls.py
```

**What to look for in current scan:**
- Any new `govstack_views.py` file = a BB has advanced from 🔴 to 🟠 or better
- Any file that existed at baseline but is now gone = regression
- For each new file: open it and verify it has real logic, not stubs

### 2b. Stub Detection

For each `govstack_views.py` found, check it is not all stubs:

```bash
# For each govstack_views.py found:
grep -n "pass\|raise NotImplementedError\|TODO\|FIXME\|stub" apps/<app>/govstack_views.py
```

A view with only `pass` or `raise NotImplementedError` does NOT count as In Progress.

### 2c. Test Counts (current, passing only)

```bash
python manage.py test --settings=config.settings.test 2>&1 | tail -5
```

Then get per-app counts:

```bash
python manage.py test apps.consent --settings=config.settings.test 2>&1 | tail -3
python manage.py test apps.payments --settings=config.settings.test 2>&1 | tail -3
python manage.py test apps.documents --settings=config.settings.test 2>&1 | tail -3
python manage.py test apps.appointments --settings=config.settings.test 2>&1 | tail -3
python manage.py test apps.notifications --settings=config.settings.test 2>&1 | tail -3
python manage.py test apps.forms --settings=config.settings.test 2>&1 | tail -3
python manage.py test apps.cms --settings=config.settings.test 2>&1 | tail -3
python manage.py test apps.volunteers --settings=config.settings.test 2>&1 | tail -3
python manage.py test apps.reports --settings=config.settings.test 2>&1 | tail -3
```

**Baseline test counts (2026-07-17):**

| App | Baseline count |
|-----|---------------|
| `apps/consent` | 246 |
| `apps/payments` | 1,115 |
| `apps/documents` | 810 |
| `apps/appointments` | 359 |
| `apps/notifications` | 77 |
| `apps/forms` | 103 |
| `apps/cms` | 0 |
| `apps/volunteers` | 831 |
| `apps/reports` | 485 |
| `apps/auth_extension` | 75 |

Record current counts next to these and flag changes of ±20 or more.

### 2d. Migration Health

```bash
python manage.py migrate --settings=config.settings.test --check 2>&1
python manage.py showmigrations --settings=config.settings.test apps/consent 2>&1 | tail -10
```

Check there are no unapplied migrations and no migration conflicts.

### 2e. Consent BB — Outstanding Issues Check

Two issues were pending at baseline. Check if they are fixed:

**H-03: serializedSnapshot type mismatch**
The spec says `serializedSnapshot` should be a `string`. The code was returning a JSON object.

```bash
grep -n "serialized_snapshot\|serializedSnapshot" apps/consent/govstack_views.py apps/consent/serializers.py
```

Look for any `json.dumps()` wrapping that converts the object to a string before returning.

**M-08: Inconsistent pagination envelopes**
List endpoints were returning inconsistent shapes across the Consent BB.

```bash
grep -n "pagination_class\|page_size\|count\|results\|items\|total" apps/consent/govstack_views.py | head -20
```

Check all list views use the same paginator class and return the same envelope shape.

### 2f. Consent Harness Result

Has the harness returned a result since baseline?

Check for any of these in the repo:
```bash
ls -la apps/consent/ | grep -i "cert\|harness\|badge\|result"
grep -r "testing.govstack.global\|harness\|passing\|certified" README.md CONSENT-SUBMISSION-CHECKLIST.md 2>/dev/null | head -10
```

If a result is known (pass or fail), note it and what changed.

### 2g. Scheduler BB — Business Logic Completeness Check

Appointments is the next-priority BB for GovStack API layer. Before building it,
verify how complete the underlying business logic is:

```bash
# Wave coverage
grep -n "Wave\|wave" apps/appointments/models.py | head -20
grep -rn "on_booking_created\|on_booking_confirmed\|on_booking_cancelled\|on_booking_rescheduled" apps/appointments/receivers.py 2>/dev/null
# Service layer
ls apps/appointments/services.py 2>/dev/null || echo "No service layer yet"
# Slot model
grep -n "class Slot\|class Booking\|class AppointmentPolicy\|class ServiceLocation" apps/appointments/models.py
```

Record: how many receiver signals exist, whether a service layer exists, and which
Wave (1–8) is currently completed.

### 2h. Pending Known Fixes

Check whether these Round 3 findings (logged in the audit history) have been resolved:

```bash
# H-03: serializedSnapshot should be a string not object
grep -n "json.dumps" apps/consent/govstack_views.py apps/consent/serializers.py

# M-08: pagination consistency
grep -rn "class.*Pagination\|pagination_class" apps/consent/ | grep -v ".pyc"

# Check ruff is clean
cd /Users/lionel/builders/govstack && python -m ruff check apps/consent/ --select=F401,E711,E712 2>&1 | head -20
```

---

## STEP 3 — Per-BB Advancement Check

For each BB below, re-evaluate the tier based on STEP 2 evidence. Use the format shown.

### Consent BB (`bb-consent`)
**Spec:** `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/main/api/consent-openapi.yaml`

Re-check:
- Is the harness result in? Pass / Fail / Still pending?
- Are H-03 and M-08 fixed?
- Any new Codex findings since the last scan?
- Current tier: [fill in based on evidence]

---

### Scheduler BB (`bb-scheduler`) — Priority 1 for next GovStack layer
**Spec:** `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-scheduler/main/api/openapi.yaml`

Re-check:
- Does `apps/appointments/govstack_views.py` exist yet?
- If yes: how many spec endpoints are implemented vs. total in spec?
- If no: is there a `govstack_views.py` stub or a branch for it?
- How complete is the underlying business logic (which waves)?
- Current tier: [fill in based on evidence]

**What would move this from 🔴 to 🟠:**
- `govstack_views.py` with at least: availability query endpoint, slot reservation
  endpoint, booking confirmation endpoint, booking cancellation endpoint
- All connected to the existing `Booking` / `Slot` service layer (not direct ORM)
- Registered in `govstack_urls.py`

---

### File Management BB (`bb-file-management` or `bb-digital-registries`) — Priority 2
**Spec:** Try `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-file-management/main/api/openapi.yaml` first.
If 404: fall back to `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-digital-registries/main/api/openapi.yaml`

Re-check:
- Which spec URL is live?
- Does `apps/documents/govstack_views.py` exist yet?
- Current tier: [fill in based on evidence]

---

### Messaging BB (`bb-messaging`) — Priority 3
**Spec:** `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-messaging/main/api/openapi.yaml`

Re-check:
- Is SMS / GC Notify implemented? (`grep -rn "gc_notify\|sms\|SMS" apps/notifications/`)
- Does `apps/notifications/govstack_views.py` exist yet?
- Current tier: [fill in based on evidence]

**What would move this from 🔴 to 🟠:**
- SMS channel implemented (GC Notify API calls working)
- Delivery receipt / status callback model added
- `govstack_views.py` with at minimum: send message endpoint, delivery status endpoint

---

### Payments BB (`bb-payments`) — Priority 4 / Long term
**Spec:** `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-payments/main/api/openapi.yaml`

Re-check:
- Does a G2P / mobile money layer exist anywhere in the codebase?
  (`grep -rn "mojaloop\|g2p\|mobile_money\|disbursement" apps/ 2>/dev/null`)
- Does `apps/payments/govstack_views.py` exist (for a G2P layer, NOT Stripe)?
- Current tier: [fill in based on evidence]

**Reminder:** CivicOS Payments (Stripe) is not the same domain as GovStack Payments (G2P).
A G2P govstack layer would be a NEW parallel module, not a refactor of `apps/payments/`.

---

### Identity BB (`bb-identity`) — Not a priority / Integration, not implementation
**Spec:** `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-identity/main/api/openapi.yaml`

Re-check:
- Is there any OIDC / MOSIP integration in `apps/auth_extension/`?
  (`grep -rn "oidc\|mosip\|openid" apps/auth_extension/ 2>/dev/null`)
- Current tier: [fill in based on evidence]

**Reminder:** CivicOS is a relying party (RP), not an identity provider. GovStack Identity
BB certification means connecting TO a MOSIP IdP via OIDC, not replacing `auth_extension`.

---

### CMS BB (`bb-cms`) — Low priority (spec is nascent)
**Spec:** `https://raw.githubusercontent.com/GovStackWorkingGroup/bb-cms/main/api/openapi.yaml`
*(Expect 404 — spec may not exist yet. Note the spec maturity.)*

Re-check:
- Does `apps/cms/` now have tests? (`python manage.py test apps.cms --settings=config.settings.test`)
- Does `apps/cms/govstack_views.py` exist?
- Current tier: [fill in based on evidence]

---

## STEP 4 — Output: Updated Readiness Table

Produce an updated version of this table with today's date and the current tiers
based only on the evidence gathered in STEP 2 and STEP 3. Do not copy the baseline —
every cell must be re-verified.

```markdown
## GovStack BB Status — Re-Scan [DATE]

| BB / Module | GovStack Spec Repo | CivicOS App | Tests (current) | CivicOS Status | GovStack BB Status | Change since baseline |
|---|---|---|---|---|---|---|
| Consent BB | bb-consent | apps/consent/ | [n] | [status] | [tier] | [same/improved/regressed] |
| Scheduler BB | bb-scheduler | apps/appointments/ | [n] | [status] | [tier] | [same/improved/regressed] |
| File Management BB | bb-file-management | apps/documents/ | [n] | [status] | [tier] | [same/improved/regressed] |
| Messaging BB | bb-messaging | apps/notifications/ | [n] | [status] | [tier] | [same/improved/regressed] |
| Payments BB | bb-payments | apps/payments/ (Stripe) | [n] | [status] | [tier] | [same/improved/regressed] |
| Identity BB | bb-identity | apps/auth_extension/ | [n] | [status] | [tier] | [same/improved/regressed] |
| CMS BB | bb-cms | apps/cms/ | [n] | [status] | [tier] | [same/improved/regressed] |
```

---

## STEP 5 — Movement Analysis

After filling in the table, answer these questions in 2–3 sentences each:

**Q1. What has advanced since the baseline?**
Name any BB that moved to a higher tier. Quote the specific evidence (new file, new test
count, harness result). If nothing has advanced, say so clearly.

**Q2. What is the single most impactful action to take right now?**
Of everything that is not yet Certified, what one action would move the most BBs
forward or unlock the next certification? (e.g., "Fix H-03 and resubmit Consent harness"
or "Start govstack_views.py for Scheduler BB")

**Q3. Are there any regressions?**
Has anything dropped to a lower tier since baseline? Has a test count dropped significantly
(>50 tests)? Has a migration conflict appeared? Are there new Codex findings that block
a BB from advancing?

**Q4. Is the certification roadmap still correct?**
Based on the current state, is the phase order (Consent → Scheduler → File Management →
Messaging → Payments) still the right order? Or has something changed that suggests
reordering (e.g., Scheduler already has a govstack_views.py and just needs spec alignment)?

---

## STEP 6 — Recommended Next Actions

Produce a short prioritized list of next actions, formatted as:

```
PRIORITY 1 — [BB Name] — [Specific action] — Estimated effort: [N sprints]
  Evidence that makes this the priority: [1 sentence]

PRIORITY 2 — [BB Name] — [Specific action] — Estimated effort: [N sprints]
  Evidence that makes this the priority: [1 sentence]

...
```

Do not list more than 5 priorities. Each must be actionable (not "improve quality" —
"add govstack_views.py endpoint for POST /scheduler/slots/{slotId}/reserve").

---

## STEP 7 — Save the Updated Assessment

After completing all steps, write the updated status table and movement analysis to:

```
/Users/lionel/builders/govstack/GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md
```

Update the **"Last updated"** date at the top of that file.
Append a new dated section at the **bottom** of that file with the heading:

```markdown
---
## Re-Scan: [YYYY-MM-DD]

[paste the updated table from STEP 4 here]
[paste the movement analysis from STEP 5 here]
[paste the next actions from STEP 6 here]
```

Do NOT overwrite the original baseline section — append below it. This preserves
the history of progress over time.

---

## HOW TO USE THIS PROMPT

1. Open a new session with Claude (Cowork or Claude Code)
2. Say: **"Run the GovStack BB re-scan"**
3. Paste this entire file as your message (or attach it and say "follow this prompt")
4. Claude will work through Steps 1–7 and produce the updated assessment
5. Review the updated `GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md` in the repo

**When to run this scan:**
- After any sprint where GovStack work was done
- When the Consent harness result comes back (pass or fail)
- Before presenting to a stakeholder who asks about GovStack certification progress
- Whenever starting a new session after more than 2 weeks of development

**What this scan does NOT do:**
- It does not run the GovStack harness — that requires submitting to `testing.govstack.global`
- It does not score spec compliance in detail — use `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md`
  for a full 10-dimension deep dive on a single BB
- It does not perform security or code quality review — use `engineering:code-review` skill for that

---

## RELATED FILES

| File | Purpose |
|------|---------|
| `GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md` | Baseline + historical re-scan results |
| `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md` | Full 10-dimension deep audit of a single BB |
| `GOVSTACK_BB_RESCAN_PROMPT.md` | **This file** — progress re-scan across all BBs |
| `CONSENT-SUBMISSION-CHECKLIST.md` | Pre-harness checklist for Consent BB specifically |
| `SPEC_APPOINTMENTS_BB.md` | Spec notes for Scheduler BB (next priority) |
| `SPEC_DOCUMENT_MANAGEMENT_BB.md` | Spec notes for File Management BB |
| `payments_bb_spec.docx` | Spec notes for Payments BB |

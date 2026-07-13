# GovStack Consent BB Audit Terminal Log

Date: 2026-07-07

## Objective

Run the attached adversarial compliance audit against the GovStack Consent BB implementation in `/Users/lionel/builders/govstack`, then capture the terminal work and evidence in a Markdown file.

## What I Checked

- `apps/consent/govstack_urls.py`
- `apps/consent/govstack_views.py`
- `apps/consent/serializers.py`
- `apps/consent/models.py`
- `apps/consent/services.py`
- `apps/consent/tests/test_govstack_api.py`
- `apps/api/urls.py`
- `apps/consent/api_urls.py`

## Terminal Log

### Repo state

Command:

```bash
git status --short --branch
```

Output:

```text
## main
?? GOVSTACK_CONSENT_API_AUDIT_PROMPT.md
?? fr.md
?? test_output.txt
```

### Memory check

Command:

```bash
rg -n "govstack|audit|compliance|terminal" /Users/lionel/.codex/memories/MEMORY.md
```

Output:

- Found prior GovStack consent audit context and a rollout summary pointer for this repo.

### URL coverage

Command:

```bash
sed -n '1,260p' apps/consent/govstack_urls.py
```

Output highlights:

- All spec paths are present.
- The audit namespace includes an extra non-spec `audit/consent-log/` route.

### View / serializer / model inspection

Commands:

```bash
sed -n '1,340p' apps/consent/govstack_views.py
sed -n '340,760p' apps/consent/govstack_views.py
sed -n '760,1120p' apps/consent/govstack_views.py
sed -n '1,360p' apps/consent/serializers.py
sed -n '1,420p' apps/consent/models.py
sed -n '420,840p' apps/consent/services.py
```

Output highlights:

- `ConfigPolicyRevisionsView` returns `policy`, `revisions`, and `total`.
- List endpoints return plural keys such as `policies`, `dataAgreements`, `individuals`, `webhooks`, `consentRecords`, plus `total` in several places.
- Audit endpoints are protected only by `IsAuthenticated`, not a dedicated auditor role.
- There is no `IsOrgUser` permission class anywhere in the repo.
- `PolicySerializer`, `DataAgreementSerializer`, `WebhookSerializer`, `IndividualSerializer`, `ConsentRecordGovStackSerializer`, `RevisionSerializer`, and `SignatureSerializer` all expose fields beyond the minimal spec surface.
- `ConsentService.dispatch_webhook()` uses HMAC-SHA256 and `transaction.on_commit()`.
- `ConsentService.right_to_be_forgotten()` deletes forgettable records and writes RTBF audit entries.

### Search sweeps

Commands:

```bash
rg -n "HTTP_201_CREATED|status_code=201|status=201|201 Created|Created" apps/consent/govstack_views.py apps/consent/tests/test_govstack_api.py
rg -n "\\bresults\\b" apps/consent/govstack_views.py apps/consent/serializers.py apps/consent/tests/test_govstack_api.py
rg -n "class IsOrgUser|IsOrgUser" .
```

Output highlights:

- No GovStack consent view/test code path returned `201 Created`.
- No `results`-style list envelope is implemented in the GovStack consent views.
- No `IsOrgUser` implementation exists.

### Branch / remote

Command:

```bash
git branch --show-current && git remote -v
```

Output:

```text
main
```

## Audit Findings

### Confirmed compliant

- All required GovStack endpoints listed in the brief are implemented.
- POST handlers observed in `apps/consent/govstack_views.py` return `200`, not `201`.
- Webhook dispatch uses `transaction.on_commit()` and HMAC-SHA256.
- RTBF deletes forgettable consent records and records audit entries.

### Confirmed drift

- Audit namespace access is too broad for the brief’s auditor-role requirement.
- No org-role permission class exists, so `/config/` cannot satisfy an admin/org split.
- List endpoints do not use the brief’s requested `results` envelope.
- `/config/policy/{policyId}/revisions/` returns a `revisions` array instead of the brief’s `policy`-only shape.
- Several serializers expose extra fields beyond the brief’s listed schema fields.

## Next Step

Use the findings above as the basis for the formal audit response and, if requested, patch the affected views/serializers to match the brief exactly.

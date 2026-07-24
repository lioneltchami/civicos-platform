# Document Management BB — Wave 8: DRF REST API

**Version:** 1.0  
**Date:** 2026-07-23  
**Status:** Approved for implementation  
**Author:** CivicOS Architecture Team  
**Depends on:** SPEC_DOCUMENT_MANAGEMENT_BB.md v1.0 (Waves 1–7 complete)

---

## 1. Preamble — What Wave 7 Left Incomplete

Waves 1–7 of the Document Management BB are fully implemented and committed as `v0.8.0`.
The test suite runs ~977 tests with 0 failures.

**The single remaining gap:** spec §18 defines a DRF REST API at `/api/v1/documents/`. Wave 5 implemented only the HTML views (`apps/documents/views/citizen.py`, `apps/documents/views/staff.py`). The JSON API layer was skipped entirely.

Evidence of the gap:

```
# apps/documents/tests/test_api.py — first line of module docstring:
"There is no DRF REST API; 'API' here means the Django view HTTP interface
consumed by browsers and citizens."
```

And in `test_pipeda.py` (spec §24.2), three invariant tests hit `/api/v1/documents/`
endpoints that currently return 404:

```python
response = self.client.get(f"/api/v1/documents/{self.doc.pk}/")
# → 404 because no DRF route exists yet
```

---

## 2. Gap Table

| # | Gap | Impact |
|---|-----|--------|
| **DOC-GAP-1** | `apps/api/documents/` module does not exist | All 8 spec §18 JSON endpoints return 404 |
| **DOC-GAP-2** | `DocumentSerializer`, `DocumentUploadRequestSerializer`, `DocumentAttachmentSerializer` not written | API module prerequisite |
| **DOC-GAP-3** | `apps/api/urls.py` missing `documents/` include | No URL routing for API |
| **DOC-GAP-4** | `test_api.py` tests HTML views, not DRF API | Tests misdirected; spec §24.1 intent not satisfied |
| **DOC-GAP-5** | `test_pipeda.py` §24.2 invariants call DRF paths; all will fail | PIPEDA non-regression tests not executable |

---

## 3. What Does NOT Need to Change

The following are complete and must not be touched:

- `apps/documents/models.py` — DocumentCategory, Document, DocumentAttachment, DocumentAccessToken, DocumentQuerySet  
- `apps/documents/services/upload.py` — presign, confirm, magic-byte validation, ZIP bomb detection  
- `apps/documents/services/download.py` — token issuance, redemption, proxy/redirect  
- `apps/documents/services/versioning.py` — create_new_version, atomic is_latest_version swap  
- `apps/documents/services/retention.py` — expire, hard_delete, mark_purpose_fulfilled  
- `apps/documents/tasks.py` — all 6 Celery tasks  
- `apps/documents/views/citizen.py` — all citizen HTML views (untouched)  
- `apps/documents/views/staff.py` — all staff HTML views (untouched)  
- `apps/documents/urls.py` — HTML URL routing (untouched)  
- All templates — WCAG-compliant upload/list/detail/staff templates  
- All existing tests **except** `test_api.py` and `test_pipeda.py` (modified only)  

---

## 4. Implementation Order

**Strict dependency ordering — execute in sequence. Do not skip ahead.**

```
Step 1 → Create apps/api/documents/ scaffold
Step 2 → Write serializers.py (3 serializers)
Step 3 → Write views.py (9 views covering 8 spec endpoints)
Step 4 → Write urls.py (URL patterns)
Step 5 → Wire into apps/api/urls.py
Step 6 → Rename test_api.py → test_views_http_contract.py
Step 7 → Write new test_api.py (DRF API tests)
Step 8 → Update test_pipeda.py (fix 3 broken invariant tests)
Step 9 → Run full test suite — 0 failures required
Step 10 → Hard review + commit
```

---

## 5. Step 1 — Scaffold `apps/api/documents/`

Create the module (following the identical structure as `apps/api/notifications/`):

```
apps/api/documents/
    __init__.py          # empty
    serializers.py       # Step 2
    views.py             # Step 3
    urls.py              # Step 4
```

No models. No migrations. No new settings. This module is a pure API presentation
layer that delegates all business logic to `apps.documents.services.*`.

---

## 6. Step 2 — `apps/api/documents/serializers.py`

### 6.1 `DocumentSerializer` (read-only)

Purpose: render a `Document` instance as a JSON object for citizen and staff reads.

**Security invariants (must NEVER be violated):**
- `_storage_key` / `storage_key` must never appear in any field or method output
- `scan_engine_result` must only appear for staff with `view_quarantined` permission
- `uploaded_by` must be serialized as `uploaded_by_id` (UUID pk only, never email)

```python
class DocumentSerializer(serializers.ModelSerializer):
    doc_id = serializers.UUIDField(source="pk", read_only=True)
    category_slug = serializers.SlugRelatedField(
        source="category", slug_field="slug", read_only=True
    )
    # category name in request language (en/fr via Accept-Language header)
    category_name = serializers.SerializerMethodField()
    scan_status_display = serializers.CharField(
        source="get_scan_status_display", read_only=True
    )
    security_classification_display = serializers.CharField(
        source="get_security_classification_display", read_only=True
    )
    # PK only — never email or full name
    uploaded_by_id = serializers.UUIDField(source="uploaded_by.pk", read_only=True)
    is_on_legal_hold = serializers.BooleanField(source="legal_hold", read_only=True)
    # scan_engine_result exposed only to staff with view_quarantined via
    # SerializerMethodField that checks request.user.has_perm()
    scan_engine_result = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "doc_id",
            "category_slug",
            "category_name",
            "original_filename",
            "mime_type",
            "size_bytes",
            "scan_status",
            "scan_status_display",
            "version_number",
            "is_latest_version",
            "security_classification",
            "security_classification_display",
            "uploaded_by_id",
            "is_on_legal_hold",
            "scan_engine_result",
            "description",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_category_name(self, obj: Document) -> str:
        request = self.context.get("request")
        lang = getattr(request, "LANGUAGE_CODE", "en")
        return obj.category.name_fr if lang.startswith("fr") else obj.category.name_en

    def get_scan_engine_result(self, obj: Document) -> str | None:
        request = self.context.get("request")
        if request and request.user.has_perm("documents.view_quarantined"):
            return obj.scan_engine_result or None
        return None  # hidden from citizens and staff without quarantine permission
```

### 6.2 `DocumentUploadRequestSerializer` (write)

Purpose: validate the `POST /api/v1/documents/request-upload/` request body.

```python
class DocumentUploadRequestSerializer(serializers.Serializer):
    category_slug = serializers.SlugField(max_length=100)
    original_filename = serializers.CharField(max_length=255)
    mime_type = serializers.CharField(max_length=100)
    size_bytes = serializers.IntegerField(min_value=1)

    def validate_category_slug(self, value: str) -> str:
        from apps.documents.models import DocumentCategory
        try:
            DocumentCategory.objects.get(slug=value)
        except DocumentCategory.DoesNotExist:
            raise serializers.ValidationError(
                f"Unknown document category: '{value}'."
            )
        return value

    def validate_mime_type(self, value: str) -> str:
        from django.conf import settings
        allowed = settings.CIVICOS.get("ALLOWED_UPLOAD_MIME_TYPES", [])
        if value not in allowed:
            raise serializers.ValidationError(
                f"MIME type '{value}' is not permitted. Allowed: {allowed}"
            )
        return value

    def validate_size_bytes(self, value: int) -> int:
        from django.conf import settings
        max_bytes = settings.CIVICOS.get(
            "DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES",
            settings.CIVICOS.get("MAX_UPLOAD_SIZE", 10 * 1024 * 1024),
        )
        if value > max_bytes:
            raise serializers.ValidationError(
                f"File size {value} bytes exceeds the maximum of {max_bytes} bytes."
            )
        return value
```

### 6.3 `DocumentAttachmentSerializer` (read-only)

Purpose: expose a `DocumentAttachment` record for the attached-documents list endpoint.

```python
class DocumentAttachmentSerializer(serializers.ModelSerializer):
    attachment_id = serializers.UUIDField(source="pk", read_only=True)
    document = DocumentSerializer(read_only=True)
    # content_type as app_label.model string (e.g. "portal.servicerequest")
    attached_to_type = serializers.SerializerMethodField()
    attached_to_id = serializers.CharField(source="object_id", read_only=True)

    class Meta:
        model = DocumentAttachment  # from apps.documents.models
        fields = [
            "attachment_id",
            "document",
            "attached_to_type",
            "attached_to_id",
            "attachment_role",
            "note",
            "created_at",
        ]
        read_only_fields = fields

    def get_attached_to_type(self, obj) -> str:
        return f"{obj.content_type.app_label}.{obj.content_type.model}"
```

---

## 7. Step 3 — `apps/api/documents/views.py`

Nine view classes (one spec endpoint per class). All share:

```python
_AUTH = [CivicOSTokenAuthentication, JWTAuthentication]
# Import from apps.api.authentication and rest_framework_simplejwt.authentication
```

### 7.1 `DocumentRequestUploadView`

```
POST /api/v1/documents/request-upload/
Auth:    IsAuthenticated (citizen or staff)
Throttle: CitizenRateThrottle
```

Logic:
1. Validate `DocumentUploadRequestSerializer`
2. Call `validate_upload_request(user, category_slug, filename, mime_type, size_bytes)` from `apps.documents.services.upload` — raises `PermissionDenied` if staff-only category
3. Call `create_presigned_upload(user, category, filename, mime_type, size_bytes)` — returns `(document, presigned_data)` dict
4. Return `HTTP 201`:

```json
{
  "doc_id": "<uuid>",
  "upload_url": "<presigned POST URL>",
  "upload_fields": { "<field>": "<value>", ... },
  "expires_at": "<ISO 8601>"
}
```

Error responses:
- `400` — serializer validation failed (dict of field errors, DRF standard)
- `403` — staff-only category, citizen not permitted
- `500` — storage backend unavailable (caught, logged, re-raised as 500)

### 7.2 `DocumentConfirmUploadView`

```
POST /api/v1/documents/{doc_id}/confirm-upload/
Auth:   IsAuthenticated
```

Logic:
1. Fetch `Document` scoped to `uploaded_by=request.user` — raise `Http404` if not found (IDOR guard)
2. Call `confirm_upload(document, request.user)` from `apps.documents.services.upload`
3. Return `HTTP 200`:

```json
{
  "doc_id": "<uuid>",
  "scan_status": "scanning"
}
```

Error responses:
- `400` — file not found in quarantine / magic byte mismatch / ZIP bomb
- `404` — doc not found or not owned (IDOR: 404 not 403)

### 7.3 `DocumentDetailView`

```
GET /api/v1/documents/{doc_id}/
Auth:   IsAuthenticated
```

Logic:
1. `get_object_or_404(Document, pk=doc_id, uploaded_by=request.user)` — **this is the IDOR guard; never remove the uploaded_by filter**
2. Serialise with `DocumentSerializer(doc, context={"request": request})`
3. Return `HTTP 200`

Do not expose deleted documents (add `deleted_at__isnull=True` to filter).

### 7.4 `DocumentDownloadInitView`

```
GET /api/v1/documents/{doc_id}/download/
Auth:   IsAuthenticated
```

Logic:
1. `get_object_or_404(Document, pk=doc_id, uploaded_by=request.user, deleted_at__isnull=True)`
2. Gate: `if doc.scan_status != Document.ScanStatus.ACTIVE: raise Http404` (scan gate — 404 not 403)
3. Call `issue_access_token(document, request.user)` from `apps.documents.services.download`
4. Build `token_url = request.build_absolute_uri(reverse("documents:api-token-redeem", args=[token.token]))`
5. Return `HTTP 200`:

```json
{
  "token_url": "https://example.gov.ca/api/v1/documents/dl/<token>/",
  "expires_at": "<ISO 8601>"
}
```

Error responses:
- `404` — not found, not owned, or not ACTIVE

### 7.5 `DocumentTokenRedeemView`

```
GET /api/v1/documents/dl/{token}/
Auth:   IsAuthenticated (token also validated independently)
```

Logic:
1. `get_object_or_404(DocumentAccessToken, token=token, issued_to=request.user, used_at__isnull=True, expires_at__gt=now())`
2. Mark `token.used_at = now(); token.save(update_fields=["used_at"])`
3. Write audit log: `ACTION_DOCUMENT_DOWNLOADED`
4. If `doc.size_bytes <= DOCUMENT_PROXY_MAX_BYTES` (dev / small files): stream via `FileResponse`
5. Else (production): `return HttpResponseRedirect(default_storage.url(doc.storage_key))`
   - Note: `doc.storage_key` is accessed via the guarded property; the raw value must **never** appear in the response headers

Error responses:
- `404` — invalid token, expired, already used, or wrong user

### 7.6 `DocumentAttachedListView`

```
GET /api/v1/documents/?attached_to={app_label}.{model}&object_id={pk}
Auth:   IsStaff + HasPermission("documents.view_all_documents")
```

Logic:
1. Parse `attached_to` query param → `ContentType.objects.get_by_natural_key(app_label, model)`
2. `DocumentAttachment.objects.filter(content_type=ct, object_id=object_id).select_related("document", "document__category")`
3. Return paginated `DocumentAttachmentSerializer` list

Error responses:
- `400` — `attached_to` not parseable or unknown content type
- `403` — not staff / missing permission

### 7.7 `DocumentAttachView`

```
POST /api/v1/documents/{doc_id}/attach/
Auth:   IsStaff + HasPermission("documents.upload_staff_document")
Body:   { "content_type": "portal.servicerequest", "object_id": "<uuid>",
          "attachment_role": "<str>", "note": "<str>" }
```

Logic:
1. `get_object_or_404(Document, pk=doc_id)` — staff can see any document
2. Parse body — inline serializer for content_type + object_id validation
3. Create `DocumentAttachment` record
4. Write audit log: `ACTION_DOCUMENT_ATTACHED`
5. Return `HTTP 201`:

```json
{ "attachment_id": "<uuid>" }
```

Error responses:
- `400` — validation errors
- `403` — missing permission
- `404` — doc_id not found

### 7.8 `DocumentDeleteView`

```
DELETE /api/v1/documents/{doc_id}/
Auth:   IsAuthenticated + HasPermission("documents.delete_document")
Body:   { "reason": "<string min 10 chars>" }
```

Logic:
1. `get_object_or_404(Document, pk=doc_id, uploaded_by=request.user, deleted_at__isnull=True)`  
   (Staff with `view_all_documents` can scope to any owner — implement via IDOR-safe check)
2. Gate: `if doc.legal_hold: return 403` with `{"detail": "Document is subject to a legal hold and cannot be deleted."}`
3. Call `soft_delete(document, request.user, reason=reason)` from `apps.documents.services.retention`
4. Return `HTTP 204`

Error responses:
- `400` — reason missing or < 10 chars
- `403` — legal hold active; or missing delete permission
- `404` — not found or not owned

### 7.9 `DocumentVersionsView`

```
GET /api/v1/documents/{doc_id}/versions/
Auth:   IsAuthenticated + HasPermission("documents.view_document_versions")
```

Logic:
1. `doc = get_object_or_404(Document, pk=doc_id, uploaded_by=request.user, deleted_at__isnull=True)`
2. `root = doc.root_document or doc`  (root_document is null iff this IS version 1)
3. `versions = Document.objects.filter(models.Q(pk=root.pk) | models.Q(root_document=root)).order_by("version_number")`
4. Return list (no pagination — version chains are short) of `DocumentSerializer`

Error responses:
- `403` — missing permission
- `404` — doc not found or not owned

---

## 8. Step 4 — `apps/api/documents/urls.py`

```python
"""
URL configuration for the Document Management REST API.
Mounted at /api/v1/documents/ by apps.api.urls.

Namespace: api-v1 (inherited from parent)

Endpoints
---------
POST   /api/v1/documents/request-upload/         → request-upload
POST   /api/v1/documents/<uuid:doc_id>/confirm-upload/ → confirm-upload
GET    /api/v1/documents/<uuid:doc_id>/          → document-detail
GET    /api/v1/documents/<uuid:doc_id>/download/ → document-download
GET    /api/v1/documents/dl/<str:token>/         → document-token-redeem
GET    /api/v1/documents/                        → document-attached-list
POST   /api/v1/documents/<uuid:doc_id>/attach/   → document-attach
DELETE /api/v1/documents/<uuid:doc_id>/          → document-delete
GET    /api/v1/documents/<uuid:doc_id>/versions/ → document-versions
"""
from django.urls import path
from apps.api.documents import views

urlpatterns = [
    # Order matters: "request-upload" and "dl/<token>" must appear before
    # the generic <uuid:doc_id> pattern to avoid the literal strings being
    # mismatched as UUIDs.
    path(
        "request-upload/",
        views.DocumentRequestUploadView.as_view(),
        name="document-request-upload",
    ),
    path(
        "dl/<str:token>/",
        views.DocumentTokenRedeemView.as_view(),
        name="api-token-redeem",
    ),
    path(
        "",
        views.DocumentAttachedListView.as_view(),
        name="document-attached-list",
    ),
    path(
        "<uuid:doc_id>/",
        views.DocumentDetailView.as_view(),
        name="document-detail",
    ),
    path(
        "<uuid:doc_id>/confirm-upload/",
        views.DocumentConfirmUploadView.as_view(),
        name="document-confirm-upload",
    ),
    path(
        "<uuid:doc_id>/download/",
        views.DocumentDownloadInitView.as_view(),
        name="document-download",
    ),
    path(
        "<uuid:doc_id>/attach/",
        views.DocumentAttachView.as_view(),
        name="document-attach",
    ),
    path(
        "<uuid:doc_id>/",
        views.DocumentDeleteView.as_view(),
        name="document-delete",
    ),
    path(
        "<uuid:doc_id>/versions/",
        views.DocumentVersionsView.as_view(),
        name="document-versions",
    ),
]
```

> **Implementation note:** `document-detail` (GET) and `document-delete` (DELETE) share the
> same path `<uuid:doc_id>/`. Combine them into a single view class that dispatches on
> `request.method` — or use DRF's `generics.RetrieveDestroyAPIView` with overridden `destroy()`.
> This avoids a duplicate URL pattern.

---

## 9. Step 5 — Wire into `apps/api/urls.py`

Add **one line** after the existing `notifications/` and `portal/` includes:

```python
# apps/api/urls.py
path("documents/", include("apps.api.documents.urls")),
```

Place it after `workflows/` so the API module list stays alphabetically ordered:
`consent/` → `documents/` → `notifications/` → `portal/` → `volunteers/` → `workflows/`.

---

## 10. Step 6 — Rename `test_api.py` → `test_views_http_contract.py`

**Action:** rename the file only.

```bash
git mv apps/documents/tests/test_api.py apps/documents/tests/test_views_http_contract.py
```

Update the module docstring (first block comment) to say:

```
apps/documents/tests/test_views_http_contract.py
================================================
HTTP protocol-contract tests for the Document Management HTML views.

These tests cover the Django HTML view layer (citizen and staff browser views),
NOT the DRF REST API. For DRF API tests, see test_api.py.
```

No other changes to this file. All 7 existing test classes remain exactly as-is.

---

## 11. Step 7 — Write New `apps/documents/tests/test_api.py`

This new file covers all 9 DRF view classes. Follow the pattern of
`apps/api/tests/test_notifications.py`.

Authentication helper — use `CivicOSTokenAuthentication` (DRF token, not session):

```python
def _token_auth(user) -> str:
    from rest_framework.authtoken.models import Token
    token, _ = Token.objects.get_or_create(user=user)
    return f"Token {token.key}"
```

All requests must use `self.client.credentials(HTTP_AUTHORIZATION=_token_auth(user))`.

### Test classes and minimum method count

| Class | Methods (min) | What it tests |
|---|---|---|
| `DocumentRequestUploadAPITests` | 6 | 201 success; 400 bad mime; 400 bad size; 403 staff-only category; 401 unauthenticated; storage_key absent from 201 response |
| `DocumentConfirmUploadAPITests` | 5 | 200 success + scan_status="scanning"; 400 quarantine miss; 404 wrong owner (IDOR); 404 not found; 401 unauth |
| `DocumentDetailAPITests` | 6 | 200 fields present; storage_key absent; 404 wrong owner (IDOR); 404 not found; 401 unauth; scan_engine_result hidden from citizen |
| `DocumentDownloadInitAPITests` | 5 | 200 + token_url; 404 scanning doc; 404 wrong owner; 404 deleted doc; 401 unauth |
| `DocumentTokenRedeemAPITests` | 6 | small file → 200; large file → 302; invalid token → 404; expired → 404; used → 404; wrong user → 404 |
| `DocumentAttachedListAPITests` | 4 | 200 paginated; 403 citizen; 400 bad content_type; 401 unauth |
| `DocumentAttachAPITests` | 4 | 201 attachment_id; 403 missing perm; 404 bad doc_id; 401 unauth |
| `DocumentDeleteAPITests` | 6 | 204 success; 403 legal hold; 403 missing perm; 400 short reason; 404 wrong owner; 401 unauth |
| `DocumentVersionsAPITests` | 4 | 200 list of versions; 403 missing perm; 404 wrong owner; correct count for 3-version chain |

**Minimum: 46 test methods.** Aim for 50+.

---

## 12. Step 8 — Update `apps/documents/tests/test_pipeda.py`

The spec §24.2 defines 6 PIPEDA invariant tests. Three of them hit the DRF API.
After Step 7, these can be made to pass by switching from `self.client.get()`
(session-based) to `APIClient` with token auth.

### Tests that currently call `/api/v1/documents/` (broken — fix these):

**`test_storage_key_never_in_api_response`:**
```python
# BEFORE (broken — uses session client, hits non-existent DRF route):
response = self.client.get(f"/api/v1/documents/{self.doc.pk}/")
self.assertNotIn("storage_key", response.data)

# AFTER (correct — uses APIClient + token auth):
from rest_framework.test import APIClient
client = APIClient()
client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
response = client.get(f"/api/v1/documents/{self.doc.pk}/")
self.assertEqual(response.status_code, 200)
self.assertNotIn("storage_key", response.data)
self.assertNotIn("_storage_key", response.data)
self.assertNotIn(self.doc._storage_key, response.content.decode())
```

**`test_idor_returns_404_not_403`:**
```python
# AFTER:
other_doc = _make_doc(user=self.other_user, status=Document.ScanStatus.ACTIVE)
client = APIClient()
client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
response = client.get(f"/api/v1/documents/{other_doc.pk}/")
self.assertEqual(response.status_code, 404)
# Confirm it is NOT 403 (403 would reveal the document exists)
self.assertNotEqual(response.status_code, 403)
```

**`test_scan_pending_blocks_download`:**
```python
# AFTER:
doc = _make_doc(user=self.user, scan_status=Document.ScanStatus.SCANNING)
client = APIClient()
client.credentials(HTTP_AUTHORIZATION=_token_auth(self.user))
response = client.get(f"/api/v1/documents/{doc.pk}/download/")
self.assertEqual(response.status_code, 404)
```

The other three tests (`test_original_filename_not_in_audit_detail`,
`test_quarantine_notification_has_no_uploader_pii`, `test_legal_hold_blocks_disposal`)
call service/task functions directly — no URL change needed.

---

## 13. Step 9 — Run Full Test Suite

```bash
python manage.py test apps.documents apps.api \
    --settings=config.settings.test \
    -v 2 2>&1 | tail -20
```

**Required result: 0 failures, 0 errors.**

Expected new test count after this wave: ~1,025+ (existing ~977 + new 50 API tests).

If any failures occur, fix them before proceeding. Do not commit a red suite.

---

## 14. Step 10 — Hard Review Checklist

Before committing, verify each invariant manually (by reading the code, not assuming):

### Security invariants (must all be TRUE)

| # | Invariant | Where to verify |
|---|-----------|-----------------|
| S1 | `DocumentSerializer` has no field named `storage_key`, `_storage_key` | serializers.py Meta.fields list |
| S2 | `scan_engine_result` is gated by `view_quarantined` perm | `get_scan_engine_result()` method |
| S3 | `uploaded_by_id` is UUID pk, not email | serializers.py field definition |
| S4 | All views scope queryset with `uploaded_by=request.user` (citizen views) | views.py get_queryset / get_object calls |
| S5 | Staff views use `HasPermission`, not `IsStaff` alone | permission_classes on 7.6, 7.7, 7.8, 7.9 |
| S6 | `DocumentTokenRedeemView` 302 Location never contains raw `_storage_key` value | views.py — use `default_storage.url()` only |
| S7 | `DocumentDeleteView` checks `legal_hold` before calling `soft_delete` | views.py — 403 with legal hold message |
| S8 | No `print()`, `logger.info(PII)`, or raw email/filename in any log call | grep `logger.` in views.py |

### API contract invariants (must all be TRUE)

| # | Invariant | Where to verify |
|---|-----------|-----------------|
| A1 | `POST /request-upload/` returns 201 (not 200) on success | DocumentRequestUploadView.post() |
| A2 | `POST /confirm-upload/` returns 200 (not 201) on success | DocumentConfirmUploadView.post() |
| A3 | `POST /attach/` returns 201 (not 200) on success | DocumentAttachView.post() |
| A4 | `DELETE /` returns 204 with empty body on success | DocumentDeleteView.delete() |
| A5 | Unauthenticated requests to all endpoints return 401 (not 302) | DRF `DEFAULT_AUTHENTICATION_CLASSES` must not include SessionAuth for these views |
| A6 | `GET /dl/<token>/` marks token as `used_at=now()` (single-use) | DocumentTokenRedeemView — used_at assignment |

### URL routing invariants (must all be TRUE)

| # | Invariant | Where to verify |
|---|-----------|-----------------|
| U1 | `request-upload/` pattern appears before `<uuid:doc_id>/` | urls.py ordering |
| U2 | `dl/<str:token>/` pattern appears before `<uuid:doc_id>/` | urls.py ordering |
| U3 | Detail (GET) and Delete (DELETE) share one view class | No duplicate path for `<uuid:doc_id>/` |

---

## 15. Step 11 — Commit

```bash
git add \
    apps/api/documents/ \
    apps/api/urls.py \
    apps/documents/tests/test_api.py \
    apps/documents/tests/test_views_http_contract.py \
    apps/documents/tests/test_pipeda.py

git commit -m "Documents BB Wave 8: DRF REST API (spec §18) + tests

DOC-GAP-1: Create apps/api/documents/ (serializers, views, urls)
DOC-GAP-2: DocumentSerializer, DocumentUploadRequestSerializer,
           DocumentAttachmentSerializer
DOC-GAP-3: Wire /api/v1/documents/ into apps/api/urls.py
DOC-GAP-4: Rename test_api.py → test_views_http_contract.py;
           write new test_api.py with 50 DRF API tests
DOC-GAP-5: Fix test_pipeda.py §24.2 invariants to use APIClient

All 5 DOC-GAPs resolved. Documents BB is now certifiably complete.
~1,025 tests, 0 failures."
```

---

## 16. Post-Wave Status Update

After this commit, update `SPEC_DOCUMENT_MANAGEMENT_BB.md` §25:

```markdown
### Wave 8 — DRF REST API (added 2026-07-23, closes DOC-GAPs 1–5)
- `apps/api/documents/` module: serializers, views, urls
- 9 views covering 8 spec §18 endpoints
- New `test_api.py` (50 DRF tests)
- Renamed old `test_api.py` → `test_views_http_contract.py`
- Fixed `test_pipeda.py` §24.2 PIPEDA invariants
- **Document Management BB is now complete and certifiably production-ready.**
```

---

## 17. Certifiability After Wave 8

Once Wave 8 is committed:

| Check | Status |
|-------|--------|
| All spec §18 endpoints implemented | ✅ |
| PIPEDA invariants testable + passing | ✅ |
| `storage_key` never in any API response | ✅ |
| `original_filename` never in audit detail | ✅ |
| IDOR guard (404 not 403) on all citizen endpoints | ✅ |
| Scan gate: PENDING/SCANNING/QUARANTINED → 404 on download | ✅ |
| Legal hold → 403 on DELETE | ✅ |
| Staff permissions required for staff endpoints | ✅ |
| DRF token + JWT authentication on all endpoints | ✅ |
| Full test suite ~1,025 tests, 0 failures | ✅ |
| **Verdict: Document Management BB — CERTIFIABLY COMPLETE** | ✅ |

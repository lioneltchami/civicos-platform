"""
apps/api/documents/urls.py
===========================
URL configuration for the Document Management BB REST API.
Mounted at /api/v1/documents/ by apps.api.urls.

Namespace: api-v1 (inherited from parent — see apps/api/urls.py app_name)

Endpoints
---------
POST   /api/v1/documents/request-upload/                 → document-request-upload
GET    /api/v1/documents/dl/<str:token>/                 → api-token-redeem
GET    /api/v1/documents/quarantined/                    → document-quarantined-list
GET    /api/v1/documents/attachments/                    → document-attached-list
GET    /api/v1/documents/                                → document-list
GET    /api/v1/documents/<uuid:doc_id>/                  → document-detail  (GET)
DELETE /api/v1/documents/<uuid:doc_id>/                  → document-detail  (DELETE)
POST   /api/v1/documents/<uuid:doc_id>/confirm-upload/   → document-confirm-upload
POST   /api/v1/documents/<uuid:doc_id>/request-download/ → document-request-download
POST   /api/v1/documents/<uuid:doc_id>/attach/           → document-attach
GET    /api/v1/documents/<uuid:doc_id>/versions/         → document-versions

URL ordering rules
------------------
U1 — Literal string paths must appear before "<uuid:doc_id>/" to prevent
     any ambiguity. Django's UUID converter would never match a literal string
     like "request-upload", but placing literals first is defensive convention.

U2 — "dl/<str:token>/" must appear before "<uuid:doc_id>/" for the same
     reason. The <str:> converter matches any non-slash string, so without
     this ordering Django would first try to coerce a 64-char hex token as
     a UUID and fail before reaching the token view.

U3 — Detail (GET) and Delete (DELETE) are dispatched by a single view class
     (DocumentDetailDeleteView) on the single path "<uuid:doc_id>/". This
     avoids a duplicate URL pattern entry.

U4 — "quarantined/" and "attachments/" are literal paths; they must appear
     before "<uuid:doc_id>/" (same rationale as U1).

GovStack spec alignment changes (Wave 8 → certifiable)
-------------------------------------------------------
- GET  /<doc_id>/download/        → POST /<doc_id>/request-download/
  (spec §18 §7.4: POST because issuing a token is state-changing)
- Response field token_url        → download_url
  (spec §18 §7.4 field name)
- GET  /                          → general document list (spec §18 §7.6)
  (was staff-only attachment list; that moved to /attachments/)
- GET  /quarantined/              newly added  (spec §18 §7.10)

Note on the "api-token-redeem" URL name
----------------------------------------
DocumentDownloadInitView.post() calls:
    reverse("api-v1:api-token-redeem", args=[token.token])
The URL name MUST remain "api-token-redeem" exactly for that reverse to work.
"""

from django.urls import path

from apps.api.documents import views

urlpatterns = [
    # ── Literal paths first (U1, U2, U4) ─────────────────────────────────────

    # POST  /api/v1/documents/request-upload/
    # Initiate upload: validate metadata, return presigned S3 POST URL.
    path(
        "request-upload/",
        views.DocumentRequestUploadView.as_view(),
        name="document-request-upload",
    ),

    # GET   /api/v1/documents/dl/<token>/
    # Redeem a single-use download token — stream or redirect to S3.
    # MUST appear before <uuid:doc_id>/ (U2: <str:> converter matches anything).
    path(
        "dl/<str:token>/",
        views.DocumentTokenRedeemView.as_view(),
        name="api-token-redeem",  # referenced by reverse("api-v1:api-token-redeem")
    ),

    # GET   /api/v1/documents/quarantined/
    # Staff: list all QUARANTINED documents.  Requires view_quarantined perm.
    # (U4: literal path — must appear before <uuid:doc_id>/)
    path(
        "quarantined/",
        views.DocumentQuarantinedListView.as_view(),
        name="document-quarantined-list",
    ),

    # GET   /api/v1/documents/attachments/?attached_to=app_label.model&object_id=…
    # Staff coordinator: list DocumentAttachments for a given content object.
    # Moved from "" to "attachments/" so root "" can serve the general list.
    path(
        "attachments/",
        views.DocumentAttachedListView.as_view(),
        name="document-attached-list",
    ),

    # GET   /api/v1/documents/
    # General document list: citizen → own docs; staff coordinator → all docs.
    # Optional query params: ?scan_status=<status>&category=<slug>
    path(
        "",
        views.DocumentListView.as_view(),
        name="document-list",
    ),

    # ── UUID-parameterised paths (must follow literal paths) ──────────────────

    # GET    /api/v1/documents/<doc_id>/
    # DELETE /api/v1/documents/<doc_id>/
    # Combined into DocumentDetailDeleteView (U3: single path, dispatch by method).
    path(
        "<uuid:doc_id>/",
        views.DocumentDetailDeleteView.as_view(),
        name="document-detail",
    ),

    # POST  /api/v1/documents/<doc_id>/confirm-upload/
    # Signal that the browser-direct S3 upload has finished; triggers ClamAV.
    path(
        "<uuid:doc_id>/confirm-upload/",
        views.DocumentConfirmUploadView.as_view(),
        name="document-confirm-upload",
    ),

    # POST  /api/v1/documents/<doc_id>/request-download/
    # Issue a single-use DocumentAccessToken; return download_url + expires_at.
    # Changed from GET /download/ → POST /request-download/ per GovStack spec §18.
    path(
        "<uuid:doc_id>/request-download/",
        views.DocumentDownloadInitView.as_view(),
        name="document-request-download",
    ),

    # POST  /api/v1/documents/<doc_id>/attach/
    # Staff: link an existing document to any CivicOS content object.
    path(
        "<uuid:doc_id>/attach/",
        views.DocumentAttachView.as_view(),
        name="document-attach",
    ),

    # GET   /api/v1/documents/<doc_id>/versions/
    # Return the full version chain for a document in version_number order.
    path(
        "<uuid:doc_id>/versions/",
        views.DocumentVersionsView.as_view(),
        name="document-versions",
    ),
]

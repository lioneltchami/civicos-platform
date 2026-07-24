"""
apps/api/documents/urls.py
===========================
URL configuration for the Document Management BB REST API.
Mounted at /api/v1/documents/ by apps.api.urls.

Namespace: api-v1 (inherited from parent — see apps/api/urls.py app_name)

Endpoints
---------
POST   /api/v1/documents/request-upload/              → document-request-upload
GET    /api/v1/documents/dl/<str:token>/               → api-token-redeem
GET    /api/v1/documents/                             → document-attached-list
GET    /api/v1/documents/<uuid:doc_id>/               → document-detail  (GET)
DELETE /api/v1/documents/<uuid:doc_id>/               → document-detail  (DELETE)
POST   /api/v1/documents/<uuid:doc_id>/confirm-upload/ → document-confirm-upload
GET    /api/v1/documents/<uuid:doc_id>/download/       → document-download
POST   /api/v1/documents/<uuid:doc_id>/attach/         → document-attach
GET    /api/v1/documents/<uuid:doc_id>/versions/       → document-versions

URL ordering rules
------------------
Two invariants govern the ordering of patterns in this file:

U1 — "request-upload/" is a literal string path and must appear before
     "<uuid:doc_id>/" to prevent any ambiguity. Django's UUID converter
     would never match the literal string "request-upload", but placing
     literals first is the correct defensive convention.

U2 — "dl/<str:token>/" must appear before "<uuid:doc_id>/" for the same
     reason. The <str:> converter matches any non-slash string, so
     without this ordering Django would first try to coerce a 64-char
     hex token as a UUID and fail before reaching the token view.

U3 — Detail (GET) and Delete (DELETE) are dispatched by a single view class
     (DocumentDetailDeleteView) on the single path "<uuid:doc_id>/". This
     avoids a duplicate URL pattern entry.

Note on the "api-token-redeem" name
-------------------------------------
DocumentDownloadInitView.get() calls:
    reverse("api-v1:api-token-redeem", args=[token.token])
The URL name MUST remain "api-token-redeem" exactly for that reverse to work.
"""

from django.urls import path

from apps.api.documents import views

urlpatterns = [
    # ── Literal paths first (U1, U2) ─────────────────────────────────────────

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

    # GET   /api/v1/documents/?attached_to=app_label.model&object_id=…
    # Staff coordinator: list DocumentAttachments for a given content object.
    path(
        "",
        views.DocumentAttachedListView.as_view(),
        name="document-attached-list",
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

    # GET   /api/v1/documents/<doc_id>/download/
    # Issue a single-use DocumentAccessToken; return token_url + expires_at.
    path(
        "<uuid:doc_id>/download/",
        views.DocumentDownloadInitView.as_view(),
        name="document-download",
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

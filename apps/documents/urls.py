"""
apps/documents/urls.py
======================
URL configuration for the Document Management Building Block (Wave 5).

Namespace: ``documents``

Citizen URL patterns
--------------------
``""``                          → ``documents:list``          — citizen's own docs
``"upload/"``                   → ``documents:upload-init``   — initiate upload
``"<uuid:pk>/confirm/"``        → ``documents:upload-confirm``— confirm upload after
                                                                direct-to-S3 transfer
``"<uuid:pk>/"``                → ``documents:detail``        — single doc detail
``"<uuid:pk>/download/"``       → ``documents:download``      — issue token + redirect
``"dl/<str:token>/"``           → ``documents:token-redeem``  — consume token + serve

Staff URL patterns
------------------
``"staff/"``                    → ``documents:staff-list``    — all docs (filtered)
``"staff/quarantine/"``         → ``documents:quarantine-list``— quarantined docs
``"staff/<uuid:pk>/"``          → ``documents:staff-detail``  — full metadata
``"staff/<uuid:pk>/legal-hold/"``→ ``documents:legal-hold``  — apply / release hold
``"staff/<uuid:pk>/audit/"``    → ``documents:audit-log``    — audit log entries

Security notes
--------------
- ``token-redeem`` uses ``<str:token>`` (64-char hex) rather than ``<uuid>``
  because access tokens are hex strings, not UUIDs.
- The ``staff/`` prefix comes *after* citizen routes so that the uuid patterns
  do not accidentally match ``staff`` as a uuid pk.
- ``staff/quarantine/`` is listed *before* ``staff/<uuid:pk>/`` to prevent the
  literal string ``"quarantine"`` being matched as a UUID (it would fail UUID
  parsing, but explicit ordering avoids confusing 404s).
"""

from __future__ import annotations

from django.urls import path

from apps.documents.views.citizen import (
    DocumentDetailView,
    DocumentDownloadView,
    DocumentListView,
    DocumentTokenRedeemView,
    DocumentUploadConfirmView,
    DocumentUploadInitView,
)
from apps.documents.views.staff import (
    DocumentAdminListView,
    DocumentAuditLogView,
    DocumentLegalHoldView,
    DocumentQuarantineListView,
    StaffDocumentDetailView,
)

app_name = "documents"

urlpatterns = [
    # ------------------------------------------------------------------
    # Citizen views
    # ------------------------------------------------------------------
    path("", DocumentListView.as_view(), name="list"),
    path("upload/", DocumentUploadInitView.as_view(), name="upload-init"),
    path(
        "<uuid:pk>/confirm/",
        DocumentUploadConfirmView.as_view(),
        name="upload-confirm",
    ),
    path("<uuid:pk>/", DocumentDetailView.as_view(), name="detail"),
    path("<uuid:pk>/download/", DocumentDownloadView.as_view(), name="download"),
    path("dl/<str:token>/", DocumentTokenRedeemView.as_view(), name="token-redeem"),
    # ------------------------------------------------------------------
    # Staff views
    # ------------------------------------------------------------------
    path("staff/", DocumentAdminListView.as_view(), name="staff-list"),
    # quarantine must appear before the generic <uuid:pk> pattern
    path(
        "staff/quarantine/",
        DocumentQuarantineListView.as_view(),
        name="quarantine-list",
    ),
    path("staff/<uuid:pk>/", StaffDocumentDetailView.as_view(), name="staff-detail"),
    path(
        "staff/<uuid:pk>/legal-hold/",
        DocumentLegalHoldView.as_view(),
        name="legal-hold",
    ),
    path(
        "staff/<uuid:pk>/audit/",
        DocumentAuditLogView.as_view(),
        name="audit-log",
    ),
]

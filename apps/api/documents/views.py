"""
apps/api/documents/views.py
============================
DRF view classes for the Document Management BB REST API (spec §18).

Nine view classes cover the eight spec endpoints. The Detail (GET) and
Delete (DELETE) share one view class (DocumentDetailDeleteView) to avoid a
duplicate URL pattern for ``<uuid:doc_id>/``.

Security invariants enforced at this layer
──────────────────────────────────────────
• IDOR prevention: all citizen-facing get_object() calls filter by
  ``uploaded_by=request.user``.  Non-owned PKs return HTTP 404, never 403
  (403 reveals that the document exists).

• Staff coordinator override: views 7.3, 7.8, and 7.9 broaden the queryset
  to ``documents.view_all_documents`` holders.  The broadened path never
  reveals extra information to ordinary citizens.

• ``storage_key`` / ``_storage_key`` must never appear in HTTP response
  bodies or response headers.  ``DocumentTokenRedeemView`` uses the guarded
  ``doc.storage_key`` property internally (to pass to
  ``generate_presigned_download_url``), but the raw value is discarded after
  the S3 signature computation.

• ``scan_engine_result`` permission gate lives entirely in
  ``DocumentSerializer.get_scan_engine_result()`` — views do not need to
  filter this field themselves.

• ``original_filename`` must NEVER appear in log calls here or in the
  service layer (may contain PII paths on Windows clients).

Authentication stack (shared across all views)
──────────────────────────────────────────────
DRF token (CivicOSTokenAuthentication) and JWT (JWTAuthentication) are both
accepted. Session authentication is deliberately excluded from this API so
that unauthenticated requests receive HTTP 401 (not 302 redirect).

All views share the ``_AUTH`` class-level constant for DRY consistency.

Django vs DRF exception conversion
───────────────────────────────────
The service layer (apps.documents.services.*) raises Django's own exceptions:

  django.core.exceptions.PermissionDenied  → wrapped to DRF PermissionDenied (403)
  django.core.exceptions.ValidationError   → wrapped to DRF ValidationError (400)
  ValueError (soft_delete race)            → wrapped to DRF ValidationError (400)

DRF's default exception_handler handles Http404 (404) and Django's
PermissionDenied (403) transparently, but NOT Django's ValidationError
(returns None → 500 if not caught here). All conversion happens at the view
boundary so the service layer stays decoupled from DRF.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import default_storage
from django.db import models as dj_models
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.api.authentication import CivicOSTokenAuthentication
from apps.api.pagination import StandardPagination
from apps.api.permissions import IsStaff
from apps.api.throttling import CitizenRateThrottle, StaffRateThrottle
from apps.api.documents.serializers import (
    DocumentAttachmentSerializer,
    DocumentSerializer,
    DocumentUploadRequestSerializer,
)
from apps.documents.models import Document, DocumentAttachment
from apps.documents.services.download import (
    consume_access_token,
    generate_presigned_download_url,
    issue_access_token,
)
from apps.documents.services.retention import soft_delete
from apps.documents.services.upload import (
    confirm_upload,
    validate_upload_request,
)

logger = logging.getLogger(__name__)

# Shared authentication backends for every view in this module.
_AUTH = [CivicOSTokenAuthentication, JWTAuthentication]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_client_ip(request) -> str | None:
    """
    Extract the originating client IP address.

    Honours X-Forwarded-For for reverse-proxy deployments.  The download
    service will mask the last octet (IPv4) or /48 prefix (IPv6) for PIPEDA
    before persisting it.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _convert_django_exceptions(exc: Exception) -> None:
    """
    Convert Django service-layer exceptions to their DRF equivalents.

    The service layer (apps.documents.services.*) raises Django's own
    exceptions rather than DRF exceptions. DRF's default exception_handler
    converts Http404 (→ 404) and Django's PermissionDenied (→ 403) correctly,
    but silently drops Django's ValidationError (→ Http 500!).

    Call this function from any view that invokes service layer code that may
    raise these exceptions, before the exception propagates to DRF.

    Args:
        exc: The caught exception.

    Raises:
        PermissionDenied (DRF): when exc is Django's PermissionDenied.
        ValidationError (DRF): when exc is Django's ValidationError or ValueError.
    """
    if isinstance(exc, DjangoPermissionDenied):
        raise PermissionDenied(str(exc) or "You do not have permission to perform this action.")
    if isinstance(exc, DjangoValidationError):
        # Django ValidationError may carry .messages (list) or .message (str).
        if hasattr(exc, "message_dict"):
            raise ValidationError(exc.message_dict)
        messages = getattr(exc, "messages", None) or [str(exc)]
        raise ValidationError({"non_field_errors": messages})
    if isinstance(exc, ValueError):
        # soft_delete() raises ValueError for legal-hold and double-delete
        # race conditions.  Surface as 400.
        raise ValidationError({"non_field_errors": [str(exc)]})


def _get_document_for_user(
    request,
    doc_id,
    *,
    require_not_deleted: bool = True,
    allow_coordinator: bool = False,
) -> Document:
    """
    IDOR-safe document lookup.

    Citizens: filtered by ``uploaded_by=request.user``.
    Coordinators (``documents.view_all_documents``): see any document when
    ``allow_coordinator=True``.

    Non-owned, non-existent, or deleted documents all return 404.  This is
    intentional: 403 would reveal that a document with that PK exists, which
    is an IDOR information leak.

    Args:
        request:              DRF request (request.user must be authenticated).
        doc_id:               UUID of the document.
        require_not_deleted:  When True (default) excludes soft-deleted docs.
        allow_coordinator:    When True, holders of ``view_all_documents`` can
                              retrieve any document (not just their own).

    Returns:
        Document instance (no select_related; callers add what they need).

    Raises:
        Http404: document not found, not owned, or deleted (per flags).
    """
    filters: dict = {"pk": doc_id}
    if require_not_deleted:
        filters["deleted_at__isnull"] = True

    if allow_coordinator and request.user.has_perm("documents.view_all_documents"):
        # Coordinator: no ownership filter — they can see any document.
        return get_object_or_404(Document, **filters)

    # Citizen (or staff without coordinator perm): own documents only.
    filters["uploaded_by"] = request.user
    return get_object_or_404(Document, **filters)


# ─────────────────────────────────────────────────────────────────────────────
# 7.1  POST /api/v1/documents/request-upload/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentRequestUploadView(APIView):
    """
    Initiate a citizen or staff document upload.

    Returns a presigned S3 POST URL the client uses to upload directly to
    object storage. Django never handles the raw bytes; only the metadata
    and policy are touched here.

    Response body (HTTP 201):
        {
            "doc_id":        "<uuid>",
            "upload_url":    "<S3 presigned POST URL>",
            "upload_fields": { "<field>": "<value>", ... },
            "expires_at":    "<ISO 8601>"
        }

    Note: ``storage_key`` is computed by the service and NEVER returned.

    Exception mapping (service raises Django exceptions; we convert to DRF):
        django.core.exceptions.PermissionDenied  → HTTP 403
        django.core.exceptions.ValidationError   → HTTP 400
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def post(self, request):
        serializer = DocumentUploadRequestSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        vd = serializer.validated_data

        # validate_upload_request() raises:
        #   Django PermissionDenied  — user may not upload to this category
        #   Django ValidationError   — invalid MIME, size, extension, etc.
        # Both must be caught and converted to DRF equivalents here; otherwise
        # Django's ValidationError propagates past DRF's exception handler
        # and produces a 500 response.
        try:
            result = validate_upload_request(
                user=request.user,
                category_slug=vd["category_slug"],
                original_filename=vd["original_filename"],
                mime_type=vd["mime_type"],
                size_bytes=vd["size_bytes"],
            )
        except (DjangoPermissionDenied, DjangoValidationError, ValueError) as exc:
            _convert_django_exceptions(exc)
        except Exception:
            # Unexpected storage/infrastructure failure — log and re-raise.
            # original_filename intentionally omitted (may contain PII paths).
            logger.exception(
                "Unexpected error in request-upload for user pk=%s",
                request.user.pk,
            )
            raise

        return Response(result, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# 7.2  POST /api/v1/documents/{doc_id}/confirm-upload/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentConfirmUploadView(APIView):
    """
    Confirm that the client has finished uploading to the presigned URL.

    Triggers async ClamAV scanning (via Celery) and transitions the document
    from PENDING_UPLOAD → SCANNING.

    Response body (HTTP 200):
        {
            "doc_id":      "<uuid>",
            "scan_status": "scanning"
        }

    IDOR guard: the service raises Http404 if the document does not belong to
    ``request.user`` (404, not 403).

    Exception mapping (service raises Django exceptions; we convert to DRF):
        django.core.exceptions.ValidationError → HTTP 400
          (quarantine miss / magic byte / ZIP bomb)
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def post(self, request, doc_id):
        # confirm_upload raises:
        #   Http404           — IDOR (doc not found or wrong owner)
        #   Django ValidationError — quarantine miss, magic byte, ZIP bomb
        # Http404 propagates transparently; ValidationError must be converted.
        try:
            doc = confirm_upload(user=request.user, doc_id=doc_id)
        except (DjangoPermissionDenied, DjangoValidationError, ValueError) as exc:
            _convert_django_exceptions(exc)

        return Response(
            {
                "doc_id": str(doc.pk),
                "scan_status": doc.scan_status,
            },
            status=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7.3 + 7.8  GET + DELETE /api/v1/documents/{doc_id}/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentDetailDeleteView(APIView):
    """
    Retrieve or soft-delete a document.

    Both methods share the same URL pattern (``<uuid:doc_id>/``) to avoid a
    duplicate path entry in urls.py.

    GET (HTTP 200)
    ──────────────
    Serialises the document with DocumentSerializer.  Coordinators with
    ``documents.view_all_documents`` can fetch any document; citizens see only
    their own.  IDOR: non-owned PKs return 404.

    DELETE (HTTP 204)
    ─────────────────
    Soft-deletes the document (sets scan_status=DELETED, deleted_at=now()).
    Requires:
    - The user owns the document (or holds ``documents.view_all_documents``).
    - A ``reason`` string of at least 10 characters in the request body.
    - ``legal_hold`` must be False (returns 403 otherwise).
    - User must hold ``documents.delete_document`` permission.
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request, doc_id):
        doc = (
            _get_document_for_user(
                request,
                doc_id,
                require_not_deleted=True,
                allow_coordinator=True,
            )
        )
        # select_related for DocumentSerializer: category (slug/name), uploaded_by (pk)
        doc = Document.objects.select_related("category", "uploaded_by").get(pk=doc.pk)
        serializer = DocumentSerializer(doc, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, doc_id):
        # Permission check — standard Django auto-created delete permission.
        if not request.user.has_perm("documents.delete_document"):
            raise PermissionDenied(
                "You do not have permission to delete documents."
            )

        doc = _get_document_for_user(
            request,
            doc_id,
            require_not_deleted=True,
            allow_coordinator=True,
        )

        # Legal hold blocks all deletion attempts (pre-check; service also
        # enforces this under lock to prevent TOCTOU races).
        if doc.legal_hold:
            raise PermissionDenied(
                "Document is subject to a legal hold and cannot be deleted."
            )

        # Require a meaningful deletion reason.
        reason = (request.data or {}).get("reason", "")
        if not isinstance(reason, str) or len(reason.strip()) < 10:
            raise ValidationError(
                {"reason": "Deletion reason must be at least 10 characters."}
            )

        # soft_delete() re-checks legal_hold and deleted_at under SELECT FOR
        # UPDATE. A concurrent legal-hold operation between our pre-check and
        # here would raise ValueError — convert to 400.
        try:
            soft_delete(
                document=doc,
                deleted_by=request.user,
                reason=reason.strip(),
            )
        except ValueError as exc:
            raise ValidationError({"non_field_errors": [str(exc)]})

        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# 7.4  GET /api/v1/documents/{doc_id}/download/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentDownloadInitView(APIView):
    """
    Issue a single-use download token for an ACTIVE document.

    The client uses the returned ``token_url`` to stream the file (or receive
    a presigned S3 redirect).  The token expires in 5 minutes by default.

    Response body (HTTP 200):
        {
            "token_url": "https://…/api/v1/documents/dl/<token>/",
            "expires_at": "<ISO 8601>"
        }

    Gates:
    - Document must be owned by request.user (IDOR: 404 for non-owned docs).
    - Document scan_status must be ACTIVE (404 otherwise — scan gate).
    - Document must not be soft-deleted.
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request, doc_id):
        doc = _get_document_for_user(
            request,
            doc_id,
            require_not_deleted=True,
            allow_coordinator=False,  # tokens are personal — coordinators use staff download
        )

        # Scan gate: only ACTIVE documents may be downloaded.
        # Return 404 (not 403) to avoid leaking document existence to bad actors.
        if doc.scan_status != Document.ScanStatus.ACTIVE:
            raise Http404("Document is not available for download.")

        # issue_access_token records a RECORD_VIEWED audit entry and returns a
        # DocumentAccessToken instance.  It raises Http404 on any failure.
        token = issue_access_token(
            user=request.user,
            document=doc,
            ip_address=_get_client_ip(request),
        )

        # Build the absolute redemption URL using the api-v1 namespace.
        token_url = request.build_absolute_uri(
            reverse("api-v1:api-token-redeem", args=[token.token])
        )

        return Response(
            {
                "token_url": token_url,
                "expires_at": token.expires_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7.5  GET /api/v1/documents/dl/{token}/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentTokenRedeemView(APIView):
    """
    Redeem a single-use download token.

    The service layer (consume_access_token) uses ``SELECT FOR UPDATE`` inside
    a transaction to ensure single-use atomicity — race conditions cannot cause
    a second redemption to succeed.

    Small files (≤ DOCUMENT_PROXY_MAX_BYTES, default 1 MiB):
        Stream via FileResponse (HTTP 200, Content-Disposition: attachment).

    Large files (> threshold):
        HTTP 302 redirect to an S3 presigned GET URL.

    Security note:
        The raw ``_storage_key`` value is passed to the storage backend
        internally and is NEVER included in any HTTP response header or body.
        The S3 presigned URL is signed with an HMAC and does not expose the
        storage key itself.

    Error responses:
        404 — invalid token, expired, already used, or wrong user.
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request, token):
        ip = _get_client_ip(request)

        # consume_access_token uses SELECT FOR UPDATE + transaction.atomic() to
        # enforce single-use. Raises Http404 for any failure (IDOR-safe).
        doc = consume_access_token(
            token_value=token,
            user=request.user,
            ip_address=ip,
        )

        civicos = getattr(settings, "CIVICOS", {})
        proxy_max_bytes = civicos.get("DOCUMENT_PROXY_MAX_BYTES", 1 * 1024 * 1024)

        if doc.size_bytes <= proxy_max_bytes:
            # Small file or dev environment: proxy through Django.
            # original_filename is used for Content-Disposition only —
            # it is never logged here.
            try:
                file_obj = default_storage.open(doc.storage_key)
                return FileResponse(
                    file_obj,
                    as_attachment=True,
                    filename=doc.original_filename,
                    content_type=doc.mime_type,
                )
            except FileNotFoundError:
                # Storage backend cannot find the object — return 404.
                logger.error(
                    "File not found in storage for document pk=%s", doc.pk
                )
                raise Http404("Document file not found in storage.")

        else:
            # Large file: generate a short-lived presigned S3 GET URL and
            # redirect the client.  storage_key is consumed internally;
            # the redirect URL is signed and does not contain the raw key.
            presigned_url = generate_presigned_download_url(
                storage_key=doc.storage_key,
                ttl_seconds=300,
            )
            return HttpResponseRedirect(presigned_url)


# ─────────────────────────────────────────────────────────────────────────────
# 7.6  GET /api/v1/documents/?attached_to=app_label.model&object_id=…
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAttachedListView(generics.ListAPIView):
    """
    List documents attached to a specific CivicOS object (staff only).

    Query parameters:
        attached_to  — content type in ``app_label.model`` format
                       (e.g. ``portal.servicerequest``).
        object_id    — the PK of the linked object (UUID or integer string).

    Permission: ``documents.view_all_documents`` (coordinator role).

    Response: paginated list of DocumentAttachmentSerializer.

    Error responses:
        400 — ``attached_to`` missing, malformed, or references an unknown model.
        403 — user is not staff or lacks the required permission.
        401 — not authenticated.
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsStaff]
    throttle_classes = [StaffRateThrottle]
    serializer_class = DocumentAttachmentSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user

        if not user.has_perm("documents.view_all_documents"):
            raise PermissionDenied(
                "You do not have permission to view attached documents."
            )

        attached_to = self.request.query_params.get("attached_to", "").strip()
        object_id = self.request.query_params.get("object_id", "").strip()

        if not attached_to or not object_id:
            raise ValidationError(
                {
                    "attached_to": (
                        "Both 'attached_to' (e.g. 'portal.servicerequest') "
                        "and 'object_id' query parameters are required."
                    )
                }
            )

        try:
            app_label, model_name = attached_to.split(".", 1)
        except ValueError:
            raise ValidationError(
                {
                    "attached_to": (
                        f"Invalid format: '{attached_to}'. "
                        "Expected 'app_label.model' (e.g. 'portal.servicerequest')."
                    )
                }
            )

        try:
            ct = ContentType.objects.get_by_natural_key(app_label, model_name)
        except ContentType.DoesNotExist:
            raise ValidationError(
                {"attached_to": f"Unknown content type: '{attached_to}'."}
            )

        # select_related includes document__uploaded_by to avoid N+1 queries.
        # DocumentSerializer accesses uploaded_by.pk — without prefetch that
        # triggers one extra query per attachment in the paginated list.
        return (
            DocumentAttachment.objects.filter(content_type=ct, object_id=object_id)
            .select_related(
                "document",
                "document__category",
                "document__uploaded_by",
                "content_type",
            )
            .order_by("-created_at")
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7.7  POST /api/v1/documents/{doc_id}/attach/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentAttachView(APIView):
    """
    Attach an existing document to any CivicOS object (staff only).

    Body:
        {
            "content_type":    "portal.servicerequest",
            "object_id":       "<uuid or integer>",
            "attachment_role": "supporting_evidence",
            "note":            "<optional staff note>"   // default ""
        }

    Permission: ``documents.upload_staff_document``.

    Response (HTTP 201):
        { "attachment_id": "<uuid>" }

    Error responses:
        400 — missing/invalid body fields, unknown content type, or
              object_id too long (max 50 characters per model field).
        403 — missing permission.
        404 — doc_id not found.
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsStaff]
    throttle_classes = [StaffRateThrottle]

    def post(self, request, doc_id):
        if not request.user.has_perm("documents.upload_staff_document"):
            raise PermissionDenied(
                "You do not have permission to attach documents to records."
            )

        # Staff coordinators can attach any document (no uploaded_by filter).
        doc = get_object_or_404(Document, pk=doc_id)

        # ── Inline body validation ────────────────────────────────────────────
        data = request.data or {}
        content_type_str = str(data.get("content_type", "")).strip()
        object_id = str(data.get("object_id", "")).strip()
        attachment_role = str(data.get("attachment_role", "")).strip()
        note = str(data.get("note", "")).strip()

        errors: dict = {}
        ct = None

        if not content_type_str:
            errors["content_type"] = ["This field is required."]
        else:
            try:
                app_label, model_name = content_type_str.split(".", 1)
                ct = ContentType.objects.get_by_natural_key(app_label, model_name)
            except (ValueError, ContentType.DoesNotExist):
                errors["content_type"] = [
                    f"Unknown content type: '{content_type_str}'. "
                    "Expected format: 'app_label.model'."
                ]

        if not object_id:
            errors["object_id"] = ["This field is required."]
        elif len(object_id) > 50:
            # DocumentAttachment.object_id is max_length=50 (supports UUID 36 chars
            # and integer PKs). Enforce here to avoid a DB-level DataError.
            errors["object_id"] = [
                f"Ensure this value has at most 50 characters (it has {len(object_id)})."
            ]

        if not attachment_role:
            errors["attachment_role"] = ["This field is required."]
        elif len(attachment_role) > 50:
            errors["attachment_role"] = [
                f"Ensure this value has at most 50 characters "
                f"(it has {len(attachment_role)})."
            ]

        if errors:
            raise ValidationError(errors)

        attachment = DocumentAttachment.objects.create(
            document=doc,
            content_type=ct,
            object_id=object_id,
            attachment_role=attachment_role,
            note=note,
            attached_by=request.user,
        )

        logger.info(
            "Document pk=%s attached to %s:%s by user pk=%s (role=%s)",
            doc.pk,
            content_type_str,
            object_id,
            request.user.pk,
            attachment_role,
        )

        return Response(
            {"attachment_id": str(attachment.pk)},
            status=status.HTTP_201_CREATED,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7.9  GET /api/v1/documents/{doc_id}/versions/
# ─────────────────────────────────────────────────────────────────────────────


class DocumentVersionsView(APIView):
    """
    List all versions in the document's version chain.

    Version chains are short (typically 1–5), so no pagination is applied.

    Algorithm:
        1. Fetch the requested document (IDOR-safe; citizen sees own only).
        2. Determine the root: ``doc.root_document`` if the doc is not v1;
           otherwise ``doc`` itself is the root.
        3. Return all docs where ``pk == root.pk OR root_document == root``,
           ordered by ``version_number`` ascending.

    Response (HTTP 200): list of DocumentSerializer objects.

    Error responses:
        404 — document not found or not owned.
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request, doc_id):
        # IDOR: citizens can only traverse version chains for their own documents.
        # Coordinators (view_all_documents) may traverse any chain.
        doc = _get_document_for_user(
            request,
            doc_id,
            require_not_deleted=True,
            allow_coordinator=True,
        )

        # root_document is NULL on version 1; the version 1 doc IS the root.
        root = doc.root_document if doc.root_document_id is not None else doc

        # select_related("category", "uploaded_by") prevents N+1 queries.
        # DocumentSerializer accesses category.slug/.name_en/.name_fr and
        # uploaded_by.pk — without prefetch those fire one query per version.
        versions = (
            Document.objects.filter(
                dj_models.Q(pk=root.pk) | dj_models.Q(root_document=root)
            )
            .select_related("category", "uploaded_by")
            .order_by("version_number")
        )

        serializer = DocumentSerializer(
            versions,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

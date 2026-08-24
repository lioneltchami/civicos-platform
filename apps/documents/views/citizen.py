"""
apps/documents/views/citizen.py
================================
Citizen-facing views for the Document Management Building Block (Wave 5).

Security invariants (must never be violated):
---------------------------------------------
1.  ``storage_key`` is NEVER placed in template context, JSON responses, HTTP
    headers, or log messages.
2.  ``original_filename`` is NEVER written to audit ``event_detail``.
3.  Citizens receive HTTP 404 (not 403) for PKs that exist but are not theirs
    (IDOR prevention).
4.  Only ``scan_status == ACTIVE`` documents are downloadable.
5.  File serving via ``DocumentTokenRedeemView``:
    - Scan status and soft-delete state are re-checked AFTER token redemption;
      a document quarantined or deleted inside the token's TTL window is not
      served (HTTP 404).
    - Small files (≤ ``DOCUMENT_PROXY_MAX_BYTES``) are streamed via
      ``FileResponse`` with a pk-derived Content-Disposition filename.
    - Large files redirect to a presigned URL generated with the same helper
      and the same ``DOCUMENT_PRESIGNED_URL_TTL_SECONDS`` (300s) TTL as the DRF
      API path (see ``_download_redirect_url``); ``default_storage.url()`` is
      used only on non-S3 (dev) backends.
    - The raw ``storage_key`` string is never present in any HTTP response.
6.  ``LoginRequiredMixin`` is always listed *before* any other mixin so
    unauthenticated requests are redirected to the login page.
7.  PII (names, email) is never written to log records — only ``.pk`` values.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from apps.documents.forms import DocumentUploadIntentForm
from apps.documents.models import Document
from apps.documents.services.download import (
    consume_access_token,
    generate_presigned_download_url,
    issue_access_token,
)
from apps.documents.services.upload import confirm_upload, validate_upload_request

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_access_denied(*, requested_pk, requesting_user) -> None:  # noqa: ANN001
    """
    Write an AuditEventType.ACCESS_DENIED entry for a denied (IDOR) access
    attempt — spec §13.1 "Access denied (non-owned PK)".

    Only call this when the requested pk genuinely belongs to someone else
    (the document exists but is not owned by requesting_user) — this is
    audit-only and never changes the view's Http404 response.

    PIPEDA: event_detail contains ONLY requested_pk and requesting_user_pk —
    no filename, storage_key, or other PII (spec §13.2). Duplicated (rather
    than imported) from the near-identical helper in
    apps.api.documents.views / apps.documents.services.download — matches
    this codebase's established convention of keeping small private helpers
    local to each module.
    """
    from apps.audit.models import AuditEventType
    from apps.audit.services import record_event

    try:
        record_event(
            event_type=AuditEventType.ACCESS_DENIED,
            actor_id=str(requesting_user.pk),
            resource_type="documents.Document",
            resource_id=str(requested_pk),
            event_detail={
                "requested_pk": str(requested_pk),
                "requesting_user_pk": str(requesting_user.pk),
            },
        )
    except Exception:
        logger.exception(
            "_record_access_denied: audit write failed for requested_pk=%s "
            "requesting_user_pk=%s",
            requested_pk,
            requesting_user.pk,
        )


def _get_client_ip(request: HttpRequest) -> str | None:
    """
    Extract the client IP from ``REMOTE_ADDR``.

    ``HTTP_X_FORWARDED_FOR`` is intentionally ignored — it can be spoofed by
    clients and is only trustworthy when the reverse-proxy strips/rewrites it,
    which is enforced at the infrastructure layer (not here).
    """
    return request.META.get("REMOTE_ADDR")


def _proxy_threshold() -> int:
    """Return the file-size threshold (bytes) below which files are proxied."""
    civicos: dict[str, Any] = getattr(settings, "CIVICOS", {})
    return civicos.get("DOCUMENT_PROXY_MAX_BYTES", 1 * 1024 * 1024)


def _download_redirect_url(storage_key: str) -> str:
    """
    Build the redirect target used to serve a large file after token redemption.

    L-2 fix — TTL parity with the DRF API path
    ──────────────────────────────────────────
    On an S3 backend the URL is generated by
    ``services.download.generate_presigned_download_url()`` with an explicit TTL
    of ``CIVICOS["DOCUMENT_PRESIGNED_URL_TTL_SECONDS"]`` (300s), i.e. exactly
    the same helper and the same TTL the DRF ``DocumentTokenRedeemView`` uses.

    ``default_storage.url()`` was used here previously. It honours
    ``AWS_QUERYSTRING_EXPIRE`` — 3600s in production — so a redirect URL
    forwarded to a third party stayed usable for a full hour: 12× the
    single-use ``DocumentAccessToken``'s own 300s TTL, and directly contrary to
    that model's documented intent ("the redirect TTL must match
    DOCUMENT_PRESIGNED_URL_TTL_SECONDS so a forwarded URL cannot outlive the
    single-use token").

    Non-S3 backends (FileSystemStorage in dev/test) have no presigned-URL or
    boto3 concept at all — ``default_storage.url()`` returns a plain ``/media/``
    path there and no query-string expiry applies, so they keep using it.

    PIPEDA: the raw storage key is passed in but never logged or returned to
    the caller in unsigned form.
    """  # noqa: RUF002
    from apps.documents.services.upload import _is_s3_storage

    if _is_s3_storage():
        civicos: dict[str, Any] = getattr(settings, "CIVICOS", {})
        ttl_seconds: int = civicos.get("DOCUMENT_PRESIGNED_URL_TTL_SECONDS", 300)
        return generate_presigned_download_url(
            storage_key=storage_key,
            ttl_seconds=ttl_seconds,
        )

    return default_storage.url(storage_key)


# ---------------------------------------------------------------------------
# DocumentListView
# ---------------------------------------------------------------------------


class DocumentListView(LoginRequiredMixin, ListView):
    """
    List the authenticated citizen's own latest active documents.

    Queryset is scoped strictly to the current user so no cross-user data
    leakage is possible at the database level.  Only ACTIVE, non-soft-deleted,
    latest-version documents are shown.

    Template context excludes ``storage_key`` — only safe document metadata.
    """

    template_name = "documents/citizen/document_list.html"
    context_object_name = "documents"
    paginate_by = 20

    def get_queryset(self):  # type: ignore[override]  # noqa: ANN201
        return (
            Document.objects.active()
            .latest_versions()
            .filter(uploaded_by=self.request.user)
            .select_related("category")
            .order_by("-created_at")
            # Explicitly defer storage_key to enforce the invariant at the ORM
            # level — it will never appear in template context.
            .defer("_storage_key")
        )

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        # total_count: use the already-filtered queryset set by ListView so the
        # count reflects only this citizen's own active documents.
        context["total_count"] = self.object_list.count()
        return context


# ---------------------------------------------------------------------------
# DocumentDetailView
# ---------------------------------------------------------------------------


class DocumentDetailView(LoginRequiredMixin, View):
    """
    Show metadata for a single document owned by the current citizen.

    Returns HTTP 404 (not 403) for documents that exist but are not owned by
    the requesting user, preventing IDOR enumeration.
    """

    template_name = "documents/citizen/document_detail.html"

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        # .defer("_storage_key") ensures the field is never accidentally passed
        # downstream, e.g., via {{ document.storage_key }} in a template.
        doc = (
            Document.objects.filter(
                pk=pk,
                uploaded_by=request.user,
                deleted_at__isnull=True,
            )
            .select_related("category", "uploaded_by")
            .defer("_storage_key")
            .first()
        )
        if doc is None:
            # Audit trail (spec §13): only a genuine IDOR-deny (pk exists,
            # owned by someone else) is audit-worthy — a bare "doesn't exist"
            # 404 carries no security signal. Audit-only; response unchanged.
            if Document.objects.filter(pk=pk, deleted_at__isnull=True).exists():
                _record_access_denied(requested_pk=pk, requesting_user=request.user)
            raise Http404

        return render(
            request,
            self.template_name,
            {
                "document": doc,
                "can_download": doc.scan_status == Document.ScanStatus.ACTIVE,
            },
        )


# ---------------------------------------------------------------------------
# DocumentUploadInitView
# ---------------------------------------------------------------------------


class DocumentUploadInitView(LoginRequiredMixin, View):
    """
    Two-phase upload initiation.

    GET  — Render ``DocumentUploadIntentForm`` so the citizen can describe the
           file they wish to upload.

    POST — Validate the form, call ``validate_upload_request()`` to create a
           ``Document`` in PENDING_UPLOAD state and obtain a presigned POST URL,
           then render the presign form that the browser will submit directly to
           object storage.

    ``storage_key`` is NEVER placed in the template context.  The presign
    context includes only ``upload_url``, ``upload_fields``, ``doc_id``, and
    ``confirm_url``.
    """

    form_template = "documents/citizen/upload_form.html"
    presign_template = "documents/citizen/upload_presign.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        form = DocumentUploadIntentForm(user=request.user)
        return render(request, self.form_template, {"form": form})

    def post(self, request: HttpRequest) -> HttpResponse:
        form = DocumentUploadIntentForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.form_template, {"form": form}, status=422)

        try:
            result = validate_upload_request(
                user=request.user,
                category_slug=form.cleaned_data["category_slug"],
                original_filename=form.cleaned_data["original_filename"],
                mime_type=form.cleaned_data["mime_type"],
                size_bytes=form.cleaned_data["size_bytes"],
            )
        except PermissionDenied:
            logger.warning(
                "UploadInitView: PermissionDenied for user pk=%s, category=%s",
                request.user.pk,
                form.cleaned_data.get("category_slug"),
            )
            messages.error(
                request,
                _("You do not have permission to upload to this category."),
            )
            return render(request, self.form_template, {"form": form}, status=403)
        except ValidationError as exc:
            logger.info(
                "UploadInitView: ValidationError for user pk=%s: %s",
                request.user.pk,
                exc.messages[0] if exc.messages else str(exc),
            )
            form.add_error(None, exc)
            return render(request, self.form_template, {"form": form}, status=422)
        except Exception:
            logger.exception(
                "UploadInitView: unexpected error for user pk=%s",
                request.user.pk,
            )
            messages.error(request, _("An unexpected error occurred. Please try again."))
            return render(request, self.form_template, {"form": form}, status=500)

        doc_id: str = result["doc_id"]

        # SECURITY: storage_key is NOT in `result` (the service returns only
        # upload_url, upload_fields, doc_id, expires_at).  No further scrubbing
        # is required here, but we are explicit about what we pass to the
        # template context.
        #
        # upload_fields is passed as a dict so the template can use
        # ``{% for name, val in upload_fields.items %}`` and the
        # ``"success_action_redirect" not in upload_fields`` dict membership
        # check both work correctly.  Python 3.7+ guarantees dict insertion
        # order, so field ordering is deterministic.
        context = {
            "upload_url": result["upload_url"],
            "upload_fields": result["upload_fields"],
            "doc_id": doc_id,
            "expires_at": result["expires_at"],
            "confirm_url": reverse("documents:upload-confirm", args=[doc_id]),
            # Pass description through so the confirm step can store it.
            "description": form.cleaned_data.get("description", ""),
        }
        return render(request, self.presign_template, context)


# ---------------------------------------------------------------------------
# DocumentUploadConfirmView
# ---------------------------------------------------------------------------


class DocumentUploadConfirmView(LoginRequiredMixin, View):
    """
    Called by the browser after the direct-to-storage upload completes.

    POST ``/<uuid:pk>/confirm/``

    Calls ``confirm_upload()`` which validates the file in quarantine, advances
    the ``Document`` to SCANNING status, and dispatches the ClamAV task.
    On success, redirects the citizen to their document list.
    """

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        try:
            confirm_upload(user=request.user, doc_id=str(pk))
        except Http404:
            # doc_id not found or not owned by this user (IDOR guard from service).
            raise Http404  # noqa: B904
        except ValidationError as exc:
            _exc_msg = exc.messages[0] if exc.messages else str(exc)
            logger.info(
                "UploadConfirmView: ValidationError for user pk=%s, doc_id=%s: %s",
                request.user.pk,
                pk,
                _exc_msg,
            )
            messages.error(
                request,
                _("Upload confirmation failed: %(detail)s") % {"detail": _exc_msg},
            )
            return redirect(reverse("documents:upload-init"))
        except Exception:
            logger.exception(
                "UploadConfirmView: unexpected error for user pk=%s, doc_id=%s",
                request.user.pk,
                pk,
            )
            messages.error(request, _("An unexpected error occurred. Please try again."))
            return redirect(reverse("documents:upload-init"))

        messages.success(
            request,
            _(
                "Your document has been received and is being scanned for security. "
                "It will be available shortly."
            ),
        )
        return redirect(reverse("documents:list"))


# ---------------------------------------------------------------------------
# DocumentDownloadView
# ---------------------------------------------------------------------------


class DocumentDownloadView(LoginRequiredMixin, View):
    """
    Issue a single-use ``DocumentAccessToken`` and redirect the citizen to the
    token-redeem URL.

    GET ``/<uuid:pk>/download/``

    Citizens receive HTTP 404 for documents that:
    - Do not exist,
    - Are not owned by them,
    - Are soft-deleted, or
    - Are not in ACTIVE scan status.

    The redirect is to ``documents:token-redeem`` which performs the actual
    file serving, so the signed URL or streamed bytes never pass through this
    view.
    """

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        # Ownership + ACTIVE check — 404 for any failure (IDOR prevention).
        doc = (
            Document.objects.filter(
                pk=pk,
                uploaded_by=request.user,
                scan_status=Document.ScanStatus.ACTIVE,
                deleted_at__isnull=True,
            )
            .defer("_storage_key")
            .first()
        )
        if doc is None:
            # Audit trail (spec §13): only a genuine IDOR-deny (pk exists,
            # owned by someone else) is audit-worthy — a bare "doesn't exist"
            # 404 (or a not-yet-ACTIVE own document) carries no such signal.
            # Audit-only; response unchanged.
            if (
                Document.objects.filter(pk=pk, deleted_at__isnull=True)
                .exclude(uploaded_by=request.user)
                .exists()
            ):
                _record_access_denied(requested_pk=pk, requesting_user=request.user)
            raise Http404

        try:
            token = issue_access_token(
                user=request.user,
                document=doc,
                ip_address=_get_client_ip(request),
            )
        except Http404:
            raise Http404  # noqa: B904
        except Exception:
            logger.exception(
                "DocumentDownloadView: failed to issue token for user pk=%s, doc pk=%s",
                request.user.pk,
                pk,
            )
            raise Http404  # noqa: B904

        redeem_url = reverse("documents:token-redeem", args=[token.token])
        return redirect(redeem_url)


# ---------------------------------------------------------------------------
# DocumentTokenRedeemView
# ---------------------------------------------------------------------------


class DocumentTokenRedeemView(LoginRequiredMixin, View):
    """
    Consume a single-use ``DocumentAccessToken`` and serve the document file.

    GET ``/dl/<str:token>/``

    The token is validated atomically by ``consume_access_token()``.  On
    success a ``Document`` instance is returned.

    Post-redemption gate (H-2):
      Even though the token was issued against an ACTIVE document, the document
      may have been QUARANTINED (delayed or re-run scan) or soft-deleted inside
      the token's TTL window.  Both are re-checked here and yield HTTP 404 —
      the same guard the DRF ``DocumentTokenRedeemView`` applies.

    File serving strategy:
    - Small files (size_bytes ≤ ``DOCUMENT_PROXY_MAX_BYTES``, default 1 MB):
      proxied through Django via ``FileResponse``.  Content-Disposition uses a
      safe pk-based filename — ``original_filename`` is NEVER used here.
    - Large files: HTTP 302 redirect to a short-lived signed URL built by
      ``_download_redirect_url()`` — a presigned S3 GET URL with a
      ``DOCUMENT_PRESIGNED_URL_TTL_SECONDS`` (300s) expiry in production, or a
      ``/media/`` path on dev's FileSystemStorage.  The raw ``storage_key``
      string is not present in any response header or body.

    SECURITY: ``storage_key`` is accessed internally (to open the file or
    generate the URL) but is NEVER placed in any response body, header, or
    log record.
    """

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        try:
            doc = consume_access_token(
                token_value=token,
                user=request.user,
                ip_address=_get_client_ip(request),
            )
        except Http404:
            raise Http404  # noqa: B904
        except Exception:
            logger.exception(
                "TokenRedeemView: unexpected error for user pk=%s",
                request.user.pk,
            )
            raise Http404  # noqa: B904

        # H-2: Re-verify scan status and soft-delete state AFTER redemption.
        #
        # The token was issued against an ACTIVE document, but the document may
        # have been quarantined by a delayed/re-run virus scan, or soft-deleted
        # by the citizen or a retention job, at any point inside the token's TTL
        # window. Without this re-check the HTML path keeps serving the bytes
        # for the remainder of that window — the DRF redeem view has always had
        # this guard; this view did not.
        #
        # 404 (the established failure convention for every other failure in
        # this view) rather than 403 — a 403 would confirm the document exists.
        if doc.scan_status != Document.ScanStatus.ACTIVE or doc.deleted_at is not None:
            logger.info(
                "TokenRedeemView: doc pk=%s no longer downloadable "
                "(scan_status=%s, deleted=%s); refusing to serve.",
                doc.pk,
                doc.scan_status,
                doc.deleted_at is not None,
            )
            raise Http404

        proxy_max = _proxy_threshold()

        if doc.size_bytes <= proxy_max:
            # Proxy small files directly through Django.
            # SECURITY: Content-Disposition uses doc.pk, NOT original_filename.
            try:
                f = default_storage.open(doc.storage_key, "rb")
                response = FileResponse(f, content_type=doc.mime_type)
                safe_name = f"document-{doc.pk}.bin"
                response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
                return response
            except Exception:
                logger.exception(
                    "TokenRedeemView: failed to open storage for doc pk=%s",
                    doc.pk,
                )
                raise Http404  # noqa: B904
        else:
            # Large files: redirect to a storage-generated URL.
            # In S3-backed environments this is a freshly signed GET URL whose
            # TTL matches DOCUMENT_PRESIGNED_URL_TTL_SECONDS (300s) — see
            # _download_redirect_url() for why default_storage.url() is not used
            # there.  In local/dev environments it returns a /media/ path.
            # Either way the raw storage_key is not exposed in the response.
            try:
                storage_url = _download_redirect_url(doc.storage_key)
            except Exception:
                logger.exception(
                    "TokenRedeemView: failed to generate storage URL for doc pk=%s",
                    doc.pk,
                )
                raise Http404  # noqa: B904

            return redirect(storage_url)

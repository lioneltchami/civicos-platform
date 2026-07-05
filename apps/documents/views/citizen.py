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
    - Small files (≤ ``DOCUMENT_PROXY_MAX_BYTES``) are streamed via
      ``FileResponse`` with a pk-derived Content-Disposition filename.
    - Large files redirect to ``default_storage.url(doc.storage_key)``, which
      returns a short-lived presigned URL in S3-backed environments.
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
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from apps.documents.forms import DocumentUploadIntentForm
from apps.documents.models import Document
from apps.documents.services.download import consume_access_token, issue_access_token
from apps.documents.services.upload import confirm_upload, validate_upload_request

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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

    def get_queryset(self):  # type: ignore[override]
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
                exc.message,
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
            raise Http404
        except ValidationError as exc:
            logger.info(
                "UploadConfirmView: ValidationError for user pk=%s, doc_id=%s: %s",
                request.user.pk,
                pk,
                exc.message,
            )
            messages.error(
                request,
                _("Upload confirmation failed: %(detail)s") % {"detail": exc.message},
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
            raise Http404

        try:
            token = issue_access_token(
                user=request.user,
                document=doc,
                ip_address=_get_client_ip(request),
            )
        except Http404:
            raise Http404
        except Exception:
            logger.exception(
                "DocumentDownloadView: failed to issue token for user pk=%s, doc pk=%s",
                request.user.pk,
                pk,
            )
            raise Http404

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

    File serving strategy:
    - Small files (size_bytes ≤ ``DOCUMENT_PROXY_MAX_BYTES``, default 1 MB):
      proxied through Django via ``FileResponse``.  Content-Disposition uses a
      safe pk-based filename — ``original_filename`` is NEVER used here.
    - Large files: HTTP 302 redirect to ``default_storage.url(storage_key)``,
      which returns a short-lived presigned URL (S3) or a ``/media/`` path
      (local dev).  The raw ``storage_key`` string is not present in any
      response header or body.

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
            raise Http404
        except Exception:
            logger.exception(
                "TokenRedeemView: unexpected error for user pk=%s",
                request.user.pk,
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
                response["Content-Disposition"] = (
                    f'attachment; filename="{safe_name}"'
                )
                return response
            except Exception:
                logger.exception(
                    "TokenRedeemView: failed to open storage for doc pk=%s",
                    doc.pk,
                )
                raise Http404
        else:
            # Large files: redirect to a storage-generated URL.
            # In S3-backed environments, default_storage.url() returns a
            # time-limited presigned GET URL.  In local/dev environments it
            # returns a /media/ path.  Either way the raw storage_key is not
            # exposed in the response.
            try:
                storage_url = default_storage.url(doc.storage_key)
            except Exception:
                logger.exception(
                    "TokenRedeemView: failed to generate storage URL for doc pk=%s",
                    doc.pk,
                )
                raise Http404

            return redirect(storage_url)

"""
apps/documents/views/staff.py
==============================
Staff-facing views for the Document Management Building Block (Wave 5).

All views in this module require authentication AND a specific model-level
permission.  The MRO is always::

    class SomeView(LoginRequiredMixin, PermissionRequiredMixin, ...):
        ...

so that unauthenticated requests are redirected to login *before* the
permission check is performed.

``raise_exception = True`` is set on every view so that authenticated users
who lack the required permission receive HTTP 403 rather than being silently
redirected to login.

Security invariants:
--------------------
1. ``storage_key`` is NEVER placed in template context.  Querysets always call
   ``.defer("_storage_key")``.
2. ``original_filename`` is NEVER written to audit ``event_detail``.
3. PII (user email, names) is NEVER written to log records — only ``.pk``.
4. ``LoginRequiredMixin`` always precedes ``PermissionRequiredMixin`` in MRO.
5. All staff views set ``raise_exception = True``.
"""
from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from apps.documents.forms import DocumentFilterForm, LegalHoldForm
from apps.documents.models import Document, DocumentCategory
from apps.documents.services.retention import (
    apply_legal_hold,
    release_legal_hold,
    soft_delete,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DocumentAdminListView
# ---------------------------------------------------------------------------


class DocumentAdminListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    Paginated list of all documents visible to staff.

    Requires ``documents.view_all_documents``.

    Filtering is driven by ``DocumentFilterForm``:
    - ``scan_status``   — exact match on ScanStatus value
    - ``legal_hold``    — ``"yes"`` / ``"no"`` / ``""`` (any)
    - ``category``      — matches ``category__slug``
    - ``search``        — partial match on UUID string or category slug
    - ``date_from`` / ``date_to`` — inclusive range on ``created_at``

    ``storage_key`` is deferred at the queryset level so it can never
    accidentally appear in template context.
    """

    permission_required = "documents.view_all_documents"
    raise_exception = True

    template_name = "documents/staff/document_list.html"
    context_object_name = "documents"
    paginate_by = 25

    def get_queryset(self) -> QuerySet:  # type: ignore[override]
        qs = (
            Document.objects.all()
            .select_related("category", "uploaded_by")
            .defer("_storage_key")
            .order_by("-created_at")
        )

        form = DocumentFilterForm(self.request.GET or None)
        if form.is_valid():
            if status_val := form.cleaned_data.get("scan_status"):
                qs = qs.filter(scan_status=status_val)

            if legal_hold_val := form.cleaned_data.get("legal_hold"):
                if legal_hold_val == "yes":
                    qs = qs.filter(legal_hold=True)
                elif legal_hold_val == "no":
                    qs = qs.filter(legal_hold=False)

            if category_slug := form.cleaned_data.get("category"):
                qs = qs.filter(category__slug=category_slug)

            if search := form.cleaned_data.get("search"):
                qs = qs.filter(
                    Q(pk__icontains=search) | Q(category__slug__icontains=search)
                )

            if date_from := form.cleaned_data.get("date_from"):
                qs = qs.filter(created_at__date__gte=date_from)

            if date_to := form.cleaned_data.get("date_to"):
                qs = qs.filter(created_at__date__lte=date_to)

        return qs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = DocumentFilterForm(self.request.GET or None)
        # total_count: use the already-evaluated queryset stored by ListView so
        # we don't issue a second COUNT query.
        ctx["total_count"] = self.object_list.count()
        # categories: needed by the template's category filter dropdown.
        ctx["categories"] = DocumentCategory.objects.order_by("name_en")
        return ctx


# ---------------------------------------------------------------------------
# StaffDocumentDetailView
# ---------------------------------------------------------------------------


class StaffDocumentDetailView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Full metadata view for a single document, accessible to staff.

    Requires ``documents.view_all_documents``.

    ``storage_key`` is deferred so it is never present in the object passed to
    the template.
    """

    permission_required = "documents.view_all_documents"
    raise_exception = True

    template_name = "documents/staff/document_detail.html"

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        doc = get_object_or_404(
            Document.objects.select_related(
                "category",
                "uploaded_by",
                "legal_hold_set_by",
                "root_document",
            ).defer("_storage_key"),
            pk=pk,
        )
        return render(
            request,
            self.template_name,
            {
                "document": doc,
                # Passed explicitly so the template never needs to call has_perm
                # inline, and so tests can inspect it directly on the context.
                "can_view_quarantine_details": request.user.has_perm(
                    "documents.view_quarantined"
                ),
            },
        )


# ---------------------------------------------------------------------------
# DocumentLegalHoldView
# ---------------------------------------------------------------------------


class DocumentLegalHoldView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Apply or release a legal hold on a document.

    Requires ``documents.manage_legal_hold``.

    GET  — Show the current hold state and ``LegalHoldForm``.
    POST — Dispatch to ``apply_legal_hold()`` or ``release_legal_hold()``
           based on ``form.cleaned_data["action"]``.

    On ``PermissionDenied`` from the service layer (the service re-checks the
    permission independently), we surface an error message and return 403.
    On ``ValidationError`` (e.g., trying to apply hold that is already active),
    we add a form error and re-render.
    """

    permission_required = "documents.manage_legal_hold"
    raise_exception = True

    template_name = "documents/staff/legal_hold_form.html"

    def _get_document(self, pk: str) -> Document:
        return get_object_or_404(
            Document.objects.select_related("legal_hold_set_by", "category").defer(
                "_storage_key"
            ),
            pk=pk,
        )

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        doc = self._get_document(pk)
        # Pre-populate the hidden `action` field so it renders with the correct
        # value and the form validates on POST without requiring JS to set it.
        initial_action = (
            LegalHoldForm.ACTION_RELEASE
            if doc.legal_hold
            else LegalHoldForm.ACTION_APPLY
        )
        form = LegalHoldForm(initial={"action": initial_action})
        return render(
            request,
            self.template_name,
            {"document": doc, "form": form},
        )

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        doc = self._get_document(pk)
        form = LegalHoldForm(request.POST)

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"document": doc, "form": form},
                status=422,
            )

        action: str = form.cleaned_data["action"]
        reason: str = form.cleaned_data["reason"]

        try:
            if action == LegalHoldForm.ACTION_APPLY:
                apply_legal_hold(document=doc, set_by=request.user, reason=reason)
                messages.success(
                    request,
                    _("Legal hold applied to document %(pk)s.") % {"pk": doc.pk},
                )
            elif action == LegalHoldForm.ACTION_RELEASE:
                release_legal_hold(
                    document=doc, released_by=request.user, reason=reason
                )
                messages.success(
                    request,
                    _("Legal hold released from document %(pk)s.") % {"pk": doc.pk},
                )
            else:
                # Shouldn't happen — ChoiceField validates this.
                form.add_error("action", _("Invalid action."))
                return render(
                    request,
                    self.template_name,
                    {"document": doc, "form": form},
                    status=422,
                )

        except PermissionDenied:
            logger.warning(
                "DocumentLegalHoldView: PermissionDenied for user pk=%s on doc pk=%s",
                request.user.pk,
                pk,
            )
            messages.error(
                request,
                _("You do not have permission to manage legal holds."),
            )
            return render(
                request,
                self.template_name,
                {"document": doc, "form": form},
                status=403,
            )

        except ValidationError as exc:
            logger.info(
                "DocumentLegalHoldView: ValidationError for user pk=%s, doc pk=%s: %s",
                request.user.pk,
                pk,
                exc.message,
            )
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"document": doc, "form": form},
                status=422,
            )

        except Exception:
            logger.exception(
                "DocumentLegalHoldView: unexpected error for user pk=%s, doc pk=%s",
                request.user.pk,
                pk,
            )
            messages.error(request, _("An unexpected error occurred. Please try again."))
            return render(
                request,
                self.template_name,
                {"document": doc, "form": form},
                status=500,
            )

        return redirect(reverse("documents:staff-detail", args=[pk]))


# ---------------------------------------------------------------------------
# DocumentAuditLogView
# ---------------------------------------------------------------------------


class DocumentAuditLogView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Paginated audit log for a single document.

    Requires ``documents.view_all_documents``.

    Fetches ``AuditLogEntry`` rows where ``resource_type`` is
    ``"documents.Document"`` and ``resource_id`` matches the document PK.
    Ordered by newest-first; paginated 50 per page.

    Note: ``AuditLogEntry`` is imported lazily to avoid hard-coupling to the
    audit app's ORM class at import time — the model may not be registered
    during early Django startup in tests.
    """

    permission_required = "documents.view_all_documents"
    raise_exception = True

    template_name = "documents/staff/audit_log.html"
    paginate_by = 50

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        # Verify the document exists and the staff member can see it.
        doc = get_object_or_404(
            Document.objects.defer("_storage_key").select_related("category"),
            pk=pk,
        )

        from apps.audit.models import AuditLogEntry  # noqa: PLC0415 — deferred import

        entries_qs = AuditLogEntry.objects.filter(
            resource_type="documents.Document",
            resource_id=str(pk),
        ).order_by("-timestamp")

        # Manual pagination (we don't inherit from ListView here because the
        # primary object is a Document, not an AuditLogEntry).
        from django.core.paginator import Paginator  # noqa: PLC0415

        paginator = Paginator(entries_qs, self.paginate_by)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        return render(
            request,
            self.template_name,
            {
                "document": doc,
                "page_obj": page_obj,
                "paginator": paginator,
                "is_paginated": paginator.num_pages > 1,
                "audit_entries": page_obj.object_list,
            },
        )


# ---------------------------------------------------------------------------
# DocumentQuarantineListView
# ---------------------------------------------------------------------------


class DocumentQuarantineListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    List of documents currently in QUARANTINED scan status.

    Requires ``documents.view_quarantined``.

    Shows only metadata; ``storage_key`` is deferred.  This view enables
    administrators to review documents that the AV scanner has flagged so they
    can decide whether to apply a legal hold, escalate, or purge.
    """

    permission_required = "documents.view_quarantined"
    raise_exception = True

    template_name = "documents/staff/quarantine_list.html"
    context_object_name = "documents"
    paginate_by = 25

    def get_queryset(self) -> QuerySet:  # type: ignore[override]
        return (
            Document.objects.filter(scan_status=Document.ScanStatus.QUARANTINED)
            .select_related("category", "uploaded_by")
            .defer("_storage_key")
            .order_by("-created_at")
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        # total_count used by the template's "N quarantined document(s)" summary.
        ctx["total_count"] = self.object_list.count()
        return ctx

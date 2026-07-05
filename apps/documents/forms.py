"""
apps/documents/forms.py
=======================
Forms for the Document Management Building Block (Wave 5).

Security invariants enforced at the form layer:
- ``original_filename`` is validated for length/encoding but NEVER echoed
  verbatim into audit logs (PII).
- ``size_bytes`` is validated against the CIVICOS upload ceiling so malformed
  requests are rejected before hitting the service layer.
- ``mime_type`` is restricted to an allow-list so the UI cannot trick the
  service into accepting an exotic content type.
- No ``storage_key`` field is present anywhere; that field is internal-only.
"""
from __future__ import annotations

import logging
import mimetypes
from typing import Any

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.documents.models import Document, DocumentCategory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Allow-list of MIME types citizens may upload. Extend via settings if needed.
_DEFAULT_ALLOWED_MIME_TYPES: list[str] = [
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
]

#: Maximum upload size (bytes). Read from CIVICOS settings; default 50 MB.
_civicos: dict[str, Any] = getattr(settings, "CIVICOS", {})
_MAX_UPLOAD_BYTES: int = _civicos.get("DOCUMENT_MAX_CITIZEN_UPLOAD_BYTES", 50 * 1024 * 1024)
_ALLOWED_MIME_TYPES: list[str] = _civicos.get(
    "ALLOWED_UPLOAD_MIME_TYPES", _DEFAULT_ALLOWED_MIME_TYPES
)


# ---------------------------------------------------------------------------
# DocumentUploadIntentForm
# ---------------------------------------------------------------------------


class DocumentUploadIntentForm(forms.Form):
    """
    Captures the metadata the citizen provides *before* the file is uploaded
    directly to object storage.

    This form does NOT handle the file bytes — it collects enough information
    to call ``validate_upload_request()`` and receive a presigned upload URL.

    Fields
    ------
    category_slug
        Slug of the ``DocumentCategory`` the upload belongs to.
    original_filename
        Client-side filename.  Validated for length and basic safety, but
        NEVER written to audit logs (potential PII).
    mime_type
        Content-type the browser reports for the file being uploaded.
    size_bytes
        Size of the file in bytes (reported by the browser).
    description
        Optional human-readable description of the document.
    """

    category_slug = forms.SlugField(
        label=_("Category"),
        max_length=100,
        widget=forms.Select,
        help_text=_("Select the document category."),
    )
    original_filename = forms.CharField(
        label=_("File name"),
        max_length=255,
        strip=True,
        help_text=_("The name of the file you are uploading."),
    )
    mime_type = forms.ChoiceField(
        label=_("File type"),
        choices=[],  # populated in __init__
        help_text=_("The content type of the file."),
    )
    size_bytes = forms.IntegerField(
        label=_("File size (bytes)"),
        min_value=1,
        max_value=_MAX_UPLOAD_BYTES,
        widget=forms.HiddenInput,
    )
    description = forms.CharField(
        label=_("Description"),
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Optional description or notes about this document."),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Populate category choices from DB (slug → display name).
        # C-5 fix: citizens must never see staff-only categories in the rendered
        # <select> element.  Only users who hold the upload_staff_document
        # permission (or superusers) may see staff-only categories.
        if user and (user.is_superuser or user.has_perm("documents.upload_staff_document")):
            categories = DocumentCategory.objects.all().order_by("name_en")
        else:
            categories = DocumentCategory.objects.filter(staff_only=False).order_by("name_en")
        self.fields["category_slug"].widget = forms.Select(  # type: ignore[assignment]
            choices=[("", _("— Select category —"))]
            + [(c.slug, c.name_en) for c in categories]
        )

        # Populate MIME type choices from the allow-list.
        self.fields["mime_type"].choices = [  # type: ignore[attr-defined]
            (m, m) for m in _ALLOWED_MIME_TYPES
        ]

    def clean_original_filename(self) -> str:
        filename: str = self.cleaned_data["original_filename"]
        # Reject path traversal attempts.
        if ".." in filename or "/" in filename or "\\" in filename:
            raise forms.ValidationError(
                _("File name must not contain path separators.")
            )
        return filename

    def clean_mime_type(self) -> str:
        mime: str = self.cleaned_data["mime_type"]
        if mime not in _ALLOWED_MIME_TYPES:
            raise forms.ValidationError(
                _("File type %(mime)s is not permitted."),
                params={"mime": mime},
                code="disallowed_mime",
            )
        return mime

    def clean_size_bytes(self) -> int:
        size: int = self.cleaned_data["size_bytes"]
        if size > _MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                _(
                    "File size exceeds the maximum allowed size of "
                    "%(max)s bytes."
                ),
                params={"max": _MAX_UPLOAD_BYTES},
                code="file_too_large",
            )
        return size


# ---------------------------------------------------------------------------
# LegalHoldForm
# ---------------------------------------------------------------------------


class LegalHoldForm(forms.Form):
    """
    Staff form to apply or release a legal hold on a ``Document``.

    ``action`` drives which service function is called in the view.  It is
    intentionally a hidden field so the two actions can be rendered as separate
    submit buttons (the view template sets the value before submitting).

    Fields
    ------
    action
        Either ``"apply"`` or ``"release"``.
    reason
        Mandatory narrative explaining the hold or its release.  Written to
        the ``Document`` model and audit log, so MUST NOT contain PII.
    """

    ACTION_APPLY = "apply"
    ACTION_RELEASE = "release"
    ACTION_CHOICES = [
        (ACTION_APPLY, _("Apply legal hold")),
        (ACTION_RELEASE, _("Release legal hold")),
    ]

    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        widget=forms.HiddenInput,
    )
    reason = forms.CharField(
        label=_("Reason"),
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_(
            "Provide a clear reason for applying or releasing the legal hold. "
            "Do not include personal information."
        ),
    )
    #: Server-side enforcement of the confirmation checkbox in the template.
    #: The checkbox ``name="confirm_action"`` in the HTML maps to this field.
    #: Without this field, a crafted POST could bypass the browser-side
    #: ``required`` attribute and submit without confirmation.
    confirm_action = forms.BooleanField(
        label=_(
            "I confirm that I want to change the legal hold on this document "
            "and that the appropriate authorisation has been obtained."
        ),
        required=True,
        error_messages={
            "required": _(
                "You must confirm your intention before applying or releasing a legal hold."
            )
        },
    )

    def clean_reason(self) -> str:
        reason: str = self.cleaned_data["reason"].strip()
        if len(reason) < 10:
            raise forms.ValidationError(
                _("Please provide a more detailed reason (at least 10 characters).")
            )
        return reason


# ---------------------------------------------------------------------------
# DocumentSoftDeleteForm
# ---------------------------------------------------------------------------


class DocumentSoftDeleteForm(forms.Form):
    """
    Staff form to soft-delete a ``Document``.

    A legal hold blocks deletion at the service layer, but we present a
    confirmation field here as an additional safeguard against accidental
    submissions.

    Fields
    ------
    reason
        Why the document is being deleted.  Stored in the audit log.
    confirm
        Must be checked to proceed.
    """

    reason = forms.CharField(
        label=_("Deletion reason"),
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_(
            "State why this document is being deleted. "
            "Do not include personal information."
        ),
    )
    confirm = forms.BooleanField(
        label=_("I confirm that I want to permanently soft-delete this document."),
        required=True,
    )


# ---------------------------------------------------------------------------
# DocumentFilterForm
# ---------------------------------------------------------------------------


class DocumentFilterForm(forms.Form):
    """
    Staff-facing filter form for :class:`apps.documents.views.staff.DocumentAdminListView`.

    All fields are optional; absent fields are ignored in the view queryset.

    Fields
    ------
    scan_status
        Filter by ``Document.ScanStatus`` value.
    category
        Filter by ``DocumentCategory`` slug.
    legal_hold
        ``"yes"`` / ``"no"`` / ``""`` (any).
    search
        Partial match on document UUID (pk) or category slug.
    date_from / date_to
        Inclusive filter on ``Document.created_at`` date.
    """

    LEGAL_HOLD_CHOICES = [
        ("", _("Any")),
        ("yes", _("On legal hold")),
        ("no", _("No legal hold")),
    ]

    scan_status = forms.ChoiceField(
        label=_("Status"),
        choices=[("", _("Any status"))]
        + [(s.value, s.label) for s in Document.ScanStatus],  # type: ignore[attr-defined]
        required=False,
    )
    category = forms.CharField(
        label=_("Category slug"),
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("e.g. identity-documents")}),
    )
    legal_hold = forms.ChoiceField(
        label=_("Legal hold"),
        choices=LEGAL_HOLD_CHOICES,
        required=False,
    )
    search = forms.CharField(
        label=_("Search (UUID or category slug)"),
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("UUID prefix or category slug")}),
    )
    date_from = forms.DateField(
        label=_("Created from"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label=_("Created to"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        date_from = cleaned.get("date_from")
        date_to = cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError(
                _("'Created from' must be on or before 'Created to'.")
            )
        return cleaned

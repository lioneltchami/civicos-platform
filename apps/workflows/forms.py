"""Forms for the workflows staff queue views."""

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from apps.workflows.models import WorkItemStatus

User = get_user_model()


class AdvanceStatusForm(forms.Form):
    """Staff advances a WorkItem to a new status with an optional note."""

    new_status = forms.ChoiceField(
        choices=WorkItemStatus.choices,
        label=_("New status"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    notes = forms.CharField(
        required=False,
        max_length=2000,
        label=_("Notes"),
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        help_text=_("Optional note explaining the status change."),
    )


class AssignForm(forms.Form):
    """Supervisor assigns a WorkItem to a staff member."""

    assignee = forms.ModelChoiceField(
        queryset=User.objects.filter(is_staff=True, is_active=True).order_by("email"),
        label=_("Assign to"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class EscalateForm(forms.Form):
    """Staff or supervisor escalates a WorkItem."""

    reason = forms.CharField(
        required=False,
        max_length=1000,
        label=_("Reason for escalation"),
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )


class CommentForm(forms.Form):
    """Internal staff comment on a WorkItem."""

    body = forms.CharField(
        max_length=5000,
        label=_("Comment"),
        widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
    )

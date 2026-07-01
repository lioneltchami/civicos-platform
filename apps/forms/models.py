"""
Form builder models for CivicOS.

Extends Wagtail's built-in form builder (wagtail.contrib.forms) with:
- Consent checkbox tracking (PIPEDA requirement)
- Submission retention policies
- Bilingual form support
- Accessible field rendering metadata
"""

import logging

from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey

logger = logging.getLogger(__name__)
from wagtail.admin.panels import FieldPanel, FieldRowPanel, InlinePanel, MultiFieldPanel, ObjectList, TabbedInterface
from wagtail.contrib.forms.models import AbstractEmailForm, AbstractFormField, AbstractFormSubmission
from wagtail.fields import RichTextField


class FormField(AbstractFormField):
    """
    Extended form field with accessibility metadata and help text.
    """

    help_text_long = models.TextField(
        blank=True,
        verbose_name=_("Expanded help text"),
        help_text=_("Shown in a collapsible hint section for complex fields."),
    )
    is_pii = models.BooleanField(
        default=False,
        verbose_name=_("Contains personal information"),
        help_text=_("Flag fields that collect PII for retention and redaction purposes."),
    )

    # ParentalKey is required (not ForeignKey) — modelcluster uses it to manage
    # the inline relation. Must point to the concrete FormPage, not abstract Page.
    page = ParentalKey(
        "forms.FormPage",
        on_delete=models.CASCADE,
        related_name="form_fields",
    )

    panels = AbstractFormField.panels

    class Meta:
        ordering = ["sort_order"]
        verbose_name = _("Form field")


class FormSubmission(AbstractFormSubmission):
    """
    Extended form submission with consent and retention tracking.
    """

    # Override the inherited relation so it does not clash with Wagtail's
    # built-in form submission model on Page.formsubmission_set.
    page = models.ForeignKey(
        "forms.FormPage",
        on_delete=models.CASCADE,
        related_name="civicos_form_submissions",
    )

    consent_given = models.BooleanField(
        default=False,
        verbose_name=_("Consent given"),
    )
    consent_text_shown = models.TextField(
        blank=True,
        verbose_name=_("Consent text shown"),
        help_text=_("The exact consent text displayed to the user at time of submission."),
    )
    submitter_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("Submitter IP"),
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Expires at"),
        help_text=_("After this date, the submission will be purged per retention policy."),
        db_index=True,
    )

    class Meta:
        verbose_name = _("Form submission")
        verbose_name_plural = _("Form submissions")
        ordering = ["-submit_time"]

    def redact_pii(self, redacted_by=None) -> None:
        """
        PIPEDA right-to-erasure: replace PII field values with a redaction marker.
        Only fields flagged is_pii=True on the FormField are redacted.
        The submission record itself is retained for audit purposes.
        """
        from django.utils import timezone

        pii_field_names = set(
            self.page.form_fields.filter(is_pii=True).values_list("clean_name", flat=True)
        )
        if not pii_field_names:
            return

        form_data = self.form_data or {}
        for key in pii_field_names:
            if key in form_data:
                form_data[key] = "[REDACTED]"

        self.form_data = form_data
        # Store redaction timestamp in a neutral field
        self.consent_text_shown = (
            f"{self.consent_text_shown}\n[PII REDACTED {timezone.now().isoformat()}]".strip()
        )
        self.save(update_fields=["form_data", "consent_text_shown"])


class FormPage(AbstractEmailForm):
    """
    A configurable form page.

    Editors build the form in the Wagtail admin using form fields.
    Submissions are stored in FormSubmission and optionally emailed to staff.
    """

    intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link", "ol", "ul"],
        verbose_name=_("Introduction"),
        help_text=_("Displayed above the form. Explain what the form is for and what happens after submission."),
    )
    thank_you_text = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
        verbose_name=_("Thank you message"),
        help_text=_("Shown after a successful submission."),
    )
    consent_text = models.TextField(
        blank=True,
        verbose_name=_("Consent statement"),
        help_text=_("If set, a mandatory consent checkbox is added to the form. State how the data will be used."),
    )
    retention_days = models.PositiveIntegerField(
        default=365,
        verbose_name=_("Submission retention (days)"),
        help_text=_("Submissions will be purged after this many days."),
    )

    def get_submission_class(self):
        """
        Override to return our extended FormSubmission.
        Wagtail does not read a submission_class class attribute — this method
        is the only way to substitute the submission model.
        """
        return FormSubmission

    def serve(self, request, *args, **kwargs):
        """
        Override to inject the request onto the form instance.
        Wagtail's default serve() does not pass request to the form,
        so process_form_submission() cannot access it without this.
        """
        from django.template.response import TemplateResponse

        if request.method == "POST":
            form = self.get_form(
                request.POST, request.FILES, page=self, user=request.user
            )
            form.request = request  # the critical injection
            if form.is_valid():
                form_submission = self.process_form_submission(form)
                return self.render_landing_page(
                    request, form_submission, *args, **kwargs
                )
        else:
            form = self.get_form(page=self, user=request.user)
            form.request = request

        context = self.get_context(request)
        context["form"] = form
        return TemplateResponse(
            request,
            self.get_template(request),
            context,
        )

    def get_form_class(self):
        """
        Extend the Wagtail-generated form class with a mandatory consent
        checkbox if the page has consent_text configured.
        """
        from django import forms as django_forms

        form_class = super().get_form_class()

        if self.consent_text:
            # Dynamically add a consent field to the generated form class
            consent_field = django_forms.BooleanField(
                required=True,
                label=self.consent_text,
                error_messages={
                    "required": _(
                        "You must accept the consent statement to submit this form. / "
                        "Vous devez accepter la déclaration de consentement pour soumettre ce formulaire."
                    )
                },
            )
            # Create a subclass so we don't mutate the cached form class
            form_class = type(
                form_class.__name__,
                (form_class,),
                {"_consent": consent_field},
            )

        return form_class

    def process_form_submission(self, form):
        """
        Override to capture submitter IP and consent before persisting.
        The request is injected onto the form instance via our serve() override.
        """
        from django.conf import settings
        from django.utils import timezone

        submission = super().process_form_submission(form)

        # Wagtail passes request on form as form.request (set in serve())
        request = getattr(form, "request", None)
        if request is not None:
            # Respect SECURE_PROXY_SSL_HEADER — same pattern as auth signals
            if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
                x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
                ip = x_forwarded_for.split(",")[0].strip() if x_forwarded_for else None
            else:
                ip = None
            submission.submitter_ip = ip or request.META.get("REMOTE_ADDR")

        if self.consent_text:
            # Consent checkbox field value is stored in form.cleaned_data under
            # a slug derived from the label. Record whether it was checked.
            consent_value = form.cleaned_data.get("_consent", False)
            submission.consent_given = bool(consent_value)
            submission.consent_text_shown = self.consent_text

        # Set expiry based on page retention policy
        expires_at = timezone.now() + timezone.timedelta(days=self.retention_days)
        submission.expires_at = expires_at

        submission.save(update_fields=["submitter_ip", "consent_given", "consent_text_shown", "expires_at"])

        try:
            from apps.core.signals import form_submission_received
            results = form_submission_received.send_robust(
                sender=self.__class__,
                form_page=self,
                submission=submission,
                request=request,
            )
            for _receiver, result in results:
                if isinstance(result, Exception):
                    logger.exception(
                        "Signal handler %s raised for form_submission_received: %s",
                        _receiver,
                        result,
                    )
        except Exception:
            logger.exception(
                "Failed to emit form_submission_received signal for page_id=%s", self.pk
            )

        return submission

    def get_submissions_list_url(self):
        """URL to the staff submission list for this form page."""
        from django.urls import reverse
        return reverse("forms:submission-list", kwargs={"page_id": self.pk})

    content_panels = AbstractEmailForm.content_panels + [
        FieldPanel("intro"),
        InlinePanel("form_fields", label=_("Form fields")),
        FieldPanel("consent_text"),
        FieldPanel("thank_you_text"),
        MultiFieldPanel(
            [
                FieldRowPanel([
                    FieldPanel("from_address", classname="col6"),
                    FieldPanel("to_address", classname="col6"),
                ]),
                FieldPanel("subject"),
            ],
            heading=_("Email notification (optional)"),
        ),
    ]

    settings_panels = AbstractEmailForm.settings_panels + [
        FieldPanel("retention_days"),
    ]

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(AbstractEmailForm.promote_panels, heading=_("SEO & sharing")),
            ObjectList(settings_panels, heading=_("Settings")),
        ]
    )

    template = "forms/form_page.html"
    landing_page_template = "forms/form_page_landing.html"

    class Meta:
        verbose_name = _("Form page")

    parent_page_types = ["cms.HomePage", "cms.GenericPage", "cms.ServiceIndexPage"]
    subpage_types = []

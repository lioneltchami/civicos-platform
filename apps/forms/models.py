"""
Form builder models for Govstack.

Extends Wagtail's built-in form builder (wagtail.contrib.forms) with:
- Consent checkbox tracking (PIPEDA requirement)
- Submission retention policies
- Bilingual form support
- Accessible field rendering metadata
"""

from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
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
        related_name="govstack_form_submissions",
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

    # Override to use our custom field and submission models
    # Wagtail discovers these via class-level attributes (not method overrides)
    form_field = FormField
    submission_class = FormSubmission

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

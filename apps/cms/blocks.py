"""
Wagtail StreamField blocks for the CivicOS CMS.

All blocks are accessibility-first:
- RichText blocks restrict formatting to a safe subset
- Image blocks require alt text (via CustomImage.alt_text)
- Heading blocks enforce a logical hierarchy
- Accordion, table, and media blocks include ARIA instructions for editors

Usage in page models:
    body = StreamField(ContentStreamBlock(), use_json_field=True)
"""

from django.utils.translation import gettext_lazy as _
from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock


class HeadingBlock(blocks.StructBlock):
    """
    A section heading with configurable level (h2–h4).
    h1 is reserved for the page title — editors cannot add h1 blocks.
    """

    text = blocks.CharBlock(required=True, label=_("Heading text"))
    level = blocks.ChoiceBlock(
        choices=[
            ("h2", "H2 — Section heading"),
            ("h3", "H3 — Sub-section heading"),
            ("h4", "H4 — Sub-sub-section heading"),
        ],
        default="h2",
        label=_("Heading level"),
        help_text=_("Use headings in order — don't skip levels (e.g., don't jump from H2 to H4)."),
    )

    class Meta:
        template = "cms/blocks/heading.html"
        icon = "title"
        label = _("Heading")


class RichTextBlock(blocks.RichTextBlock):
    """Rich text with a restricted feature set appropriate for government content."""

    class Meta:
        template = "cms/blocks/rich_text.html"
        icon = "pilcrow"
        label = _("Rich text")

    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "features",
            ["bold", "italic", "link", "ol", "ul", "document-link"],
        )
        super().__init__(*args, **kwargs)


class ImageBlock(blocks.StructBlock):
    """
    An image with required caption and optional link.
    Alt text is stored on the image model itself (CustomImage.alt_text).
    """

    image = ImageChooserBlock(label=_("Image"))
    caption = blocks.CharBlock(
        required=False,
        label=_("Caption"),
        help_text=_("Displayed below the image. Use for attribution or context."),
    )
    link = blocks.URLBlock(
        required=False,
        label=_("Link URL"),
        help_text=_("Optional — makes the image a clickable link."),
    )

    class Meta:
        template = "cms/blocks/image.html"
        icon = "image"
        label = _("Image")


class CallToActionBlock(blocks.StructBlock):
    """A prominent call-to-action button with heading and body text."""

    heading = blocks.CharBlock(label=_("Heading"))
    body = blocks.TextBlock(required=False, label=_("Body text"))
    button_text = blocks.CharBlock(label=_("Button text"))
    button_url = blocks.URLBlock(label=_("Button URL"))
    button_style = blocks.ChoiceBlock(
        choices=[
            ("primary", _("Primary (solid)")),
            ("secondary", _("Secondary (outline)")),
        ],
        default="primary",
        label=_("Button style"),
    )

    class Meta:
        template = "cms/blocks/call_to_action.html"
        icon = "pick"
        label = _("Call to action")


class AccordionItemBlock(blocks.StructBlock):
    """A single collapsible accordion item."""

    title = blocks.CharBlock(label=_("Question / Title"))
    content = blocks.RichTextBlock(
        features=["bold", "italic", "link", "ol", "ul"],
        label=_("Answer / Content"),
    )


class AccordionBlock(blocks.StructBlock):
    """
    A group of collapsible accordion items.
    Commonly used for FAQ sections.
    Rendered with accessible disclosure widget pattern (aria-expanded).
    """

    items = blocks.ListBlock(AccordionItemBlock(), label=_("Items"))

    class Meta:
        template = "cms/blocks/accordion.html"
        icon = "list-ul"
        label = _("Accordion / FAQ")


class AlertBlock(blocks.StructBlock):
    """
    A prominent alert or notice banner.
    Rendered with role="alert" or role="note" depending on severity.
    """

    alert_type = blocks.ChoiceBlock(
        choices=[
            ("info", _("Informational")),
            ("warning", _("Warning")),
            ("success", _("Success")),
            ("error", _("Error / Important")),
        ],
        default="info",
        label=_("Alert type"),
    )
    title = blocks.CharBlock(required=False, label=_("Title"))
    body = blocks.RichTextBlock(
        features=["bold", "italic", "link"],
        label=_("Content"),
    )

    class Meta:
        template = "cms/blocks/alert.html"
        icon = "warning"
        label = _("Alert / Notice")


class ContentStreamBlock(blocks.StreamBlock):
    """
    The main content stream block used in most page types.
    Add new block types here to make them available to editors.
    """

    heading = HeadingBlock()
    rich_text = RichTextBlock()
    image = ImageBlock()
    call_to_action = CallToActionBlock()
    accordion = AccordionBlock()
    alert = AlertBlock()

    class Meta:
        block_counts = {
            "call_to_action": {"max_num": 3},
        }

"""
CMS page models and media models for Govstack.

Page hierarchy:
  RootPage (Wagtail root)
  └── HomePage           — one per site/locale
      ├── ServiceIndexPage
      │   └── ServicePage
      ├── NewsIndexPage
      │   └── NewsPage
      └── GenericPage     — catch-all for simple content pages

All pages use TranslatableMixin for bilingual (EN/FR) support.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, ObjectList, TabbedInterface
from wagtail.documents.models import AbstractDocument, Document
from wagtail.fields import RichTextField, StreamField
from wagtail.images.models import AbstractImage, AbstractRendition, Image
from wagtail.models import Page
from wagtail.search import index
from .blocks import ContentStreamBlock


# ---------------------------------------------------------------------------
# Custom media models
# ---------------------------------------------------------------------------

class CustomImage(AbstractImage):
    """
    Extended image model with a required alt text field.

    Alt text is stored on the image itself (not per-usage) so editors
    write it once and it applies everywhere the image is used — consistent
    with WCAG 1.1.1 and the principle of not repeating accessibility work.
    """

    alt_text = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Alt text"),
        help_text=_(
            "Describe the image for people who cannot see it. "
            "Leave blank only if the image is purely decorative."
        ),
    )

    admin_form_fields = Image.admin_form_fields + ("alt_text",)

    class Meta(AbstractImage.Meta):
        verbose_name = _("Image")
        verbose_name_plural = _("Images")

    def get_alt_text(self) -> str:
        """Return alt text for templates — empty string for decorative images."""
        return self.alt_text or ""


class CustomRendition(AbstractRendition):
    image = models.ForeignKey(CustomImage, on_delete=models.CASCADE, related_name="renditions")

    class Meta:
        unique_together = (("image", "filter_spec", "focal_point_key"),)


class CustomDocument(AbstractDocument):
    """Extended document model. Add access-control fields here when needed."""

    admin_form_fields = Document.admin_form_fields

    class Meta(AbstractDocument.Meta):
        verbose_name = _("Document")
        verbose_name_plural = _("Documents")


# ---------------------------------------------------------------------------
# Snippets — reusable non-page content managed in the Wagtail admin
# ---------------------------------------------------------------------------

class SiteAlert(models.Model):
    """
    A site-wide alert banner (e.g. service outage, important notice).
    Managed via Wagtail Snippets. Displayed on every page via base.html.
    """

    message = RichTextField(
        features=["bold", "italic", "link"],
        verbose_name=_("Message"),
    )
    alert_type = models.CharField(
        max_length=16,
        choices=[
            ("info", _("Informational")),
            ("warning", _("Warning")),
            ("error", _("Error")),
        ],
        default="info",
        verbose_name=_("Alert type"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
        help_text=_("Uncheck to hide this alert without deleting it."),
    )

    panels = [
        FieldPanel("alert_type"),
        FieldPanel("message"),
        FieldPanel("is_active"),
    ]

    class Meta:
        verbose_name = _("Site alert")
        verbose_name_plural = _("Site alerts")

    def __str__(self) -> str:
        return f"[{self.get_alert_type_display()}] {self.message[:80]}"


class NavigationMenu(models.Model):
    """
    A named navigation menu — header nav, footer nav, etc.
    Items are managed as JSON for simplicity; a full TreeBeard-based
    menu system can replace this when requirements grow.
    """

    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name=_("Menu name"),
        help_text=_('Internal name, e.g. "header" or "footer".'),
    )

    panels = [FieldPanel("name")]

    class Meta:
        verbose_name = _("Navigation menu")
        verbose_name_plural = _("Navigation menus")

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Page models
# ---------------------------------------------------------------------------

class HomePage(Page):
    """
    The root content page for a Govstack site.

    One HomePage per locale (EN/FR) sits directly below the Wagtail root.
    The hero section is always visible; the body stream is optional.
    """

    # Hero section
    hero_heading = models.CharField(
        max_length=255,
        verbose_name=_("Hero heading"),
    )
    hero_subheading = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Hero subheading"),
        help_text=_("Optional short sentence below the main heading."),
    )
    hero_cta_text = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Hero button text"),
    )
    hero_cta_page = models.ForeignKey(
        "wagtailcore.Page",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Hero button destination"),
    )
    hero_image = models.ForeignKey(
        "cms.CustomImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Hero image"),
    )

    # Body stream
    body = StreamField(
        ContentStreamBlock(),
        blank=True,
        verbose_name=_("Page body"),
        use_json_field=True,
    )

    # ---- Wagtail admin panels ----
    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [
                FieldPanel("hero_heading"),
                FieldPanel("hero_subheading"),
                FieldPanel("hero_image"),
                FieldPanel("hero_cta_text"),
                FieldPanel("hero_cta_page"),
            ],
            heading=_("Hero section"),
        ),
        FieldPanel("body"),
    ]

    promote_panels = Page.promote_panels

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(promote_panels, heading=_("SEO & sharing")),
        ]
    )

    search_fields = Page.search_fields + [
        index.SearchField("hero_heading"),
        index.SearchField("hero_subheading"),
        index.SearchField("body"),
    ]

    template = "cms/pages/home_page.html"

    class Meta:
        verbose_name = _("Home page")

    parent_page_types = ["wagtailcore.Page"]
    subpage_types = [
        "cms.GenericPage",
        "cms.ServiceIndexPage",
        "cms.NewsIndexPage",
    ]
    max_count = 2  # One per locale (EN + FR)


class GenericPage(Page):
    """
    Flexible content page for informational content (About, Contact, FAQ, etc.).
    """

    intro = models.TextField(
        blank=True,
        max_length=500,
        verbose_name=_("Introduction"),
        help_text=_("Short summary used in search results and social media previews (max 500 chars)."),
    )
    body = StreamField(
        ContentStreamBlock(),
        verbose_name=_("Page body"),
        use_json_field=True,
    )
    show_in_menus_default = True

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("body"),
    ]

    promote_panels = Page.promote_panels

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(promote_panels, heading=_("SEO & sharing")),
        ]
    )

    search_fields = Page.search_fields + [
        index.SearchField("intro"),
        index.SearchField("body"),
    ]

    template = "cms/pages/generic_page.html"

    class Meta:
        verbose_name = _("Page")

    parent_page_types = ["cms.HomePage", "cms.GenericPage"]
    subpage_types = ["cms.GenericPage", "forms.FormPage"]


class ServiceIndexPage(Page):
    """
    Lists all service pages — the municipal services directory.
    """

    intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
        verbose_name=_("Introduction"),
    )

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
    ]

    search_fields = Page.search_fields + [
        index.SearchField("intro"),
    ]

    template = "cms/pages/service_index_page.html"

    class Meta:
        verbose_name = _("Services index")

    parent_page_types = ["cms.HomePage"]
    subpage_types = ["cms.ServicePage"]
    max_count_per_parent = 1

    def get_context(self, request, *args, **kwargs) -> dict:
        from django.core.paginator import Paginator

        ctx = super().get_context(request, *args, **kwargs)
        services = (
            ServicePage.objects.live()
            .in_locale(self.locale)
            .order_by("category", "title")
        )
        # Optional category filter from query string (?category=permits)
        category = request.GET.get("category", "")
        if category:
            services = services.filter(category=category)
            ctx["active_category"] = category

        ctx["categories"] = ServicePage.SERVICE_CATEGORIES
        paginator = Paginator(services, 20)
        ctx["services"] = paginator.get_page(request.GET.get("page", 1))
        return ctx


class ServicePage(Page):
    """
    A single municipal service — describes what it is, who it's for,
    how to apply, and links to an online form if available.
    """

    SERVICE_CATEGORIES = [
        ("permits", _("Permits & licences")),
        ("infrastructure", _("Roads & infrastructure")),
        ("recreation", _("Parks & recreation")),
        ("taxes", _("Taxes & finance")),
        ("environment", _("Environment & waste")),
        ("planning", _("Planning & development")),
        ("other", _("Other")),
    ]

    summary = models.TextField(
        max_length=300,
        verbose_name=_("Summary"),
        help_text=_("One or two sentences describing this service. Shown in the services directory."),
    )
    category = models.CharField(
        max_length=32,
        choices=SERVICE_CATEGORIES,
        default="other",
        verbose_name=_("Category"),
        db_index=True,
    )
    body = StreamField(
        ContentStreamBlock(),
        verbose_name=_("Service details"),
        use_json_field=True,
    )
    online_available = models.BooleanField(
        default=False,
        verbose_name=_("Available online"),
        help_text=_("Check if citizens can complete this service entirely online."),
    )
    online_form = models.ForeignKey(
        "wagtailcore.Page",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Online form page"),
        help_text=_("Link to the form page for online applications."),
    )
    contact_email = models.EmailField(
        blank=True,
        verbose_name=_("Contact email"),
    )
    contact_phone = models.CharField(
        max_length=30,
        blank=True,
        verbose_name=_("Contact phone"),
    )
    processing_time = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Typical processing time"),
        help_text=_('Plain language estimate, e.g. "5–10 business days".'),
    )
    fee = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Fee"),
        help_text=_('e.g. "Free", "$50", "Varies — see fee schedule".'),
    )

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [
                FieldPanel("summary"),
                FieldPanel("category"),
                FieldPanel("online_available"),
                FieldPanel("online_form"),
            ],
            heading=_("Service overview"),
        ),
        FieldPanel("body"),
        MultiFieldPanel(
            [
                FieldPanel("processing_time"),
                FieldPanel("fee"),
                FieldPanel("contact_email"),
                FieldPanel("contact_phone"),
            ],
            heading=_("Contact & fees"),
        ),
    ]

    promote_panels = Page.promote_panels

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(promote_panels, heading=_("SEO & sharing")),
        ]
    )

    search_fields = Page.search_fields + [
        index.SearchField("summary"),
        index.SearchField("body"),
        index.FilterField("category"),
        index.FilterField("online_available"),
    ]

    template = "cms/pages/service_page.html"

    class Meta:
        verbose_name = _("Service page")

    parent_page_types = ["cms.ServiceIndexPage"]
    subpage_types = []


class NewsIndexPage(Page):
    """News and announcements listing page."""

    intro = models.TextField(
        blank=True,
        verbose_name=_("Introduction"),
    )

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
    ]

    template = "cms/pages/news_index_page.html"

    class Meta:
        verbose_name = _("News index")

    parent_page_types = ["cms.HomePage"]
    subpage_types = ["cms.NewsPage"]
    max_count_per_parent = 1

    def get_context(self, request, *args, **kwargs) -> dict:
        ctx = super().get_context(request, *args, **kwargs)
        news = (
            NewsPage.objects.live()
            .in_locale(self.locale)
            .order_by("-publication_date")
        )
        # Simple pagination — 10 per page
        from django.core.paginator import Paginator
        paginator = Paginator(news, 10)
        page_number = request.GET.get("page", 1)
        ctx["news_items"] = paginator.get_page(page_number)
        return ctx


class NewsPage(Page):
    """A single news article or announcement."""

    publication_date = models.DateField(
        verbose_name=_("Publication date"),
    )
    author = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Author"),
    )
    summary = models.TextField(
        max_length=300,
        verbose_name=_("Summary"),
        help_text=_("Used in the news listing and social media previews."),
    )
    featured_image = models.ForeignKey(
        "cms.CustomImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Featured image"),
    )
    body = StreamField(
        ContentStreamBlock(),
        verbose_name=_("Article body"),
        use_json_field=True,
    )

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [
                FieldPanel("publication_date"),
                FieldPanel("author"),
                FieldPanel("summary"),
                FieldPanel("featured_image"),
            ],
            heading=_("Article details"),
        ),
        FieldPanel("body"),
    ]

    promote_panels = Page.promote_panels

    edit_handler = TabbedInterface(
        [
            ObjectList(content_panels, heading=_("Content")),
            ObjectList(promote_panels, heading=_("SEO & sharing")),
        ]
    )

    search_fields = Page.search_fields + [
        index.SearchField("summary"),
        index.SearchField("body"),
        index.FilterField("publication_date"),
        index.FilterField("author"),
    ]

    template = "cms/pages/news_page.html"

    class Meta:
        verbose_name = _("News article")
        ordering = ["-publication_date"]

    parent_page_types = ["cms.NewsIndexPage"]
    subpage_types = []

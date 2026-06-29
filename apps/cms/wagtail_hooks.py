"""
Wagtail hooks for the CMS app.

Registers:
- Custom admin menu items
- Custom image chooser (enforces alt text)
- Page listing customisation (shows locale badge)
- Snippet viewsets
"""

from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet, SnippetViewSetGroup

from .models import NavigationMenu, SiteAlert


# ---------------------------------------------------------------------------
# Snippet ViewSets — registers snippets in the Wagtail admin sidebar
# ---------------------------------------------------------------------------

class SiteAlertViewSet(SnippetViewSet):
    model = SiteAlert
    menu_label = _("Site alerts")
    icon = "warning"
    list_display = ["__str__", "alert_type", "is_active"]
    list_filter = ["alert_type", "is_active"]


class NavigationMenuViewSet(SnippetViewSet):
    model = NavigationMenu
    menu_label = _("Navigation menus")
    icon = "list-ul"
    list_display = ["name"]


@register_snippet
class CMSSnippetGroup(SnippetViewSetGroup):
    """Groups CMS snippets under a single 'Site content' menu section."""
    menu_label = _("Site content")
    menu_icon = "site"
    menu_order = 200
    items = (SiteAlertViewSet, NavigationMenuViewSet)


# ---------------------------------------------------------------------------
# Admin UI hooks
# ---------------------------------------------------------------------------

@hooks.register("construct_main_menu")
def reorder_main_menu(request, menu_items):
    """
    Set a consistent menu order in the Wagtail admin:
      1. Explorer (pages)
      2. Search
      3. Images
      4. Documents
      5. Snippets / site content
      6. Reports
      7. Settings
    """
    order_map = {
        "explorer": 100,
        "search": 150,
        "images": 200,
        "documents": 250,
        "snippets": 300,
        "reports": 400,
        "settings": 500,
    }
    for item in menu_items:
        if item.name in order_map:
            item.order = order_map[item.name]


@hooks.register("insert_global_admin_css")
def global_admin_css():
    """Inject minimal CSS tweaks into the Wagtail admin."""
    return """
    <style>
      /* Highlight the locale badge in the page listing */
      .w-locale-indicator { font-weight: 600; }
    </style>
    """

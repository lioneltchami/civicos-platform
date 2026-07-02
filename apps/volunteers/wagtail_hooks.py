"""
Wagtail admin hooks for the Volunteer Management BB.

Registers a "Volunteers" item in the Wagtail sidebar that links coordinators
directly to the coordinator dashboard view.

This file is automatically discovered by Wagtail when it is present in an
installed app's package directory — no import in apps.py is required
(Wagtail imports all wagtail_hooks.py modules at startup via AppConfig.ready()).
"""
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem


@hooks.register("register_admin_menu_item")
def register_volunteers_menu_item():
    """
    Add a "Volunteers" entry to the Wagtail admin sidebar.

    Links to the coordinator dashboard URL (Django URL namespace: volunteers).
    Order 500 places it below CMS content items (order ~200–300) and above
    settings (order 600+).

    Icon "group" is a standard Wagtail icon representing multiple users.
    """
    return MenuItem(
        label=_("Volunteers"),
        url="/volunteers/coordinator/",
        icon_name="group",
        order=500,
    )

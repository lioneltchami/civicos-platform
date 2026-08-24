"""
Volunteer Management BB — Custom template filters and tags.
"""

from django import template

register = template.Library()


@register.filter(name="get_item")
def get_item(dictionary, key):  # noqa: ANN001, ANN201
    """
    Retrieve a value from a dict by key in a Django template.

    Usage:
        {{ my_dict|get_item:some_variable }}

    Returns empty string if the key is not found or the value is falsy,
    so it's safe to use in attribute strings.
    """
    if not isinstance(dictionary, dict):
        return ""
    return dictionary.get(key, "")

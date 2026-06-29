"""
Custom template filters for the Forms building block.
"""
from django import template

register = template.Library()


@register.filter(name="get_item")
def get_item(dictionary, key):
    """
    Get a value from a dict by a dynamic key.

    Django templates cannot do {{ dict[variable] }} — this filter enables:
        {{ submission.form_data|get_item:field.clean_name }}

    Returns empty string if the dict is None or the key is not present.
    """
    if not isinstance(dictionary, dict):
        return ""
    return dictionary.get(key, "")


@register.filter(name="get_form_field")
def get_form_field(form, field_name):
    """
    Retrieve a BoundField from a Django form by name, including names that
    start with underscores (which Django's template engine blocks via normal
    attribute access).

    Usage:
        {% with consent_field=form|get_form_field:"_consent" %}

    Returns None if the field does not exist on the form.
    """
    try:
        return form[field_name]
    except (KeyError, TypeError):
        return None

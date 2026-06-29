"""
Custom template filters for the Forms building block.
"""
from django import template
from django.utils.safestring import mark_safe

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


@register.filter(name="render_field_with_error_attrs")
def render_field_with_error_attrs(field, describedby_id=""):
    """
    Render a BoundField's widget with WCAG-required error attributes injected.

    Always adds:
      - aria-invalid="true"   (WCAG 3.3.1 — Error Identification)

    When describedby_id is provided or can be auto-derived from field errors:
      - aria-describedby="<id>"  (WCAG 1.3.1 — Info and Relationships)

    When the field is required:
      - aria-required="true"  (WCAG 4.1.2 — Name, Role, Value)

    The describedby_id defaults to ``{field.id_for_label}-error`` when the
    field has errors and no explicit id is passed — this matches the id
    pattern used on the error <span> elements in form_page.html.

    Works correctly for all widget types, including CheckboxSelectMultiple
    (Django propagates extra attrs to each individual <input> it renders).

    Usage:
        {{ field|render_field_with_error_attrs }}
        {{ field|render_field_with_error_attrs:"my-custom-error-id" }}
    """
    attrs = {"aria-invalid": "true"}

    # Auto-derive describedby_id from the error span's id when not explicitly
    # provided — keeps the template call site simple.
    if not describedby_id and field.errors:
        describedby_id = f"{field.id_for_label}-error"

    if describedby_id:
        attrs["aria-describedby"] = describedby_id

    # aria-required belongs on the interactive element, not on a wrapper div.
    # WCAG 4.1.2: required state must be programmatically determinable.
    if field.field.required:
        attrs["aria-required"] = "true"

    return mark_safe(field.as_widget(attrs=attrs))


@register.filter(name="render_field_with_required_attrs")
def render_field_with_required_attrs(field):
    """
    Render a BoundField's widget with aria-required="true" and, when the field
    also has hint text, aria-describedby pointing at the hint span.

    Use this for required fields that have no validation errors yet — the
    companion to render_field_with_error_attrs.

    Injects:
      - aria-required="true"  when field.field.required
      - aria-describedby="{id}-hint"  when field.help_text is present

    Usage:
        {{ field|render_field_with_required_attrs }}
    """
    attrs = {}
    if field.field.required:
        attrs["aria-required"] = "true"
    if field.help_text:
        attrs["aria-describedby"] = f"{field.id_for_label}-hint"
    if not attrs:
        return mark_safe(field.as_widget())
    return mark_safe(field.as_widget(attrs=attrs))


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

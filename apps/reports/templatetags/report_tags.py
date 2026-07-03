"""
Reports BB — custom template filters.

Provides locale-aware number formatting for CRA financial figures that
works correctly in both en-CA and fr-CA.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.formats import number_format

register = template.Library()


@register.filter(is_safe=True)
def cad_money(value, decimal_pos: int = 2) -> str:
    """
    Format a Decimal (or numeric value) as a locale-aware monetary amount
    with exactly ``decimal_pos`` decimal places and locale-appropriate
    thousands grouping.

    Replaces the ``|floatformat:2|intcomma`` pipeline.  ``intcomma`` from
    ``django.contrib.humanize`` is NOT locale-aware — it always uses a
    comma as the thousands separator.  This filter delegates to Django's
    ``number_format()`` which consults the active language:

      - en-CA: ``1,234.56``
      - fr-CA: ``1 234,56`` (thin non-breaking space + comma decimal)

    Usage in templates::

        {% load report_tags %}
        {% blocktrans with amount=value|cad_money %}${{ amount }}{% endblocktrans %}
        {# or simply: #}
        {{ value|cad_money }}
        {# with non-default precision: #}
        {{ value|cad_money:1 }}

    Args:
        value:       A ``Decimal``, ``float``, ``int``, or numeric string.
        decimal_pos: Number of decimal places (default 2).

    Returns:
        A locale-formatted string, e.g. ``"1,234.56"`` (en) or
        ``"1 234,56"`` (fr).  Returns ``"0.00"`` on error.
    """
    try:
        decimal_pos = int(decimal_pos)
    except (TypeError, ValueError):
        decimal_pos = 2

    if value is None:
        return f"0.{'0' * decimal_pos}"

    try:
        # Ensure we have a Decimal for precise formatting.
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return number_format(
            value,
            decimal_pos=decimal_pos,
            use_l10n=True,
            use_grouping=True,
        )
    except (InvalidOperation, TypeError, ValueError):
        return f"0.{'0' * decimal_pos}"

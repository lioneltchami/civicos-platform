"""
Idempotent management command to seed Canadian provincial/territorial tax rates.

Usage:
    python manage.py seed_tax_rates
    python manage.py seed_tax_rates --verbosity=2

Data current as of 2026-01-01.
Sources: CRA, provincial revenue authorities.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.payments.models import TaxRate

# effective_date for all seeded rows
_EFFECTIVE_DATE = date(2026, 1, 1)

# (province, federal_rate, provincial_rate, combined_rate, tax_name_en, tax_name_fr)
TAX_DATA = [
    # Alberta — GST only
    ("AB", "0.0500", "0.0000", "0.0500", "GST", "TPS"),
    # British Columbia — GST + PST
    ("BC", "0.0500", "0.0700", "0.1200", "GST + PST", "TPS + TVP"),
    # Manitoba — GST + PST
    ("MB", "0.0500", "0.0700", "0.1200", "GST + PST", "TPS + TVP"),
    # New Brunswick — HST
    ("NB", "0.0000", "0.0000", "0.1500", "HST", "TVH"),
    # Newfoundland and Labrador — HST
    ("NL", "0.0000", "0.0000", "0.1500", "HST", "TVH"),
    # Nova Scotia — HST
    ("NS", "0.0000", "0.0000", "0.1500", "HST", "TVH"),
    # Northwest Territories — GST only
    ("NT", "0.0500", "0.0000", "0.0500", "GST", "TPS"),
    # Nunavut — GST only
    ("NU", "0.0500", "0.0000", "0.0500", "GST", "TPS"),
    # Ontario — HST
    ("ON", "0.0000", "0.0000", "0.1300", "HST", "TVH"),
    # Prince Edward Island — HST
    ("PE", "0.0000", "0.0000", "0.1500", "HST", "TVH"),
    # Quebec — GST + QST
    ("QC", "0.0500", "0.0975", "0.1475", "GST + QST", "TPS + TVQ"),
    # Saskatchewan — GST + PST
    ("SK", "0.0500", "0.0600", "0.1100", "GST + PST", "TPS + TVP"),
    # Yukon — GST only
    ("YT", "0.0500", "0.0000", "0.0500", "GST", "TPS"),
]


class Command(BaseCommand):
    help = "Seed Canadian provincial/territorial tax rates (idempotent)."

    def handle(self, *args, **options):
        verbosity = options.get("verbosity", 1)
        created_count = 0
        updated_count = 0

        for province, federal, provincial, combined, name_en, name_fr in TAX_DATA:
            defaults = {
                "federal_rate": Decimal(federal),
                "provincial_rate": Decimal(provincial),
                "combined_rate": Decimal(combined),
                "tax_name_en": name_en,
                "tax_name_fr": name_fr,
                "effective_date": _EFFECTIVE_DATE,
            }
            obj, created = TaxRate.objects.update_or_create(
                province=province,
                defaults=defaults,
            )
            if created:
                created_count += 1
                if verbosity >= 2:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Created: {province} — {name_en} ({combined})"
                        )
                    )
            else:
                updated_count += 1
                if verbosity >= 2:
                    self.stdout.write(
                        f"  Updated: {province} — {name_en} ({combined})"
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_tax_rates: {created_count} created, {updated_count} updated "
                f"({created_count + updated_count} provinces/territories total)."
            )
        )

"""
Idempotent management command to seed Canadian provincial/territorial tax rates.

Usage:
    python manage.py seed_tax_rates
    python manage.py seed_tax_rates --dry-run
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
# All rates use 5 decimal places to match TaxRate.decimal_places=5.
# HST provinces: federal 5% + provincial portion = combined HST.
#   ON:            5% + 8%     = 13%
#   NB/NL/NS/PE:   5% + 10%   = 15%
# QST: 9.975% (not 9.75%) per Revenue Québec.
RATES = [
    # Alberta — GST only
    ("AB", "0.05000", "0.00000", "0.05000", "GST", "TPS"),
    # British Columbia — GST + PST
    ("BC", "0.05000", "0.07000", "0.12000", "GST + PST", "TPS + TVP"),
    # Manitoba — GST + PST
    ("MB", "0.05000", "0.07000", "0.12000", "GST + PST", "TPS + TVP"),
    # New Brunswick — HST (federal 5% + provincial 10%)
    ("NB", "0.05000", "0.10000", "0.15000", "HST", "TVH"),
    # Newfoundland and Labrador — HST (federal 5% + provincial 10%)
    ("NL", "0.05000", "0.10000", "0.15000", "HST", "TVH"),
    # Nova Scotia — HST (federal 5% + provincial 10%)
    ("NS", "0.05000", "0.10000", "0.15000", "HST", "TVH"),
    # Northwest Territories — GST only
    ("NT", "0.05000", "0.00000", "0.05000", "GST", "TPS"),
    # Nunavut — GST only
    ("NU", "0.05000", "0.00000", "0.05000", "GST", "TPS"),
    # Ontario — HST (federal 5% + provincial 8%)
    ("ON", "0.05000", "0.08000", "0.13000", "HST", "TVH"),
    # Prince Edward Island — HST (federal 5% + provincial 10%)
    ("PE", "0.05000", "0.10000", "0.15000", "HST", "TVH"),
    # Quebec — GST + QST (QST = 9.975%, combined = 14.975%)
    ("QC", "0.05000", "0.09975", "0.14975", "GST + QST", "TPS + TVQ"),
    # Saskatchewan — GST + PST
    ("SK", "0.05000", "0.06000", "0.11000", "GST + PST", "TPS + TVP"),
    # Yukon — GST only
    ("YT", "0.05000", "0.00000", "0.05000", "GST", "TPS"),
]


class Command(BaseCommand):
    help = "Seed Canadian provincial/territorial tax rates (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be created/updated without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verbosity = options.get("verbosity", 1)
        prefix = "[DRY RUN] " if dry_run else ""

        # Data integrity check: federal + provincial must equal combined for every row.
        for prov, federal, provincial, combined, *_ in RATES:
            assert Decimal(federal) + Decimal(provincial) == Decimal(combined), (
                f"Rate data error for {prov}: {federal} + {provincial} != {combined}"
            )

        created_count = 0
        updated_count = 0

        for province, federal, provincial, combined, name_en, name_fr in RATES:
            if dry_run:
                # Determine what would happen without touching the DB.
                exists = TaxRate.objects.filter(
                    province=province,
                    effective_date=_EFFECTIVE_DATE,
                ).exists()
                action = "Update" if exists else "Create"
                self.stdout.write(
                    f"{prefix}{action}: {province} — {name_en} "
                    f"(federal={federal}, provincial={provincial}, combined={combined})"
                )
                if exists:
                    updated_count += 1
                else:
                    created_count += 1
                continue

            obj, created = TaxRate.objects.update_or_create(
                province=province,
                effective_date=_EFFECTIVE_DATE,
                defaults={
                    "federal_rate": Decimal(federal),
                    "provincial_rate": Decimal(provincial),
                    "combined_rate": Decimal(combined),
                    "tax_name_en": name_en,
                    "tax_name_fr": name_fr,
                },
            )
            if created:
                created_count += 1
                if verbosity >= 2:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  {prefix}Created: {province} — {name_en} ({combined})"
                        )
                    )
            else:
                updated_count += 1
                if verbosity >= 2:
                    self.stdout.write(
                        f"  {prefix}Updated: {province} — {name_en} ({combined})"
                    )

        summary = (
            f"{prefix}seed_tax_rates: {created_count} created, {updated_count} updated "
            f"({created_count + updated_count} provinces/territories total)."
        )
        if dry_run:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

"""
Management command to find OfficialDonationReceipt rows with placeholder donor addresses.

Run:
    python manage.py check_missing_donor_addresses [--tax-year 2025] [--csv]

Outputs a list of affected receipt serial numbers so ops can contact donors
to update their addresses. The receipts themselves cannot be corrected in place
(immutable fields) -- they must be cancelled and reissued after the donor updates
their profile at /accounts/profile/.

PIPEDA: This command outputs only receipt serial numbers, donor PKs, and donation
dates -- never email addresses or names.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.payments.models import OfficialDonationReceipt

PLACEHOLDER_PATTERNS = [
    "[Address required",
    "Address on file",
    "[Address on file",
]


class Command(BaseCommand):
    help = (
        "Find issued donation receipts with missing or placeholder donor addresses. "
        "These receipts may be non-compliant with CRA IT-110R3. "
        "Outputs: serial_number, donor_pk, donation_date -- no PII."
    )

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument(
            "--tax-year",
            type=int,
            default=None,
            help="Limit to a specific tax year (default: all years).",
        )
        parser.add_argument(
            "--csv",
            action="store_true",
            help="Output in CSV format for spreadsheet import.",
        )

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        tax_year = options["tax_year"]
        as_csv = options["csv"]

        qs = OfficialDonationReceipt.objects.filter(
            status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        ).select_related("donation__donor")

        if tax_year:
            qs = qs.filter(donation__created_at__year=tax_year)

        placeholder_filter = Q(donor_address_line1="")
        for pattern in PLACEHOLDER_PATTERNS:
            placeholder_filter |= Q(donor_address_line1__startswith=pattern)

        affected = qs.filter(placeholder_filter)
        count = affected.count()

        if count == 0:
            self.stdout.write(
                self.style.SUCCESS("No receipts with missing or placeholder addresses found.")
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"Found {count} receipt(s) with placeholder/missing donor addresses."
            )
        )
        self.stdout.write(
            "These may be non-compliant with CRA IT-110R3. "
            "Donors must update their profile, then receipts must be cancelled and reissued."
        )
        self.stdout.write("")

        if as_csv:
            self.stdout.write("serial_number,donor_pk,donation_date")
        else:
            self.stdout.write(
                "{:<25} {:<40} {}".format("Serial Number", "Donor PK", "Donation Date")
            )
            self.stdout.write("-" * 80)

        for receipt in affected.order_by("serial_number"):
            donor_pk = str(receipt.donation.donor_id)
            donation_date = str(receipt.donation.created_at.date())
            if as_csv:
                self.stdout.write(f"{receipt.serial_number},{donor_pk},{donation_date}")
            else:
                self.stdout.write(f"{receipt.serial_number:<25} {donor_pk:<40} {donation_date}")

        self.stdout.write("")
        self.stdout.write(
            f"Action required: {count} donor(s) must update their postal address at "
            "/accounts/profile/ then have their receipts cancelled and reissued."
        )

"""
Management command: verify_audit_chain

Walks the audit log chain and reports any integrity violations.

Usage
-----
    python manage.py verify_audit_chain
    python manage.py verify_audit_chain --start-id 5000
    python manage.py verify_audit_chain --start-id 5000 --end-id 9999
    python manage.py verify_audit_chain --batch-size 1000
    python manage.py verify_audit_chain --quiet   # exits 1 on violations, no output

Exit codes
----------
    0 — chain is intact
    1 — violations found (or unexpected error)

GovStack Compliance
-------------------
This command satisfies the GovStack Consent BB v1.3.0 Section 6.3 requirement:

    "All consent logs shall be tamperproof, Audit logging (REQUIRED)"
    "It shall be possible to filter and sort all objects' revision histories (REQUIRED)"

Run it periodically (e.g. via a Celery beat task) and alert on non-zero exit.
"""

import sys

from django.core.management.base import BaseCommand, CommandError

from apps.audit.models import AuditLogEntry


class Command(BaseCommand):
    help = (
        "Verify the integrity of the audit log hash chain. "
        "Reports any entries whose hashes do not match or whose "
        "prev_hash linkage is broken."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--start-id",
            type=int,
            default=None,
            metavar="ID",
            help="Only verify entries with id >= START_ID.",
        )
        parser.add_argument(
            "--end-id",
            type=int,
            default=None,
            metavar="ID",
            help="Only verify entries with id <= END_ID.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            metavar="N",
            help="Number of entries fetched per database query (default: 500).",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress all output; use exit code only.",
        )

    def handle(self, *args, **options) -> None:
        start_id = options["start_id"]
        end_id = options["end_id"]
        batch_size = options["batch_size"]
        quiet = options["quiet"]

        if not quiet:
            scope_parts = []
            if start_id:
                scope_parts.append(f"from id={start_id}")
            if end_id:
                scope_parts.append(f"to id={end_id}")
            scope = f" ({', '.join(scope_parts)})" if scope_parts else ""
            self.stdout.write(f"Verifying audit chain{scope}…")

        result = AuditLogEntry.verify_chain(
            start_id=start_id,
            end_id=end_id,
            batch_size=batch_size,
        )

        if not quiet:
            self.stdout.write(
                f"  Entries checked : {result.entries_checked:,}"
            )
            self.stdout.write(
                f"  Violations found: {len(result.violations):,}"
            )

        if result.ok:
            if not quiet:
                self.stdout.write(
                    self.style.SUCCESS("✓ Audit chain is intact.")
                )
            return

        # Violations — print details and exit non-zero.
        if not quiet:
            self.stderr.write(
                self.style.ERROR(
                    f"\n✗ Chain integrity violations detected "
                    f"({len(result.violations)} total):\n"
                )
            )
            for v in result.violations:
                self.stderr.write(
                    self.style.ERROR(
                        f"  entry_id={v['entry_id']}  {v['reason']}"
                    )
                )

        sys.exit(1)

"""
Management command: seed_govstack_vouchers

Seeds GovStack Payments BB harness test vouchers into the database.

Purpose
-------
The GovStack Voucher harness feature files (voucher_preactivation,
voucher_activation, voucher_redemption, voucherstatuscheck, cancellation)
chain test scenarios that reference specific pre-existing serial numbers.
Those serials must already exist in STATUS_PREACTIVATED state before the
harness runner fires its first request.  This command seeds them
idempotently using get_or_create so repeated runs are safe.

Why a management command, not a data migration?
-----------------------------------------------
Migration 0017 in this project is ``0017_tighten_payee_functional_id_validator``
— a schema migration.  Inserting test fixtures via a data migration would
permanently pollute production databases and cannot be reversed cleanly.
A management command is the correct pattern for environment-specific seed
data (see also: ``setup_periodic_tasks.py``, ``seed_tax_rates.py``).

Serial number range conflict
----------------------------
``_generate_voucher_serial()`` produces 6-digit integers (100,000–999,999).
The harness references 4-digit serials (5550–6004) and 5-digit serials
(60000–60001), which fall *outside* that range and will NEVER be produced
by the generator.  This command bypasses the generator entirely and inserts
the exact string values required by the harness.

Security invariants
-------------------
- ``payee_functional_id`` is intentionally OMITTED from all seed rows.
  Seed vouchers carry no PII and no financial address.
- ``voucher_secret`` is intentionally OMITTED.  The field is blank=True;
  seed vouchers start with an empty (encrypted) secret.
- No financial_address is stored here.
- Serial numbers 5550–60001 are outside the auto-generation range, so they
  cannot collide with production vouchers on a properly configured deployment.

Usage
-----
::

    # Seed all 14 harness vouchers (idempotent):
    python manage.py seed_govstack_vouchers

    # Force-recreate all seed rows (e.g. after a harness run consumed them):
    python manage.py seed_govstack_vouchers --reset

    # Silent mode (CI pipelines):
    python manage.py seed_govstack_vouchers --verbosity 0

    # Verbose (shows per-row create/skip details):
    python manage.py seed_govstack_vouchers --verbosity 2
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("apps.payments.management.seed_govstack_vouchers")


# ---------------------------------------------------------------------------
# Seed data — 14 harness-required vouchers
# ---------------------------------------------------------------------------
# Tuple format: (serial_number, group_code, amount_str, currency_iso4217)
#
# Serial numbers use 4-digit and 5-digit values that are permanently outside
# the auto-generation range (100,000–999,999), ensuring they cannot appear
# in production by accident.  group_code values match the GovStack harness
# fixture expectations exactly (case-sensitive).
#
# amount_str uses string form to ensure exact Decimal conversion without
# floating-point rounding — never pass a float literal to DecimalField.

_SEED_VOUCHERS: list[tuple[str, str, str, str]] = [
    # ── Primary FOOD range (serials 5550–5555) ───────────────────────────────
    ("5550",  "FOOD",      "100.00", "CAD"),
    ("5551",  "FOOD",      "100.00", "CAD"),
    ("5552",  "FOOD",      "200.00", "CAD"),
    ("5553",  "FOOD",      "150.00", "CAD"),
    ("5554",  "FOOD",      "100.00", "CAD"),
    ("5555",  "FOOD",      "100.00", "CAD"),
    # ── HEALTH range (serials 5556–5560) ────────────────────────────────────
    ("5556",  "HEALTH",    "75.00",  "CAD"),
    ("5557",  "HEALTH",    "75.00",  "CAD"),
    ("5558",  "HEALTH",    "75.00",  "CAD"),
    ("5559",  "HEALTH",    "75.00",  "CAD"),
    ("5560",  "HEALTH",    "75.00",  "CAD"),
    # ── Alternate-group serial ───────────────────────────────────────────────
    ("6004",  "HEALTH",    "200.00", "CAD"),
    # ── 5-digit TRANSPORT range ──────────────────────────────────────────────
    ("60000", "TRANSPORT", "50.00",  "CAD"),
    ("60001", "TRANSPORT", "50.00",  "CAD"),
]

# Tag used on all rows created by this command.  Allows targeted reset and
# easy identification in the Django admin / support queries.
_HARNESS_ISSUING_BB: str = "GS-HARNESS"

# Expiry duration for freshly created seed vouchers.
_SEED_EXPIRY_DAYS: int = 365


class Command(BaseCommand):
    help = (
        "Seed GovStack Payments BB harness test vouchers (idempotent). "
        "Run immediately before submitting to the GovStack test harness at "
        "testing.govstack.global.  Use --reset to force-recreate all seed "
        "rows — e.g. after a harness run that consumed or cancelled them."
    )

    # ------------------------------------------------------------------
    # Argument parsing
    # ------------------------------------------------------------------

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            default=False,
            help=(
                "Delete ALL vouchers whose serial_number is in the seed list, "
                "then recreate them fresh in STATUS_PREACTIVATED. "
                "Use only in CI / test environments — NEVER in production."
            ),
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        # Lazy imports — avoids model loading before Django app registry is ready.
        from apps.payments.govstack_models import GovStackRegisteredBB, GovStackVoucher  # noqa: PLC0415

        verbosity: int = options["verbosity"]
        do_reset: bool = options["reset"]

        seed_serials: list[str] = [row[0] for row in _SEED_VOUCHERS]
        expiry = timezone.now() + timedelta(days=_SEED_EXPIRY_DAYS)

        # All DB writes (delete + get_or_create loop) are wrapped in a single
        # atomic transaction so that --reset can never leave the seed set in a
        # partially-deleted state if the command crashes mid-loop.
        with transaction.atomic():

            # ── Optional reset ────────────────────────────────────────────────
            if do_reset:
                # Filter by serial_number only (not issuing_bb) so that any row
                # occupying a seed serial — regardless of how it was created — is
                # cleared.  Since these serials are outside the auto-generation
                # range (100,000–999,999), this cannot accidentally delete
                # production vouchers.
                deleted_count, _ = GovStackVoucher.objects.filter(
                    serial_number__in=seed_serials,
                ).delete()

                if verbosity >= 1:
                    self.stdout.write(
                        self.style.WARNING(
                            f"--reset: deleted {deleted_count} voucher(s) with seed serials."
                        )
                    )

                logger.info(
                    "seed_govstack_vouchers.reset deleted_count=%d",
                    deleted_count,
                )

            # ── Idempotent upsert loop ────────────────────────────────────────
            created_count = 0
            skipped_count = 0

            for serial, group_code, amount_str, currency in _SEED_VOUCHERS:
                _obj, was_created = GovStackVoucher.objects.get_or_create(
                    serial_number=serial,
                    defaults={
                        "amount": Decimal(amount_str),
                        "currency": currency,
                        "group_code": group_code,
                        # Explicit status even though it is the model default —
                        # avoids silent breakage if the default ever changes.
                        "status": GovStackVoucher.STATUS_PREACTIVATED,
                        "issuing_bb": _HARNESS_ISSUING_BB,
                        "expiry_date": expiry,
                        # Security: payee_functional_id intentionally omitted (no PII).
                        # voucher_secret intentionally omitted (blank=True on the field;
                        # an encrypted empty string is stored).
                        # batch_id, callback_url, registering_institution_id all
                        # default to "" (blank=True) — no need to set them here.
                    },
                )

                if was_created:
                    created_count += 1
                    if verbosity >= 2:
                        self.stdout.write(
                            f"  Created  serial={serial!r:>6}  group={group_code!r}  "
                            f"amount={amount_str}  currency={currency}"
                        )
                else:
                    skipped_count += 1
                    if verbosity >= 2:
                        self.stdout.write(
                            f"  Skipped  serial={serial!r:>6}  (already exists)"
                        )

            # ── Seed GovStackRegisteredBB harness row ─────────────────────────
            # Create (or ensure existence of) the harness BB row so that when
            # GOVSTACK_REQUIRE_REGISTERED_BB=True is enabled, the harness
            # institution ID "GS-HARNESS" passes IsTrustedSourceBB.has_permission().
            # This is idempotent: if the row already exists it is left unchanged.
            _bb_obj, _bb_created = GovStackRegisteredBB.objects.get_or_create(
                bb_id=_HARNESS_ISSUING_BB,
                defaults={
                    "description": (
                        "GovStack test harness institution. "
                        "Created automatically by seed_govstack_vouchers."
                    ),
                    "is_active": True,
                    # admin: harness BB must be able to exercise every Scheduler
                    # actor role tier (resource/organizer/admin) during
                    # certification testing.
                    "role": "admin",
                },
            )

            if verbosity >= 2:
                action = "Created" if _bb_created else "Skipped"
                self.stdout.write(
                    f"  {action}  GovStackRegisteredBB bb_id={_HARNESS_ISSUING_BB!r}"
                )

            logger.info(
                "seed_govstack_vouchers.registered_bb bb_id=%r created=%s",
                _HARNESS_ISSUING_BB,
                _bb_created,
            )

            # ── Summary ───────────────────────────────────────────────────────
            total = len(_SEED_VOUCHERS)
            summary = (
                f"seed_govstack_vouchers complete — "
                f"{created_count} created, {skipped_count} already existed "
                f"(seed set size: {total}). "
                f"GovStackRegisteredBB({_HARNESS_ISSUING_BB!r}) "
                f"{'created' if _bb_created else 'already existed'}."
            )

            if verbosity >= 1:
                self.stdout.write(self.style.SUCCESS(summary))

            logger.info(
                "seed_govstack_vouchers.done created=%d skipped=%d total=%d",
                created_count,
                skipped_count,
                total,
            )

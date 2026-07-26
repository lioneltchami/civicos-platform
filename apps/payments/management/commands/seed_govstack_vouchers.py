"""
Management command: seed_govstack_vouchers

Seeds GovStack Payments BB harness test vouchers into the database, plus the
GovStackRegisteredBB allowlist rows those flows need when the production-only
allowlist flags are enabled (see _SEED_REGISTERED_BBS below, which also
documents which harness Gov_Stack_BB fixture values deliberately CANNOT be
seeded and why).

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

# ---------------------------------------------------------------------------
# GovStackRegisteredBB rows seeded by this command (P2)
# ---------------------------------------------------------------------------
#
# These matter ONLY when GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True (voucher
# Gov_Stack_BB body field, see govstack_services._is_unregistered_gov_stack_bb)
# or GOVSTACK_REQUIRE_REGISTERED_BB=True (G2P X-Registering-Institution-ID
# header, see govstack_auth.IsTrustedSourceBB).  Both flags default to True only
# in config/settings/production.py and are absent (→ False) everywhere else, so
# `manage.py test` and harness runs never consult this table at all.
#
# ⚠ The harness's own POSITIVE Gov_Stack_BB fixture values CANNOT be seeded here
#   as GovStackRegisteredBB.bb_id is currently defined, and are therefore
#   deliberately omitted rather than forced through:
#
#     "Gov_Stack_BB"          — preactivation's positive fixture value (the
#                               harness really does send the field NAME as the
#                               value).  Rejected by _BB_ID_VALIDATOR
#                               (^[a-zA-Z0-9\-]{1,20}$) because of the
#                               underscores.
#     "bb-digital-registries" — activation / redemption / cancellation's
#                               positive fixture value.  21 characters, so it
#                               exceeds bb_id's max_length=20 AND fails the
#                               same validator's {1,20} bound.
#
#   Storing either would require a model change (widening bb_id.max_length and
#   relaxing _BB_ID_VALIDATOR) plus a migration — out of scope for P2, and not
#   worth weakening a security validator for.  A fabricated truncation such as
#   "bb-digital-reg" is deliberately NOT seeded either: no caller anywhere ever
#   sends that string, so it would be dead, misleading data.
#
#   The practical consequence is narrow but must be respected:
#   GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB must stay False in ANY environment
#   pointed at the GovStack harness, otherwise every positive voucher scenario
#   would 460/463.  It is absent (→ False) outside production.py precisely so
#   this cannot happen by accident.  Production deployments register their real
#   BB ids via Django admin (or by extending the list below).
#
# Each entry: (bb_id, description).
_SEED_REGISTERED_BBS: list[tuple[str, str]] = [
    (
        _HARNESS_ISSUING_BB,
        "GovStack test harness institution. "
        "Created automatically by seed_govstack_vouchers.",
    ),
]

# role assigned to every seeded row: the harness/reference BBs must be able to
# exercise every Scheduler actor role tier (resource/organizer/admin) during
# certification testing.
_SEED_REGISTERED_BB_ROLE: str = "admin"

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

            # ── Seed GovStackRegisteredBB allowlist rows ──────────────────────
            # Create (or ensure existence of) every row in _SEED_REGISTERED_BBS
            # so that when GOVSTACK_REQUIRE_REGISTERED_BB=True (G2P header) or
            # GOVSTACK_VOUCHER_REQUIRE_REGISTERED_BB=True (voucher Gov_Stack_BB
            # body field, P2) is enabled, those ids pass their allowlist check.
            # Idempotent: rows that already exist are left unchanged.
            # See the _SEED_REGISTERED_BBS comment block for why the harness's
            # own positive Gov_Stack_BB fixture values are NOT seedable here.
            _bb_created_ids: list[str] = []
            _bb_skipped_ids: list[str] = []

            for bb_id, bb_description in _SEED_REGISTERED_BBS:
                _bb_obj, _bb_row_created = GovStackRegisteredBB.objects.get_or_create(
                    bb_id=bb_id,
                    defaults={
                        "description": bb_description,
                        "is_active": True,
                        "role": _SEED_REGISTERED_BB_ROLE,
                    },
                )

                (_bb_created_ids if _bb_row_created else _bb_skipped_ids).append(bb_id)

                if verbosity >= 2:
                    action = "Created" if _bb_row_created else "Skipped"
                    self.stdout.write(
                        f"  {action}  GovStackRegisteredBB bb_id={bb_id!r}"
                    )

                logger.info(
                    "seed_govstack_vouchers.registered_bb bb_id=%r created=%s",
                    bb_id,
                    _bb_row_created,
                )

            # ── Summary ───────────────────────────────────────────────────────
            total = len(_SEED_VOUCHERS)
            summary = (
                f"seed_govstack_vouchers complete — "
                f"{created_count} created, {skipped_count} already existed "
                f"(seed set size: {total}). "
                f"GovStackRegisteredBB: {len(_bb_created_ids)} created, "
                f"{len(_bb_skipped_ids)} already existed "
                f"(allowlist set size: {len(_SEED_REGISTERED_BBS)})."
            )

            if verbosity >= 1:
                self.stdout.write(self.style.SUCCESS(summary))

            logger.info(
                "seed_govstack_vouchers.done created=%d skipped=%d total=%d",
                created_count,
                skipped_count,
                total,
            )

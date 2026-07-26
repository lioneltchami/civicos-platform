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
Most of those serials must exist in STATUS_PREACTIVATED state before the
harness runner fires its first request.  Three serials are deliberately
seeded in a DIFFERENT state because individual harness scenarios require it
(see "Special-cased serials" below).  This command seeds all 16 of them
idempotently using get_or_create so repeated runs are safe — see
"Idempotency and --reset" below for the exact guarantee that does (and does
not) provide.

Special-cased serials
----------------------
- ``6001`` is seeded already ``STATUS_CONSUMED`` (with ``redeemed_at`` and
  ``redeemed_merchant_name`` populated for audit-trail coherence), for the
  voucher-status-check scenario that expects HTTP 458 (VoucherAlreadyUsed).
- ``6002`` is seeded ``STATUS_ACTIVATED`` with ``expiry_date`` set 30 days in
  the *past*, for the voucher-status-check scenario that expects HTTP 459
  (VoucherExpired).  It is deliberately NOT ``STATUS_CONSUMED``:
  ``get_status()`` checks CONSUMED (→458) before comparing expiry (→459), so
  a consumed-and-expired voucher would never reach the 459 path (see
  ``govstack_services.GovStackVoucherService.get_status()``).  There is no
  ``STATUS_EXPIRED`` value on the model — expiry is purely a date comparison.
- ``6004`` is seeded already ``STATUS_ACTIVATED`` (not the usual default
  ``STATUS_PREACTIVATED``), because the redemption smoke-test scenario
  against this serial calls ``redeem()``, whose ``ALLOWED_TRANSITIONS`` only
  permit ``ACTIVATED → CONSUMED``.

All other 13 serials keep the default ``STATUS_PREACTIVATED`` / +365 day
expiry, unchanged from before.

Idempotency and --reset
------------------------
The upsert loop uses ``get_or_create()``, NOT ``update_or_create()`` — this
is a deliberate engineering decision, not an oversight.  Per the "Usage"
section below, this command is documented to run *once*, immediately before
a harness submission.  A harness run legitimately mutates these vouchers'
status as it exercises preactivation → activation → redemption/cancellation
scenarios.  If the loop instead used ``update_or_create()`` unconditionally,
re-running this command mid-harness-run (e.g. a CI step retrying it, or an
operator running it again "just to be safe") would silently reset any
voucher's status back to its seed default — destroying in-progress harness
state with no warning.  That risk was judged worse than the convenience of
auto-repairing a stale row on every plain run, so ``get_or_create()`` is
kept: it only ever creates rows that do not yet exist and never mutates an
existing row.

The consequence, and the operational action it requires: a database seeded
by a version of this command *older* than the 6001/6002/6004 special-casing
described above (e.g. one where 6004 was created as STATUS_PREACTIVATED, or
one seeded before 6001/6002 existed in ``_SEED_VOUCHERS`` at all) will NOT
be self-healed by a plain re-run — a pre-existing 6004 row will remain stuck
at its old, wrong status forever, because ``get_or_create`` skips rows that
already exist.  **Operators upgrading an existing environment to this
version of the command MUST run ``--reset`` once** to delete and recreate
every seed row in its correct state.  After that one-time repair, plain runs
are safe again.

Why a management command, not a data migration?
-----------------------------------------------
Migration 0017 in this project is ``0017_tighten_payee_functional_id_validator``
— a schema migration.  Inserting test fixtures via a data migration would
permanently pollute production databases and cannot be reversed cleanly.
A management command is the correct pattern for environment-specific seed
data (see also: ``setup_periodic_tasks.py``, ``seed_tax_rates.py``).

Serial number range conflict
----------------------------
``_generate_voucher_serial()`` produces 18-digit numeric strings (the harness's
own JSON schema requires 16-25 characters; 18 digits sits comfortably inside
both that range and this codebase's own max_length=20 request-serializer
ceiling — see govstack_models.py and SPEC_GOVSTACK_PAYMENTS_BB.md section 24.1).
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

    # Seed all 16 harness vouchers (idempotent):
    python manage.py seed_govstack_vouchers

    # Force-recreate all seed rows in their correct seeded state — e.g.
    # after a harness run consumed/cancelled/activated them, OR as the
    # REQUIRED one-time repair step when upgrading an environment that was
    # seeded by a version of this command older than the 6001/6002/6004
    # special-casing described above (see "Idempotency and --reset"):
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
# Seed data — 16 harness-required vouchers
# ---------------------------------------------------------------------------
# Tuple format:
#   (serial_number, group_code, amount_str, currency_iso4217, status,
#    expiry_offset_days)
#
# status and expiry_offset_days are explicit per-row (rather than a blanket
# default applied uniformly) because 3 of the 16 rows require a non-default
# seeded state — see "Special-cased serials" in the module docstring:
#   - 6001: STATUS_CONSUMED   (voucher-status-check → 458 VoucherAlreadyUsed)
#   - 6002: STATUS_ACTIVATED, expiry_offset_days=-30 (in the past)
#           (voucher-status-check → 459 VoucherExpired)
#   - 6004: STATUS_ACTIVATED  (redemption smoke test; ALLOWED_TRANSITIONS
#           only permits ACTIVATED → CONSUMED)
# All other 13 rows use the historical default: STATUS_PREACTIVATED,
# expiry_offset_days=_SEED_EXPIRY_DAYS (+365) — unchanged behavior.
#
# status values are plain string literals matching GovStackVoucher.STATUS_*
# EXACTLY (see govstack_models.py) rather than references to the model
# class itself — this module deliberately avoids importing GovStackVoucher
# at module scope (see the lazy import in Command.handle()), so the model's
# app registry does not need to be ready merely to import this file.
#
# Serial numbers use 4-digit and 5-digit values that are permanently outside
# the auto-generation range (18-digit numeric strings; see
# _generate_voucher_serial() in govstack_models.py), ensuring they cannot
# appear in production by accident.  group_code values match the GovStack harness
# fixture expectations exactly (case-sensitive) for the original 14 rows; no
# harness scenario asserts group_code/amount/currency for 6001 or 6002 (there
# is no harness-specified value to match), so they use safe, consistent
# defaults matching the existing FOOD group.
#
# amount_str uses string form to ensure exact Decimal conversion without
# floating-point rounding — never pass a float literal to DecimalField.

_SEED_STATUS_PREACTIVATED: str = "preactivated"
_SEED_STATUS_ACTIVATED: str = "activated"
_SEED_STATUS_CONSUMED: str = "consumed"

# Expiry duration for freshly created seed vouchers (defined here, ahead of
# _SEED_VOUCHERS, because the table below references it per-row).
_SEED_EXPIRY_DAYS: int = 365

_SEED_VOUCHERS: list[tuple[str, str, str, str, str, int]] = [
    # ── Primary FOOD range (serials 5550–5555) ───────────────────────────────
    ("5550",  "FOOD",      "100.00", "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5551",  "FOOD",      "100.00", "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5552",  "FOOD",      "200.00", "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5553",  "FOOD",      "150.00", "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5554",  "FOOD",      "100.00", "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5555",  "FOOD",      "100.00", "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    # ── HEALTH range (serials 5556–5560) ────────────────────────────────────
    ("5556",  "HEALTH",    "75.00",  "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5557",  "HEALTH",    "75.00",  "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5558",  "HEALTH",    "75.00",  "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5559",  "HEALTH",    "75.00",  "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("5560",  "HEALTH",    "75.00",  "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    # ── Special-cased status serials (voucherstatuscheck / redemption smoke) ──
    # 6001: already CONSUMED → voucherstatuscheck harness scenario expects 458.
    ("6001",  "FOOD",      "100.00", "CAD", _SEED_STATUS_CONSUMED,    _SEED_EXPIRY_DAYS),
    # 6002: ACTIVATED but expired 30 days ago (NOT consumed — get_status()
    # checks CONSUMED/458 before expiry/459, so 458 would win if this were
    # also CONSUMED and the 459 scenario would never be reachable).
    ("6002",  "FOOD",      "100.00", "CAD", _SEED_STATUS_ACTIVATED,   -30),
    # 6004: ACTIVATED (not the usual PREACTIVATED default) — redemption smoke
    # test calls redeem(), which only permits ACTIVATED → CONSUMED.
    ("6004",  "HEALTH",    "200.00", "CAD", _SEED_STATUS_ACTIVATED,   _SEED_EXPIRY_DAYS),
    # ── 5-digit TRANSPORT range ──────────────────────────────────────────────
    ("60000", "TRANSPORT", "50.00",  "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
    ("60001", "TRANSPORT", "50.00",  "CAD", _SEED_STATUS_PREACTIVATED, _SEED_EXPIRY_DAYS),
]

# Placeholder redemption-metadata value stamped onto any seed row created
# already STATUS_CONSUMED (currently only 6001), so that a CONSUMED seed
# voucher's audit-trail fields are populated rather than blank — a CONSUMED
# row with no redemption metadata looks like a data bug to anyone inspecting
# the DB later.  Keyed off `status == STATUS_CONSUMED` in the seeding loop
# below, not off the specific serial, so any future CONSUMED seed row gets
# the same treatment automatically.
_SEED_REDEMPTION_MERCHANT_NAME: str = "GS-HARNESS-SEED-MERCHANT"

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


class Command(BaseCommand):
    help = (
        "Seed GovStack Payments BB harness test vouchers (idempotent). "
        "Run immediately before submitting to the GovStack test harness at "
        "testing.govstack.global.  Use --reset to force-recreate all seed "
        "rows — e.g. after a harness run that consumed, activated, or "
        "cancelled them, or as a REQUIRED one-time repair step when "
        "upgrading an environment seeded by an older version of this "
        "command (see module docstring, 'Idempotency and --reset')."
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
                "then recreate them fresh in each serial's seed-defined "
                "status (STATUS_PREACTIVATED for most; 6001=CONSUMED, "
                "6002/6004=ACTIVATED — see _SEED_VOUCHERS). "
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

        # All DB writes (delete + get_or_create loop) are wrapped in a single
        # atomic transaction so that --reset can never leave the seed set in a
        # partially-deleted state if the command crashes mid-loop.
        with transaction.atomic():

            # ── Optional reset ────────────────────────────────────────────────
            if do_reset:
                # Filter by serial_number only (not issuing_bb) so that any row
                # occupying a seed serial — regardless of how it was created — is
                # cleared.  Since these serials are outside the auto-generation
                # range (18-digit numeric strings), this cannot accidentally
                # delete production vouchers.
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
            # Deliberately get_or_create(), NOT update_or_create() — see the
            # module docstring section "Idempotency and --reset" for the full
            # reasoning.  In short: this command is documented to run once,
            # immediately before a harness submission; a harness run
            # legitimately mutates these vouchers' status afterwards, and an
            # unconditional update_or_create() would silently revert that
            # in-progress state on any incidental re-run.  get_or_create()
            # only ever creates missing rows and never touches an existing
            # one, so --reset (delete + recreate) is the sole repair path
            # for rows already seeded in a stale/wrong state.
            created_count = 0
            skipped_count = 0

            for serial, group_code, amount_str, currency, status, expiry_offset_days in (
                _SEED_VOUCHERS
            ):
                expiry = timezone.now() + timedelta(days=expiry_offset_days)

                defaults = {
                    "amount": Decimal(amount_str),
                    "currency": currency,
                    "group_code": group_code,
                    "status": status,
                    "issuing_bb": _HARNESS_ISSUING_BB,
                    "expiry_date": expiry,
                    # Security: payee_functional_id intentionally omitted (no PII).
                    # voucher_secret intentionally omitted (blank=True on the field;
                    # an encrypted empty string is stored).
                    # batch_id, callback_url, registering_institution_id all
                    # default to "" (blank=True) — no need to set them here.
                }

                # CONSUMED seed rows must carry redemption audit-trail metadata
                # — a CONSUMED voucher with blank redemption fields looks like
                # a data bug to anyone inspecting the DB later.  Keyed off the
                # row's `status`, not the specific serial, so any future
                # CONSUMED seed row gets the same treatment automatically.
                if status == GovStackVoucher.STATUS_CONSUMED:
                    defaults["redeemed_at"] = timezone.now()
                    defaults["redeemed_merchant_name"] = _SEED_REDEMPTION_MERCHANT_NAME

                _obj, was_created = GovStackVoucher.objects.get_or_create(
                    serial_number=serial,
                    defaults=defaults,
                )

                if was_created:
                    created_count += 1
                    if verbosity >= 2:
                        self.stdout.write(
                            f"  Created  serial={serial!r:>6}  group={group_code!r}  "
                            f"amount={amount_str}  currency={currency}  status={status!r}"
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

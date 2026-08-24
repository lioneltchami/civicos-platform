"""
Management command: govstack_generate_bb_credential

Provisions (or rotates) the GovStack Scheduler BB high-entropy authentication
credential for a given GovStackRegisteredBB row (identified by its public
bb_id). This is the operational counterpart to the Finding #1 security fix in
apps/appointments/govstack_auth.py and apps/appointments/models.py
(GovStackBBCredential): bb_id is a public infrastructure identifier and must
never be treated as a secret, so a SEPARATE, hashed, high-entropy secret is
generated here and printed to the operator's terminal exactly once.

Usage
-----
::

    # Provision a credential for an already-registered BB (bb_id must exist
    # as a GovStackRegisteredBB row — create it via Django admin first):
    python manage.py govstack_generate_bb_credential --bb-id GS-HARNESS

    # Rotate an existing credential (invalidates the previous secret
    # immediately — any caller still using the old value will start failing
    # authentication):
    python manage.py govstack_generate_bb_credential --bb-id GS-HARNESS --rotate

Security
--------
- The plaintext secret is printed to stdout exactly once and is NEVER logged,
  stored, or retrievable again. If it is lost, the only recovery is to rotate
  (generate a new one).
- Only django.contrib.auth.hashers.make_password (via
  GovStackBBCredential.set_token()) ever sees the plaintext; the DB only ever
  stores the hash plus an 8-character display prefix (not sufficient entropy
  to reconstruct the secret).
- Running the command WITHOUT --rotate on a BB that already has a credential
  is refused (fails closed) rather than silently overwriting it, to avoid an
  operator accidentally invalidating a live production credential by
  forgetting the flag.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Generate (or --rotate) the GovStack Scheduler BB credential for a "
        "registered BB, identified by its public bb_id. Prints the plaintext "
        "secret exactly once — it is never stored or logged."
    )

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument(
            "--bb-id",
            required=True,
            help="The public bb_id of an existing GovStackRegisteredBB row.",
        )
        parser.add_argument(
            "--rotate",
            action="store_true",
            default=False,
            help=(
                "Replace an existing credential's secret. Without this flag, "
                "the command refuses to run if a credential already exists "
                "for the given bb_id (fail-closed — prevents accidentally "
                "invalidating a live credential)."
            ),
        )

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        # Lazy imports — avoid model loading before the app registry is ready.
        import getpass

        from apps.appointments.models import BookingAuditLog, GovStackBBCredential
        from apps.appointments.services.govstack_log import (
            record_admin_audit_event,
        )
        from apps.payments.govstack_models import GovStackRegisteredBB

        bb_id: str = options["bb_id"]
        rotate: bool = options["rotate"]

        try:
            bb = GovStackRegisteredBB.objects.get(bb_id=bb_id)
        except GovStackRegisteredBB.DoesNotExist as exc:
            raise CommandError(
                f"No GovStackRegisteredBB row with bb_id={bb_id!r}. "
                "Create it first (Django admin or seed_govstack_vouchers)."
            ) from exc

        plaintext = GovStackBBCredential.generate_plaintext_token()

        with transaction.atomic():
            existing = GovStackBBCredential.objects.filter(bb=bb).first()
            if existing is not None and not rotate:
                raise CommandError(
                    f"bb_id={bb_id!r} already has a credential. "
                    "Pass --rotate to replace it (this invalidates the old secret)."
                )

            credential = existing or GovStackBBCredential(bb=bb)
            credential.set_token(plaintext)
            credential.save()

        action = "Rotated" if existing is not None else "Created"

        # Round 2 certifiability re-audit fix (MEDIUM): every BB-credential
        # create/rotate must leave a real, queryable, tamper-evident record
        # of who did it and when — this is an operator-run management
        # command, not an HTTP request, so there is no requestor_id/resolved
        # role to attribute; the OS user running the command is the closest
        # available identity (never the plaintext secret itself — that is
        # never logged anywhere, see module docstring). See
        # services.govstack_log.record_admin_audit_event's docstring for the
        # full BookingAuditLog(booking=None) design rationale.
        record_admin_audit_event(
            action=BookingAuditLog.ACTION_ADMIN_CREDENTIAL_MUTATED,
            resource_pk=bb.bb_id,
            operation="rotate" if existing is not None else "create",
            actor_id=f"cli:{getpass.getuser()}",
            actor_role="admin",
        )

        self.stdout.write(self.style.SUCCESS(f"{action} credential for bb_id={bb_id!r}."))
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "COPY THIS SECRET NOW — it will not be shown again and is not "
                "recoverable from the database:"
            )
        )
        self.stdout.write("")
        self.stdout.write(f"    request_token = {plaintext}")
        self.stdout.write("")
        self.stdout.write(
            f"Use with requestor_id={bb_id!r} as the two GovStack Scheduler " "BB query parameters."
        )

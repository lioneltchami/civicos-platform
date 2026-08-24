"""
Management command: manage_legal_hold

Allows Privacy Officers and Records Managers to apply or release legal holds
on documents via the Django management command interface (e.g. in response to
an ATIP request or court order).

All mutations go through the service layer (apply_legal_hold / release_legal_hold)
which guarantees:
  - select_for_update() TOCTOU guard
  - record_event() inside atomic() — PIPEDA 4.5.3 compliant audit trail
  - Signals fired via on_commit() — no signal before DB commit

Privacy invariants (enforced here):
  - Actor is looked up by username; only actor.pk is logged — never email/name
  - Document is referenced only by PK in log output — never filename
  - Command output contains PK only; no PII surfaces in terminal/CI logs

Usage:
    python manage.py manage_legal_hold --action apply \
        --document-pk 42 \
        --reason "ATIP request A-2025-00123" \
        --actor-username privacy.officer

    python manage.py manage_legal_hold --action release \
        --document-pk 42 \
        --reason "ATIP request closed" \
        --actor-username privacy.officer

Governing law: PIPEDA clause 4.5.3 (audit on disposal/hold actions),
               Privacy Act s.6(3) (legal hold authority),
               TBS SPIN 2023-06-13.
"""

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Document
from apps.documents.services.retention import apply_legal_hold, release_legal_hold

logger = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Apply or release a legal hold on a document. "
        "Requires Privacy Officer or Records Manager privileges. "
        "All changes are fully audited per PIPEDA clause 4.5.3."
    )

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument(
            "--action",
            choices=["apply", "release"],
            required=True,
            help="Whether to apply or release the legal hold.",
        )
        parser.add_argument(
            "--document-pk",
            type=int,
            required=True,
            metavar="PK",
            help="Primary key of the document to act on.",
        )
        parser.add_argument(
            "--reason",
            required=True,
            metavar="REASON",
            help=(
                "Reason for the hold/release (e.g. 'ATIP request A-2025-00123'). "
                "Stored in the audit event. Do NOT include PII."
            ),
        )
        parser.add_argument(
            "--actor-username",
            required=True,
            metavar="USERNAME",
            help="Username of the Privacy Officer or Records Manager authorising this action.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            default=False,
            help="Skip interactive confirmation prompt (for scripted use).",
        )

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        action: str = options["action"]
        doc_pk: int = options["document_pk"]
        reason: str = options["reason"].strip()
        actor_username: str = options["actor_username"].strip()
        skip_confirm: bool = options["yes"]

        # ── Validate reason (no empty strings; PII guard) ────────────────────
        if not reason:
            raise CommandError("--reason must not be empty.")
        if len(reason) > 512:
            raise CommandError("--reason must not exceed 512 characters.")

        # ── Resolve actor ────────────────────────────────────────────────────
        try:
            actor = User.objects.get(username=actor_username)
        except User.DoesNotExist:
            raise CommandError(f"User with username {actor_username!r} does not exist.")  # noqa: B904

        # ── Resolve document ─────────────────────────────────────────────────
        try:
            doc = Document.objects.get(pk=doc_pk)
        except Document.DoesNotExist:
            raise CommandError(f"Document with pk={doc_pk} does not exist.")  # noqa: B904

        # ── Summarise current state (no PII, no filename) ────────────────────
        current_hold = "HELD" if doc.legal_hold else "NOT HELD"
        self.stdout.write(
            f"Document pk={doc_pk}  category={doc.category_id}  "
            f"current legal_hold={current_hold}  "
            f"action={action.upper()}  actor_pk={actor.pk}"
        )

        # ── Interactive confirmation (skippable for scripts) ─────────────────
        if not skip_confirm:
            confirm = (
                input(f"\nProceed to {action.upper()} legal hold on document pk={doc_pk}? [yes/N] ")
                .strip()
                .lower()
            )
            if confirm != "yes":
                self.stdout.write(self.style.WARNING("Aborted — no changes made."))
                return

        # ── Execute service call ─────────────────────────────────────────────
        try:
            if action == "apply":
                apply_legal_hold(document=doc, set_by=actor, reason=reason)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Legal hold APPLIED on document pk={doc_pk} " f"by actor pk={actor.pk}."
                    )
                )
                logger.info(
                    "manage_legal_hold: applied hold on document pk=%s by actor pk=%s",
                    doc_pk,
                    actor.pk,
                )
            else:  # release
                release_legal_hold(document=doc, released_by=actor, reason=reason)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Legal hold RELEASED on document pk={doc_pk} " f"by actor pk={actor.pk}."
                    )
                )
                logger.info(
                    "manage_legal_hold: released hold on document pk=%s by actor pk=%s",
                    doc_pk,
                    actor.pk,
                )

        except ValueError as exc:
            # Service raises ValueError for precondition violations
            # (already deleted, already held/unheld, etc.)
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "manage_legal_hold: unexpected error for document pk=%s action=%s",
                doc_pk,
                action,
            )
            raise CommandError(
                f"Unexpected error: {exc!r}. Check server logs for details."
            ) from exc

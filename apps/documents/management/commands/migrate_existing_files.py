"""
Management command: migrate_existing_files

Migrates legacy file references into proper Document BB records.

Models covered:
  - Certification.document       (FileField)  → Document + Certification.document_v2 (FK)
  - OfficialDonationReceipt.pdf_path (CharField) → Document + OfficialDonationReceipt.document (FK)
  - DataExportRequest.storage_path  (CharField) → Document + DataExportRequest.document (FK)
  - Honorarium / Screening: no legacy file data — logged and skipped.

Usage:
    python manage.py migrate_existing_files [--dry-run] [--model MODEL]

PIPEDA constraints (enforced throughout):
  - NEVER log original_filename (may contain PII such as a person's name in the filename).
  - NEVER log storage_key (internal S3 path).
  - NEVER log volunteer/citizen email, name, or any PII — log .pk only.
  - Safe to log: Document.pk, model name, migration count, error type names.

Governing law: PIPEDA clause 4.5.3 & 4.7.5, Privacy Act s.6(1).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from django.core.exceptions import FieldDoesNotExist
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEventType
from apps.audit.services import record_event
from apps.documents.models import Document, DocumentCategory
from apps.documents.services.retention import schedule_expiry

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Internal result accumulator
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _MigrationResult:
    """Counters for one model's migration pass."""

    model_label: str
    migrated: int = 0
    skipped: int = 0
    failed: int = 0
    phase_3_applied: bool = False

    def summary_line(self, dry_run: bool = False) -> str:
        suffix = " (DRY RUN)" if dry_run else ""
        if self.phase_3_applied:
            return (
                f"{self.model_label:<30} "
                f"Phase 3 already applied — legacy field removed, no action needed"
                f"{suffix}"
            )
        return (
            f"{self.model_label:<30} "
            f"{self.migrated} migrated, "
            f"{self.skipped} skipped (no file), "
            f"{self.failed} failed"
            f"{suffix}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Command
# ─────────────────────────────────────────────────────────────────────────────


class Command(BaseCommand):
    help = (
        "Migrate legacy file references (FileField / CharField paths) into "
        "proper Document BB records. Idempotent: already-migrated rows are "
        "skipped automatically."
    )

    # ── Argument definitions ──────────────────────────────────────────────────

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Simulate without writing to the database.",
        )
        parser.add_argument(
            "--model",
            choices=[
                "certification",
                "honorarium-receipt",
                "screening",
                "donation-receipt",
                "data-export",
                "all",
            ],
            default="all",
            help="Which model to migrate. Default: all.",
        )

    # ── Entry point ───────────────────────────────────────────────────────────

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        model_choice: str = options["model"]

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] No database writes will occur."))

        # Dispatch table — maps CLI choice to handler method
        handlers: dict[str, Callable[[bool], _MigrationResult]] = {
            "certification": self._migrate_certifications,
            "donation-receipt": self._migrate_donation_receipts,
            "data-export": self._migrate_data_exports,
            "honorarium-receipt": self._migrate_honorarium_receipts,
            "screening": self._migrate_screening_records,
        }

        if model_choice == "all":
            selected = list(handlers.keys())
        else:
            selected = [model_choice]

        results: list[_MigrationResult] = []
        for key in selected:
            handler = handlers[key]
            result = handler(dry_run)
            results.append(result)

        # ── Summary table ─────────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write("─" * 70)
        self.stdout.write("Migration summary")
        self.stdout.write("─" * 70)
        any_failures = False
        for r in results:
            self.stdout.write(r.summary_line(dry_run=dry_run))
            if r.failed:
                any_failures = True
        self.stdout.write("─" * 70)

        if any_failures:
            self.stderr.write(
                self.style.ERROR(
                    "One or more rows failed to migrate. "
                    "Check logs (level=ERROR) for details. "
                    "PIPEDA: error messages contain error type names only — no PII."
                )
            )
            raise SystemExit(1)

    # ─────────────────────────────────────────────────────────────────────────
    # Certification
    # ─────────────────────────────────────────────────────────────────────────

    def _migrate_certifications(self, dry_run: bool) -> _MigrationResult:
        result = _MigrationResult(model_label="Certification")
        dry_prefix = "[DRY RUN] " if dry_run else ""

        try:
            from apps.volunteers.models import Certification
        except ImportError:
            logger.warning(
                "%sCertification model not importable — skipping.", dry_prefix
            )
            return result

        # Detect if Phase 3 has already removed the legacy FileField
        try:
            Certification._meta.get_field("document")
        except FieldDoesNotExist:
            logger.info(
                "%sCertification: Phase 3 migration already applied — "
                "legacy 'document' FileField no longer exists. No action needed.",
                "[DRY RUN] " if dry_run else "",
            )
            return _MigrationResult(model_label="Certification", migrated=0, skipped=0, failed=0, phase_3_applied=True)

        category, _ = DocumentCategory.objects.get_or_create(
            slug="volunteer-certification",
            defaults={
                "name_en": "Volunteer Certification",
                "name_fr": "Certification bénévole",
                "description_en": (
                    "Criminal Record Checks, Vulnerable Sector Checks, "
                    "and other volunteer certification documents."
                ),
                "description_fr": (
                    "Vérifications des antécédents criminels, vérifications "
                    "du secteur vulnérable et autres documents de certification."
                ),
                "security_classification": (
                    DocumentCategory.SecurityClassification.PROTECTED_B
                ),
                "min_retention_days": 730,   # Privacy Act s.6(1) — 2 years
                "max_retention_days": 2555,  # 7 years
                "is_transitory": False,
                "staff_only": True,
            },
        )

        # Only rows that have an old file but no new FK yet (idempotent filter)
        qs = (
            Certification.objects.filter(document__isnull=False)
            .exclude(document="")
            .filter(document_v2__isnull=True)
            .select_related("volunteer__user")
        )

        logger.info(
            "%sCertification: %d row(s) to process.", dry_prefix, qs.count()
        )

        for cert in qs.iterator():
            pk = cert.pk
            try:
                original_filename = os.path.basename(cert.document.name) or "certification"
                storage_key = cert.document.name
                size_bytes = _get_file_size(cert.document)
                uploaded_by = cert.volunteer.user

                if dry_run:
                    logger.info(
                        "[DRY RUN] Would migrate Certification pk=%s → new Document.",
                        pk,
                    )
                    result.migrated += 1
                    continue

                with transaction.atomic():
                    doc = Document(
                        category=category,
                        uploaded_by=uploaded_by,
                        original_filename=original_filename,  # stored in DB; NEVER logged
                        _storage_key=storage_key,             # NEVER logged
                        mime_type="application/octet-stream",
                        size_bytes=size_bytes,
                        scan_status=Document.ScanStatus.ACTIVE,
                        version_number=1,
                        is_latest_version=True,
                        security_classification=(
                            DocumentCategory.SecurityClassification.PROTECTED_B
                        ),
                    )
                    doc.save()

                    schedule_expiry(document=doc)

                    cert.document_v2 = doc
                    cert.save(update_fields=["document_v2", "updated_at"])

                    # PIPEDA: event_detail contains NO original_filename, NO storage_key
                    record_event(
                        event_type=AuditEventType.RECORD_CREATED,
                        resource_type="documents.Document",
                        resource_id=str(doc.pk),
                        event_detail={
                            "category_slug": category.slug,
                            "migration": True,
                            "source_model": "Certification",
                            "source_pk": str(pk),
                        },
                    )

                result.migrated += 1
                logger.info(
                    "Migrated Certification pk=%s → Document pk=%s.",
                    pk,
                    doc.pk,
                )

            except Exception as exc:
                result.failed += 1
                # PIPEDA: log error type name only — never exc.args (may contain PII)
                logger.error(
                    "%sFailed to migrate Certification pk=%s: %s",
                    dry_prefix,
                    pk,
                    type(exc).__name__,
                )

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # OfficialDonationReceipt
    # ─────────────────────────────────────────────────────────────────────────

    def _migrate_donation_receipts(self, dry_run: bool) -> _MigrationResult:
        result = _MigrationResult(model_label="OfficialDonationReceipt")
        dry_prefix = "[DRY RUN] " if dry_run else ""

        try:
            from apps.payments.models import OfficialDonationReceipt
        except ImportError:
            logger.warning(
                "%sOfficialDonationReceipt model not importable — skipping.", dry_prefix
            )
            return result

        # Detect if Phase 3 has already removed the legacy CharField
        try:
            OfficialDonationReceipt._meta.get_field("pdf_path")
        except FieldDoesNotExist:
            logger.info(
                "%sOfficialDonationReceipt: Phase 3 migration already applied — "
                "legacy 'pdf_path' CharField no longer exists. No action needed.",
                "[DRY RUN] " if dry_run else "",
            )
            return _MigrationResult(model_label="OfficialDonationReceipt", migrated=0, skipped=0, failed=0, phase_3_applied=True)

        category, _ = DocumentCategory.objects.get_or_create(
            slug="donation-receipt-pdf",
            defaults={
                "name_en": "Official Donation Receipt (PDF)",
                "name_fr": "Reçu officiel de don (PDF)",
                "description_en": (
                    "CRA-compliant official donation receipts issued to donors."
                ),
                "description_fr": (
                    "Reçus officiels de don conformes à l'ARC émis aux donateurs."
                ),
                "security_classification": (
                    DocumentCategory.SecurityClassification.PROTECTED_B
                ),
                "min_retention_days": 2555,  # CRA: 6 years + current year = 7 years
                "max_retention_days": 2555,
                "is_transitory": False,
                "staff_only": False,
            },
        )

        # Must use _default_manager to respect any custom manager filtering
        qs = (
            OfficialDonationReceipt._default_manager
            .filter(pdf_path__isnull=False)
            .exclude(pdf_path="")
            .filter(document__isnull=True)
        )

        logger.info(
            "%sOfficialDonationReceipt: %d row(s) to process.", dry_prefix, qs.count()
        )

        for receipt in qs.select_related(
            "donation__payment_intent__payer"
        ).iterator():
            pk = receipt.pk
            try:
                original_filename = (
                    os.path.basename(receipt.pdf_path)
                    or f"receipt-{receipt.serial_number}.pdf"
                )
                storage_key = receipt.pdf_path
                uploaded_by = receipt.donation.payment_intent.payer

                if dry_run:
                    logger.info(
                        "[DRY RUN] Would migrate OfficialDonationReceipt pk=%s → new Document.",
                        pk,
                    )
                    result.migrated += 1
                    continue

                with transaction.atomic():
                    doc = Document(
                        category=category,
                        uploaded_by=uploaded_by,
                        original_filename=original_filename,  # NEVER logged
                        _storage_key=storage_key,             # NEVER logged
                        mime_type="application/pdf",
                        size_bytes=0,  # S3 path — not locally inspectable
                        scan_status=Document.ScanStatus.ACTIVE,
                        version_number=1,
                        is_latest_version=True,
                        security_classification=(
                            DocumentCategory.SecurityClassification.PROTECTED_B
                        ),
                    )
                    doc.save()

                    schedule_expiry(document=doc)

                    # Must use _base_manager.filter().update() — OfficialDonationReceipt.save()
                    # raises ValueError on certain immutable field mutations; the `document`
                    # FK was added after the original save() guard was written.
                    # Direct .update() bypasses the save() guard safely.
                    OfficialDonationReceipt._base_manager.filter(pk=receipt.pk).update(
                        document=doc,
                        updated_at=timezone.now(),
                    )

                    # PIPEDA: event_detail contains NO original_filename, NO storage_key
                    record_event(
                        event_type=AuditEventType.RECORD_CREATED,
                        resource_type="documents.Document",
                        resource_id=str(doc.pk),
                        event_detail={
                            "category_slug": category.slug,
                            "migration": True,
                            "source_model": "OfficialDonationReceipt",
                            "source_pk": str(pk),
                        },
                    )

                result.migrated += 1
                logger.info(
                    "Migrated OfficialDonationReceipt pk=%s → Document pk=%s.",
                    pk,
                    doc.pk,
                )

            except Exception as exc:
                result.failed += 1
                logger.error(
                    "%sFailed to migrate OfficialDonationReceipt pk=%s: %s",
                    dry_prefix,
                    pk,
                    type(exc).__name__,
                )

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # DataExportRequest
    # ─────────────────────────────────────────────────────────────────────────

    def _migrate_data_exports(self, dry_run: bool) -> _MigrationResult:
        result = _MigrationResult(model_label="DataExportRequest")
        dry_prefix = "[DRY RUN] " if dry_run else ""

        try:
            from apps.consent.models import DataExportRequest
        except ImportError:
            logger.warning(
                "%sDataExportRequest model not importable — skipping.", dry_prefix
            )
            return result

        # Detect if Phase 3 has already removed the legacy CharField
        try:
            DataExportRequest._meta.get_field("storage_path")
        except FieldDoesNotExist:
            logger.info(
                "%sDataExportRequest: Phase 3 migration already applied — "
                "legacy 'storage_path' CharField no longer exists. No action needed.",
                "[DRY RUN] " if dry_run else "",
            )
            return _MigrationResult(model_label="DataExportRequest", migrated=0, skipped=0, failed=0, phase_3_applied=True)

        category, _ = DocumentCategory.objects.get_or_create(
            slug="pipeda-data-export",
            defaults={
                "name_en": "PIPEDA Data Export Package",
                "name_fr": "Ensemble de données LPRPDE",
                "description_en": (
                    "Personal information export package provided to a citizen "
                    "under PIPEDA access request. Transitory — destroyed once delivered."
                ),
                "description_fr": (
                    "Ensemble de renseignements personnels remis à un citoyen "
                    "dans le cadre d'une demande d'accès en vertu de la LPRPDE. "
                    "Transitoire — détruit une fois livré."
                ),
                "security_classification": (
                    DocumentCategory.SecurityClassification.PROTECTED_B
                ),
                "min_retention_days": 0,    # Transitory: no mandatory minimum
                "max_retention_days": 30,   # Destroy within 30 days of delivery
                "is_transitory": True,      # LAC DA #2016/001
                "staff_only": False,
            },
        )

        qs = (
            DataExportRequest.objects.filter(storage_path__isnull=False)
            .exclude(storage_path="")
            .filter(document__isnull=True)
            .select_related("citizen")
        )

        logger.info(
            "%sDataExportRequest: %d row(s) to process.", dry_prefix, qs.count()
        )

        for export in qs.iterator():
            pk = export.pk
            try:
                original_filename = (
                    os.path.basename(export.storage_path)
                    or f"export-{export.pk}.json"
                )
                storage_key = export.storage_path
                uploaded_by = export.citizen

                if dry_run:
                    logger.info(
                        "[DRY RUN] Would migrate DataExportRequest pk=%s → new Document.",
                        pk,
                    )
                    result.migrated += 1
                    continue

                with transaction.atomic():
                    doc = Document(
                        category=category,
                        uploaded_by=uploaded_by,
                        original_filename=original_filename,  # NEVER logged
                        _storage_key=storage_key,             # NEVER logged
                        mime_type="application/json",
                        size_bytes=0,  # S3 path — not locally inspectable
                        scan_status=Document.ScanStatus.ACTIVE,
                        version_number=1,
                        is_latest_version=True,
                        security_classification=(
                            DocumentCategory.SecurityClassification.PROTECTED_B
                        ),
                    )
                    doc.save()

                    schedule_expiry(document=doc)

                    export.document = doc
                    export.save(update_fields=["document"])

                    # PIPEDA: event_detail contains NO original_filename, NO storage_key
                    record_event(
                        event_type=AuditEventType.RECORD_CREATED,
                        resource_type="documents.Document",
                        resource_id=str(doc.pk),
                        event_detail={
                            "category_slug": category.slug,
                            "migration": True,
                            "source_model": "DataExportRequest",
                            "source_pk": str(pk),
                        },
                    )

                result.migrated += 1
                logger.info(
                    "Migrated DataExportRequest pk=%s → Document pk=%s.",
                    pk,
                    doc.pk,
                )

            except Exception as exc:
                result.failed += 1
                logger.error(
                    "%sFailed to migrate DataExportRequest pk=%s: %s",
                    dry_prefix,
                    pk,
                    type(exc).__name__,
                )

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Honorarium T4A (no legacy data — intentional no-op)
    # ─────────────────────────────────────────────────────────────────────────

    def _migrate_honorarium_receipts(self, dry_run: bool) -> _MigrationResult:
        result = _MigrationResult(model_label="Honorarium (T4A)")
        dry_prefix = "[DRY RUN] " if dry_run else ""
        # Honorarium.t4a_document is a new OneToOne field added in Wave 6.
        # The old Honorarium model had no storage field for T4A PDFs
        # (t4a_issued=True only tracked issuance status, not a file path).
        # There are no legacy file paths to migrate — this is intentionally a no-op.
        logger.info(
            "%sHonorarium (T4A): no legacy file paths to migrate. "
            "t4a_document is a new field with no legacy equivalent. Skipping.",
            dry_prefix,
        )
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # ScreeningRecord (no legacy data — intentional no-op)
    # ─────────────────────────────────────────────────────────────────────────

    def _migrate_screening_records(self, dry_run: bool) -> _MigrationResult:
        result = _MigrationResult(model_label="ScreeningRecord")
        dry_prefix = "[DRY RUN] " if dry_run else ""
        # ScreeningRecord.vsc_confirmation_doc is a new field added in Wave 6
        # with no legacy equivalent. No existing records have file paths stored.
        logger.info(
            "%sScreeningRecord: no legacy file paths to migrate. "
            "vsc_confirmation_doc is a new field with no legacy equivalent. Skipping.",
            dry_prefix,
        )
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_file_size(file_field) -> int:
    """
    Attempt to determine the size of a FieldFile in bytes.

    For local filesystem storage: uses os.path.getsize().
    For S3/remote storage (or any case where the path is not locally accessible):
    returns 0 as a safe default rather than raising.

    PIPEDA: this function does NOT log the file path at any level.
    """
    try:
        local_path = file_field.path
        return os.path.getsize(local_path)
    except (NotImplementedError, OSError, AttributeError, ValueError):
        # NotImplementedError: raised by S3/remote storage backends for .path
        # OSError:             file not found locally
        # AttributeError:     file_field is None or has no .path
        # ValueError:         empty path
        return 0

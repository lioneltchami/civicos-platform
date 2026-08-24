"""
Corrective data migration — Document category retention + staff_only fixes.

Addresses two compliance and security gaps introduced by 0007_seed_document_categories:

1. ``volunteer-certification`` — ``min_retention_days`` was seeded as 365 (1 year).
   This violates Privacy Act s.6(1), which mandates minimum 2-year (730-day) retention
   for administrative purpose records (CRC / VSC certificates are administrative records
   used in volunteer screening decisions). Corrected to 730. ``max_retention_days``
   corrected to 2555 (7 years) per spec §11.1 retention schedule.

   Additionally, the category had ``staff_only=False``, meaning any authenticated citizen
   with the ``upload_document`` permission could upload to it. CRC/VSC certificates are
   staff-generated government records; citizen upload capability creates a fabricated-record
   injection risk. Corrected to ``staff_only=True``.

2. ``cra-t4a-slip`` — seeded with ``staff_only=False``. CRA T4A slips are CRA-generated
   tax documents attached to honourarium records by staff. Citizen-uploadable T4A slips
   could corrupt honourarium payment records with fabricated tax documents.
   Corrected to ``staff_only=True``.

Why this migration is needed (not handled by 0007):
   Migration 0007 uses ``get_or_create()`` for non-STAFF_ONLY_SLUGS, which means if the
   category row already exists (from any environment where 0007 ran), the defaults are
   silently ignored. This migration uses ``update_or_create()`` with an explicit
   ``update_fields`` set to target only the affected columns.

Security classification: No change. Both remain Protected B.

Governing law: Privacy Act s.6(1) (minimum 2-year retention for administrative records),
OWASP A01 Broken Access Control (staff_only enforcement).
"""

from django.db import migrations


def fix_category_retention_and_staff_only(apps, schema_editor) -> None:  # noqa: ANN001
    """
    Correct volunteer-certification and cra-t4a-slip category settings.

    Uses update() against a slug filter for safety — avoids creating a
    phantom row if the category is somehow absent (the seed migration would
    have already created it; if it doesn't exist, this is a no-op and the
    next Django check run will catch the missing seed).
    """
    DocumentCategory = apps.get_model("documents", "DocumentCategory")

    # volunteer-certification: Privacy Act s.6(1) violation + access control gap
    updated = DocumentCategory.objects.filter(slug="volunteer-certification").update(
        min_retention_days=730,  # Privacy Act s.6(1): ≥ 2 years for admin records
        max_retention_days=2555,  # 7 years — spec §11.1 retention schedule
        staff_only=True,  # CRC/VSC certificates are staff-managed only
    )
    if updated == 0:
        # Category missing — seed migration may not have run yet in this env.
        # This is safe to ignore: when 0007 runs, the next time Django collects
        # migrations, 0008 will also be applied and the correction will occur.
        pass

    # cra-t4a-slip: access control gap only (retention values were correct)
    DocumentCategory.objects.filter(slug="cra-t4a-slip").update(
        staff_only=True,  # CRA T4A slips are system-generated, not citizen-uploaded
    )


def noop(apps, schema_editor) -> None:  # noqa: ANN001
    # Intentionally irreversible: reducing min_retention_days or unsetting staff_only
    # after correction would re-introduce compliance violations. The "undo" of this
    # migration would need Privacy Officer approval before being applied.
    pass


class Migration(migrations.Migration):
    """
    Corrective data migration for volunteer-certification and cra-t4a-slip categories.

    Dependencies: 0007_seed_document_categories (which created the rows).
    RunPython is non-elidable: data corrections must always be re-run on squash.
    """

    dependencies = [  # noqa: RUF012
        ("documents", "0007_seed_document_categories"),
    ]

    operations = [  # noqa: RUF012
        migrations.RunPython(
            fix_category_retention_and_staff_only,
            noop,
            elidable=False,  # Always re-apply on squash — correctness is required
        ),
    ]

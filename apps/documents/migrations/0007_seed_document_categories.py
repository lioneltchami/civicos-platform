from django.db import migrations

CATEGORIES = [
    {
        "slug": "service-request-evidence",
        "name_en": "Service Request — Supporting Evidence",
        "name_fr": "Demande de service — Pièces justificatives",
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "is_transitory": False,
    },
    {
        "slug": "service-request-decision-letter",
        "name_en": "Service Request — Decision Letter",
        "name_fr": "Demande de service — Lettre de décision",
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "is_transitory": False,
    },
    {
        "slug": "volunteer-application-docs",
        "name_en": "Volunteer Application — Supporting Documents",
        "name_fr": "Candidature bénévole — Documents justificatifs",
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 1825,
        "is_transitory": False,
    },
    {
        "slug": "volunteer-certification",
        "name_en": "Volunteer Certification",
        "name_fr": "Certification bénévole",
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 365,
        "max_retention_days": 1825,
        "is_transitory": False,
    },
    {
        "slug": "cra-t4a-slip",
        "name_en": "CRA T4A Slip",
        "name_fr": "Feuillet T4A ARC",
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 2555,
        "max_retention_days": 2555,
        "is_transitory": False,
    },
    {
        "slug": "donation-receipt-pdf",
        "name_en": "Donation Receipt PDF",
        "name_fr": "Reçu de don PDF",
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 2555,
        "max_retention_days": 2555,
        "is_transitory": False,
    },
    {
        "slug": "pipeda-data-export",
        "name_en": "PIPEDA Data Export Archive",
        "name_fr": "Archive d’exportation de données LPRPDE",  # noqa: RUF001
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 0,
        "max_retention_days": 30,
        "is_transitory": True,
    },
    {
        "slug": "staff-decision-memo",
        "name_en": "Staff Decision Memo",
        "name_fr": "Mémo de décision du personnel",
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 3650,
        "is_transitory": False,
    },
    {
        "slug": "system-generated-report",
        "name_en": "System-Generated Report",
        "name_fr": "Rapport généré par le système",
        "description_en": "",
        "description_fr": "",
        "security_classification": "protected_b",
        "allowed_mime_types": [],
        "max_size_bytes": 0,
        "min_retention_days": 730,
        "max_retention_days": 2555,
        "is_transitory": False,
    },
]


STAFF_ONLY_SLUGS = {"staff-decision-memo", "system-generated-report"}


def seed_document_categories(apps, schema_editor) -> None:  # noqa: ANN001
    DocumentCategory = apps.get_model("documents", "DocumentCategory")
    for data in CATEGORIES:
        staff_only = data["slug"] in STAFF_ONLY_SLUGS
        if staff_only:
            # update_or_create so that existing rows seeded with staff_only=False
            # are corrected to staff_only=True even after the migration first ran.
            DocumentCategory.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "name_en": data["name_en"],
                    "name_fr": data["name_fr"],
                    "description_en": data["description_en"],
                    "description_fr": data["description_fr"],
                    "security_classification": data["security_classification"],
                    "allowed_mime_types": data["allowed_mime_types"],
                    "max_size_bytes": data["max_size_bytes"],
                    "min_retention_days": data["min_retention_days"],
                    "max_retention_days": data["max_retention_days"],
                    "is_transitory": data["is_transitory"],
                    "staff_only": True,  # staff-only: citizens cannot upload to this category
                },
            )
        else:
            DocumentCategory.objects.get_or_create(
                slug=data["slug"],
                defaults={
                    "name_en": data["name_en"],
                    "name_fr": data["name_fr"],
                    "description_en": data["description_en"],
                    "description_fr": data["description_fr"],
                    "security_classification": data["security_classification"],
                    "allowed_mime_types": data["allowed_mime_types"],
                    "max_size_bytes": data["max_size_bytes"],
                    "min_retention_days": data["min_retention_days"],
                    "max_retention_days": data["max_retention_days"],
                    "is_transitory": data["is_transitory"],
                },
            )


def noop(apps, schema_editor) -> None:  # noqa: ANN001
    # Reverse is a no-op: categories may be in use by existing documents.
    pass


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("documents", "0006_add_staff_view_permissions"),
    ]

    operations = [  # noqa: RUF012
        migrations.RunPython(seed_document_categories, noop, elidable=False),
    ]

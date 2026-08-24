"""
Idempotent management command to seed the 5 standard government consent categories.

Usage:
    python manage.py seed_consent_categories
    python manage.py seed_consent_categories --verbosity=2
"""

from django.core.management.base import BaseCommand

from apps.consent.models import ConsentCategory

CATEGORIES = [
    {
        "slug": "service-delivery",
        "name_en": "Service Delivery",
        "name_fr": "Prestation de services",
        "purpose_en": (
            "We collect and use your personal information to deliver the government services "
            "you apply for. This is required for us to process your requests."
        ),
        "purpose_fr": (
            "Nous recueillons et utilisons vos renseignements personnels pour fournir les services "
            "gouvernementaux que vous demandez. Cela est nécessaire pour traiter vos demandes."
        ),
        "lawful_basis": "legal_obligation",
        "is_required": True,
        "sort_order": 1,
    },
    {
        "slug": "account-management",
        "name_en": "Account & Security",
        "name_fr": "Compte et sécurité",
        "purpose_en": (
            "We use your information to manage your account, verify your identity, and protect "
            "your account from unauthorized access."
        ),
        "purpose_fr": (
            "Nous utilisons vos informations pour gérer votre compte, vérifier votre identité "
            "et protéger votre compte contre les accès non autorisés."
        ),
        "lawful_basis": "legal_obligation",
        "is_required": True,
        "sort_order": 2,
    },
    {
        "slug": "email-notifications",
        "name_en": "Email Notifications",
        "name_fr": "Notifications par courriel",
        "purpose_en": (
            "We send you email updates about your service requests, application status changes, "
            "and important account activity."
        ),
        "purpose_fr": (
            "Nous vous envoyons des mises à jour par courriel sur vos demandes de services, "
            "les changements d'état des demandes et l'activité importante du compte."
        ),
        "lawful_basis": "consent",
        "is_required": False,
        "sort_order": 3,
    },
    {
        "slug": "service-improvement",
        "name_en": "Service Improvement Analytics",
        "name_fr": "Analytique pour l'amélioration des services",
        "purpose_en": (
            "With your permission, we collect anonymized usage data to understand how citizens "
            "use our services and identify areas for improvement. No personal information is shared."  # noqa: E501
        ),
        "purpose_fr": (
            "Avec votre permission, nous recueillons des données d'utilisation anonymisées pour "
            "comprendre comment les citoyens utilisent nos services. Aucun renseignement personnel "
            "n'est partagé."
        ),
        "lawful_basis": "consent",
        "is_required": False,
        "sort_order": 4,
    },
    {
        "slug": "third-party-sharing",
        "name_en": "Third-Party Information Sharing",
        "name_fr": "Partage d'informations avec des tiers",
        "purpose_en": (
            "In some cases, we may need to share your information with other government departments "  # noqa: E501
            "or agencies to deliver an integrated service. We will always tell you who receives "
            "your information."
        ),
        "purpose_fr": (
            "Dans certains cas, nous pouvons avoir besoin de partager vos informations avec "
            "d'autres ministères ou organismes gouvernementaux pour fournir un service intégré."
        ),
        "lawful_basis": "consent",
        "is_required": False,
        "sort_order": 5,
    },
]


class Command(BaseCommand):
    help = "Seed the 5 standard government consent categories (idempotent)."

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        verbosity = options.get("verbosity", 1)
        created_count = 0
        updated_count = 0

        for data in CATEGORIES:
            # Use a non-mutating pattern — never pop() from the module-level constant
            # because that would corrupt CATEGORIES on the second call to handle()
            # (e.g. in tests that run the command multiple times in one process).
            slug = data["slug"]
            defaults = {k: v for k, v in data.items() if k != "slug"}
            obj, created = ConsentCategory.objects.get_or_create(
                slug=slug,
                defaults=defaults,
            )
            if created:
                created_count += 1
                if verbosity >= 2:
                    self.stdout.write(self.style.SUCCESS(f"  Created: {slug}"))
            else:
                # Update fields in case they changed
                for field, value in defaults.items():
                    setattr(obj, field, value)
                obj.save()
                updated_count += 1
                if verbosity >= 2:
                    self.stdout.write(f"  Updated: {slug}")

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_consent_categories: {created_count} created, {updated_count} updated."
            )
        )

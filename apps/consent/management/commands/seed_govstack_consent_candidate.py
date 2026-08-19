"""Seed disposable records required by the pinned GovStack Consent API suite.

The command is intentionally fail-closed: it runs only inside a local candidate
container marked with ``GOVSTACK_TEST_TARGET=local``. It creates no production
records and does not make network calls.
"""

from __future__ import annotations

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.consent.models import ConsentCategory, ConsentPolicy
from apps.consent.services import ConsentService

_DATA_AGREEMENT_ID = 1
_DATA_AGREEMENT_DEFAULTS = {
    "slug": "govstack-official-suite-data-agreement",
    "name_en": "CivicOS GovStack Official Suite Data Agreement",
    "name_fr": "Entente de données de la suite officielle GovStack CivicOS",
    "purpose_en": "Synthetic local record used only by the pinned GovStack Consent suite.",
    "purpose_fr": "Enregistrement local synthétique utilisé uniquement par la suite Consent GovStack épinglée.",
    "lawful_basis": "consent",
    "is_required": False,
    "is_active": True,
    "sort_order": 0,
    "version": "1.0.0",
    "data_use": "",
    "dpia": "Local test fixture only.",
    "forgettable": True,
    "controller_name": "CivicOS Local Candidate",
    "controller_url": "https://civicos.example.gov/local-testing",
    "attributes": [],
}


class Command(BaseCommand):
    help = "Seed local-only deterministic records for the pinned GovStack Consent suite."

    def handle(self, *args, **options):
        if os.environ.get("GOVSTACK_TEST_TARGET") != "local":
            raise CommandError(
                "seed_govstack_consent_candidate requires GOVSTACK_TEST_TARGET=local."
            )

        # The policy command is separately idempotent and creates the alias
        # expected by the official smoke path GET /service/policy/1/.
        call_command("seed_consent_policy", verbosity=options.get("verbosity", 1))
        policy = ConsentPolicy.objects.get(harness_alias_id=1)

        category, created = ConsentCategory.objects.update_or_create(
            pk=_DATA_AGREEMENT_ID,
            defaults={**_DATA_AGREEMENT_DEFAULTS, "policy": policy},
        )
        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"seed_govstack_consent_candidate: DataAgreement {category.pk} {action}."
            )
        )

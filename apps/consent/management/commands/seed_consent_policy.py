"""
Idempotent management command to seed exactly one "well-known" ConsentPolicy
for GovStack reference-harness compatibility.

Why this exists
----------------
The upstream GovStackWorkingGroup/bb-consent reference test harness
(test/gherkin/features/smoke.feature) hardcodes:

    GET /service/policy/1/

with no environment-variable override for that literal "1". CivicOS's real
Policy primary key is (and remains) a UUID — a spec-conformant choice, since
GovStack's own Policy.id schema is an opaque string, not necessarily numeric.
A plain UUID-only lookup can never satisfy the harness's hardcoded "1", so
this command seeds exactly one Policy carrying
``ConsentPolicy.harness_alias_id = 1``, which
``ServicePolicyDetailView._resolve_policy_pk_or_alias()`` falls back to when
a path segment isn't a valid UUID but is a plain positive integer.

This is harness-compatibility scaffolding ONLY — it does not change the real
Policy PK scheme, and no other Policy should ever carry a harness_alias_id.

Usage:
    python manage.py seed_consent_policy
"""
from django.core.management.base import BaseCommand

from apps.consent.models import ConsentPolicy
from apps.consent.services import ConsentService

_HARNESS_POLICY_ALIAS = 1
_HARNESS_POLICY_DEFAULTS = {
    "name": "CivicOS GovStack Harness-Compatibility Policy",
    "description": (
        "Seeded solely so the upstream GovStackWorkingGroup/bb-consent reference "
        "harness's hardcoded GET /service/policy/1/ smoke test has a real Policy "
        "to find. Not a production privacy policy — see ConsentPolicy.harness_alias_id "
        "and ServicePolicyDetailView for the compatibility mechanism this backs."
    ),
    "version": "1.0",
    "url": "https://civicos.example.gov/privacy/harness-compat-policy",
    "jurisdiction": "Canada",
    "is_active": True,
}


class Command(BaseCommand):
    help = "Seed the well-known GovStack harness-compatibility ConsentPolicy (idempotent)."

    def handle(self, *args, **options):
        verbosity = options.get("verbosity", 1)

        existing = ConsentPolicy.objects.filter(harness_alias_id=_HARNESS_POLICY_ALIAS).first()
        if existing is not None:
            if verbosity >= 2:
                self.stdout.write(f"  Already exists: Policy {existing.pk} (harness_alias_id=1)")
            self.stdout.write(
                self.style.SUCCESS("seed_consent_policy: 0 created, 1 already existed.")
            )
            return

        data = dict(_HARNESS_POLICY_DEFAULTS)
        data["harness_alias_id"] = _HARNESS_POLICY_ALIAS
        policy, revision = ConsentService.create_policy(data, actor=None)

        if verbosity >= 2:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Created: Policy {policy.pk} (harness_alias_id=1), "
                    f"revision {revision.pk}"
                )
            )
        self.stdout.write(
            self.style.SUCCESS("seed_consent_policy: 1 created, 0 already existed.")
        )

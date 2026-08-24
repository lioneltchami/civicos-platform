"""Regression tests for the local pinned GovStack Consent candidate fixture."""

from __future__ import annotations

from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.consent.models import ConsentCategory, ConsentPolicy


class GovStackConsentCandidateFixtureTests(TestCase):
    def test_fixture_command_refuses_any_non_local_target(self):
        with patch.dict("os.environ", {"GOVSTACK_TEST_TARGET": "staging"}, clear=False):
            with self.assertRaises(CommandError):
                call_command("seed_govstack_consent_candidate")

    def test_fixture_command_seeds_official_suite_records_idempotently(self):
        with patch.dict("os.environ", {"GOVSTACK_TEST_TARGET": "local"}, clear=False):
            call_command("seed_govstack_consent_candidate")
            call_command("seed_govstack_consent_candidate")

        policy = ConsentPolicy.objects.get(harness_alias_id=1)
        agreement = ConsentCategory.objects.get(pk=1)
        self.assertEqual(agreement.policy, policy)
        self.assertEqual(agreement.slug, "govstack-official-suite-data-agreement")
        self.assertTrue(agreement.is_active)
        self.assertEqual(ConsentCategory.objects.filter(pk=1).count(), 1)

from unittest.mock import patch

from django.test import TestCase

from apps.payments.govstack_models import ProviderRegistration
from apps.payments.provider_registry import (
    FACTORY_DETERMINISTIC,
    SCHEMA_VERSION,
    ProviderRegistryError,
    resolve_provider,
)
from apps.payments.providers.deterministic import DeterministicProvider


class ProviderRegistryTests(TestCase):
    tenant_id = "registry-tenant"
    operation = "g2p_bulk_instruction"

    def registration(self, **changes):
        values = {
            "tenant_id": self.tenant_id,
            "operation": self.operation,
            "provider_name": "DeterministicProvider",
            "factory_key": FACTORY_DETERMINISTIC,
            "schema_version": SCHEMA_VERSION,
            "configuration_version": "allowlisted-registry-v1",
            "configuration": {"outcomes": [], "statuses": {}},
            "audit_metadata": {"test": True},
            "active": True,
        }
        values.update(changes)
        return ProviderRegistration.objects.create(**values)

    def test_fresh_worker_resolution_uses_allowlisted_configuration(self):
        self.registration()
        provider = resolve_provider(tenant_id=self.tenant_id, operation=self.operation)
        self.assertIsInstance(provider, DeterministicProvider)

    def test_unknown_and_malformed_configuration_fail_before_provider_call(self):
        cases = [
            {"factory_key": "unknown.v1"},
            {"configuration": {"adapter_path": "os.system"}},
            {"configuration": {"outcomes": ["not-a-result"], "statuses": {}}},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.registration(**changes)
                with patch(
                    "apps.payments.providers.deterministic.DeterministicProvider.submit"
                ) as submit:
                    with self.assertRaises(ProviderRegistryError):
                        resolve_provider(tenant_id=self.tenant_id, operation=self.operation)
                    submit.assert_not_called()
                ProviderRegistration.objects.all().delete()

    def test_inactive_and_wrong_scope_fail_closed(self):
        self.registration(active=False)
        with self.assertRaises(ProviderRegistryError):
            resolve_provider(tenant_id=self.tenant_id, operation=self.operation)
        ProviderRegistration.objects.all().delete()
        self.registration()
        with self.assertRaises(ProviderRegistryError):
            resolve_provider(tenant_id="wrong-tenant", operation=self.operation)

import pytest

from apps.consent.integration_boundary import (
    ConsentIntegrationBoundary,
    ConsentIntegrationUnavailableError,
    IntegrationMessage,
)


def test_missing_external_configuration_fails_closed_without_network_call():
    boundary = ConsentIntegrationBoundary()
    with pytest.raises(ConsentIntegrationUnavailableError):
        boundary.publish(
            IntegrationMessage("record.changed", "subject-1", "record-1", "idem-1", {})
        )


def test_message_requires_subject_and_idempotency_key():
    boundary = ConsentIntegrationBoundary("https://identity.invalid", "https://im.invalid")
    with pytest.raises(ValueError):
        boundary.publish(IntegrationMessage("record.changed", "", "record-1", "idem-1", {}))
    with pytest.raises(ValueError):
        boundary.publish(IntegrationMessage("record.changed", "subject-1", "record-1", "", {}))


def test_configured_stub_does_not_claim_transport_support():
    boundary = ConsentIntegrationBoundary("https://identity.invalid", "https://im.invalid")
    with pytest.raises(NotImplementedError):
        boundary.publish(
            IntegrationMessage("record.changed", "subject-1", "record-1", "idem-1", {})
        )

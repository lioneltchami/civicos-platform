"""Fail-closed boundary for unavailable external Identity and Information Mediator services."""
from dataclasses import dataclass


class ConsentIntegrationUnavailableError(RuntimeError):
    """Raised when external Consent dependencies are not explicitly configured."""


@dataclass(frozen=True)
class IntegrationMessage:
    event_type: str
    subject_id: str
    resource_id: str
    idempotency_key: str
    payload: dict[str, object]


class ConsentIntegrationBoundary:
    def __init__(
        self,
        identity_endpoint: str | None = None,
        information_mediator_endpoint: str | None = None,
    ) -> None:
        self.identity_endpoint = identity_endpoint
        self.information_mediator_endpoint = information_mediator_endpoint

    def require_configured(self) -> None:
        if not self.identity_endpoint or not self.information_mediator_endpoint:
            raise ConsentIntegrationUnavailableError(
                "Identity and Information Mediator endpoints are required; "
                "no outbound request was made"
            )

    def publish(self, message: IntegrationMessage) -> None:
        if not message.subject_id or not message.idempotency_key:
            raise ValueError("subject_id and idempotency_key are required")
        self.require_configured()
        raise NotImplementedError(
            "External transport is deployment-owned and not implemented by this bounded stub"
        )

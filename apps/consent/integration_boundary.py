from dataclasses import dataclass


class ConsentIntegrationUnavailableError(RuntimeError):
    """Raised before transport when external Consent dependencies are unavailable."""


@dataclass(frozen=True)
class IntegrationMessage:
    event_type: str
    subject_id: str
    resource_id: str
    idempotency_key: str
    payload: dict[str, object]

    def validate(self) -> None:
        fields = (self.event_type, self.subject_id, self.resource_id, self.idempotency_key)
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise ValueError(
                "event_type, subject_id, resource_id, and idempotency_key are required"
            )
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a JSON object")


class ConsentIntegrationBoundary:
    def __init__(
        self, identity_endpoint: str | None = None, information_mediator_endpoint: str | None = None
    ) -> None:
        self.identity_endpoint = identity_endpoint
        self.information_mediator_endpoint = information_mediator_endpoint

    def require_configured(self) -> None:
        if not all(
            isinstance(v, str) and v.strip()
            for v in (self.identity_endpoint, self.information_mediator_endpoint)
        ):
            raise ConsentIntegrationUnavailableError(
                "Identity and Information Mediator endpoints are required; no outbound request was made"  # noqa: E501
            )

    def publish(self, message: IntegrationMessage) -> None:
        message.validate()
        self.require_configured()
        raise NotImplementedError(
            "External transport is deployment-owned and not implemented by this bounded stub"
        )

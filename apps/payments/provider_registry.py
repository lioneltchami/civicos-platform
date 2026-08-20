"""Allowlisted worker-only provider factory registry for Item 02.

Legacy ProviderRegistration rows intentionally fail closed until operators migrate
an approved factory key and schema version through controlled configuration.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from django.utils import timezone

from .govstack_models import ProviderRegistration
from .govstack_provider import PaymentProvider

SCHEMA_VERSION = 1
FACTORY_DETERMINISTIC = "deterministic.v1"


class ProviderRegistryError(RuntimeError):
    """Raised before a provider adapter is instantiated or invoked."""


class UnknownProviderFactory(ProviderRegistryError):
    pass


class MalformedProviderConfiguration(ProviderRegistryError):
    pass


def _json_object(value: Any) -> dict[str, Any]:
    try:
        normalized = json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError) as exc:
        raise MalformedProviderConfiguration("provider configuration is not JSON-safe") from exc
    if not isinstance(normalized, dict):
        raise MalformedProviderConfiguration("provider configuration must be an object")
    return normalized


def _deterministic_configuration(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) - {"outcomes", "statuses"}:
        raise MalformedProviderConfiguration("deterministic configuration has unsupported keys")
    outcomes = value.get("outcomes", [])
    statuses = value.get("statuses", {})
    if not isinstance(outcomes, list) or not isinstance(statuses, dict):
        raise MalformedProviderConfiguration("deterministic configuration has invalid containers")
    if any(not isinstance(item, dict) for item in outcomes) or any(not isinstance(item, dict) for item in statuses.values()):
        raise MalformedProviderConfiguration("deterministic results must be objects")
    return {"outcomes": outcomes, "statuses": statuses}


def _deterministic_factory(*, configuration: Mapping[str, Any]) -> PaymentProvider:
    from .provider_runtime import ProviderRuntime
    from .providers.deterministic import DeterministicProvider

    config = _deterministic_configuration(configuration)
    return DeterministicProvider(
        outcomes=[ProviderRuntime._deserialize_result(item) for item in config["outcomes"]],
        statuses={key: ProviderRuntime._deserialize_result(item) for key, item in config["statuses"].items()},
    )


FACTORIES: dict[str, tuple[int, Callable[..., PaymentProvider], Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    FACTORY_DETERMINISTIC: (SCHEMA_VERSION, _deterministic_factory, _deterministic_configuration),
}


def resolve_provider(*, tenant_id: str, operation: str) -> PaymentProvider:
    """Resolve exactly one active, in-window, allowlisted worker adapter."""
    if not tenant_id or not operation:
        raise ProviderRegistryError("provider scope is blank")

    registrations = list(ProviderRegistration.objects.filter(tenant_id=tenant_id, operation=operation, active=True))
    now = timezone.now()
    registrations = [
        item for item in registrations
        if (item.active_from is None or item.active_from <= now)
        and (item.active_until is None or item.active_until > now)
    ]
    if len(registrations) != 1:
        raise ProviderRegistryError("provider registration is missing, expired, or ambiguous")

    registration = registrations[0]
    factory_spec = FACTORIES.get(registration.factory_key)
    if factory_spec is None or registration.schema_version != factory_spec[0]:
        raise UnknownProviderFactory("provider factory is not allowlisted")

    configuration = _json_object(registration.configuration)
    validated = factory_spec[2](configuration)
    try:
        provider = factory_spec[1](configuration=validated)
    except Exception as exc:
        raise ProviderRegistryError("provider factory failed") from exc
    if not isinstance(provider, PaymentProvider):
        raise ProviderRegistryError("provider factory returned invalid adapter")
    return provider

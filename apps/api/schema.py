"""
OpenAPI schema customizations for CivicOS API.

Extends drf-spectacular's AutoSchema to add consistent security schemes,
error response schemas, and tag groupings for the CivicOS API.
"""

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import build_parameter_type
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema  # noqa: F401


class GovStackAutoSchema(AutoSchema):
    """Add the BB credential pair required by GovStack authenticators."""

    def _get_parameters(self):
        parameters = super()._get_parameters()
        authentication_classes = getattr(self.view, "authentication_classes", ())
        target_names = {"GovStackSchedulerAuth", "GovStackCitizenAuth"}
        if any(cls.__name__ in target_names for cls in authentication_classes):
            existing = {parameter.get("name") for parameter in parameters}
            required = (
                OpenApiParameter(
                    "requestor_id",
                    OpenApiParameter.QUERY,
                    str,
                    required=True,
                    description="Registered GovStack BB requestor identifier.",
                ),
                OpenApiParameter(
                    "request_token",
                    OpenApiParameter.QUERY,
                    str,
                    required=True,
                    description=(
                        "Registered GovStack BB request token. Both requestor_id "
                        "and request_token are required."
                    ),
                ),
            )
            parameters.extend(
                build_parameter_type(
                    parameter.name,
                    {"type": "string"},
                    "query",
                    required=True,
                    description=parameter.description,
                )
                for parameter in required
                if parameter.name not in existing
            )
        return parameters


def preprocess_include_consent_endpoints(endpoints, **kwargs):
    """
    Limit a generated schema to the GovStack consent submission surface.

    drf-spectacular's SCHEMA_PATH_PREFIX helps with path/tag handling, but it
    does not filter unrelated endpoints out of the schema. For submission-facing
    docs we need a real endpoint filter so reviewers see the GovStack-facing
    consent evidence rather than the whole CivicOS platform or CivicOS-only
    consent extensions.
    """
    allowed_prefixes = (
        "/api/v1/consent/config/",
        "/api/v1/consent/service/",
        "/api/v1/consent/audit/",
    )
    excluded_paths = {
        "/api/v1/consent/config/webhook/{webhook_id}/payload/",
        "/api/v1/consent/audit/consent-log/",
    }

    return [
        endpoint
        for endpoint in endpoints
        if endpoint[0].startswith(allowed_prefixes) and endpoint[0] not in excluded_paths
    ]


class CivicOSTokenAuthScheme(OpenApiAuthenticationExtension):
    """Register CivicOSTokenAuthentication in the OpenAPI security schemes."""

    target_class = "apps.api.authentication.CivicOSTokenAuthentication"
    name = "TokenAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Token-based authentication. Format: `Token <token>`",
        }


class GovStackSchedulerAuthScheme(OpenApiAuthenticationExtension):
    """Describe GovStackSchedulerAuth's requestor and token query credentials."""

    target_class = "apps.appointments.govstack_auth.GovStackSchedulerAuth"
    name = "GovStackSchedulerAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, str]:
        return {
            "type": "apiKey",
            "in": "query",
            "name": "request_token",
            "description": (
                "GovStack Scheduler credential. Supply both non-empty "
                "requestor_id and request_token query parameters."
            ),
        }


class GovStackCitizenAuthScheme(OpenApiAuthenticationExtension):
    """Describe GovStackCitizenAuth's GovStack query credential and optional JWT."""

    target_class = "apps.appointments.govstack_auth.GovStackCitizenAuth"
    name = "GovStackCitizenAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, str]:
        return {
            "type": "apiKey",
            "in": "query",
            "name": "request_token",
            "description": (
                "GovStack Scheduler credential. Supply both non-empty "
                "requestor_id and request_token query parameters. The BB pair is "
                "mandatory; when supplied, an Authorization Bearer JWT is validated "
                "and may affect subscriber scope."
            ),
        }


# Reusable error response schema for extend_schema decorators
ERROR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "detail": {"type": "string"},
                "status": {"type": "integer"},
            },
        }
    },
}

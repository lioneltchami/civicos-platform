"""
OpenAPI schema customizations for CivicOS API.

Extends drf-spectacular's AutoSchema to add consistent security schemes,
error response schemas, and tag groupings for the CivicOS API.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiExample, extend_schema  # noqa: F401


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
        if endpoint[0].startswith(allowed_prefixes)
        and endpoint[0] not in excluded_paths
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

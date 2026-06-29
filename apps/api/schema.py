"""
OpenAPI schema customizations for Govstack API.

Extends drf-spectacular's AutoSchema to add consistent security schemes,
error response schemas, and tag groupings for the Govstack API.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiExample, extend_schema  # noqa: F401


class GovstackTokenAuthScheme(OpenApiAuthenticationExtension):
    """Register GovstackTokenAuthentication in the OpenAPI security schemes."""

    target_class = "apps.api.authentication.GovstackTokenAuthentication"
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

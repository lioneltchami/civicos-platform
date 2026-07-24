"""
Top-level URL configuration for the CivicOS API (v1).

Include this module in the project's root urls.py under the "api/v1/" prefix:

    path("api/v1/", include("apps.api.urls", namespace="api-v1")),
"""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from apps.api.throttling import TokenObtainThrottle, TokenRefreshThrottle

app_name = "api-v1"

urlpatterns = [
    # ------------------------------------------------------------------
    # JWT authentication endpoints
    # ------------------------------------------------------------------
    path(
        "auth/token/",
        TokenObtainPairView.as_view(throttle_classes=[TokenObtainThrottle]),
        name="token-obtain",
    ),
    path(
        "auth/token/refresh/",
        TokenRefreshView.as_view(throttle_classes=[TokenRefreshThrottle]),
        name="token-refresh",
    ),
    path(
        "auth/token/verify/",
        TokenVerifyView.as_view(),
        name="token-verify",
    ),
    # ------------------------------------------------------------------
    # OpenAPI / Swagger docs (drf-spectacular)
    # ------------------------------------------------------------------
    path(
        "schema/",
        SpectacularAPIView.as_view(),
        name="schema",
    ),
    path(
        "consent/schema/",
        SpectacularAPIView.as_view(
            custom_settings={
                "TITLE": "CivicOS Consent API",
                "DESCRIPTION": (
                    "GovStack-facing OpenAPI surface for the CivicOS Consent "
                    "Building Block submission."
                ),
                "SCHEMA_PATH_PREFIX": r"^/api/v1/consent/",
                "SCHEMA_PATH_PREFIX_TRIM": False,
                "PREPROCESSING_HOOKS": [
                    "apps.api.schema.preprocess_include_consent_endpoints",
                ],
            }
        ),
        name="consent-schema",
    ),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="api-v1:schema"),
        name="docs",
    ),
    path(
        "consent/docs/",
        SpectacularSwaggerView.as_view(url_name="api-v1:consent-schema"),
        name="consent-docs",
    ),
    # ------------------------------------------------------------------
    # Building blocks (alphabetical: consent, documents, notifications,
    #                  portal, volunteers, workflows)
    # ------------------------------------------------------------------
    path("consent/", include("apps.consent.api_urls", namespace="consent")),
    # GovStack Consent BB v1.3.0 — /config/, /service/, /audit/ namespaces
    path("consent/", include("apps.consent.govstack_urls")),
    # Document Management BB — DRF REST API (spec §18, Wave 8)
    path("documents/", include("apps.api.documents.urls")),
    path("notifications/", include("apps.api.notifications.urls")),
    path("portal/", include("apps.api.portal.urls")),
    path("volunteers/", include("apps.api.volunteers.urls", namespace="volunteers")),
    path("workflows/", include("apps.api.workflows.urls")),
]

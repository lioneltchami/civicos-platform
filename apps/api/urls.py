"""
Top-level URL configuration for the Govstack API (v1).

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

app_name = "api-v1"

urlpatterns = [
    # ------------------------------------------------------------------
    # JWT authentication endpoints
    # ------------------------------------------------------------------
    path(
        "auth/token/",
        TokenObtainPairView.as_view(),
        name="token-obtain",
    ),
    path(
        "auth/token/refresh/",
        TokenRefreshView.as_view(),
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
        "docs/",
        SpectacularSwaggerView.as_view(url_name="api-v1:schema"),
        name="docs",
    ),
    # ------------------------------------------------------------------
    # Building blocks
    # ------------------------------------------------------------------
    path("portal/", include("apps.api.portal.urls")),
    path("notifications/", include("apps.api.notifications.urls")),
    path("workflows/", include("apps.api.workflows.urls")),
]

"""
URL configuration for Govstack.

Structure:
  /                     → Wagtail CMS (public pages)
  /cms/                 → Wagtail admin
  /django-admin/        → Django admin (staff only, restricted)
  /accounts/            → django-allauth (registration, login, email verification)
  /two-factor/          → MFA login flow
  /portal/              → Authenticated citizen portal
  /api/v1/              → REST API (future)
  /__debug__/           → Django Debug Toolbar (development only)
"""

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
import two_factor.urls as two_factor_urls
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.documents import urls as wagtaildocs_urls

if isinstance(two_factor_urls.urlpatterns, tuple):
    two_factor_urls.urlpatterns, two_factor_app_name = two_factor_urls.urlpatterns
    two_factor_urls.app_name = two_factor_app_name

urlpatterns = [
    # Wagtail admin
    path("cms/", include(wagtailadmin_urls)),
    # Django admin — behind a non-obvious path
    path("django-admin/", admin.site.urls),
    # Document downloads (protected)
    path("documents/", include(wagtaildocs_urls)),
    # MFA / two-factor auth (must come before allauth)
    path("account/two-factor/", include(two_factor_urls, namespace="two_factor")),
    # Authentication (allauth)
    path("account/", include("allauth.urls")),
    # Health check endpoint for load balancers and Kubernetes probes
    path("health/", include("apps.core.urls.health")),
]

# Internationalised URL patterns — wrapped in language prefix (/en/, /fr/)
urlpatterns += i18n_patterns(
    # Citizen portal
    path("portal/", include("apps.portal.urls", namespace="portal")),
    # Citizen notification inbox
    path("notifications/", include("apps.notifications.urls", namespace="notifications")),
    # Citizen account (auth_extension building block)
    path("account/", include("apps.auth_extension.urls", namespace="auth_extension")),
    # Public-facing pages (Wagtail CMS) — must be last
    path("", include(wagtail_urls)),
    prefix_default_language=False,  # /en/ not required; /fr/ prefix for French
)

# ---------------------------------------------------------------------------
# Development extras
# ---------------------------------------------------------------------------

if settings.DEBUG:
    urlpatterns = [path("__debug__/", include("debug_toolbar.urls")), *urlpatterns]

    # Serve media files via Django in development
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

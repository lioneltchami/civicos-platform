"""
URL configuration for Govstack.

Structure:
  /                     → Wagtail CMS (public pages)
  /cms/                 → Wagtail admin
  /django-admin/        → Django admin (staff only, restricted)
  /accounts/            → django-allauth (registration, login, email verification)
  /two-factor/          → MFA login flow
  /portal/              → Authenticated citizen portal
  /api/v1/              → REST API (notifications, workflows, portal)
  /consent/             → Citizen consent & privacy dashboard (PIPEDA)
  /backoffice/          → Staff back-office (StaffRequiredMixin enforced at view level)
  /__debug__/           → Django Debug Toolbar (development only)
"""

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from django_otp.admin import OTPAdminSite
from two_factor.urls import urlpatterns as two_factor_urlpatterns
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.documents import urls as wagtaildocs_urls

# ---------------------------------------------------------------------------
# Security: enforce OTP / MFA on the Django admin site.
# Without this, the standard AdminSite authenticates via its own login flow
# and completely bypasses two-factor verification — allowing any staff user
# with only a password to reach donor PII, Stripe keys, and audit data.
# OTPAdminSite.has_permission() requires request.user.is_verified() (i.e. the
# user must have completed a second factor via django-two-factor-auth before
# being granted access). This is the standard pattern recommended by django-otp.
# ---------------------------------------------------------------------------
admin.site.__class__ = OTPAdminSite

urlpatterns = [
    # Wagtail admin
    path("cms/", include(wagtailadmin_urls)),
    # Django admin — behind a non-obvious path; OTP enforcement applied above
    path("django-admin/", admin.site.urls),
    # Document downloads (protected)
    path("documents/", include(wagtaildocs_urls)),
    # MFA / two-factor auth (must come before allauth).
    # two_factor.urls.urlpatterns is a (list, 'two_factor') 2-tuple where every
    # internal pattern already begins with "account/" (e.g. "account/login/",
    # "account/two_factor/setup/"). Mount at "" so reverse('two_factor:login')
    # resolves to /account/login/ — matching allauth's path — rather than
    # doubling the prefix to /account/two-factor/account/login/. Because this
    # entry appears before the allauth include, two_factor's login view wins
    # for /account/login/, enforcing MFA for all logins.
    path("", include(two_factor_urlpatterns)),
    # Authentication (allauth)
    path("account/", include("allauth.urls")),
    # Health check endpoint for load balancers and Kubernetes probes
    path("health/", include("apps.core.urls.health")),
    # REST API v1 — language-prefix-free; authentication via JWT / Token
    path("api/v1/", include("apps.api.urls", namespace="api-v1")),
    # Back-office — staff-only; access control enforced via StaffRequiredMixin on every view.
    # Mounted outside i18n_patterns: staff tools do not require language prefixes.
    path("backoffice/", include("apps.backoffice.urls", namespace="backoffice")),
    # Payments building block — gateway webhooks and payment flows (no language prefix)
    path("payments/", include("apps.payments.urls", namespace="payments")),
    # Donations public-facing flows
    path("donate/", include("apps.payments.donation_urls", namespace="donate")),
    # Donor payments portal — authenticated view of giving history, receipts, recurring plans
    path("donate/portal/", include("apps.payments.portal_urls", namespace="donor_portal")),
    # Django i18n — provides the {% url 'set_language' %} view used in base.html
    # Must be a non-i18n (language-prefix-free) URL so the language switcher works
    # regardless of which language is currently active.
    path("i18n/", include("django.conf.urls.i18n")),
]

# Internationalised URL patterns — wrapped in language prefix (/en/, /fr/)
urlpatterns += i18n_patterns(
    # Redirect allauth's bare login URL to the MFA-enforcing two_factor login.
    # Must come before the allauth include so it intercepts /en/account/login/
    # and /fr/account/login/ before allauth can serve a single-factor form.
    path(
        "account/login/",
        RedirectView.as_view(pattern_name="two_factor:login", permanent=True),
        name="account_login_redirect",
    ),
    # Citizen portal
    path("portal/", include("apps.portal.urls", namespace="portal")),
    # Citizen notification inbox
    path("notifications/", include("apps.notifications.urls", namespace="notifications")),
    # Forms building block — staff submission management
    path("forms/", include("apps.forms.urls", namespace="forms")),
    # Staff workflow queue (workflows building block)
    path("workflows/", include("apps.workflows.urls", namespace="workflows")),
    # Citizen account (auth_extension building block)
    path("account/", include("apps.auth_extension.urls", namespace="auth_extension")),
    # Consent & Privacy building block
    path("consent/", include("apps.consent.urls", namespace="consent")),
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

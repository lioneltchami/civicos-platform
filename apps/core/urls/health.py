"""
Health check endpoint for load balancers and Kubernetes liveness/readiness probes.

GET /health/          → 200 OK with JSON status (checks DB + cache + Stripe)
GET /health/live/     → 200 OK if process is running (no DB check; Kubernetes liveness)
GET /health/ready/    → 200 OK if all dependencies are reachable (readiness probe)

Stripe connectivity is reported as "ok" or "degraded" — a Stripe outage alone does
not return 503, because the application itself is still healthy. Load balancers will
continue routing; alerting on the "degraded" field should be configured separately.
"""

import stripe
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.urls import path
from django.db import transaction
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


def _check_stripe(api_key: str) -> bool:
    """Return True if the Stripe API is reachable, False on network/API errors.

    Uses a minimal read-only call (list 1 customer) with the provided key.
    An AuthenticationError means the key is invalid but the API is reachable —
    still returns True so a misconfigured key doesn't mask real connectivity.
    """
    try:
        stripe.Customer.list(limit=1, api_key=api_key)
        return True
    except stripe.error.AuthenticationError:
        # Key invalid but API is reachable — connectivity is fine
        return True
    except (stripe.error.APIConnectionError, stripe.error.APIError):
        return False
    except Exception:
        return False


@never_cache
@require_GET
def liveness(request):
    """Kubernetes liveness probe — just confirms the process is alive."""
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
@transaction.non_atomic_requests
def readiness(request):
    """
    Kubernetes readiness probe — confirms all dependencies are reachable.
    Returns 503 if DB or cache is unavailable so the load balancer stops
    routing traffic to this instance.

    Stripe is checked as a degraded signal only: a Stripe outage does not
    make the application itself unavailable, but the "degraded" value in the
    response payload should trigger an alert in your monitoring system.
    """
    checks = {}
    status_code = 200

    # Database check
    try:
        connection.ensure_connection()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        status_code = 503

    # Cache check
    try:
        cache.set("health_check", "ok", timeout=5)
        val = cache.get("health_check")
        checks["cache"] = "ok" if val == "ok" else "error: unexpected value"
        if val != "ok":
            status_code = 503
    except Exception as exc:
        checks["cache"] = f"error: {exc}"
        status_code = 503

    # Stripe connectivity check (degraded — Stripe outage != app down)
    stripe_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if stripe_key:
        checks["stripe"] = "ok" if _check_stripe(stripe_key) else "degraded"
    else:
        checks["stripe"] = "unconfigured"

    return JsonResponse({"status": "ok" if status_code == 200 else "degraded", "checks": checks}, status=status_code)


urlpatterns = [
    path("", readiness, name="health"),
    path("live/", liveness, name="health-live"),
    path("ready/", readiness, name="health-ready"),
]

"""
Health check endpoint for load balancers and Kubernetes liveness/readiness probes.

GET /health/          → 200 OK with JSON status (checks DB + cache)
GET /health/live/     → 200 OK if process is running (no DB check; Kubernetes liveness)
GET /health/ready/    → 200 OK if all dependencies are reachable (readiness probe)
"""

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def liveness(request):
    """Kubernetes liveness probe — just confirms the process is alive."""
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def readiness(request):
    """
    Kubernetes readiness probe — confirms all dependencies are reachable.
    Returns 503 if any dependency is unavailable so the load balancer stops
    routing traffic to this instance.
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

    return JsonResponse({"status": "ok" if status_code == 200 else "degraded", "checks": checks}, status=status_code)


urlpatterns = [
    path("", readiness, name="health"),
    path("live/", liveness, name="health-live"),
    path("ready/", readiness, name="health-ready"),
]

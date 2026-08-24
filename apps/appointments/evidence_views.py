from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .services.local_evidence import LocalSchedulerStatusService


@require_GET
def scheduler_status(request):  # noqa: ANN001, ANN201
    """Return bounded non-PII status for the authenticated persisted owner only."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=401)
    owner_id = getattr(user, "scheduler_owner_key", None) or getattr(user, "owner_key", None)
    tenant_id = getattr(user, "scheduler_tenant_id", None) or getattr(user, "tenant_id", None)
    if not owner_id or not tenant_id:
        return JsonResponse({"detail": "authorized Scheduler ownership required"}, status=403)
    try:
        limit = int(request.GET.get("limit", "100"))
        status, rows = LocalSchedulerStatusService().from_database(
            owner_id=owner_id, tenant_id=tenant_id, limit=limit
        )
    except (ValueError, PermissionError):
        return JsonResponse({"detail": "invalid bounded query"}, status=400)
    return JsonResponse(
        {"owner": owner_id, "tenant": tenant_id, "status": status.__dict__, "deliveries": rows}
    )

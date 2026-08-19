"""Platform persistence helpers for Item 02; provider execution is intentionally out of scope."""
from __future__ import annotations

import hashlib
import json
from typing import Any
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.views import APIView
from apps.payments.govstack_auth import IsTrustedPayerFI
from apps.payments.govstack_models import IdempotencyLedger, PaymentReconciliation


def canonical_fingerprint(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class IdempotencyService:
    """Migration-backed reservation/replay with tenant, method, path and key scope."""
    @staticmethod
    def reserve(*, tenant_id: str, method: str, path: str, key: str, payload: Any):
        fingerprint = canonical_fingerprint(payload)
        normalized_method = method.upper()
        with transaction.atomic():
            try:
                # Isolate a uniqueness collision in a savepoint so the outer
                # transaction remains usable to lock and inspect its winner.
                with transaction.atomic():
                    row = IdempotencyLedger.objects.create(
                        tenant_id=tenant_id,
                        method=normalized_method,
                        path=path,
                        key=key,
                        fingerprint=fingerprint,
                    )
                return row, True
            except IntegrityError:
                row = IdempotencyLedger.objects.select_for_update().get(
                    tenant_id=tenant_id,
                    method=normalized_method,
                    path=path,
                    key=key,
                )
                if row.fingerprint != fingerprint:
                    raise ValueError("idempotency key was reused with a different payload")
                return row, False

    @staticmethod
    def complete(row, *, status_code: int, body: dict, headers: dict | None = None):
        row.status_code = status_code
        row.body = body
        row.headers = headers or {}
        row.state = IdempotencyLedger.STATE_COMPLETE
        row.completed_at = timezone.now()
        row.save(update_fields=["status_code", "body", "headers", "state", "completed_at", "updated_at"])

    @staticmethod
    def replay(row):
        return JsonResponse(row.body, status=row.status_code, headers=row.headers)


class ReconciliationReportView(APIView):
    permission_classes = [IsTrustedPayerFI]
    page_size = 50

    def get(self, request):
        tenant_id = (request.headers.get("X-Platform-TenantId") or request.headers.get("Platform-TenantId") or "").strip()
        if not tenant_id or len(tenant_id) > 100:
            return JsonResponse({"error": "tenant authorization required"}, status=403)
        qs = PaymentReconciliation.objects.filter(attempt__tenant_id=tenant_id).select_related("attempt").order_by("-created_at")
        try:
            limit = min(max(int(request.GET.get("limit", self.page_size)), 1), self.page_size)
        except (TypeError, ValueError):
            limit = self.page_size
        offset = max(int(request.GET.get("offset", 0) or 0), 0)
        rows = list(qs[offset:offset + limit + 1])
        next_offset = offset + limit if len(rows) > limit else None
        data = [{"id": str(r.id), "attempt_id": str(r.attempt_id), "classification": r.status, "internal_status": r.internal_status, "provider_status": r.provider_status, "created_at": r.created_at.isoformat()} for r in rows[:limit]]
        return JsonResponse({"results": data, "next_offset": next_offset})

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from apps.payments.payment_command_boundary import PaymentScope, PaymentScopeDenied
from apps.payments.platform_scope import ReconciliationReportView


class AuthorisedReconciliationReadTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def request(self, **headers):
        return self.factory.get(
            "/govstack/payments/reconciliation/report",
            data={"limit": 1, "offset": 0},
            **{f"HTTP_{key.upper().replace('-', '_')}": value for key, value in headers.items()},
        )

    @patch("apps.payments.platform_scope.PaymentReconciliation.objects")
    @patch("apps.payments.platform_scope.resolve_registered_bb_scope")
    def test_authorised_same_tenant_read_uses_scope(self, resolve, manager):
        resolve.return_value = PaymentScope(caller_bb_id="BB-A", tenant_id="TENANT-A")
        row = SimpleNamespace(
            id="report-1",
            attempt_id="attempt-1",
            status="accepted",
            internal_status="settled",
            provider_status="ok",
            created_at=SimpleNamespace(isoformat=lambda: "2026-08-20T00:00:00+00:00"),
        )
        manager.filter.return_value.select_related.return_value.order_by.return_value.__getitem__.side_effect = [  # noqa: E501
            [row],
            [],
        ]

        response = ReconciliationReportView.as_view()(
            self.request(**{"X-Platform-TenantId": "TENANT-A"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["results"][0]["classification"], "accepted")
        manager.filter.assert_called_once_with(attempt__tenant_id="TENANT-A")

    @patch("apps.payments.platform_scope.PaymentReconciliation.objects")
    @patch("apps.payments.platform_scope.resolve_registered_bb_scope")
    def test_denied_scope_has_no_query_or_cross_tenant_leakage(self, resolve, manager):
        resolve.side_effect = PaymentScopeDenied("not authorised")
        response = ReconciliationReportView.as_view()(
            self.request(**{"X-Platform-TenantId": "TENANT-B"})
        )
        self.assertEqual(response.status_code, 403)
        manager.filter.assert_not_called()
        self.assertNotIn("TENANT-A", response.content.decode())

    @patch("apps.payments.platform_scope.resolve_registered_bb_scope")
    def test_missing_or_forged_scope_is_denied(self, resolve):
        resolve.side_effect = PaymentScopeDenied("scope required")
        for headers in ({}, {"X-Platform-TenantId": "TENANT-FORGED"}):
            with self.subTest(headers=headers):
                response = ReconciliationReportView.as_view()(self.request(**headers))
                self.assertEqual(response.status_code, 403)

    @patch("apps.payments.platform_scope.resolve_registered_bb_scope")
    @patch("apps.payments.platform_scope.PaymentReconciliation.objects")
    def test_authorised_read_resolves_no_provider(self, manager, resolve):
        resolve.return_value = PaymentScope(caller_bb_id="BB-A", tenant_id="TENANT-A")
        manager.filter.return_value.select_related.return_value.order_by.return_value.__getitem__.side_effect = [  # noqa: E501
            [],
            [],
        ]
        provider = Mock()
        with patch("apps.payments.provider_runtime.ProviderRuntime.resolve", provider):
            response = ReconciliationReportView.as_view()(
                self.request(**{"X-Platform-TenantId": "TENANT-A"})
            )
        self.assertEqual(response.status_code, 200)
        provider.assert_not_called()

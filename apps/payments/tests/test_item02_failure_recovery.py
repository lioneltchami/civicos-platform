from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.payments.govstack_failure_services import PaymentLifecycleService
from apps.payments.govstack_models import (
    CallbackDelivery,
    GovStackPaymentAuditEntry,
    PaymentAttempt,
    PaymentOutcome,
    PaymentReconciliation,
)
from apps.payments.govstack_tasks import triage_govstack_uncertain_attempts


class Item02FailureRecoveryTests(TestCase):
    def _attempt(self, request_id: str = "item02-request-1") -> PaymentAttempt:
        attempt, _ = PaymentLifecycleService.get_or_create_attempt(
            tenant_id="tenant-a",
            operation="g2p_bulk_instruction",
            request_id=request_id,
            payload={"instruction_pk": "instruction-1", "amount": "10.00"},
            amount=Decimal("10.00"),
            currency="USD",
            source_bb_id="source-bb",
        )
        return attempt

    def test_callback_failure_is_durable_then_dead_lettered(self):
        attempt = self._attempt()
        delivery, created = PaymentLifecycleService.queue_callback(
            attempt=attempt,
            callback_url="https://example.invalid/status",
            payload={"RequestID": "item02-request-1", "Status": "COMPLETED"},
        )
        self.assertTrue(created)
        for _ in range(PaymentLifecycleService.MAX_CALLBACK_ATTEMPTS):
            delivery = PaymentLifecycleService.record_callback_result(
                delivery,
                error_code="ConnectionError",
            )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, CallbackDelivery.STATUS_DEAD)
        self.assertIsNone(delivery.next_attempt_at)
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_CALLBACK_DEAD_LETTERED,
                object_pk=str(attempt.pk),
            ).exists()
        )

    def test_uncertain_outcome_is_reconciled_then_kicked_to_review(self):
        attempt = self._attempt("item02-request-2")
        PaymentLifecycleService.apply_outcome(
            attempt,
            PaymentOutcome(
                "uncertain",
                code="TIMEOUT_AFTER_ACCEPT",
                category="timeout",
                provider_attempt_id="provider-attempt-1",
            ),
        )
        attempt.refresh_from_db()
        attempt.next_retry_at = timezone.now()
        attempt.save(update_fields=["next_retry_at", "updated_at"])

        self.assertEqual(triage_govstack_uncertain_attempts(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.STATUS_REVIEW)
        self.assertTrue(
            PaymentReconciliation.objects.filter(
                attempt=attempt,
                status=PaymentReconciliation.STATUS_UNKNOWN,
            ).exists()
        )
        self.assertTrue(
            GovStackPaymentAuditEntry.objects.filter(
                action=GovStackPaymentAuditEntry.ACTION_PAYMENT_REVIEW_REQUIRED,
                object_pk=str(attempt.pk),
            ).exists()
        )

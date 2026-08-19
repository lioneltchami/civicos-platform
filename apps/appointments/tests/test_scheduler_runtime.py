from unittest import mock

from django.test import TestCase, override_settings

from apps.appointments.models import GovStackAlertSchedule, SchedulerOutbox, SchedulerRecipientDelivery
from apps.appointments.services import scheduler_runtime
from apps.appointments.services.govstack_alert_schedule import alert_schedule_create
from apps.appointments.tasks import dispatch_alert_schedule
from apps.appointments.tests.test_govstack_alert_schedule import (
    _create_full_slot,
    _create_message,
)


class SchedulerRuntimeTests(TestCase):
    def setUp(self):
        self.slot, self.staff, _, self.org = _create_full_slot(
            gs_alert_preference="push",
            gs_alert_url="https://example.com/alert",
        )
        self.message = _create_message(entity_id=self.org.pk)
        self.schedule = alert_schedule_create(
            event_id=str(self.slot.pk),
            message_id=self.message.pk,
            target_category="resource",
            alert_datetime="2027-06-01T09:00:00Z",
        )

    def _materialize(self):
        return scheduler_runtime.materialize(
            schedule=self.schedule,
            owner_key=f"schedule:{self.schedule.pk}",
            correlation_id="correlation-runtime-1",
            recipient_kind="staff",
            recipient_ref=str(self.staff.pk),
            payload={},
            generation=self.schedule.delivery_generation,
        )

    def test_materialization_is_idempotent_and_creates_one_outbox_row(self):
        delivery, created = self._materialize()
        duplicate, duplicate_created = self._materialize()

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(delivery.pk, duplicate.pk)
        self.assertEqual(SchedulerRecipientDelivery.objects.count(), 1)
        self.assertEqual(SchedulerOutbox.objects.count(), 1)
        self.assertEqual(delivery.schedule_id, self.schedule.pk)
        self.assertEqual(delivery.recipient_kind, "staff")
        self.assertEqual(delivery.payload, {})

    def test_claim_fences_stale_outcome_and_acknowledgement_is_separate(self):
        delivery, _ = self._materialize()
        token = scheduler_runtime.claim(idempotency_key=delivery.idempotency_key)

        self.assertIsNotNone(token)
        self.assertIsNone(scheduler_runtime.claim(idempotency_key=delivery.idempotency_key))
        self.assertFalse(scheduler_runtime.succeed(idempotency_key=delivery.idempotency_key, lease_token="stale"))
        self.assertTrue(scheduler_runtime.succeed(idempotency_key=delivery.idempotency_key, lease_token=token))
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SchedulerRecipientDelivery.DELIVERED)
        self.assertTrue(scheduler_runtime.acknowledge(idempotency_key=delivery.idempotency_key))
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SchedulerRecipientDelivery.ACKNOWLEDGED)

    def test_failure_is_retryable_then_dead_letters_at_bound(self):
        delivery, _ = self._materialize()
        delivery.max_attempts = 1
        delivery.save(update_fields=["max_attempts"])
        token = scheduler_runtime.claim(idempotency_key=delivery.idempotency_key)

        self.assertTrue(
            scheduler_runtime.fail(
                idempotency_key=delivery.idempotency_key,
                lease_token=token,
                error_class="timeout",
            )
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SchedulerRecipientDelivery.DEAD_LETTER)
        self.assertTrue(scheduler_runtime.replay(idempotency_key=delivery.idempotency_key))
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SchedulerRecipientDelivery.RETRY)

    @override_settings(GOVSTACK_SCHEDULER_DURABLE_RUNTIME_ENABLED=True)
    @mock.patch("apps.appointments.scheduler_tasks.publish_scheduler_outbox.delay")
    def test_live_dispatch_materializes_durable_work_without_http(self, publish_delay):
        with self.captureOnCommitCallbacks(execute=True):
            result = dispatch_alert_schedule.run(str(self.schedule.pk))

        self.assertEqual(result["materialized"], 1)
        delivery = SchedulerRecipientDelivery.objects.get(schedule=self.schedule)
        self.assertEqual(delivery.recipient_kind, "staff")
        self.assertEqual(delivery.status, SchedulerRecipientDelivery.PENDING)
        self.assertTrue(SchedulerOutbox.objects.filter(delivery=delivery).exists())
        self.assertEqual(delivery.payload, {})
        publish_delay.assert_called_once()

    def test_modify_bumps_generation_and_cancels_non_terminal_work(self):
        delivery, _ = self._materialize()
        from apps.appointments.services.govstack_alert_schedule import alert_schedule_modify

        updated, _, _ = alert_schedule_modify(
            self.schedule.pk,
            message_id=self.message.pk,
            target_category="subscriber",
        )
        updated.refresh_from_db()
        delivery.refresh_from_db()
        self.assertEqual(updated.delivery_generation, 2)
        self.assertEqual(delivery.status, SchedulerRecipientDelivery.CANCELLED)

from __future__ import annotations

from django.test import TransactionTestCase

from apps.appointments.models import GovStackAlertSchedule, SchedulerOutbox, SchedulerRecipientDelivery
from apps.appointments.services import scheduler_runtime
from apps.appointments.services.govstack_alert_schedule import alert_schedule_create
from apps.appointments.tests.test_govstack_alert_schedule import _create_full_slot, _create_message


class SchedulerLifecycleTransactionTests(TransactionTestCase):
    reset_sequences = True

    def test_cancel_schedule_fences_non_terminal_work_and_blocks_admission(self):
        slot, staff, _, org = _create_full_slot(
            gs_alert_preference="push",
            gs_alert_url="https://example.com/cancel-only",
        )
        message = _create_message(entity_id=org.pk)
        schedule = alert_schedule_create(
            event_id=str(slot.pk),
            message_id=message.pk,
            target_category="resource",
            alert_datetime="2027-06-01T09:00:00Z",
        )
        recipients = [("staff", str(staff.pk))]
        admitted = scheduler_runtime.admit_schedule_generation(
            schedule_id=schedule.pk,
            expected_generation=schedule.delivery_generation,
            recipients=recipients,
        )
        self.assertEqual(admitted["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        delivery = admitted["deliveries"][0]
        lease_token = scheduler_runtime.claim(idempotency_key=delivery.idempotency_key)
        self.assertIsNotNone(lease_token)
        outbox = SchedulerOutbox.objects.get(delivery=delivery)
        claimed_outbox = scheduler_runtime.claim_outbox()
        self.assertIsNotNone(claimed_outbox)

        cancelled = scheduler_runtime.cancel_schedule(schedule_id=schedule.pk)
        self.assertEqual(cancelled, 1)

        schedule.refresh_from_db()
        delivery.refresh_from_db()
        outbox.refresh_from_db()
        self.assertFalse(schedule.delivery_admittable)
        self.assertIsNone(schedule.admitted_generation)
        self.assertEqual(schedule.admission_outcome, "")
        self.assertEqual(delivery.status, SchedulerRecipientDelivery.CANCELLED)
        self.assertIsNotNone(delivery.cancelled_at)
        self.assertIsNone(delivery.lease_token)
        self.assertIsNone(delivery.lease_expires_at)
        self.assertIsNotNone(outbox.cancelled_at)
        self.assertIsNone(outbox.publisher_token)
        self.assertEqual(outbox.publisher_owner, "")
        self.assertIsNone(outbox.publisher_lease_expires_at)
        self.assertIsNone(scheduler_runtime.claim_outbox())
        self.assertFalse(scheduler_runtime.due_outbox().filter(pk=outbox.pk).exists())

        before_deliveries = SchedulerRecipientDelivery.objects.filter(schedule=schedule).count()
        before_outboxes = SchedulerOutbox.objects.filter(delivery__schedule=schedule).count()
        blocked = scheduler_runtime.admit_schedule_generation(
            schedule_id=schedule.pk,
            expected_generation=schedule.delivery_generation,
            recipients=recipients,
        )
        self.assertEqual(blocked["outcome"], "not_admittable")
        self.assertEqual(blocked["created"], 0)
        self.assertEqual(blocked["deliveries"], [])
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=schedule).count(), before_deliveries)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=schedule).count(), before_outboxes)
        schedule.refresh_from_db()
        self.assertIsNone(schedule.admitted_generation)
        self.assertEqual(schedule.admission_outcome, "")

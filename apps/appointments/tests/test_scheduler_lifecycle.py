from __future__ import annotations

from django.test import TransactionTestCase

from apps.appointments.models import GovStackAlertSchedule, SchedulerOutbox, SchedulerRecipientDelivery
from apps.appointments.services import scheduler_runtime
from apps.appointments.services.govstack_alert_schedule import (
    alert_schedule_create,
    alert_schedule_delete,
    alert_schedule_modify,
)
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


    def test_modify_delivery_content_invalidates_old_generation_before_new_admission(self):
        slot, staff, _, org = _create_full_slot(
            gs_alert_preference="push",
            gs_alert_url="https://example.com/modify-only",
        )
        message = _create_message(entity_id=org.pk)
        schedule = alert_schedule_create(
            event_id=str(slot.pk),
            message_id=message.pk,
            target_category="resource",
            alert_datetime="2027-06-02T09:00:00Z",
        )
        recipients = [("staff", str(staff.pk))]
        old_generation = schedule.delivery_generation
        old_admission = scheduler_runtime.admit_schedule_generation(
            schedule_id=schedule.pk,
            expected_generation=old_generation,
            recipients=recipients,
        )
        self.assertEqual(old_admission["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        old_delivery = old_admission["deliveries"][0]
        self.assertIsNotNone(scheduler_runtime.claim(idempotency_key=old_delivery.idempotency_key))
        old_outbox = SchedulerOutbox.objects.get(delivery=old_delivery)
        self.assertIsNotNone(scheduler_runtime.claim_outbox())

        modified, _, _ = alert_schedule_modify(
            alert_schedule_id=schedule.pk,
            target_category="",
        )
        modified.refresh_from_db()
        old_delivery.refresh_from_db()
        old_outbox.refresh_from_db()
        self.assertEqual(modified.delivery_generation, old_generation + 1)
        self.assertTrue(modified.delivery_admittable)
        self.assertIsNone(modified.admitted_generation)
        self.assertEqual(modified.admission_outcome, "")
        self.assertEqual(old_delivery.status, SchedulerRecipientDelivery.CANCELLED)
        self.assertIsNone(old_delivery.lease_token)
        self.assertIsNotNone(old_outbox.cancelled_at)
        self.assertIsNone(old_outbox.publisher_token)
        self.assertIsNone(scheduler_runtime.claim_outbox())

        before_deliveries = SchedulerRecipientDelivery.objects.filter(schedule=modified).count()
        before_outboxes = SchedulerOutbox.objects.filter(delivery__schedule=modified).count()
        stale = scheduler_runtime.admit_schedule_generation(
            schedule_id=modified.pk,
            expected_generation=old_generation,
            recipients=recipients,
        )
        self.assertEqual(stale["outcome"], GovStackAlertSchedule.ADMISSION_STALE_GENERATION)
        self.assertEqual(stale["created"], 0)
        self.assertEqual(stale["deliveries"], [])
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=modified).count(), before_deliveries)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=modified).count(), before_outboxes)

        new_admission = scheduler_runtime.admit_schedule_generation(
            schedule_id=modified.pk,
            expected_generation=modified.delivery_generation,
            recipients=recipients,
        )
        self.assertEqual(new_admission["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        modified.refresh_from_db()
        self.assertEqual(modified.delivery_generation, old_generation + 1)
        self.assertEqual(modified.admitted_generation, old_generation + 1)


    def test_delete_schedule_cannot_leave_recreatable_admission_work(self):
        slot, staff, _, org = _create_full_slot(
            gs_alert_preference="push",
            gs_alert_url="https://example.com/delete-only",
        )
        message = _create_message(entity_id=org.pk)
        schedule = alert_schedule_create(
            event_id=str(slot.pk),
            message_id=message.pk,
            target_category="resource",
            alert_datetime="2027-06-03T09:00:00Z",
        )
        schedule_id = schedule.pk
        generation = schedule.delivery_generation
        recipients = [("staff", str(staff.pk))]
        admitted = scheduler_runtime.admit_schedule_generation(
            schedule_id=schedule_id,
            expected_generation=generation,
            recipients=recipients,
        )
        delivery = admitted["deliveries"][0]
        self.assertIsNotNone(scheduler_runtime.claim(idempotency_key=delivery.idempotency_key))
        outbox = SchedulerOutbox.objects.get(delivery=delivery)
        self.assertIsNotNone(scheduler_runtime.claim_outbox())

        alert_schedule_delete(schedule_id)
        self.assertFalse(GovStackAlertSchedule.objects.filter(pk=schedule_id).exists())
        self.assertFalse(SchedulerRecipientDelivery.objects.filter(schedule_id=schedule_id).exists())
        self.assertFalse(SchedulerOutbox.objects.filter(delivery_id=delivery.pk).exists())

        with self.assertRaises(GovStackAlertSchedule.DoesNotExist):
            scheduler_runtime.admit_schedule_generation(
                schedule_id=schedule_id,
                expected_generation=generation,
                recipients=recipients,
            )
        self.assertFalse(SchedulerRecipientDelivery.objects.filter(schedule_id=schedule_id).exists())
        self.assertFalse(SchedulerOutbox.objects.filter(delivery_id=delivery.pk).exists())

    def test_rearm_advances_generation_and_admits_exactly_once(self):
        slot, staff, _, org = _create_full_slot(
            gs_alert_preference="push",
            gs_alert_url="https://example.com/rearm-only",
        )
        message = _create_message(entity_id=org.pk)
        schedule = alert_schedule_create(
            event_id=str(slot.pk),
            message_id=message.pk,
            target_category="resource",
            alert_datetime="2027-06-04T09:00:00Z",
        )
        recipients = [("staff", str(staff.pk))]
        old_generation = schedule.delivery_generation
        admitted = scheduler_runtime.admit_schedule_generation(
            schedule_id=schedule.pk,
            expected_generation=old_generation,
            recipients=recipients,
        )
        old_delivery = admitted["deliveries"][0]
        self.assertIsNotNone(scheduler_runtime.claim(idempotency_key=old_delivery.idempotency_key))
        old_outbox = SchedulerOutbox.objects.get(delivery=old_delivery)
        self.assertIsNotNone(scheduler_runtime.claim_outbox())

        # Seed the durable cancelled/non-admittable precondition while leaving
        # active child work for the distinct Re-arm fence to invalidate.
        schedule.delivery_admittable = False
        schedule.admitted_generation = None
        schedule.admission_outcome = ""
        schedule.save(update_fields=["delivery_admittable", "admitted_generation", "admission_outcome", "updated_at"])

        rearmed = scheduler_runtime.rearm_schedule(schedule_id=schedule.pk)
        schedule.refresh_from_db()
        old_delivery.refresh_from_db()
        old_outbox.refresh_from_db()
        self.assertEqual(rearmed["outcome"], "rearmed")
        self.assertEqual(rearmed["generation"], old_generation + 1)
        self.assertEqual(schedule.delivery_generation, old_generation + 1)
        self.assertTrue(schedule.delivery_admittable)
        self.assertIsNone(schedule.admitted_generation)
        self.assertEqual(schedule.admission_outcome, "")
        self.assertEqual(old_delivery.status, SchedulerRecipientDelivery.CANCELLED)
        self.assertIsNone(old_delivery.lease_token)
        self.assertIsNotNone(old_outbox.cancelled_at)
        self.assertIsNone(old_outbox.publisher_token)

        stale = scheduler_runtime.admit_schedule_generation(
            schedule_id=schedule.pk,
            expected_generation=old_generation,
            recipients=recipients,
        )
        self.assertEqual(stale["outcome"], GovStackAlertSchedule.ADMISSION_STALE_GENERATION)
        self.assertEqual(stale["created"], 0)
        self.assertEqual(stale["deliveries"], [])

        new_admission = scheduler_runtime.admit_schedule_generation(
            schedule_id=schedule.pk,
            expected_generation=old_generation + 1,
            recipients=recipients,
        )
        self.assertEqual(new_admission["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        delivery_count = SchedulerRecipientDelivery.objects.filter(schedule=schedule).count()
        outbox_count = SchedulerOutbox.objects.filter(delivery__schedule=schedule).count()
        duplicate_admission = scheduler_runtime.admit_schedule_generation(
            schedule_id=schedule.pk,
            expected_generation=old_generation + 1,
            recipients=recipients,
        )
        self.assertEqual(duplicate_admission["outcome"], GovStackAlertSchedule.ADMISSION_DUPLICATE)
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=schedule).count(), delivery_count)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=schedule).count(), outbox_count)

        repeated_rearm = scheduler_runtime.rearm_schedule(schedule_id=schedule.pk)
        schedule.refresh_from_db()
        self.assertEqual(repeated_rearm["outcome"], GovStackAlertSchedule.ADMISSION_DUPLICATE)
        self.assertEqual(schedule.delivery_generation, old_generation + 1)

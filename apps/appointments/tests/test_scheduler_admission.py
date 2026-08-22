from __future__ import annotations

import threading
import time
from unittest import mock

from django.db import OperationalError, close_old_connections
from django.test import TransactionTestCase

from apps.appointments.models import GovStackAlertSchedule, SchedulerOutbox, SchedulerRecipientDelivery
from apps.appointments.services import scheduler_runtime
from apps.appointments.services.govstack_alert_schedule import alert_schedule_create
from apps.appointments.tasks import dispatch_alert_schedule
from apps.appointments.tests.test_govstack_alert_schedule import _create_full_slot, _create_message


class SchedulerAdmissionTransactionTests(TransactionTestCase):
    reset_sequences = True

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
        self.recipients = [("staff", str(self.staff.pk))]

    def _admit(self, *, generation=None, recipients=None):
        return scheduler_runtime.admit_schedule_generation(
            schedule_id=self.schedule.pk,
            expected_generation=generation or self.schedule.delivery_generation,
            recipients=self.recipients if recipients is None else recipients,
        )

    def test_current_generation_materializes_one_recipient_and_one_outbox_per_recipient(self):
        second_recipient = ("staff", f"{self.staff.pk}-secondary")
        result = self._admit(recipients=[*self.recipients, second_recipient])

        self.assertEqual(result["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        self.assertEqual(result["created"], 2)
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 2)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 2)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.admitted_generation, self.schedule.delivery_generation)
        self.assertEqual(self.schedule.admission_outcome, GovStackAlertSchedule.ADMISSION_CREATED)

    def test_duplicate_admission_converges_without_duplicate_rows(self):
        first = self._admit()
        duplicate = self._admit()

        self.assertEqual(first["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        self.assertEqual(duplicate["outcome"], GovStackAlertSchedule.ADMISSION_DUPLICATE)
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 1)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 1)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.delivery_generation, 1)

    def test_concurrent_current_generation_admission_is_single_winner(self):
        barrier = threading.Barrier(2)
        outcomes = []
        failures = []

        def admit_from_independent_connection():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                for attempt in range(8):
                    try:
                        result = scheduler_runtime.admit_schedule_generation(
                            schedule_id=self.schedule.pk,
                            expected_generation=1,
                            recipients=self.recipients,
                        )
                        outcomes.append(result["outcome"])
                        return
                    except OperationalError:
                        if attempt == 7:
                            raise
                        time.sleep(0.03)
            except Exception as exc:  # asserted below
                failures.append(exc)
            finally:
                close_old_connections()

        first = threading.Thread(target=admit_from_independent_connection)
        second = threading.Thread(target=admit_from_independent_connection)
        first.start()
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])
        self.assertCountEqual(
            outcomes,
            [GovStackAlertSchedule.ADMISSION_CREATED, GovStackAlertSchedule.ADMISSION_DUPLICATE],
        )
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 1)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 1)

    def test_stale_generation_cannot_materialize_current_work(self):
        result = self._admit(generation=self.schedule.delivery_generation + 1)

        self.assertEqual(result["outcome"], GovStackAlertSchedule.ADMISSION_STALE_GENERATION)
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 0)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 0)
        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.admitted_generation)
        self.assertEqual(self.schedule.admission_outcome, "")

    def test_materialization_rollback_leaves_no_recipient_or_outbox_or_dispatched_claim(self):
        real_materialize = scheduler_runtime._materialize_locked

        def fail_after_first_materialization(**kwargs):
            real_materialize(**kwargs)
            raise RuntimeError("injected-admission-failure")

        with mock.patch.object(scheduler_runtime, "_materialize_locked", side_effect=fail_after_first_materialization):
            with self.assertRaisesRegex(RuntimeError, "injected-admission-failure"):
                self._admit()

        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 0)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 0)
        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.admitted_generation)
        self.assertEqual(self.schedule.admission_outcome, "")
        self.assertFalse(self.schedule.dispatched)

    def test_zero_recipient_admission_is_deterministic_and_rowless(self):
        first = self._admit(recipients=[])
        second = self._admit(recipients=[])

        self.assertEqual(first["outcome"], GovStackAlertSchedule.ADMISSION_ZERO_RECIPIENTS)
        self.assertEqual(second["outcome"], GovStackAlertSchedule.ADMISSION_DUPLICATE)
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 0)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 0)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.admitted_generation, self.schedule.delivery_generation)
        self.assertEqual(self.schedule.admission_outcome, GovStackAlertSchedule.ADMISSION_ZERO_RECIPIENTS)


    @mock.patch("apps.appointments.scheduler_tasks.publish_scheduler_outbox.delay")
    def test_legacy_dispatched_and_celery_state_never_authorize_admission(self, publish_delay):
        self.schedule.dispatched = True
        self.schedule.celery_task_id = "legacy-task-id"
        self.schedule.save(update_fields=["dispatched", "celery_task_id", "updated_at"])

        first = dispatch_alert_schedule.run(str(self.schedule.pk))
        self.assertEqual(first["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        self.assertEqual(first["materialized"], 1)
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 1)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 1)
        publish_delay.assert_called_once()

        self.schedule.dispatched = False
        self.schedule.celery_task_id = ""
        self.schedule.save(update_fields=["dispatched", "celery_task_id", "updated_at"])
        duplicate = dispatch_alert_schedule.run(str(self.schedule.pk))
        self.assertEqual(duplicate["outcome"], GovStackAlertSchedule.ADMISSION_DUPLICATE)
        self.assertEqual(duplicate["materialized"], 0)
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 1)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 1)
        publish_delay.assert_called_once()

    @mock.patch("apps.appointments.tasks._attempt_alert_delivery")
    @mock.patch("apps.appointments.tasks.requests.post")
    @mock.patch("apps.appointments.scheduler_tasks.publish_scheduler_outbox.delay")
    def test_no_transport_io_before_admission_commit(self, publish_delay, requests_post, attempt_delivery):
        from django.db import connection

        observed = []

        def wakeup_observer():
            observed.append(connection.in_atomic_block)
            schedule = GovStackAlertSchedule.objects.get(pk=self.schedule.pk)
            self.assertEqual(schedule.admitted_generation, schedule.delivery_generation)
            self.assertTrue(SchedulerRecipientDelivery.objects.filter(schedule=schedule).exists())
            self.assertTrue(SchedulerOutbox.objects.filter(delivery__schedule=schedule).exists())

        publish_delay.side_effect = wakeup_observer
        result = dispatch_alert_schedule.run(str(self.schedule.pk))

        self.assertEqual(result["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        self.assertEqual(observed, [False])
        publish_delay.assert_called_once()
        requests_post.assert_not_called()
        attempt_delivery.assert_not_called()

    @mock.patch("apps.appointments.tasks._attempt_alert_delivery")
    @mock.patch("apps.appointments.tasks.requests.post")
    @mock.patch("apps.appointments.scheduler_tasks.publish_scheduler_outbox.delay")
    def test_rolled_back_admission_does_not_publish_or_schedule_transport(self, publish_delay, requests_post, attempt_delivery):
        real_materialize = scheduler_runtime._materialize_locked

        def fail_after_materialization(**kwargs):
            real_materialize(**kwargs)
            raise RuntimeError("injected-live-task-admission-failure")

        with mock.patch.object(
            scheduler_runtime,
            "_materialize_locked",
            side_effect=fail_after_materialization,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected-live-task-admission-failure"):
                dispatch_alert_schedule.run(str(self.schedule.pk))

        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 0)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 0)
        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.admitted_generation)
        self.assertEqual(self.schedule.admission_outcome, "")
        self.assertFalse(self.schedule.dispatched)
        publish_delay.assert_not_called()
        requests_post.assert_not_called()
        attempt_delivery.assert_not_called()

    @mock.patch("apps.appointments.scheduler_tasks.publish_scheduler_outbox.delay")
    def test_post_commit_wakeup_failure_preserves_durable_admission(self, publish_delay):
        publish_delay.side_effect = RuntimeError("injected-wakeup-failure")

        result = dispatch_alert_schedule.run(str(self.schedule.pk))

        self.assertEqual(result["outcome"], GovStackAlertSchedule.ADMISSION_CREATED)
        self.assertTrue(result["wakeup_registered"])
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.admitted_generation, self.schedule.delivery_generation)
        self.assertEqual(self.schedule.admission_outcome, GovStackAlertSchedule.ADMISSION_CREATED)
        self.assertEqual(SchedulerRecipientDelivery.objects.filter(schedule=self.schedule).count(), 1)
        self.assertEqual(SchedulerOutbox.objects.filter(delivery__schedule=self.schedule).count(), 1)
        publish_delay.assert_called_once()

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.appointments.models import (
    SchedulerOutbox,
    SchedulerRecipientDelivery,
)
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
        self.assertFalse(
            scheduler_runtime.succeed(idempotency_key=delivery.idempotency_key, lease_token="stale")
        )
        self.assertTrue(
            scheduler_runtime.succeed(idempotency_key=delivery.idempotency_key, lease_token=token)
        )
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
        # Exercise PostgreSQL's nullable joined relation under the schedule-only lock.
        self.slot.resource_id = None
        self.slot.save(update_fields=["resource"])
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


@override_settings(
    GOVSTACK_SCHEDULER_PUBLISH_MAX_ATTEMPTS=2,
    GOVSTACK_SCHEDULER_PUBLISH_RETRY_BASE_SECONDS=5,
    GOVSTACK_SCHEDULER_PUBLISH_RETRY_MAX_SECONDS=20,
)
class SchedulerPublisherRecoveryPostgresTests(TransactionTestCase):
    """Real PostgreSQL proof for the bounded SCH-02.1 publisher contract."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        if connection.vendor != "postgresql":
            self.skipTest("SCH-02.1 publisher evidence requires PostgreSQL")
        self.slot, self.staff, _, self.org = _create_full_slot(
            gs_alert_preference="push", gs_alert_url="https://example.com/alert"
        )
        self.message = _create_message(entity_id=self.org.pk)
        self.schedule = alert_schedule_create(
            event_id=str(self.slot.pk),
            message_id=self.message.pk,
            target_category="resource",
            alert_datetime="2027-06-01T09:00:00Z",
        )

    def _outbox(self, correlation_id="sch02-correlation", recipient_ref=None):
        delivery, created = scheduler_runtime.materialize(
            schedule=self.schedule,
            owner_key=f"schedule:{self.schedule.pk}",
            correlation_id=correlation_id,
            recipient_kind="staff",
            recipient_ref=recipient_ref or str(self.staff.pk),
            payload={},
            generation=self.schedule.delivery_generation,
        )
        self.assertTrue(created)
        return SchedulerOutbox.objects.get(delivery=delivery)

    def _claim(self, now=None, owner="publisher-a"):
        claim = scheduler_runtime.claim_outbox(now=now, owner=owner)
        self.assertIsNotNone(claim)
        return claim

    def _fail(self, claim, now, unknown=False):
        outbox_id, _, token, generation = claim
        return scheduler_runtime.mark_outbox_failed(
            outbox_id=outbox_id,
            token=token,
            generation=generation,
            error_class="TimeoutError" if unknown else "BrokerUnavailable",
            now=now,
            unknown_handoff=unknown,
        )

    def test_claim_outbox_consumes_attempt_and_sets_publisher_lease(self):
        outbox = self._outbox()
        now = timezone.now()
        outbox_id, key, token, generation = self._claim(now=now)
        outbox.refresh_from_db()
        self.assertEqual((outbox_id, key), (outbox.pk, outbox.delivery.idempotency_key))
        self.assertEqual(outbox.publisher_state, SchedulerOutbox.CLAIMED)
        self.assertEqual(outbox.publish_attempts, 1)
        self.assertEqual((outbox.publisher_token, outbox.publisher_generation), (token, generation))
        self.assertGreater(outbox.publisher_lease_expires_at, now)

    def test_local_enqueue_failure_is_classified_backed_off_and_reclaimable(self):
        outbox = self._outbox()
        now = timezone.now()
        self.assertTrue(self._fail(self._claim(now=now), now))
        outbox.refresh_from_db()
        self.assertEqual(outbox.publisher_state, SchedulerOutbox.LOCAL_FAILURE)
        self.assertEqual(outbox.publisher_failure_class, "BrokerUnavailable")
        self.assertEqual(outbox.available_at, now + timedelta(seconds=5))
        self.assertIsNone(scheduler_runtime.claim_outbox(now=now))
        self.assertIsNotNone(scheduler_runtime.claim_outbox(now=outbox.available_at))

    def test_unknown_handoff_is_distinct_and_preserves_correlation(self):
        outbox = self._outbox(correlation_id="ambiguous")
        correlation = outbox.delivery.correlation_id
        now = timezone.now()
        self.assertTrue(self._fail(self._claim(now=now), now, unknown=True))
        outbox.refresh_from_db()
        self.assertEqual(outbox.publisher_state, SchedulerOutbox.UNKNOWN_HANDOFF)
        self.assertEqual(outbox.publisher_failure_class, "TimeoutError")
        self.assertEqual(outbox.delivery.correlation_id, correlation)
        self.assertIsNone(outbox.published_at)

    def test_retry_policy_is_bounded_and_not_an_unbounded_hot_loop(self):
        outbox = self._outbox()
        now = timezone.now()
        self.assertTrue(self._fail(self._claim(now=now), now))
        outbox.refresh_from_db()
        self.assertGreater(outbox.available_at, now)
        self.assertFalse(scheduler_runtime.due_outbox(now=now).exists())
        self.assertTrue(self._fail(self._claim(now=outbox.available_at), outbox.available_at))
        outbox.refresh_from_db()
        self.assertEqual(outbox.publisher_state, SchedulerOutbox.EXHAUSTED)
        self.assertFalse(scheduler_runtime.due_outbox(now=outbox.available_at).exists())

    def test_attempt_budget_transitions_to_terminal_publisher_failure(self):
        outbox = self._outbox()
        now = timezone.now()
        self._fail(self._claim(now=now), now)
        outbox.refresh_from_db()
        self._fail(self._claim(now=outbox.available_at), outbox.available_at)
        outbox.refresh_from_db()
        self.assertEqual(outbox.publisher_state, SchedulerOutbox.EXHAUSTED)
        self.assertEqual(outbox.publish_attempts, 2)
        self.assertIsNotNone(outbox.exhausted_at)
        self.assertIsNone(outbox.publisher_token)
        self.assertIsNone(scheduler_runtime.claim_outbox(now=outbox.available_at))

    def test_guarded_replay_reopens_terminal_row_without_losing_correlation(self):
        outbox = self._outbox(correlation_id="replay")
        key = outbox.delivery.idempotency_key
        now = timezone.now()
        self._fail(self._claim(now=now), now)
        outbox.refresh_from_db()
        self._fail(self._claim(now=outbox.available_at), outbox.available_at)
        self.assertTrue(
            scheduler_runtime.replay_outbox(outbox_id=outbox.pk, now=outbox.available_at)
        )
        outbox.refresh_from_db()
        self.assertEqual(outbox.publisher_state, SchedulerOutbox.PENDING)
        self.assertEqual(outbox.delivery.correlation_id, "replay")
        self.assertEqual(outbox.delivery.idempotency_key, key)

    def test_replay_rejects_published_and_cancelled_rows(self):
        published = self._outbox("published")
        outbox_id, _, token, generation = self._claim()
        self.assertTrue(
            scheduler_runtime.mark_outbox_published(
                outbox_id=outbox_id, token=token, generation=generation
            )
        )
        self.assertFalse(scheduler_runtime.replay_outbox(outbox_id=published.pk))
        cancelled = self._outbox("cancelled", "cancelled-recipient")
        SchedulerOutbox.objects.filter(pk=cancelled.pk).update(
            cancelled_at=timezone.now(), publisher_state=SchedulerOutbox.CANCELLED
        )
        self.assertFalse(scheduler_runtime.replay_outbox(outbox_id=cancelled.pk))

    def test_published_and_failure_require_current_publisher_token_and_generation(self):
        self._outbox()
        outbox_id, _, stale_token, stale_generation = self._claim(owner="publisher-a")
        SchedulerOutbox.objects.filter(pk=outbox_id).update(
            publisher_lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        _, _, current_token, current_generation = self._claim(owner="publisher-b")
        self.assertFalse(
            scheduler_runtime.mark_outbox_published(
                outbox_id=outbox_id, token=stale_token, generation=stale_generation
            )
        )
        self.assertFalse(
            scheduler_runtime.mark_outbox_failed(
                outbox_id=outbox_id,
                token=stale_token,
                generation=stale_generation,
                error_class="stale",
            )
        )
        self.assertTrue(
            scheduler_runtime.mark_outbox_published(
                outbox_id=outbox_id, token=current_token, generation=current_generation
            )
        )

    def test_existing_outbox_rows_migrate_to_safe_publisher_states(self):
        pending = self._outbox("legacy-pending")
        claimed = self._outbox("legacy-claimed", "claimed-recipient")
        published = self._outbox("legacy-published", "published-recipient")
        cancelled = self._outbox("legacy-cancelled", "cancelled-recipient")
        SchedulerOutbox.objects.filter(pk=claimed.pk).update(publisher_token="legacy-token")
        SchedulerOutbox.objects.filter(pk=published.pk).update(published_at=timezone.now())
        SchedulerOutbox.objects.filter(pk=cancelled.pk).update(cancelled_at=timezone.now())
        migration = import_module(
            "apps.appointments.migrations.0023_sch02_1_bounded_publisher_recovery"
        )

        class CurrentApps:
            @staticmethod
            def get_model(app_label, model_name):
                return SchedulerOutbox

        migration.backfill_publisher_states(CurrentApps(), None)
        for row, state in (
            (pending, SchedulerOutbox.PENDING),
            (claimed, SchedulerOutbox.CLAIMED),
            (published, SchedulerOutbox.PUBLISHED),
            (cancelled, SchedulerOutbox.CANCELLED),
        ):
            row.refresh_from_db()
            self.assertEqual(row.publisher_state, state)

    def test_real_competing_publishers_have_one_current_owner(self):
        self._outbox()
        outbox_id, _, stale_token, stale_generation = self._claim(owner="publisher-a")
        SchedulerOutbox.objects.filter(pk=outbox_id).update(
            publisher_lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        def compete(owner):
            try:
                return scheduler_runtime.claim_outbox(owner=owner)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(compete, ["publisher-b", "publisher-c"]))
        current = [claim for claim in claims if claim is not None]
        self.assertEqual(len(current), 1)
        _, _, token, generation = current[0]
        self.assertFalse(
            scheduler_runtime.mark_outbox_published(
                outbox_id=outbox_id, token=stale_token, generation=stale_generation
            )
        )
        self.assertTrue(
            scheduler_runtime.mark_outbox_published(
                outbox_id=outbox_id, token=token, generation=generation
            )
        )

from apps.appointments.services.delivery_state import DeliveryStatus, DeliveryStore


def test_duplicate_is_idempotent_and_timeout_retries_then_delivers():
    store = DeliveryStore()
    first = store.create(correlation_id="corr-1", idempotency_key="idem-1", recipient_ref="fake:1")
    duplicate = store.create(
        correlation_id="corr-1", idempotency_key="idem-1", recipient_ref="fake:1"
    )
    assert duplicate is first
    assert first.claim(lease_token="w1", now=0)
    assert first.fail(lease_token="w1", error="timeout", now=0, backoff=lambda n: 2**n)
    assert first.status == DeliveryStatus.RETRY and first.next_attempt_at == 2
    assert not first.claim(lease_token="w2", now=1)
    assert first.claim(lease_token="w2", now=2)
    assert first.succeed(lease_token="w2")
    assert first.acknowledge(now=3)


def test_4xx_and_5xx_are_bounded_and_dead_lettered_then_replayed():
    store = DeliveryStore()
    row = store.create(
        correlation_id="corr-2", idempotency_key="idem-2", recipient_ref="fake:2", max_attempts=2
    )
    assert row.claim(lease_token="w1", now=0)
    assert row.fail(lease_token="w1", error="http_500", now=0, backoff=lambda _: 1)
    assert row.claim(lease_token="w2", now=1)
    assert row.fail(lease_token="w2", error="http_400", now=1, backoff=lambda _: 1)
    assert row.status == DeliveryStatus.DEAD_LETTER
    assert row.replay()
    assert row.claim(lease_token="w3", now=1)
    assert row.succeed(lease_token="w3")


def test_partial_recipient_failure_and_cancellation_race():
    store = DeliveryStore()
    ok = store.create(correlation_id="corr-3", idempotency_key="idem-ok", recipient_ref="fake:ok")
    bad = store.create(
        correlation_id="corr-3", idempotency_key="idem-bad", recipient_ref="fake:bad"
    )
    assert ok.claim(lease_token="w1", now=0) and ok.succeed(lease_token="w1")
    assert bad.claim(lease_token="w1", now=0)
    assert bad.cancel()
    assert not bad.succeed(lease_token="w1")
    assert ok.status == DeliveryStatus.DELIVERED and bad.status == DeliveryStatus.CANCELLED


def test_worker_loss_and_late_ack_are_fenced_without_false_delivery():
    store = DeliveryStore()
    row = store.create(correlation_id="corr-4", idempotency_key="idem-4", recipient_ref="fake:4")
    assert row.claim(lease_token="old-worker", now=0)
    # A replacement worker cannot mutate a lease it does not own.
    assert not row.succeed(lease_token="new-worker")
    assert not row.fail(lease_token="new-worker", error="late", now=1, backoff=lambda _: 1)
    assert row.status == DeliveryStatus.IN_FLIGHT
    assert row.fail(lease_token="old-worker", error="worker_lost", now=1, backoff=lambda _: 1)
    assert row.status == DeliveryStatus.RETRY


def test_metrics_are_non_pii_and_authorized_status_is_owner_scoped():
    store = DeliveryStore()
    store.create(
        correlation_id="corr-5", idempotency_key="idem-5", recipient_ref="secret@example.test"
    )
    assert store.metrics()["pending"] == 1
    try:
        store.operational_status(owner="ops", authorized=False)
    except PermissionError:
        pass
    else:
        raise AssertionError("unauthorized status must be rejected")
    status = store.operational_status(owner="ops", authorized=True)[0]
    assert "secret@example.test" not in repr(status)
    assert status["owner"] == "ops"

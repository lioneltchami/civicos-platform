from apps.payments.govstack_provider import ProviderOutcome, ProviderResult
from apps.payments.providers.deterministic import DeterministicProvider
from apps.payments.govstack_http_idempotency import canonical_fingerprint, decide
from apps.payments.govstack_status_views import canonical_status
from apps.payments.govstack_batch_policy import evaluate
from apps.payments.govstack_operations import review


def test_deterministic_provider_timeout_then_status_settled():
    p = DeterministicProvider([ProviderResult(ProviderOutcome.TIMEOUT, retryable=True)], {"tx-1": ProviderResult(ProviderOutcome.SETTLED, external_transaction_id="tx-1")})
    first = p.submit(request_id="r1", payment={"amount": "1"})
    assert first.outcome is ProviderOutcome.TIMEOUT
    assert p.get_status(request_id="r1", external_transaction_id="tx-1").is_settled
    assert p.submissions == ["r1"]


def test_idempotency_replay_conflict_and_route_method_tenant_scope():
    a = canonical_fingerprint(tenant="t1", method="post", path="/payments/", key="k", payload={"x": 1})
    b = canonical_fingerprint(tenant="t1", method="POST", path="payments", key="k", payload={"x": 1})
    c = canonical_fingerprint(tenant="t2", method="POST", path="payments", key="k", payload={"x": 1})
    assert a == b and a != c
    assert decide(None, a).kind == "first_writer"
    assert decide(a, a).kind == "replay"
    assert decide(a, c).kind == "conflict"


def test_status_and_batch_exclude_settled_items():
    assert canonical_status(internal="uncertain", provider="settled") == "review"
    result = evaluate([{"id": "1", "status": "settled"}, {"id": "2", "status": "retryable"}], failure_threshold=.9)
    assert result.retry_ids == ("2",) and result.settled_ids == ("1",)
    assert review("a", "ops", "mismatch").action == "review"


def test_batch_policy_all_settled_is_completed():
    result = evaluate([{"id": "1", "status": "settled"}, {"id": "2", "status": "settled"}])
    assert result.state == "completed" and result.kick_back is False


def test_batch_policy_settled_and_rejected_is_terminal_partial():
    result = evaluate([{"id": "1", "status": "settled"}, {"id": "2", "status": "rejected"}], failure_threshold=1.0)
    assert result.state == "partial" and result.kick_back is False
    assert result.settled_ids == ("1",)


def test_batch_policy_non_final_states_are_not_terminal():
    for status, expected in (("uncertain", "uncertain"), ("retryable", "retryable"), ("review", "review"), ("unresolved", "unresolved")):
        result = evaluate([{"id": "1", "status": status}], failure_threshold=1.0)
        assert result.state == expected
        assert result.kick_back is True
        assert result.state not in {"completed", "partial"}


def test_batch_policy_threshold_pause_is_explicit():
    result = evaluate([{"id": "1", "status": "settled"}, {"id": "2", "status": "uncertain"}], failure_threshold=0.4)
    assert result.state == "paused" and result.kick_back is True
    assert result.retry_ids == ("2",)


def test_batch_policy_non_final_prevents_partial_even_with_rejection():
    result = evaluate([
        {"id": "1", "status": "settled"},
        {"id": "2", "status": "rejected"},
        {"id": "3", "status": "uncertain"},
    ], failure_threshold=1.0)
    assert result.state == "uncertain"
    assert result.state not in {"completed", "partial"}

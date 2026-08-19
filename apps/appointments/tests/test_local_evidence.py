from dataclasses import dataclass
import pytest
from apps.appointments.services.local_evidence import (
    FakeOutcome, LocalAuthorityContract, LocalPaymentsFake, LocalConsentFake,
    LocalSchedulerStatusService, redact_trace,
)

@dataclass
class R:
    owner_id: str
    tenant_id: str
    state: str

def test_fakes_are_disabled_by_default():
    with pytest.raises(RuntimeError):
        LocalPaymentsFake().invoke(correlation_id="c1")
    with pytest.raises(RuntimeError):
        LocalConsentFake().invoke(correlation_id="c1")

def test_fake_outcomes_and_duplicate_are_deterministic():
    fake = LocalPaymentsFake(enabled=True)
    assert fake.invoke(operation="success", correlation_id="c1").outcome is FakeOutcome.SUCCESS
    assert fake.invoke(operation="success", correlation_id="c1").outcome is FakeOutcome.DUPLICATE
    assert fake.invoke(operation="timeout", correlation_id="c2").outcome is FakeOutcome.TIMEOUT
    assert fake.invoke(operation="malformed", correlation_id="c3").outcome is FakeOutcome.MALFORMED
    assert fake.invoke(operation="not-allowed", correlation_id="c4").outcome is FakeOutcome.REJECTED

def test_authority_contract_preserves_authority_labels():
    result = LocalAuthorityContract(enabled=True).call("Consent", correlation_id="c1", operation="success")
    assert result.authority == "Consent"
    assert result.outcome is FakeOutcome.SUCCESS

def test_status_is_scoped_to_owner_and_tenant():
    records = [R("o1", "t1", "queued"), R("o1", "t1", "dead_letter"), R("o1", "t2", "queued"), R("o2", "t1", "leased")]
    status = LocalSchedulerStatusService().get(records, owner_id="o1", tenant_id="t1")
    assert (status.queued, status.dead_letter, status.leased) == (1, 1, 0)

def test_trace_redacts_pii_and_credentials():
    safe = redact_trace({"email": "a@example.test", "token": "secret", "status": 200})
    assert safe == {"email": "[REDACTED]", "token": "[REDACTED]", "status": 200}

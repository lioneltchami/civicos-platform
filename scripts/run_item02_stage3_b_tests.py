from apps.payments.tests.test_item02_stage3_b_primitives import *  # noqa: F403

test_deterministic_provider_timeout_then_status_settled()  # noqa: F405
test_idempotency_replay_conflict_and_route_method_tenant_scope()  # noqa: F405
test_status_and_batch_exclude_settled_items()  # noqa: F405
print("3 deterministic Stage 3 B tests passed")

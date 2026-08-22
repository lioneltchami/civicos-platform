# Payments RB-02 Scope-Pure Artifact — Base Settings Decision

## Decision: INCLUDE MINIMUM RB-02.3 POLICY CONTENT

Include only the following two definitions from `config/settings/base.py`, which were introduced by RB-02.3 and control persisted batch-decision policy rather than refund execution:

```python
GOVSTACK_BULK_RETURN_FUNDS_ENABLED = env.bool(
    "GOVSTACK_BULK_RETURN_FUNDS_ENABLED", default=False
)
GOVSTACK_BULK_FAILURE_THRESHOLD = env.float(
    "GOVSTACK_BULK_FAILURE_THRESHOLD", default=0.25
)
```

No other base-settings content, environment overlay, secret, deployment setting, queue/scheduler option, or refund execution path is admitted. The inclusion is limited to the exact policy definitions above; they do not perform refunds and must not be interpreted as authority to execute them.

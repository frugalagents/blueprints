# Local agent handoff contract v1

This checkpoint uses local HTTP, not the A2A protocol. OTel remains the telemetry
model. Existing independent Agent 1 and Agent 2 controls retain their behavior.

Agent A calls POST /internal/eligibility, carrying W3C traceparent/tracestate
headers and JSON with: contract_version=agent-handoff-v1, session_id,
workflow_id, handoff_id, handoff_attempt_id, caller_run_id, caller_attempt_id,
order (order_id, age_days, amount_cents), scenario, model_mode.
The loopback service requires its local runner token. No AWS credentials travel
in the request. These IDs identify the session, complete business execution,
logical delegation, transport attempt, and distinct A execution.

B creates its own task/run/attempt IDs. Its SERVER span adopts the extracted
remote context. OpenInference's trace-container span runs beneath that SERVER
span. B returns its decision, independent verification, run identity, and echoes
the workflow/handoff/attempt IDs. A checks all correlation values, order/policy,
eligibility consistency, source, and independent verification before accepting
any decision. Missing evidence, mismatch, failure, or timeout prevents refund.

The inspector joins actual captures by workflow/handoff IDs and verifies that
B's SERVER span has A's CLIENT span as its parent in the same OTel trace.
Original native SDK IDs are preserved and linked to bridge spans via source
annotations. Raw SDK-native, raw bridge OTel, and explicit semantic corrections
remain separately inspectable.

The deadline scenario exercises a real local async deadline before B's model
call. It is labelled injected; it does not simulate a Bedrock timeout. Additional
transport timeout tests confirm A fails closed without a response.

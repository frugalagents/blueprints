# Local telemetry contract v1

Version: refund-observability-v1. This is the source mapping for Agent 1, not yet a multi-agent backend contract.

| Identifier | Meaning | Current implementation |
|---|---|---|
| session_id | Groups related user tasks | Editable, persisted in the viewer; reused across executions |
| task_id | One requested business task | New UUID-derived ID per normal run; CLI override for explicit task correlation |
| run_id | One execution | Unique live run ID or generated CLI ID |
| attempt_id | Execution attempt for a task | Unique per execution; CLI override supported |
| trace_id / span_id | Original OTel identities | Preserved unchanged, with parent references |
| tool_call_id | Model-selected tool invocation | Original framework call ID, distinct from helper calls |

Reusing a session groups runs; it does not restore model conversation history or share SQLite state. Every execution still uses an isolated sandbox. A retry may deliberately reuse task_id while receiving a new run_id/attempt_id. There is no automatic retry manager yet.

## Export records

spans.json remains a list of original SDK span JSON records, enriched with instrumentation_scope (name, version, schema_url) and resource_schema_url. Original resource attributes, attributes, events, status, timing and links remain intact.

capture.json records contract/adapter versions, installed package versions, semantic convention selection, agent identity, model vendor, serving platform and synthetic content policy. Empty scope versions/schema URLs remain empty rather than fabricated. Frame.runtime/package version is separately recorded when the framework emits no scope version.

run.json contains session/task/run/attempt IDs, source metadata references, actual agent-selected tool invocations, internal_operation_calls, observed trajectory and independent status fields:

- execution_status: whether the agent execution completed or raised an exception.
- business_outcome: refunded, denied, failed or incomplete.
- verification_status: whether the independently checked expected scenario outcome passed.

The expected injected DB failure can have verification_status=passed and business_outcome=failed. Its process exit code indicates scenario verification, not business success. Fixture mode can have execution_status=error when its direct call raises the expected SQL exception, whereas the framework may handle the tool exception and finish its response.

Source classification uses instrumentation scope, not a guess based on attribute names. Legacy captures lacking scope are labelled unknown/legacy and are never rewritten to imply new capture provenance.

## Observed trajectory

The trajectory lists model calls, model-selected tools, eligibility, decision validation, refund/denial and independent verification. It is ordered by operation start time, retains timestamps, parent IDs and source span IDs, and preserves event names and original status. Parent and child operations overlap. A commit operation may start before its nested validation operation; the refund_committed event establishes the later successful commit.

This is an execution trajectory, not hidden model reasoning. It currently covers one task/agent. A2A handoff/message IDs, native Agent 2 IDs, trace links and missing-edge indicators will be added only after inspecting actual Agent 2 output.

## Capture policy and remaining boundaries

synthetic-full-content-v1 documents existing full-content capture. It is not a redaction implementation. Real-data cleaning, access controls, retention and collector processing remain outstanding. Original gen_ai.provider.name is preserved; capture metadata separately names Anthropic and Amazon Bedrock without rewriting the source field.

The viewer groups saved run/task counts by session. It does not aggregate multiple tasks into a single trace or invent cross-trace parents. Local JSON export is not OTLP transport and has no production retry/backpressure guarantees.

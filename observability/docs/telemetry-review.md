# Agent 1 telemetry review

Verdict: the checkpoint demonstrates real automatic, manual and extended OTel capture. It is sufficient to study one agent, but needs a few corrections before becoming the source contract for heterogeneous consolidation. This review inspected code and saved actual Bedrock captures; it did not make new model calls or change implementation.

## Evidence checked

| Capture | Framework spans | SQL spans | Manual spans | Database evidence |
|---|---:|---:|---:|---|
| eligible_final | 12 | 8 | 5 | One refund and a refunded outcome |
| denied_final | 12 | 5 | 4 | Zero refunds and a denied outcome |
| db_error_final | 12 | 7 | 5 | Zero refunds and no successful outcome record |
| Latest UI execution | 12 | 8 | 5 | One refund and a refunded outcome |

Every capture contains one trace and valid parent references. Seven business tests passed again, covering repeat protection, denial, missing/stale evidence, independent policy validation and rollback. These checks do not prove production readiness or cross-agent propagation.

## What works

- Framework spans capture agent execution, model calls, agent-selected tool invocations and internal event-loop cycles.
- SQLite instrumentation captures actual cursor SQL operations. INSERT alone does not establish commit; the manual refund_committed event is emitted after the transaction context exits successfully.
- Manual spans capture eligibility, decision validation, refund action, denial and independent outcome verification.
- Allowlisted baggage is copied into span attributes: task, session, attempt, scenario and execution mode. Extensions enrich existing spans rather than create an unrelated telemetry stream.
- The database-failure trace has ERROR at SQL, refund operation, framework tool and root request. The final model response reports failure. A successful model call does not imply a successful business operation.
- The synthetic fixture is labelled explicitly. There is no claim of a real Agent 2 or A2A handoff.

## Findings and required changes

### 1. Preserve source identity in the exporter before consolidation — high priority

JsonExporter stores only json.loads(span.to_json()). The current JSON records have no instrumentation_scope or scope schema URL. The viewer therefore classifies sources using custom app.instrumentation.capture and db.system. That is a heuristic, not a reliable source contract across SDKs.

Explicitly serialize scope name/version/schema URL and preserve resource metadata. Add a versioned export envelope and adapter/source identity. Record framework, instrumentor and semantic-convention selection in run metadata. Keep original attributes alongside normalized fields. A file containing SDK span JSON is not yet an OTLP receiver/Collector pipeline.

### 2. Separate business outcome from test execution status — high priority

The DB-error case exits zero when the expected rollback is verified. server.py maps that exit code to status=complete; the UI says Execution complete. This correctly means the scenario test completed, but can be read as a successful refund.

Expose independent fields: run execution status, business outcome and verification status. Display Failed refund / rollback verified for this case; display Denied / verified for policy denial. Do not derive business success from a process exit code or a model span's OK status. Preserve failure type/cause and handle timeout or missing run.json explicitly.

### 3. Fix reported tool-call accounting — high priority

Eligible evidence reports five tool_calls but has three framework execute_tool spans. order() adds lookup_order to the list both when the agent invokes the tool and when assess()/commit() call the internal helper. Denied similarly reports four calls versus three agent-selected tools.

Separate agent tool invocations from internal function/DB activity. Derive agent-selected invocations from tool spans and call IDs, or count at the actual tool entry points. Keep business-operation attempts as a separate measure. Use those distinctions when reconstructing a trajectory.

### 4. Session and trajectory are not yet implemented as meaningful views — high priority

The live server generates a fresh session ID for every run, so session currently equals one execution. The archive cases share session-demo-1 despite being independent scenarios. There is no persistent conversation or multi-turn session aggregation. The tree shows span nesting, including framework event loops, but has no explicit ordered task trajectory.

Define independent session_id, task_id, run_id and attempt_id. A session should be able to contain multiple tasks/traces. Construct an observed trajectory from model calls, tool selection/results, eligibility decision, action and verified outcome. Reference the underlying spans and events; do not label it the model's hidden reasoning. Future A2A task/message identifiers should remain distinct and be mapped explicitly.

### 5. Content governance is absent — required before real data

Actual exports contain gen_ai.input.messages, gen_ai.output.messages, system instructions and tool results; SQL text and exception stack traces are also retained. There is no cleaning/redaction stage. Safe synthetic fixtures make this acceptable for the checkpoint, not a policy for real workloads.

Define capture allowlists, prompt/tool-result redaction, retention and access rules before introducing real data or centralized export. Preserve non-sensitive correlation IDs. Make capture policy/version visible in the telemetry contract. Do not send user identifiers or credentials in baggage.

### 6. Extensions need ownership and precise meaning — before Agent 2

ContextExtensions sets app.policy.version and app.agent.version on every span, including SQL spans. This is useful workflow context, but does not identify the component that produced the span. The exporter also preserves gen_ai.provider.name=strands-agents; treating that blindly as model vendor would give the wrong consolidated label.

Separate emitting component, agent identity/version, model vendor, serving platform, model/profile and business policy. Keep original provider fields and explicitly map them. Version custom extension definitions and normalize units. Keep model-call token totals separate from agent aggregate totals; keep reported durations separate from span wall time. The existing non-streaming time_to_first_token field should not be presented as a verified streaming latency metric.

### 7. End-to-end collection and propagation are still unproven — next checkpoints

propagation.json proves local trace-context and baggage injection only. No receiver extracts it. There are no remote client/server spans, handoff links, native-to-OTel adapter, Collector processing, ingestion acknowledgement, metrics stream or correlated log stream.

Add these incrementally. First verify Agent 2's original native output. Then validate a real A2A exchange with inject/extract, shared task/session correlation and preserved original native IDs. Do not invent trace parentage if the native source cannot represent it. Use explicit links/mappings and label gaps.

The direct synchronous exporter rewrites all accumulated spans for each export; it can add execution overhead and has no durable retry/backpressure design. This is suitable for local inspection. A later Collector/backend checkpoint should validate batching, export failures, duplicates, partial traces, shutdown flushing and sampling compatibility across agents.

## Recommended next checkpoint

Before adding Agent 2, fix source metadata, tool counts and status semantics; agree the session/task/run/attempt and trajectory mapping. Then inspect a second independently captured agent before implementing the merge adapter. Moving this unchanged into an AWS backend would not resolve these semantic gaps. Backend selection can follow the telemetry contract; AgentCore deployment remains a separate decision.

## Implemented after review

Source scope/version/schema metadata and capture package/adapter metadata are now preserved for new runs. Agent-selected tool calls are derived from framework tool spans, separately from internal helper activity. The viewer separates execution, business outcome and verification; it exposes reusable sessions with per-task/run/attempt IDs and an evidence-linked observed trajectory. Definitions are in Telemetry_Contract_v1.md.

Validation: 14 tests passed. Fresh Bedrock capture live-9e6ef6c6e332494c has 25 spans, three agent tool invocations, 11 trajectory steps and a verified refund. A fixture-mode rollback capture in the same session verifies zero refund rows and displays failed business outcome separately from passed verification. This also demonstrates two distinct tasks grouped into one session without implying shared model memory or database state.

Content redaction, Collector/OTLP transport, native Agent 2 capture and cross-agent/A2A propagation remain outstanding. Original archived captures were not retroactively rewritten.

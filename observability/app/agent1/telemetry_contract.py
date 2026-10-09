"""Versioned local mapping; original span fields remain unchanged."""
CONTRACT_VERSION = "refund-observability-v1"


def source(span):
    scope = (span.get("instrumentation_scope") or {}).get("name", "")
    if scope == "refund.business":
        return "manual"
    if scope.startswith("opentelemetry.instrumentation.sqlite3"):
        return "sql"
    if scope.startswith("strands"):
        return "framework"
    return "unknown"


def trajectory(spans):
    """Observed model/tool/business steps, not hidden model reasoning."""
    steps = []
    for span in sorted(spans, key=lambda item: item["start_time"]):
        attrs = span.get("attributes", {})
        operation = attrs.get("gen_ai.operation.name")
        if operation not in ("chat", "execute_tool") and span["name"] not in (
            "app.eligibility.fixture", "app.decision.validate",
            "app.refund.commit", "app.refund.deny", "app.outcome.verify",
        ):
            continue
        steps.append({
            "step": len(steps) + 1,
            "operation": operation or span["name"],
            "name": span["name"],
            "start_time": span["start_time"],
            "end_time": span["end_time"],
            "trace_id": span["context"]["trace_id"],
            "span_id": span["context"]["span_id"],
            "parent_span_id": span.get("parent_id"),
            "tool_call_id": attrs.get("gen_ai.tool.call.id"),
            "status": span.get("status", {}).get("status_code", "UNSET"),
            "events": [event["name"] for event in span.get("events", [])],
            "outcome": attrs.get("app.outcome"),
        })
    return steps


def tool_invocations(spans):
    return [
        {"name": span["attributes"].get("gen_ai.tool.name"),
         "call_id": span["attributes"].get("gen_ai.tool.call.id"),
         "span_id": span["context"]["span_id"],
         "status": span["status"]["status_code"]}
        for span in sorted(spans, key=lambda item: item["start_time"])
        if span.get("attributes", {}).get("gen_ai.operation.name") == "execute_tool"
    ]

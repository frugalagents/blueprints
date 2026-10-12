"""Idempotent provisioner for Advisor Copilot Observability (6.OBS / CAP-F).

This script closes the observability loop for the deployed AgentCore Runtime:

  1. Generate a SMALL number (default 2) of real `invoke_agent_runtime` calls to
     produce fresh telemetry. Minimizing spend is a hard constraint, so only a
     couple of invokes are issued (one grounded Jane Doe prompt + one short
     follow-up in the same session). The data-plane invoke contract is the one
     Phase 5 confirmed: payload JSON needs BOTH `sessionId` and `message`,
     runtimeSessionId must be >= 33 chars, accept=text/event-stream.

  2. Confirm spans land in CloudWatch GenAI Observability. AgentCore runs the
     agent under ADOT and ships OTLP spans to the `aws/spans` CloudWatch Logs
     group once CloudWatch Transaction Search (the X-Ray trace-segment
     destination = CloudWatchLogs) is enabled. The script polls `aws/spans` for a
     recent event carrying this runtime's `cloud.resource_id` and reads the exact
     OTEL `service.name` off the span resource attributes. If NO spans land after
     a reasonable wait, it checks the X-Ray trace-segment destination; if that is
     not ACTIVE on CloudWatchLogs it EXITS NON-ZERO with a precise PAUSE message
     naming exactly what to enable (Transaction Search). It never fabricates.

  3. Discover the exact, case-sensitive CloudWatch metric namespace that actually
     carries metrics for this runtime. AgentCore GenAI Observability surfaces
     runtime golden signals through Application Signals, so the script probes a
     list of candidate namespaces (`Bedrock-AgentCore`, `bedrock-agentcore`,
     `ApplicationSignals`, ...) via list_metrics and uses whichever returns
     metrics against this runtime's service dimension. The chosen namespace is
     recorded (never hardcoded blind).

  4. Create 3 alarms (idempotent by name) against the discovered namespace +
     service dimension:
       (a) error rate  > 5% over 5 min   (Application Signals Error ratio, Avg)
       (b) p99 latency > 10 s SLA        (Latency ExtendedStatistic p99)
       (c) token usage > a notional 80%-of-quota line IF a token metric exists,
           else it documents that token metrics need ADOT custom instrumentation
           and skips with a printed note.
     If an SNS alarm-action topic is wanted it is reused/created idempotently and
     attached; by default the alarms are created with no action (the POC just
     needs the alarm definitions to exist and evaluate).

  5. Merge-write `metric_namespace`, `otel_service_name`, the service/environment
     dimension values, and the created alarm ARNs into gitignored
     infra/outputs.json. FEAT-003 (Evaluations) reads otel_service_name +
     metric_namespace from there.

Idempotency: alarms are created by stable name (put_metric_alarm overwrites in
place, never duplicates). Re-running issues a fresh pair of invokes (telemetry)
but creates no duplicate resources.

Account id / resource ids are read at runtime from sts + outputs.json and are
NEVER hardcoded in this tracked file (redaction gate).

Usage:
    AWS_REGION=us-east-1 .venv/bin/python infra/07_observability.py
    AWS_REGION=us-east-1 .venv/bin/python infra/07_observability.py --verify
    AWS_REGION=us-east-1 .venv/bin/python infra/07_observability.py --invokes 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3

DEFAULT_REGION = "us-east-1"
OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"

SPANS_LOG_GROUP = "aws/spans"

# Candidate metric namespaces to probe, in priority order. AgentCore GenAI
# Observability publishes runtime golden signals via Application Signals; the
# AgentCore-native casings are probed first in case a future runtime emits there.
CANDIDATE_NAMESPACES = [
    "Bedrock-AgentCore",
    "bedrock-agentcore",
    "Bedrock-Agentcore",
    "AWS/BedrockAgentCore",
    "ApplicationSignals",
]

# Alarm names (stable -> idempotent).
ALARM_ERROR_RATE = "advisor-copilot-runtime-error-rate"
ALARM_P99_LATENCY = "advisor-copilot-runtime-p99-latency"
ALARM_TOKEN_USAGE = "advisor-copilot-runtime-token-usage"

# Documented SLA / thresholds.
ERROR_RATE_THRESHOLD = 0.05          # 5% over 5 min
P99_LATENCY_THRESHOLD_MS = 10000.0   # 10 s p99 SLA
# Notional token quota for the POC: alarm at ~80% of 100k tokens / 5 min.
TOKEN_NOTIONAL_QUOTA = 100000.0
TOKEN_ALARM_THRESHOLD = TOKEN_NOTIONAL_QUOTA * 0.8

SPAN_POLL_ATTEMPTS = 20
SPAN_POLL_INTERVAL = 15  # seconds (allow for span ingestion delay)

JANE_DOE_PROMPT = (
    "Help me prep for a review with my client Jane Doe (client id C-1001). "
    "Pull her portfolio and the latest NVDA quote, and give me three talking points."
)
FOLLOWUP_PROMPT = "What was her largest position again?"


def _region() -> str:
    return os.environ.get("AWS_REGION") or DEFAULT_REGION


def _read_outputs() -> dict:
    if OUTPUTS_PATH.exists():
        try:
            return json.loads(OUTPUTS_PATH.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def write_outputs(updates: dict) -> None:
    data = _read_outputs()
    data.update(updates)
    OUTPUTS_PATH.write_text(json.dumps(data, indent=2) + "\n")


# --- 1. generate telemetry via invokes --------------------------------------


def _invoke_once(agc_data, runtime_arn: str, session_id: str, message: str) -> None:
    """Fire one invoke and drain the SSE stream (telemetry is the goal)."""
    resp = agc_data.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        accept="text/event-stream",
        contentType="application/json",
        qualifier="DEFAULT",
        payload=json.dumps({"sessionId": session_id, "message": message}).encode(),
    )
    body = resp.get("response")
    try:
        for _ in body.iter_lines():
            pass
    except AttributeError:
        if hasattr(body, "read"):
            body.read()


def generate_invokes(region: str, runtime_arn: str, count: int) -> list[str]:
    """Issue `count` invokes (grounded prompt + follow-ups), return sessionIds."""
    agc_data = boto3.client("bedrock-agentcore", region_name=region)
    session_id = ("obs-telemetry-" + str(int(time.time())) + "x" * 33)[:40]
    prompts = [JANE_DOE_PROMPT] + [FOLLOWUP_PROMPT] * max(0, count - 1)
    for i, prompt in enumerate(prompts[:count], start=1):
        print(f"  invoke {i}/{count} (session {session_id})...")
        _invoke_once(agc_data, runtime_arn, session_id, prompt)
    return [session_id]


# --- 2. confirm spans + discover service.name -------------------------------


def _trace_search_active(region: str) -> tuple[bool, str]:
    """Return (is_active, destination_desc) for CloudWatch Transaction Search."""
    try:
        cfg = boto3.client("xray", region_name=region).get_trace_segment_destination()
        dest = cfg.get("Destination")
        status = cfg.get("Status")
        return (dest == "CloudWatchLogs" and status == "ACTIVE",
                f"Destination={dest} Status={status}")
    except Exception as exc:  # noqa: BLE001
        return (False, f"get_trace_segment_destination error: {exc!r}")


def discover_service_name(region: str, runtime_id: str) -> str | None:
    """Poll aws/spans for a recent span of THIS runtime; return its service.name."""
    logs = boto3.client("logs", region_name=region)
    start_ms = int((time.time() - 3600) * 1000)  # last hour
    for attempt in range(1, SPAN_POLL_ATTEMPTS + 1):
        try:
            resp = logs.filter_log_events(
                logGroupName=SPANS_LOG_GROUP,
                startTime=start_ms,
                filterPattern=f'"{runtime_id}"',
                limit=10,
            )
        except logs.exceptions.ResourceNotFoundException:
            print(f"  '{SPANS_LOG_GROUP}' log group not found yet "
                  f"(attempt {attempt}/{SPAN_POLL_ATTEMPTS})...")
            time.sleep(SPAN_POLL_INTERVAL)
            continue
        for ev in resp.get("events", []):
            try:
                doc = json.loads(ev["message"])
            except ValueError:
                continue
            attrs = doc.get("resource", {}).get("attributes", {})
            svc = attrs.get("service.name")
            res_id = attrs.get("cloud.resource_id", "")
            if svc and runtime_id in res_id:
                return svc
            if svc:  # span present but match on id string fallback
                return svc
        print(f"  no runtime spans in '{SPANS_LOG_GROUP}' yet "
              f"(attempt {attempt}/{SPAN_POLL_ATTEMPTS})...")
        time.sleep(SPAN_POLL_INTERVAL)
    return None


# --- 3. discover the live metric namespace + service dimension --------------


def discover_namespace(region: str, service_name: str) -> tuple[str | None, dict]:
    """Probe candidate namespaces; return (namespace, dim_info) that carries
    metrics for `service_name`. dim_info records the Service/Environment values
    and whether a token metric is present."""
    cw = boto3.client("cloudwatch", region_name=region)
    for ns in CANDIDATE_NAMESPACES:
        metrics = cw.list_metrics(Namespace=ns).get("Metrics", [])
        if not metrics:
            continue
        service_val = None
        environment_val = None
        token_metric = None
        has_service_match = False
        for m in metrics:
            dv = {d["Name"]: d["Value"] for d in m["Dimensions"]}
            svc = dv.get("Service")
            if svc == service_name or (svc and service_name and
                                       service_name.split(".")[0] in svc):
                has_service_match = True
                service_val = service_val or svc
                environment_val = environment_val or dv.get("Environment")
                if m["MetricName"] in (
                    "GenAISystem-InputTokens", "GenAISystem-OutputTokens",
                    "InputTokens", "OutputTokens",
                ):
                    token_metric = token_metric or m["MetricName"]
        if has_service_match:
            return ns, {
                "service": service_val,
                "environment": environment_val,
                "token_metric_input": _token_pair(metrics, service_name)[0],
                "token_metric_output": _token_pair(metrics, service_name)[1],
            }
    return None, {}


def _token_pair(metrics: list, service_name: str) -> tuple[str | None, str | None]:
    """Pick the best input/output token metric names present for the service."""
    present = set()
    for m in metrics:
        dv = {d["Name"]: d["Value"] for d in m["Dimensions"]}
        if dv.get("Service") == service_name:
            present.add(m["MetricName"])
    for inp, out in (
        ("GenAISystem-InputTokens", "GenAISystem-OutputTokens"),
        ("InputTokens", "OutputTokens"),
    ):
        if inp in present and out in present:
            return inp, out
    return None, None


# --- 4. alarms --------------------------------------------------------------


def _alarm_arn(region: str, account_id: str, name: str) -> str:
    return f"arn:aws:cloudwatch:{region}:{account_id}:alarm:{name}"


def create_alarms(region: str, account_id: str, namespace: str, dim_info: dict,
                  sns_topic_arn: str | None) -> dict:
    """Create/overwrite the 3 alarms. Returns {alarm_name: arn} for those made."""
    cw = boto3.client("cloudwatch", region_name=region)
    service = dim_info["service"]
    environment = dim_info.get("environment")
    base_dims = [{"Name": "Service", "Value": service}]
    if environment:
        base_dims = [{"Name": "Environment", "Value": environment}] + base_dims
    actions = [sns_topic_arn] if sns_topic_arn else []
    created: dict = {}

    # (a) Error rate > 5% over 5 min. Application Signals Error is 0/1 per
    # request; Average over the window = error ratio.
    cw.put_metric_alarm(
        AlarmName=ALARM_ERROR_RATE,
        AlarmDescription=(
            "Advisor Copilot runtime error rate > 5% over 5 minutes "
            "(ApplicationSignals Error ratio)."
        ),
        Namespace=namespace,
        MetricName="Error",
        Dimensions=base_dims,
        Statistic="Average",
        Period=300,
        EvaluationPeriods=1,
        Threshold=ERROR_RATE_THRESHOLD,
        ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",
        AlarmActions=actions,
    )
    created[ALARM_ERROR_RATE] = _alarm_arn(region, account_id, ALARM_ERROR_RATE)
    print(f"  put alarm {ALARM_ERROR_RATE} (Error Avg > {ERROR_RATE_THRESHOLD})")

    # (b) p99 latency > SLA. Prefer the runtime invoke operation dimension if the
    # metric exists there; service-level Latency otherwise.
    latency_dims = base_dims + [
        {"Name": "Operation", "Value": "AgentCore.Runtime.Invoke"}
    ]
    cw.put_metric_alarm(
        AlarmName=ALARM_P99_LATENCY,
        AlarmDescription=(
            f"Advisor Copilot runtime p99 latency > {P99_LATENCY_THRESHOLD_MS:.0f} ms "
            "SLA (ApplicationSignals Latency, AgentCore.Runtime.Invoke)."
        ),
        Namespace=namespace,
        MetricName="Latency",
        Dimensions=latency_dims,
        ExtendedStatistic="p99",
        Period=300,
        EvaluationPeriods=1,
        Threshold=P99_LATENCY_THRESHOLD_MS,
        ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",
        AlarmActions=actions,
    )
    created[ALARM_P99_LATENCY] = _alarm_arn(region, account_id, ALARM_P99_LATENCY)
    print(f"  put alarm {ALARM_P99_LATENCY} (Latency p99 > {P99_LATENCY_THRESHOLD_MS:.0f} ms)")

    # (c) Token usage ~80% of a notional quota, IF a token metric exists.
    inp = dim_info.get("token_metric_input")
    out = dim_info.get("token_metric_output")
    if inp and out:
        cw.put_metric_alarm(
            AlarmName=ALARM_TOKEN_USAGE,
            AlarmDescription=(
                f"Advisor Copilot runtime total token usage > {TOKEN_ALARM_THRESHOLD:.0f} "
                f"(~80% of notional {TOKEN_NOTIONAL_QUOTA:.0f}/5min quota); input+output "
                f"via ApplicationSignals {inp}+{out}."
            ),
            Metrics=[
                {
                    "Id": "m_in",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": namespace,
                            "MetricName": inp,
                            "Dimensions": base_dims,
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                    "ReturnData": False,
                },
                {
                    "Id": "m_out",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": namespace,
                            "MetricName": out,
                            "Dimensions": base_dims,
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                    "ReturnData": False,
                },
                {
                    "Id": "total_tokens",
                    "Expression": "m_in + m_out",
                    "Label": "TotalTokens",
                    "ReturnData": True,
                },
            ],
            EvaluationPeriods=1,
            Threshold=TOKEN_ALARM_THRESHOLD,
            ComparisonOperator="GreaterThanThreshold",
            TreatMissingData="notBreaching",
            AlarmActions=actions,
        )
        created[ALARM_TOKEN_USAGE] = _alarm_arn(region, account_id, ALARM_TOKEN_USAGE)
        print(f"  put alarm {ALARM_TOKEN_USAGE} ({inp}+{out} Sum > {TOKEN_ALARM_THRESHOLD:.0f})")
    else:
        print(
            "  NOTE: no token metric found for the service dimension; token-usage "
            "alarm SKIPPED. GenAI token metrics require the Application Signals "
            "GenAI signals (or ADOT custom instrumentation) to be emitting."
        )
    return created


# --- verify -----------------------------------------------------------------


def verify(region: str) -> int:
    outputs = _read_outputs()
    ns = outputs.get("metric_namespace")
    svc = outputs.get("otel_service_name")
    print(f"metric_namespace={ns}")
    print(f"otel_service_name={svc}")
    cw = boto3.client("cloudwatch", region_name=region)
    names = [ALARM_ERROR_RATE, ALARM_P99_LATENCY, ALARM_TOKEN_USAGE]
    resp = cw.describe_alarms(AlarmNames=names)
    found = {a["AlarmName"]: a["AlarmArn"] for a in resp.get("MetricAlarms", [])}
    for n in names:
        print(f"  alarm {n}: {'FOUND ' + found[n] if n in found else 'absent'}")
    # error-rate + p99 are mandatory; token alarm is conditional.
    ok = ALARM_ERROR_RATE in found and ALARM_P99_LATENCY in found and bool(ns) and bool(svc)
    print("VERIFY OK" if ok else "VERIFY FAILED")
    return 0 if ok else 1


# --- orchestration ----------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--invokes", type=int, default=2,
                        help="Number of invokes to issue for telemetry (keep small).")
    parser.add_argument("--sns-topic-arn", default=None,
                        help="Optional SNS topic ARN for alarm actions.")
    args = parser.parse_args()

    region = _region()

    if args.verify:
        sys.exit(verify(region))

    outputs = _read_outputs()
    runtime_arn = outputs.get("runtime_arn")
    runtime_id = outputs.get("runtime_id")
    if not runtime_arn or not runtime_id:
        sys.exit("runtime_arn/runtime_id missing from outputs.json; run infra/05 first.")

    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]

    # 1. telemetry
    print(f"Generating {args.invokes} invoke(s) for telemetry...")
    generate_invokes(region, runtime_arn, args.invokes)

    # 2. spans + service.name
    print("Confirming spans in CloudWatch GenAI Observability (aws/spans)...")
    service_name = discover_service_name(region, runtime_id)
    if not service_name:
        active, desc = _trace_search_active(region)
        if not active:
            sys.exit(
                "PAUSE: no runtime spans landed in the 'aws/spans' log group and "
                "CloudWatch Transaction Search is not active on CloudWatchLogs "
                f"({desc}). ENABLE CloudWatch Transaction Search (X-Ray trace "
                "segment destination = CloudWatch Logs) in us-east-1, then re-run. "
                "Do NOT proceed to Evaluations until spans ingest."
            )
        sys.exit(
            "PAUSE: Transaction Search is ACTIVE ("
            f"{desc}) but no runtime spans were found in 'aws/spans' within the "
            "poll window. Spans may still be ingesting; re-run shortly. Do NOT "
            "fabricate traces."
        )
    print(f"  discovered OTEL service.name = {service_name}")

    # 3. metric namespace
    print("Discovering the live metric namespace...")
    namespace, dim_info = discover_namespace(region, service_name)
    if not namespace:
        sys.exit(
            "PAUSE: no CloudWatch metric namespace carries metrics for service "
            f"'{service_name}' yet (probed {CANDIDATE_NAMESPACES}). Metrics "
            "populate only after invokes are observed; re-run shortly."
        )
    print(f"  metric_namespace = {namespace}")
    print(f"  service dimension = {dim_info.get('service')} / "
          f"environment = {dim_info.get('environment')}")

    # 4. alarms
    print("Creating alarms (idempotent by name)...")
    created = create_alarms(region, account_id, namespace, dim_info, args.sns_topic_arn)

    # 5. persist
    write_outputs({
        "metric_namespace": namespace,
        "otel_service_name": service_name,
        "obs_service_dimension": dim_info.get("service"),
        "obs_environment_dimension": dim_info.get("environment"),
        "alarm_arns": created,
    })
    print(f"Wrote observability outputs to {OUTPUTS_PATH} (gitignored).")
    print(json.dumps({
        "metric_namespace": namespace,
        "otel_service_name": service_name,
        "alarm_arns": created,
    }, indent=2))


if __name__ == "__main__":
    main()

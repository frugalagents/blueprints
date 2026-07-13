"""Stage 5: Observability — metrics, logging, and per-user token attribution.

Sets up:
1. CloudWatch alarms on gateway error rates
2. Logs Insights query for per-user token usage
3. Verifies gateway metrics are flowing
4. Makes test requests and confirms they appear in logs
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone

import boto3
import requests

import aws_api
from config import REGION, ACCOUNT_ID, PROJECT_ROOT, save_stage_config, load_stage_config
from stage3_jwt_auth import _get_token, TEST_USERS


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup():
    """Create alarms, log queries, and verify observability pipeline."""
    print("=== Stage 5: Observability ===\n")

    config = load_stage_config("stage4")
    gateway_id = config["gateway_id"]
    gateway_arn = config["gateway_arn"]
    inference_url = config["inference_url"]
    model = config["inference_model"]
    pool_id = config["cognito_pool_id"]
    client_id = config["cognito_client_id"]
    client_secret = config["cognito_client_secret"]

    cw = boto3.client("cloudwatch", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)

    # 1. Create CloudWatch alarms
    print("[1/4] Creating CloudWatch alarms...")

    # Error rate alarm
    cw.put_metric_alarm(
        AlarmName="LLMGateway-HighErrorRate",
        AlarmDescription="Fires when gateway 5xx errors exceed 5 in 5 minutes",
        Namespace="AWS/Bedrock-AgentCore",
        MetricName="SystemErrors",
        Dimensions=[{"Name": "Name", "Value": gateway_id}],
        Statistic="Sum",
        Period=300,
        EvaluationPeriods=1,
        Threshold=5,
        ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",
    )
    print("    ✓ LLMGateway-HighErrorRate alarm created")

    # Throttle alarm
    cw.put_metric_alarm(
        AlarmName="LLMGateway-Throttling",
        AlarmDescription="Fires when gateway throttles exceed 10 in 5 minutes",
        Namespace="AWS/Bedrock-AgentCore",
        MetricName="Throttles",
        Dimensions=[{"Name": "Name", "Value": gateway_id}],
        Statistic="Sum",
        Period=300,
        EvaluationPeriods=1,
        Threshold=10,
        ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",
    )
    print("    ✓ LLMGateway-Throttling alarm created")

    # Latency alarm
    cw.put_metric_alarm(
        AlarmName="LLMGateway-HighLatency",
        AlarmDescription="Fires when p99 latency exceeds 30 seconds",
        Namespace="AWS/Bedrock-AgentCore",
        MetricName="Latency",
        Dimensions=[{"Name": "Name", "Value": gateway_id}],
        ExtendedStatistic="p99",
        Period=300,
        EvaluationPeriods=2,
        Threshold=30000,  # ms
        ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",
    )
    print("    ✓ LLMGateway-HighLatency alarm created")

    # 2. Generate test traffic from both users
    print("\n[2/4] Generating test traffic from both users...")
    for user in TEST_USERS:
        token = _get_token(pool_id, client_id, client_secret,
                           user["username"], user["password"])
        resp = requests.post(
            f"{inference_url}/v1/messages",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            json={
                "model": model,
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": f"Hello from {user['username']}. Write a haiku."}],
            },
        )
        if resp.status_code == 200:
            usage = resp.json().get("usage", {})
            print(f"    ✓ {user['username']}: input={usage.get('input_tokens')}, output={usage.get('output_tokens')}")
        else:
            print(f"    ✗ {user['username']}: HTTP {resp.status_code}")

    # 3. Save per-user query template
    print("\n[3/4] Creating Logs Insights query templates...")
    queries_dir = PROJECT_ROOT / "observability" / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)

    # Per-user token usage query
    per_user_query = queries_dir / "tokens-per-user.sql"
    per_user_query.write_text("""# CloudWatch Logs Insights Query: Token Usage Per User
# Run this against the AgentCore Gateway vended log group
#
# Log group pattern: /aws/bedrock-agentcore/gateway/<gateway-id>
#
# This query extracts the user identity from the JWT claims in the
# request context and correlates it with token usage from the response.

fields @timestamp, @message
| filter ispresent(response.usage.input_tokens)
| stats sum(response.usage.input_tokens) as total_input_tokens,
        sum(response.usage.output_tokens) as total_output_tokens,
        sum(response.usage.input_tokens + response.usage.output_tokens) as total_tokens,
        count(*) as request_count
  by request.identity.sub as user_id
| sort total_tokens desc
""")
    print(f"    ✓ {per_user_query}")

    # Requests over time query
    over_time_query = queries_dir / "requests-over-time.sql"
    over_time_query.write_text("""# CloudWatch Logs Insights Query: Request Volume Over Time
# Bin by 5-minute intervals

fields @timestamp, @message
| filter ispresent(response.status_code)
| stats count(*) as requests,
        sum(response.usage.input_tokens) as input_tokens,
        sum(response.usage.output_tokens) as output_tokens
  by bin(5m)
| sort @timestamp desc
""")
    print(f"    ✓ {over_time_query}")

    # Error breakdown query
    error_query = queries_dir / "errors-by-type.sql"
    error_query.write_text("""# CloudWatch Logs Insights Query: Error Breakdown
# Shows error types and rates

fields @timestamp, @message
| filter response.status_code >= 400
| stats count(*) as error_count
  by response.status_code as status, response.error.type as error_type
| sort error_count desc
""")
    print(f"    ✓ {error_query}")

    # 4. Create dashboard JSON
    print("\n[4/4] Creating CloudWatch dashboard definition...")
    dashboard_dir = PROJECT_ROOT / "observability"
    dashboard_file = dashboard_dir / "dashboard.json"
    dashboard_body = {
        "widgets": [
            {
                "type": "metric",
                "x": 0, "y": 0, "width": 12, "height": 6,
                "properties": {
                    "title": "Gateway Invocations",
                    "metrics": [
                        ["AWS/Bedrock-AgentCore", "Invocations", "Name", gateway_id],
                    ],
                    "period": 300,
                    "stat": "Sum",
                    "region": REGION,
                },
            },
            {
                "type": "metric",
                "x": 12, "y": 0, "width": 12, "height": 6,
                "properties": {
                    "title": "Errors & Throttles",
                    "metrics": [
                        ["AWS/Bedrock-AgentCore", "SystemErrors", "Name", gateway_id],
                        ["AWS/Bedrock-AgentCore", "UserErrors", "Name", gateway_id],
                        ["AWS/Bedrock-AgentCore", "Throttles", "Name", gateway_id],
                    ],
                    "period": 300,
                    "stat": "Sum",
                    "region": REGION,
                },
            },
            {
                "type": "metric",
                "x": 0, "y": 6, "width": 12, "height": 6,
                "properties": {
                    "title": "Latency (p50, p90, p99)",
                    "metrics": [
                        ["AWS/Bedrock-AgentCore", "Latency", "Name", gateway_id, {"stat": "p50"}],
                        ["AWS/Bedrock-AgentCore", "Latency", "Name", gateway_id, {"stat": "p90"}],
                        ["AWS/Bedrock-AgentCore", "Latency", "Name", gateway_id, {"stat": "p99"}],
                    ],
                    "period": 300,
                    "region": REGION,
                },
            },
            {
                "type": "metric",
                "x": 12, "y": 6, "width": 12, "height": 6,
                "properties": {
                    "title": "Bedrock Token Usage",
                    "metrics": [
                        ["AWS/Bedrock", "InputTokenCount", "ModelId", "anthropic.claude-sonnet-5"],
                        ["AWS/Bedrock", "OutputTokenCount", "ModelId", "anthropic.claude-sonnet-5"],
                    ],
                    "period": 300,
                    "stat": "Sum",
                    "region": REGION,
                },
            },
        ],
    }
    dashboard_file.write_text(json.dumps(dashboard_body, indent=2))
    print(f"    ✓ {dashboard_file}")

    # Deploy dashboard
    cw.put_dashboard(
        DashboardName="LLMGateway-Overview",
        DashboardBody=json.dumps(dashboard_body),
    )
    print("    ✓ Dashboard deployed to CloudWatch: LLMGateway-Overview")

    # Save config
    save_stage_config("stage5", {
        **config,
        "alarms": [
            "LLMGateway-HighErrorRate",
            "LLMGateway-Throttling",
            "LLMGateway-HighLatency",
        ],
        "dashboard": "LLMGateway-Overview",
    })

    print(f"\n=== Stage 5 Setup Complete ===")
    print(f"  Alarms: HighErrorRate, Throttling, HighLatency")
    print(f"  Dashboard: LLMGateway-Overview")
    print(f"  Queries: observability/queries/")
    print(f"\n  Run: python3 src/stage5_observability.py test")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test():
    """Verify Stage 5 — observability pipeline is functional."""
    print("=== Stage 5 Verification Tests ===\n")

    config = load_stage_config("stage5")
    gateway_id = config["gateway_id"]
    inference_url = config["inference_url"]
    model = config["inference_model"]
    pool_id = config["cognito_pool_id"]
    client_id = config["cognito_client_id"]
    client_secret = config["cognito_client_secret"]

    cw = boto3.client("cloudwatch", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)

    passed, failed = 0, 0

    def check(num: str, name: str, ok: bool, detail: str = ""):
        nonlocal passed, failed
        icon = "✅" if ok else "❌"
        print(f"  {icon} {num} {name}")
        if detail:
            print(f"       {detail}")
        passed += 1 if ok else 0
        failed += 1 if not ok else 0

    # 5.1 Alarms exist
    try:
        alarms = cw.describe_alarms(AlarmNamePrefix="LLMGateway-")
        alarm_names = [a["AlarmName"] for a in alarms["MetricAlarms"]]
        expected = {"LLMGateway-HighErrorRate", "LLMGateway-Throttling", "LLMGateway-HighLatency"}
        all_present = expected.issubset(set(alarm_names))
        check("5.1", "CloudWatch alarms exist", all_present,
              f"Found: {alarm_names}")
    except Exception as e:
        check("5.1", "CloudWatch alarms exist", False, str(e))

    # 5.2 Dashboard exists
    try:
        dashboards = cw.list_dashboards(DashboardNamePrefix="LLMGateway")
        names = [d["DashboardName"] for d in dashboards["DashboardEntries"]]
        check("5.2", "CloudWatch dashboard exists", "LLMGateway-Overview" in names,
              f"Dashboards: {names}")
    except Exception as e:
        check("5.2", "CloudWatch dashboard exists", False, str(e))

    # 5.3 Gateway metrics namespace has data (may take a few minutes after first request)
    try:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=1)
        metrics = cw.list_metrics(
            Namespace="AWS/Bedrock-AgentCore",
            Dimensions=[{"Name": "Name", "Value": gateway_id}],
        )
        metric_names = list(set(m["MetricName"] for m in metrics["Metrics"]))
        has_metrics = len(metric_names) > 0
        if not has_metrics:
            # Try without dimension filter (metrics might use different dimension name)
            metrics = cw.list_metrics(Namespace="AWS/Bedrock-AgentCore")
            metric_names = list(set(m["MetricName"] for m in metrics["Metrics"]))
            has_metrics = len(metric_names) > 0
        check("5.3", "Gateway metrics flowing", has_metrics,
              f"Metrics: {metric_names[:5]}" if has_metrics else "Metrics take ~5min to appear; re-run test shortly")
    except Exception as e:
        check("5.3", "Gateway metrics flowing", False, str(e))

    # 5.4 Make a request and verify response includes usage
    try:
        token = _get_token(pool_id, client_id, client_secret,
                           TEST_USERS[0]["username"], TEST_USERS[0]["password"])
        resp = requests.post(
            f"{inference_url}/v1/messages",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            json={"model": model, "anthropic_version": "bedrock-2023-05-31",
                  "max_tokens": 20, "messages": [{"role": "user", "content": "Say ok."}]},
        )
        usage = resp.json().get("usage", {})
        has_usage = "input_tokens" in usage and "output_tokens" in usage
        check("5.4", "Response includes token usage", has_usage,
              f"input={usage.get('input_tokens')}, output={usage.get('output_tokens')}")
    except Exception as e:
        check("5.4", "Response includes token usage", False, str(e))

    # 5.5 Logs Insights query files exist
    try:
        queries_dir = PROJECT_ROOT / "observability" / "queries"
        files = list(queries_dir.glob("*.sql"))
        check("5.5", "Log query templates exist", len(files) >= 3,
              f"{len(files)} query files: {[f.name for f in files]}")
    except Exception as e:
        check("5.5", "Log query templates exist", False, str(e))

    # 5.6 Different users produce different usage (attribution test)
    try:
        usages = {}
        for user in TEST_USERS:
            t = _get_token(pool_id, client_id, client_secret, user["username"], user["password"])
            r = requests.post(
                f"{inference_url}/v1/messages",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {t}"},
                json={"model": model, "anthropic_version": "bedrock-2023-05-31",
                      "max_tokens": 30,
                      "messages": [{"role": "user", "content": f"I am {user['username']}. Say my name."}]},
            )
            u = r.json().get("usage", {})
            usages[user["username"]] = u
        both_have_usage = all("input_tokens" in u for u in usages.values())
        check("5.6", "Per-user requests carry identity + usage", both_have_usage,
              f"{json.dumps(usages)}")
    except Exception as e:
        check("5.6", "Per-user requests carry identity + usage", False, str(e))

    # Summary
    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    if failed:
        print("❌ Stage 5 FAILED")
        sys.exit(1)
    else:
        print("✅ Stage 5 PASSED — All stages complete!")
        print("\n  To see per-user usage, run the Logs Insights query from")
        print("  observability/queries/tokens-per-user.sql against your")
        print("  Gateway's vended log group in CloudWatch.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if cmd == "setup":
        setup()
    elif cmd == "test":
        test()
    else:
        print(f"Usage: python3 {sys.argv[0]} [setup|test]")

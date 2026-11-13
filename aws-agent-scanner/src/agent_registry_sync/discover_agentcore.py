"""Discover AgentCore Gateway targets (agents/tools referenced by gateways).

Runtimes and gateways themselves are covered by Agent Registry auto-detection;
`inventory()` is used only to reconcile that coverage.
"""
from __future__ import annotations

import json
import logging

import boto3

from .models import Candidate, safe_name

log = logging.getLogger(__name__)


def _paginate(fn, key, **kw):
    token = None
    while True:
        resp = fn(**kw, **({"nextToken": token} if token else {}))
        yield from resp.get(key, [])
        token = resp.get("nextToken")
        if not token:
            return


def inventory(region: str, session: boto3.Session | None = None) -> dict:
    """Runtimes and gateways in a region (for the coverage report)."""
    c = (session or boto3).client("bedrock-agentcore-control", region_name=region)
    return {
        "runtimes": list(_paginate(c.list_agent_runtimes, "agentRuntimes")),
        "gateways": list(_paginate(c.list_gateways, "items")),
    }


def gateway_target_candidates(region: str, session: boto3.Session | None = None) -> list[Candidate]:
    c = (session or boto3).client("bedrock-agentcore-control", region_name=region)
    out: list[Candidate] = []
    for gw in _paginate(c.list_gateways, "items"):
        gw_id, gw_name = gw["gatewayId"], gw["name"]
        for t in _paginate(c.list_gateway_targets, "items", gatewayIdentifier=gw_id):
            try:
                detail = c.get_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"])
            except Exception as e:  # noqa: BLE001 - keep scanning other targets
                log.warning("get_gateway_target failed %s/%s: %s", gw_id, t["targetId"], e)
                continue
            cand = _target_to_candidate(region, gw_id, gw_name, t, detail)
            if cand:
                out.append(cand)
    return out


def _target_to_candidate(region, gw_id, gw_name, summary, detail) -> Candidate | None:
    cfg = detail.get("targetConfiguration", {})
    name = safe_name("gw", f"{region}-{gw_name}", summary["name"])
    desc = (summary.get("description") or f"Gateway target {summary['name']} on gateway {gw_name}")[:4096]
    tags = {"source": "agentcore-gateway", "region": region, "gateway": gw_name[:128]}

    mcp = cfg.get("mcp", {})
    http = cfg.get("http", {})

    if "mcpServer" in mcp and mcp["mcpServer"].get("endpoint", "").startswith("https://"):
        url = mcp["mcpServer"]["endpoint"]
        return Candidate(name, "MCP",
                         {"mcpServer": {"source": {"fromUrl": {"url": url}}}},
                         desc, summary["name"], tags)

    passthrough = http.get("passthrough", {})
    if passthrough.get("endpoint", "").startswith("https://") and passthrough.get("protocolType") == "A2A":
        return Candidate(name, "AGENT",
                         {"a2aAgentCard": {"source": {"fromUrl": {"url": passthrough["endpoint"]}}}},
                         desc, summary["name"], tags)

    if "agentcoreRuntime" in http:
        # Already covered by runtime auto-detection; skip to avoid duplicates.
        log.info("skip %s: target points at AgentCore runtime %s", name, http["agentcoreRuntime"].get("arn"))
        return None

    # Lambda / OpenAPI / Smithy / API Gateway / other: catalog as CUSTOM metadata.
    kind = next(iter(mcp), None) or next(iter(http), None) or summary.get("targetType", "unknown")
    payload = {"gatewayId": gw_id, "gatewayName": gw_name, "targetId": summary["targetId"],
               "targetName": summary["name"], "targetType": kind, "region": region}
    return Candidate(name, "CUSTOM", {"custom": {"data": json.dumps(payload)}},
                     desc, summary["name"], {**tags, "targetType": str(kind)[:128]})

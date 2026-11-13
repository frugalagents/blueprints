"""Discover agents on EKS via annotated Kubernetes Services.

Convention (set on the Service of each agent / MCP server):
  labels:       agent-registry/enabled: "true"
  annotations:  agent-registry/url:      https://agents.example.com/orders   (required, HTTPS)
                agent-registry/protocol: a2a | mcp                           (default a2a)
                agent-registry/description: free text                        (optional)

Auth to the cluster uses the caller's IAM identity (EKS access entry required).
"""
from __future__ import annotations

import base64
import json
import logging
import ssl
import urllib.request

import boto3
from botocore.signers import RequestSigner

from .models import Candidate, safe_name

log = logging.getLogger(__name__)

LABEL = "agent-registry/enabled=true"
A_URL, A_PROTO, A_DESC = "agent-registry/url", "agent-registry/protocol", "agent-registry/description"


def list_clusters(region: str, session: boto3.Session) -> list[str]:
    eks = session.client("eks", region_name=region)
    names: list[str] = []
    for page in eks.get_paginator("list_clusters").paginate():
        names += page["clusters"]
    return names


def _token(session: boto3.Session, region: str, cluster: str) -> str:
    sts = session.client("sts", region_name=region)
    signer = RequestSigner(sts.meta.service_model.service_id, region, "sts", "v4",
                           session.get_credentials(), session.events)
    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {}, "headers": {"x-k8s-aws-id": cluster}, "context": {},
    }
    url = signer.generate_presigned_url(params, region_name=region, expires_in=60, operation_name="")
    return "k8s-aws-v1." + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _services(session, region, cluster) -> list[dict]:
    info = session.client("eks", region_name=region).describe_cluster(name=cluster)["cluster"]
    ctx = ssl.create_default_context(cadata=base64.b64decode(info["certificateAuthority"]["data"]).decode())
    req = urllib.request.Request(
        f"{info['endpoint']}/api/v1/services?labelSelector={LABEL.replace('/', '%2F').replace('=', '%3D')}",
        headers={"Authorization": f"Bearer {_token(session, region, cluster)}"},
    )
    with urllib.request.urlopen(req, context=ctx, timeout=20) as r:  # noqa: S310 - https endpoint from EKS API
        return json.load(r).get("items", [])


def eks_candidates(region: str, session: boto3.Session | None = None,
                   clusters: list[str] | None = None) -> list[Candidate]:
    session = session or boto3.Session()
    out: list[Candidate] = []
    for cluster in clusters or list_clusters(region, session):
        try:
            items = _services(session, region, cluster)
        except Exception as e:  # noqa: BLE001 - one bad cluster must not stop the scan
            log.error("EKS scan failed for %s/%s: %s", region, cluster, e)
            continue
        for svc in items:
            md, ann = svc["metadata"], svc["metadata"].get("annotations", {})
            url = ann.get(A_URL, "")
            if not url.startswith("https://"):
                log.warning("skip %s/%s: missing/non-HTTPS %s annotation", md["namespace"], md["name"], A_URL)
                continue
            proto = ann.get(A_PROTO, "a2a").lower()
            descriptor = ({"mcpServer": {"source": {"fromUrl": {"url": url}}}} if proto == "mcp"
                          else {"a2aAgentCard": {"source": {"fromUrl": {"url": url}}}})
            out.append(Candidate(
                name=safe_name("eks", f"{region}-{cluster}", md["namespace"], md["name"]),
                record_type="MCP" if proto == "mcp" else "AGENT",
                descriptors=descriptor,
                description=(ann.get(A_DESC) or f"{md['name']} on EKS cluster {cluster}")[:4096],
                display_name=md["name"],
                tags={"source": "eks", "region": region, "cluster": cluster[:128], "namespace": md["namespace"][:128]},
            ))
    return out

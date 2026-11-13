"""Orchestration: scan sources, upsert records, submit drafts, report coverage."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import boto3

from .discover_agentcore import gateway_target_candidates, inventory
from .discover_eks import eks_candidates
from .registry import Registry

log = logging.getLogger(__name__)


@dataclass
class Config:
    registry_id: str
    registry_region: str = "us-east-1"
    scan_regions: list[str] = field(default_factory=lambda: ["us-east-1"])
    eks_clusters: list[str] | None = None   # None = all clusters in each region
    sources: tuple[str, ...] = ("gateway", "eks")
    auto_submit: bool = True
    prune: bool = False
    dry_run: bool = False


def run(cfg: Config, session: boto3.Session | None = None) -> dict:
    session = session or boto3.Session()
    reg = Registry(cfg.registry_id, cfg.registry_region, session)
    cands = []
    for region in cfg.scan_regions:
        try:
            if "gateway" in cfg.sources:
                cands += gateway_target_candidates(region, session)
            if "eks" in cfg.sources:
                cands += eks_candidates(region, session, cfg.eks_clusters)
        except Exception as e:  # noqa: BLE001 - region may not offer the service
            log.warning("skipping region %s: %s", region, type(e).__name__)

    existing = {r["name"]: r for r in reg.records()}
    result = {"discovered": len(cands), "created": [], "updated": [], "errors": []}
    for cand in cands:
        try:
            action = reg.upsert(cand, existing, cfg.dry_run)
            result["created" if "create" in action else "updated"].append(cand.name)
        except Exception as e:  # noqa: BLE001 - report per-record failures, keep going
            log.error("upsert failed %s: %s", cand.name, e)
            result["errors"].append({"name": cand.name, "error": str(e)})

    if cfg.auto_submit:
        result["submitted_for_approval"] = reg.submit_drafts(cfg.dry_run)
    if cfg.prune:
        result["deprecated"] = reg.prune({c.name for c in cands}, cfg.dry_run)
    result["coverage"] = coverage(cfg, reg, session)
    log.info("sync result: %s", result)
    return result


def coverage(cfg: Config, reg: Registry, session: boto3.Session) -> dict:
    """Runtimes/gateways that exist but have no auto-detected record yet."""
    names = [r["name"].lower() for r in reg.records() if r.get("createdByAutoDetection")]
    missing = {"runtimes": [], "gateways": []}
    for region in cfg.scan_regions:
        try:
            inv = inventory(region, session)
        except Exception:  # noqa: BLE001
            continue
        for rt in inv["runtimes"]:
            if not any(rt["agentRuntimeId"].lower() in n for n in names):
                missing["runtimes"].append(f"{region}:{rt['agentRuntimeName']}")
        for gw in inv["gateways"]:
            if not any(gw["gatewayId"].lower() in n for n in names):
                missing["gateways"].append(f"{region}:{gw['name']}")
    return missing

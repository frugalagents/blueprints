"""Local CLI: PYTHONPATH=src python3 -m agent_registry_sync.cli --registry-id X --regions all --dry-run"""
import argparse
import json
import logging

import boto3

from .sync import Config, run


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--registry-id", required=True)
    p.add_argument("--registry-region", default="us-east-1")
    p.add_argument("--regions", default="us-east-1", help="comma list of regions, or 'all' for every enabled region")
    p.add_argument("--eks-clusters", default="", help="comma list; default all")
    p.add_argument("--sources", default="gateway,eks")
    p.add_argument("--profile")
    p.add_argument("--no-submit", action="store_true")
    p.add_argument("--prune", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    session = boto3.Session(profile_name=a.profile)
    regions = [r for r in a.regions.split(",") if r]
    if regions == ["all"]:
        regions = sorted(r["RegionName"] for r in session.client("ec2", region_name=a.registry_region).describe_regions()["Regions"])
    cfg = Config(
        registry_id=a.registry_id, registry_region=a.registry_region,
        scan_regions=regions,
        eks_clusters=[c for c in a.eks_clusters.split(",") if c] or None,
        sources=tuple(s for s in a.sources.split(",") if s),
        auto_submit=not a.no_submit, prune=a.prune, dry_run=a.dry_run,
    )
    print(json.dumps(run(cfg, session), indent=2, default=str))


if __name__ == "__main__":
    main()

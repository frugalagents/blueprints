#!/usr/bin/env python3
"""Create (idempotently) the org-wide registry with auto-detection enabled."""
import argparse
import time

import boto3


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="org-agent-registry")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--profile")
    p.add_argument("--auto-approve", action="store_true", help="NOT recommended; see AWS docs on compensating controls")
    p.add_argument("--no-auto-detection", action="store_true")
    a = p.parse_args()
    c = boto3.Session(profile_name=a.profile).client("agent-registry-control", region_name=a.region)

    for r in c.list_registries().get("registries", []):
        if r["name"] == a.name:
            print("exists:", r["registryId"], r["status"], r.get("autoDetection"))
            return
    kw = dict(name=a.name, description="Org-wide catalog of agents, MCP servers and tools",
              discoveryConfiguration={"authorizerType": "AWS_IAM"})
    if not a.no_auto_detection:
        kw["autoDetectionConfiguration"] = {"scope": "ORGANIZATION", "enabled": True}
    arn = c.create_registry(**kw)["registryArn"]
    rid = arn.rsplit("/", 1)[-1]
    for _ in range(30):
        r = c.get_registry(registryId=rid)
        if r["status"] != "CREATING":
            break
        time.sleep(5)
    print("registry:", rid, r["status"], r.get("autoDetection"))


if __name__ == "__main__":
    main()

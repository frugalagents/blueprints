#!/usr/bin/env python3
"""Idempotent org bootstrap for Agent Registry auto-detection.

Run in the ORGANIZATION MANAGEMENT account:
  python scripts/bootstrap_org.py --delegated-admin <acct-id> [--dry-run]
Then run `scripts/create_registry.py` in the delegated admin account
(or the management account if you pass --delegated-admin <same account>).
"""
import argparse

import boto3
from botocore.exceptions import ClientError

SP = "agent-registry.amazonaws.com"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--delegated-admin", required=True)
    p.add_argument("--profile")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    s = boto3.Session(profile_name=a.profile)
    org, iam = s.client("organizations"), s.client("iam")
    me = s.client("sts").get_caller_identity()["Account"]
    master = org.describe_organization()["Organization"]["MasterAccountId"]
    assert me == master, f"run in management account {master}, not {me}"

    enabled = any(x["ServicePrincipal"] == SP for x in org.list_aws_service_access_for_organization()["EnabledServicePrincipals"])
    print("trusted access:", "already enabled" if enabled else "enabling")
    if not enabled and not a.dry_run:
        org.enable_aws_service_access(ServicePrincipal=SP)

    try:
        iam.get_role(RoleName="AWSServiceRoleForAgentRegistry")
        print("service-linked role: exists")
    except ClientError:
        print("service-linked role: creating")
        if not a.dry_run:
            iam.create_service_linked_role(AWSServiceName=SP)

    if a.delegated_admin == master:
        print("registry owner is the management account (not recommended); skipping delegated admin")
        return
    admins = [d["Id"] for d in org.list_delegated_administrators(ServicePrincipal=SP)["DelegatedAdministrators"]]
    print("delegated admin:", "already registered" if a.delegated_admin in admins else "registering")
    if a.delegated_admin not in admins and not a.dry_run:
        org.register_delegated_administrator(AccountId=a.delegated_admin, ServicePrincipal=SP)


if __name__ == "__main__":
    main()

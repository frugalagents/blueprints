"""Thin idempotent wrapper over the agent-registry-control API."""
from __future__ import annotations

import logging

import boto3

from .models import MANAGED_PREFIXES, Candidate

log = logging.getLogger(__name__)


class Registry:
    def __init__(self, registry_id: str, region: str, session: boto3.Session | None = None):
        self.id = registry_id
        self.c = (session or boto3).client("agent-registry-control", region_name=region)

    def records(self, status: str | None = None) -> list[dict]:
        kw = {"registryId": self.id}
        if status:
            kw["filters"] = [{"name": "status", "values": [status]}]
        out, token = [], None
        while True:
            resp = self.c.list_registry_records(**kw, **({"nextToken": token} if token else {}))
            out += resp.get("registryRecords", [])
            token = resp.get("nextToken")
            if not token:
                return out

    @staticmethod
    def is_managed(rec: dict) -> bool:
        return (not rec.get("createdByAutoDetection")) and rec["name"].split("/", 1)[0] in MANAGED_PREFIXES

    def upsert(self, cand: Candidate, existing: dict[str, dict], dry_run: bool = False) -> str:
        rec = existing.get(cand.name)
        if dry_run:
            return "would-update" if rec else "would-create"
        if rec:
            self.c.update_registry_record(
                registryId=self.id, recordId=rec["recordId"],
                description={"optionalValue": cand.description or cand.name},
                descriptors={k: {"optionalValue": v} for k, v in cand.descriptors.items()},
                triggerSynchronization=True,
            )
            return "updated"
        kw = dict(registryId=self.id, name=cand.name, recordType=cand.record_type,
                  descriptors=cand.descriptors, description=cand.description or cand.name,
                  tags=cand.tags)
        if cand.display_name:
            kw["displayName"] = cand.display_name
        self.c.create_registry_record(**kw)
        return "created"

    def submit_drafts(self, dry_run: bool = False) -> int:
        n = 0
        for rec in self.records(status="DRAFT"):
            if not self.is_managed(rec):
                continue
            if not dry_run:
                self.c.submit_registry_record_for_approval(registryId=self.id, recordId=rec["recordId"])
            n += 1
        return n

    def prune(self, keep: set[str], dry_run: bool = False) -> list[str]:
        """Deprecate scanner-owned records whose source no longer exists."""
        gone = []
        for rec in self.records():
            if self.is_managed(rec) and rec["name"] not in keep and rec["status"] not in ("DEPRECATED", "REJECTED"):
                gone.append(rec["name"])
                if not dry_run and rec["status"] == "APPROVED":
                    self.c.update_registry_record_status(
                        registryId=self.id, recordId=rec["recordId"], status="DEPRECATED",
                        statusReason="Source no longer discovered")
        return gone

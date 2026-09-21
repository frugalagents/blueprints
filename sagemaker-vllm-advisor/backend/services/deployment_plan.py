from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from backend.domain.models import DeploymentPlan, DeploymentPlanRequest


def create_deployment_plan(request: DeploymentPlanRequest) -> DeploymentPlan:
    cleanup_deadline = datetime.now(UTC) + timedelta(hours=request.expires_in_hours)
    canonical = {
        "assessment": request.assessment.model_dump(mode="json", by_alias=True),
        "candidate": request.candidate.model_dump(mode="json", by_alias=True),
        "modelRevision": request.model_revision,
        "containerImageDigest": request.container_image_digest,
        "maximumTestCostUsd": request.maximum_test_cost_usd,
        "cleanupDeadline": cleanup_deadline.isoformat(),
    }
    plan_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DeploymentPlan(
        plan_id=str(uuid.uuid4()),
        plan_hash=plan_hash,
        assessment=request.assessment,
        candidate=request.candidate,
        model_revision=request.model_revision,
        container_image_digest=request.container_image_digest,
        maximum_test_cost_usd=request.maximum_test_cost_usd,
        cleanup_deadline=cleanup_deadline,
    )

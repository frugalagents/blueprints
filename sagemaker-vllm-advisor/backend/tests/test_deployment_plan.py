from backend.calculators.sizing import analyze
from backend.domain.models import DeploymentPlanRequest
from backend.services.deployment_plan import create_deployment_plan
from backend.tests.test_sizing import sample_input


def test_deployment_plan_is_non_executable_and_hashed():
    assessment = sample_input()
    candidate = analyze(assessment).recommendation
    plan = create_deployment_plan(
        DeploymentPlanRequest(
            assessment=assessment,
            candidate=candidate,
            model_revision="abc123",
            container_image_digest="sha256:example",
        )
    )
    assert len(plan.plan_hash) == 64
    assert plan.executable is False
    assert "approval" in plan.blocked_reason.lower()

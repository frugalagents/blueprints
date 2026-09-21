from __future__ import annotations

from backend.calculators.sizing import COST_PENALTY, analyze
from backend.domain.models import (
    AdvisorResult,
    AgentTraceStep,
    AssessmentInput,
    EvidenceRecord,
)
from backend.providers.aws_readonly import AwsReadOnlyProvider


def _candidate_key(candidate) -> tuple:
    return (
        candidate.live_aws.quota_status != "verified" if candidate.live_aws else True,
        candidate.fit != "comfortable",
        COST_PENALTY[candidate.instance.instance_type],
        -candidate.score,
    )


def analyze_assessment(
    value: AssessmentInput, include_live_aws: bool = True
) -> AdvisorResult:
    result = analyze(value)
    if not include_live_aws or not result.candidates:
        return result

    provider = AwsReadOnlyProvider(value.region)
    result.candidates = provider.enrich_candidates(result.candidates)
    result.live_aws_enabled = True
    result.candidates.sort(key=_candidate_key)
    result.recommendation = result.candidates[0]

    priced = [
        candidate
        for candidate in result.candidates
        if candidate.live_aws and candidate.live_aws.hourly_price_usd is not None
    ]
    result.lower_cost_alternative = (
        min(priced, key=lambda item: item.live_aws.hourly_price_usd)
        if priced
        else min(
            result.candidates,
            key=lambda item: COST_PENALTY[item.instance.instance_type],
        )
    )
    result.lower_latency_alternative = max(
        result.candidates,
        key=lambda item: item.estimated_output_tokens_per_second,
    )
    result.trace.append(
        AgentTraceStep(
            phase="Observe",
            title="Read live AWS account evidence",
            detail=(
                "Applied SageMaker endpoint quotas and unambiguous endpoint-hosting "
                "prices were queried in the selected Region."
            ),
            evidence="aws",
        )
    )
    result.evidence.append(
        EvidenceRecord(
            name="aws_account_readiness",
            value={
                item.instance.instance_type: item.live_aws.model_dump(
                    mode="json", by_alias=True
                )
                for item in result.candidates
            },
            evidence_type="aws_api",
            source="AWS Service Quotas and Price List APIs",
            region=value.region,
        )
    )
    return result

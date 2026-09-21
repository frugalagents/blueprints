from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from backend.domain.models import AssessmentInput
from backend.providers.aws_readonly import AwsReadOnlyProvider
from backend.providers.huggingface import HuggingFaceProvider
from backend.services.advisor_service import analyze_assessment


class ResolveModelArgs(BaseModel):
    model_id: str = Field(min_length=3, max_length=300)


class AwsReadinessArgs(BaseModel):
    region: str = Field(pattern=r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
    instance_types: list[str] = Field(min_length=1, max_length=10)


class AnalyzeArgs(BaseModel):
    assessment: AssessmentInput
    include_live_aws: bool = True


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        validator: type[BaseModel],
        handler: Callable[[BaseModel], Awaitable[Any]],
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.validator = validator
        self.handler = handler

    async def execute(self, raw_input: dict[str, Any]) -> Any:
        validated = self.validator.model_validate(raw_input)
        return await self.handler(validated)

    def bedrock_spec(self) -> dict[str, Any]:
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {"json": self.input_schema},
            }
        }


async def _resolve_model(args: ResolveModelArgs):
    return (
        await HuggingFaceProvider().resolve(args.model_id)
    ).model_dump(mode="json", by_alias=True)


async def _analyze(args: AnalyzeArgs):
    return analyze_assessment(
        args.assessment, include_live_aws=args.include_live_aws
    ).model_dump(mode="json", by_alias=True)


async def _aws_readiness(args: AwsReadinessArgs):
    provider = AwsReadOnlyProvider(args.region)
    quotas = provider.endpoint_quotas()
    result = {}
    for instance_type in args.instance_types:
        quota = quotas.get(instance_type.lower())
        price, source = provider.endpoint_hourly_price(instance_type)
        result[instance_type] = {
            "endpointQuota": quota,
            "hourlyPriceUsd": price,
            "priceSource": source,
        }
    return result


TOOLS = {
    tool.name: tool
    for tool in [
        Tool(
            name="resolve_huggingface_model",
            description=(
                "Resolve public Hugging Face model architecture, parameter count, "
                "precision, and immutable revision."
            ),
            input_schema=ResolveModelArgs.model_json_schema(),
            validator=ResolveModelArgs,
            handler=_resolve_model,
        ),
        Tool(
            name="analyze_sagemaker_candidates",
            description=(
                "Run deterministic memory, KV-cache, vLLM, replica, quota, and pricing "
                "analysis for a complete assessment."
            ),
            input_schema=AnalyzeArgs.model_json_schema(by_alias=True),
            validator=AnalyzeArgs,
            handler=_analyze,
        ),
        Tool(
            name="get_aws_readiness",
            description=(
                "Read applied SageMaker endpoint quotas and unambiguous endpoint "
                "pricing for selected instance types. This tool is read-only."
            ),
            input_schema=AwsReadinessArgs.model_json_schema(),
            validator=AwsReadinessArgs,
            handler=_aws_readiness,
        ),
    ]
}

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.config import Config
from pydantic import ValidationError

from backend.agent.tools import TOOLS
from backend.domain.models import AgentAnswer, AgentQuestion, AgentTraceStep

SYSTEM_PROMPT = """You are the SageMaker vLLM Deployment Advisor.
Use tools to retrieve facts and perform calculations. Never invent AWS prices,
quotas, model architecture, benchmark results, or deployment status.
Clearly separate customer input, deterministic calculation, AWS API evidence,
planning heuristic, and benchmark-required claims.
You are read-only. You cannot create, update, or delete AWS resources.
If deployment is requested, explain that an immutable deployment plan and
explicit approval are required. Keep answers concise and practical."""


class BedrockAdvisorAgent:
    def __init__(self, client: Any | None = None, model_id: str | None = None):
        self.model_id = model_id or os.getenv(
            "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
        )
        self.client = client or boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
        )

    async def ask(self, request: AgentQuestion) -> AgentAnswer:
        context = request.question
        if request.assessment:
            context += "\n\nCurrent assessment JSON:\n" + request.assessment.model_dump_json(
                by_alias=True
            )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": context}]}
        ]
        tool_config = {"tools": [tool.bedrock_spec() for tool in TOOLS.values()]}
        trace: list[AgentTraceStep] = [
            AgentTraceStep(
                phase="Reason",
                title="Interpret the customer question",
                detail="Determine which verified facts or calculations are needed.",
                evidence="input",
            )
        ]
        tools_used: list[str] = []

        for _ in range(6):
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                inferenceConfig={"maxTokens": 1200, "temperature": 0.1},
                toolConfig=tool_config,
            )
            assistant_message = response["output"]["message"]
            messages.append(assistant_message)
            if response.get("stopReason") != "tool_use":
                answer = "\n".join(
                    block["text"]
                    for block in assistant_message.get("content", [])
                    if "text" in block
                ).strip()
                return AgentAnswer(
                    answer=answer or "The agent returned no text.",
                    model_id=self.model_id,
                    trace=trace,
                    tools_used=tools_used,
                )

            tool_results = []
            for block in assistant_message.get("content", []):
                tool_use = block.get("toolUse")
                if not tool_use:
                    continue
                name = tool_use["name"]
                trace.append(
                    AgentTraceStep(
                        phase="Act",
                        title=f"Call {name}",
                        detail="Validate the requested tool arguments and execute read-only logic.",
                        evidence="calculated",
                    )
                )
                try:
                    tool = TOOLS[name]
                    result = await tool.execute(tool_use.get("input", {}))
                    status = "success"
                    tools_used.append(name)
                except (KeyError, ValidationError, ValueError, PermissionError) as error:
                    result = {"error": str(error)}
                    status = "error"
                trace.append(
                    AgentTraceStep(
                        phase="Observe",
                        title=f"{name} returned {status}",
                        detail=(
                            "Tool output was returned to the reasoning model with provenance."
                            if status == "success"
                            else str(result)
                        ),
                        evidence="aws" if name == "get_aws_readiness" else "calculated",
                    )
                )
                payload = json.dumps(result, default=str)
                if len(payload) > 45_000:
                    payload = payload[:45_000] + ',"truncated":true}'
                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_use["toolUseId"],
                            "status": status,
                            "content": [{"text": payload}],
                        }
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError("The agent exceeded the maximum of six reasoning turns.")

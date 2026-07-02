from __future__ import annotations

import json
from typing import Any


class BedrockClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        import boto3

        self.client = boto3.client("bedrock-runtime", region_name=config.get("region", "us-east-1"))

    def converse_text(self, prompt: str, model_key: str = "synthesis_model", system: str | None = None) -> str:
        model_id = self.config.get(model_key) or self.config.get("planning_model")
        kwargs: dict[str, Any] = {
            "modelId": model_id,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 4096, "temperature": 0.1},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        response = self.client.converse(**kwargs)
        return response["output"]["message"]["content"][0]["text"]

    def converse_json(self, prompt: str, model_key: str = "planning_model") -> dict[str, Any]:
        text = self.converse_text(prompt, model_key=model_key)
        text = _strip_markdown_json(text)
        return json.loads(text)


def _strip_markdown_json(text: str) -> str:
    if "```json" in text:
        return text.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text.strip()

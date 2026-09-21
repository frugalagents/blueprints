from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from backend.domain.models import (
    EvidenceRecord,
    ModelMetadata,
    ModelSearchResult,
    Precision,
)


def precision_from_dtype(dtype: str | None) -> Precision | None:
    normalized = (dtype or "").lower()
    if normalized in {"bfloat16", "bf16"}:
        return Precision.BF16
    if normalized in {"float16", "fp16"}:
        return Precision.FP16
    if normalized in {"float8", "fp8"}:
        return Precision.FP8
    return None


class HuggingFaceProvider:
    def __init__(self, token: str | None = None, timeout_seconds: float = 15):
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout_seconds

    async def search(self, query: str, limit: int = 8) -> list[ModelSearchResult]:
        params = {
            "search": query,
            "filter": "text-generation",
            "sort": "downloads",
            "direction": "-1",
            "limit": str(min(max(limit, 1), 25)),
        }
        async with httpx.AsyncClient(
            headers=self._headers, timeout=self._timeout
        ) as client:
            response = await client.get("https://huggingface.co/api/models", params=params)
            response.raise_for_status()
            return [
                ModelSearchResult(
                    id=item["id"],
                    downloads=item.get("downloads", 0),
                    likes=item.get("likes", 0),
                    gated=item.get("gated", False),
                    pipeline_tag=item.get("pipeline_tag"),
                )
                for item in response.json()
            ]

    async def resolve(self, model_id: str) -> ModelMetadata:
        encoded = "/".join(quote(part, safe="") for part in model_id.split("/"))
        async with httpx.AsyncClient(
            headers=self._headers,
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            details_response, config_response = await asyncio.gather(
                client.get(f"https://huggingface.co/api/models/{encoded}"),
                client.get(
                    f"https://huggingface.co/{encoded}/resolve/main/config.json"
                ),
            )
        details_response.raise_for_status()
        details: dict[str, Any] = details_response.json()
        gated = bool(details.get("gated"))
        if config_response.status_code >= 400:
            if gated:
                raise PermissionError(
                    "This gated model requires a configured Hugging Face token."
                )
            config_response.raise_for_status()
        config: dict[str, Any] = config_response.json()

        total_parameters = (details.get("safetensors") or {}).get("total")
        revision = details.get("sha")
        architecture = (config.get("architectures") or ["Unknown architecture"])[0]
        attention_heads = config.get("num_attention_heads") or config.get("n_head")
        kv_heads = config.get("num_key_value_heads") or attention_heads
        context = config.get("max_position_embeddings") or config.get("n_positions")

        return ModelMetadata(
            id=details["id"],
            revision=revision,
            architecture=architecture,
            model_type=config.get("model_type", "unknown"),
            gated=gated,
            parameter_billions=(
                total_parameters / 1_000_000_000 if total_parameters else None
            ),
            layers=config.get("num_hidden_layers") or config.get("n_layer"),
            hidden_size=config.get("hidden_size") or config.get("n_embd"),
            attention_heads=attention_heads,
            kv_heads=kv_heads,
            max_position_embeddings=context,
            precision=precision_from_dtype(config.get("torch_dtype")),
            evidence=[
                EvidenceRecord(
                    name="model_revision",
                    value=revision,
                    evidence_type="huggingface_api",
                    source="Hugging Face Hub API",
                    source_reference=f"https://huggingface.co/{model_id}",
                ),
                EvidenceRecord(
                    name="model_config",
                    value=architecture,
                    evidence_type="huggingface_api",
                    source="Hugging Face config.json",
                    source_reference=(
                        f"https://huggingface.co/{model_id}/resolve/main/config.json"
                    ),
                ),
            ],
        )

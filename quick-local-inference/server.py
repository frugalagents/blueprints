#!/usr/bin/env python3
"""
Local Inference MCP server.

Exposes local LLM inference (Ollama / LM Studio / llama.cpp — anything with an
OpenAI-compatible /v1/chat/completions endpoint) as MCP tools that Amazon Quick
(or any MCP client) can call.

Tools:
  - local_infer(prompt, system=None, temperature=0.7, max_tokens=1024)
  - local_list_models()

Config via environment variables:
  LOCAL_LLM_BASE_URL   default: http://localhost:11434/v1   (Ollama)
  LOCAL_LLM_MODEL      default: llama3.1
  LOCAL_LLM_API_KEY    default: "ollama" (Ollama ignores it; LM Studio too)

Run:
  pip install "mcp[cli]" httpx
  python server.py
"""

# /// script
# dependencies = ["mcp[cli]", "httpx"]
# ///

import os
import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
MODEL = os.environ.get("LOCAL_LLM_MODEL", "llama3.1")
API_KEY = os.environ.get("LOCAL_LLM_API_KEY", "ollama")

mcp = FastMCP("local-inference")


@mcp.tool()
async def local_infer(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> str:
    """Run a prompt through the locally-hosted LLM and return the completion.

    Args:
        prompt: The user prompt to send to the local model.
        system: Optional system prompt to steer behavior.
        temperature: Sampling temperature (0.0 = deterministic).
        max_tokens: Maximum tokens to generate.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {API_KEY}"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions", json=payload, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]


@mcp.tool()
async def local_list_models() -> str:
    """List models available on the local inference server."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{BASE_URL}/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    models = [m.get("id", "?") for m in data.get("data", [])]
    return "\n".join(models) if models else "No models found."


if __name__ == "__main__":
    mcp.run()  # stdio transport

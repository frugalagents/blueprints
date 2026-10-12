"""Shared configuration loader for the Advisor Copilot POC.

Reads settings from the environment with sane local defaults. Infrastructure IDs
(Guardrail, Gateway, Runtime, Memory) default to empty and MUST NOT raise when absent:
while they are blank the application runs in *local mode* (local tools, no AgentCore
infra). Uses only the standard library so importing config does not pull in a hard
pydantic dependency.

Run directly to print the resolved config:

    python3 agent/config.py
    AWS_REGION=us-west-2 python3 agent/config.py   # region override
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict

# Local defaults. The model id mirrors .env.example (Claude Sonnet 4.5 in us-east-1).
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# Infra IDs that, when all blank, mean "local mode".
_INFRA_FIELDS = ("guardrail_id", "gateway_mcp_url", "runtime_endpoint_arn", "memory_id")


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    aws_region: str
    bedrock_model_id: str
    guardrail_id: str
    guardrail_version: str
    gateway_mcp_url: str
    runtime_endpoint_arn: str
    memory_id: str
    agent_tools: str

    def is_local_mode(self) -> bool:
        """True when no AgentCore infra IDs are configured (all blank)."""
        return not any(getattr(self, field).strip() for field in _INFRA_FIELDS)

    def use_gateway_tools(self) -> bool:
        """True when the agent should call the Gateway MCP tools (not local).

        Resolution order (an explicit flag always wins so local dev stays
        possible even with a gateway URL present):
          * ``AGENT_TOOLS=local`` or ``LOCAL_TOOLS=1`` -> local adapters.
          * ``AGENT_TOOLS=gateway`` -> gateway tools (requires a gateway URL).
          * otherwise -> gateway tools when a ``gateway_mcp_url`` is configured,
            local adapters when it is blank.
        """
        choice = (self.agent_tools or "").strip().lower()
        if choice in ("local", "0", "false", "off"):
            return False
        if choice in ("gateway", "mcp", "1", "true", "on"):
            return bool(self.gateway_mcp_url.strip())
        return bool(self.gateway_mcp_url.strip())

    def to_dict(self) -> dict:
        return asdict(self)


def _env(name: str, default: str = "") -> str:
    """Read an env var, treating unset or empty as the default."""
    value = os.environ.get(name)
    return value if value is not None else default


def _agent_tools_env() -> str:
    """Resolve the tool-source flag from AGENT_TOOLS or the LOCAL_TOOLS switch."""
    explicit = _env("AGENT_TOOLS")
    if explicit:
        return explicit
    # LOCAL_TOOLS=1 is an alias forcing the local adapters even in INFRA mode.
    if _env("LOCAL_TOOLS").strip().lower() in ("1", "true", "on", "yes"):
        return "local"
    return ""


def load_config() -> Config:
    """Build a Config from the environment with local-safe defaults."""
    return Config(
        aws_region=_env("AWS_REGION", DEFAULT_AWS_REGION) or DEFAULT_AWS_REGION,
        bedrock_model_id=_env("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL_ID)
        or DEFAULT_BEDROCK_MODEL_ID,
        guardrail_id=_env("GUARDRAIL_ID"),
        guardrail_version=_env("GUARDRAIL_VERSION"),
        gateway_mcp_url=_env("GATEWAY_MCP_URL"),
        runtime_endpoint_arn=_env("RUNTIME_ENDPOINT_ARN"),
        memory_id=_env("MEMORY_ID"),
        agent_tools=_agent_tools_env(),
    )


def is_local_mode() -> bool:
    """Convenience helper: True when the current environment is local mode."""
    return load_config().is_local_mode()


def main() -> None:
    config = load_config()
    print("Advisor Copilot — resolved configuration")
    print(f"  mode               : {'LOCAL' if config.is_local_mode() else 'INFRA'}")
    print(f"  aws_region         : {config.aws_region}")
    print(f"  bedrock_model_id   : {config.bedrock_model_id}")
    print(f"  guardrail_id       : {config.guardrail_id or '(unset)'}")
    print(f"  guardrail_version  : {config.guardrail_version or '(unset)'}")
    print(f"  gateway_mcp_url    : {config.gateway_mcp_url or '(unset)'}")
    print(f"  runtime_endpoint_arn: {config.runtime_endpoint_arn or '(unset)'}")
    print(f"  memory_id          : {config.memory_id or '(unset)'}")
    print(f"  tools              : {'GATEWAY' if config.use_gateway_tools() else 'LOCAL'}"
          f" (AGENT_TOOLS={config.agent_tools or '(unset)'})")


if __name__ == "__main__":
    main()

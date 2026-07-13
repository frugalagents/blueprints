"""Shared configuration for all stages."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / ".config"

# AWS
REGION = "us-east-1"
ACCOUNT_ID = None  # Set from environment or .config files

# Bedrock
MODEL_ID = "us.anthropic.claude-sonnet-4-6"
GUARDRAIL_NAME = "llm-gateway-guardrail"

# IAM
GATEWAY_ROLE_NAME = "AgentCoreGatewayRole"
GATEWAY_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{GATEWAY_ROLE_NAME}"


def save_stage_config(stage: str, data: dict):
    """Save stage output config for downstream stages."""
    CONFIG_DIR.mkdir(exist_ok=True)
    config_file = CONFIG_DIR / f"{stage}.json"
    config_file.write_text(json.dumps(data, indent=2))
    print(f"    Config saved to .config/{stage}.json")


def load_stage_config(stage: str) -> dict:
    """Load config from a previous stage."""
    config_file = CONFIG_DIR / f"{stage}.json"
    if not config_file.exists():
        raise FileNotFoundError(f"Run stage {stage} setup first. Missing: {config_file}")
    return json.loads(config_file.read_text())

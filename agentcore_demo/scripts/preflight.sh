#!/usr/bin/env bash
#
# preflight.sh — Advisor Copilot AgentCore POC preflight check.
#
# Run this MANUALLY, with valid AWS credentials for us-east-1, before starting the
# Phase 2 AgentCore deploy. It performs read-only checks only (no resources created).
# Phase 0/1 make ZERO AWS calls, so this script is intentionally never run during them.
#
# Usage:
#   ./scripts/preflight.sh
#
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"

echo "== AWS CLI version =="
aws --version

echo
echo "== Caller identity =="
aws sts get-caller-identity

echo
echo "== Enabled Anthropic Bedrock models in ${REGION} =="
aws bedrock list-foundation-models \
  --region "${REGION}" \
  --by-provider anthropic \
  --query 'modelSummaries[].modelId' \
  --output table

echo
echo "Preflight checks completed."

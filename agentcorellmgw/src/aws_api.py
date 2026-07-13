"""Low-level SigV4-signed HTTP client for AgentCore APIs.

The boto3 SDK doesn't yet support inference targets, so we use raw HTTP
with SigV4 signing for gateway operations that need it.
"""

import json
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import requests

from config import REGION

SERVICE = "bedrock-agentcore"
ENDPOINT = f"https://bedrock-agentcore-control.{REGION}.amazonaws.com"


def _get_creds():
    session = boto3.Session(region_name=REGION)
    return session.get_credentials().get_frozen_credentials()


def _signed_request(method: str, path: str, body: dict | None = None) -> requests.Response:
    """Make a SigV4-signed request to the AgentCore control plane."""
    url = f"{ENDPOINT}{path}"
    data = json.dumps(body) if body else None
    headers = {"Content-Type": "application/json"}

    aws_request = AWSRequest(method=method, url=url, data=data, headers=headers)
    SigV4Auth(_get_creds(), SERVICE, REGION).add_auth(aws_request)

    return requests.request(method, url, data=data, headers=dict(aws_request.headers))


def create_gateway(name: str, role_arn: str, authorizer_type: str = "NONE",
                   authorizer_config: dict | None = None,
                   exception_level: str = "DEBUG") -> dict:
    """Create an AgentCore Gateway."""
    body = {
        "name": name,
        "roleArn": role_arn,
        "authorizerType": authorizer_type,
        "protocolType": "MCP",
        "exceptionLevel": exception_level,
    }
    if authorizer_config:
        body["authorizerConfiguration"] = authorizer_config
    resp = _signed_request("POST", "/gateways", body)
    resp.raise_for_status()
    return resp.json()


def get_gateway(gateway_id: str) -> dict:
    """Get gateway details."""
    resp = _signed_request("GET", f"/gateways/{gateway_id}")
    resp.raise_for_status()
    return resp.json()


def update_gateway(gateway_id: str, body: dict) -> dict:
    """Update an existing gateway."""
    resp = _signed_request("PUT", f"/gateways/{gateway_id}", body)
    resp.raise_for_status()
    return resp.json()


def list_gateways() -> list:
    """List all gateways."""
    resp = _signed_request("GET", "/gateways")
    resp.raise_for_status()
    return resp.json().get("items", [])


def delete_gateway(gateway_id: str) -> dict:
    """Delete a gateway."""
    resp = _signed_request("DELETE", f"/gateways/{gateway_id}")
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def create_gateway_target(gateway_id: str, name: str,
                          target_configuration: dict,
                          credential_configs: list | None = None) -> dict:
    """Create a gateway target (inference or MCP)."""
    body = {
        "name": name,
        "targetConfiguration": target_configuration,
    }
    if credential_configs:
        body["credentialProviderConfigurations"] = credential_configs
    resp = _signed_request("POST", f"/gateways/{gateway_id}/targets", body)
    resp.raise_for_status()
    return resp.json()


def get_gateway_target(gateway_id: str, target_id: str) -> dict:
    """Get target details."""
    resp = _signed_request("GET", f"/gateways/{gateway_id}/targets/{target_id}")
    resp.raise_for_status()
    return resp.json()


def list_gateway_targets(gateway_id: str) -> list:
    """List all targets for a gateway."""
    resp = _signed_request("GET", f"/gateways/{gateway_id}/targets")
    resp.raise_for_status()
    return resp.json().get("items", [])


def delete_gateway_target(gateway_id: str, target_id: str) -> dict:
    """Delete a gateway target."""
    resp = _signed_request("DELETE", f"/gateways/{gateway_id}/targets/{target_id}")
    resp.raise_for_status()
    return resp.json() if resp.text else {}

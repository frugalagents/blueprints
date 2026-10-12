"""Gateway MCP client wiring for the Advisor Copilot agent (task 5.1).

In INFRA mode the agent reaches its five tools (portfolio / market / research)
through the AgentCore **Gateway** MCP endpoint instead of the in-process local
adapters. The gateway is created with ``authorizerType=AWS_IAM`` (see
``infra/03_gateway.py``), so every MCP request MUST be signed with AWS SigV4
(service ``bedrock-agentcore``, region ``us-east-1``) — the same signing proven
in ``infra/03_gateway.py:_mcp_request``.

This module provides:

* ``SigV4HttpxAuth`` — an ``httpx.Auth`` that signs each outgoing request with
  botocore ``SigV4Auth`` so the Strands ``MCPClient`` can talk to the AWS_IAM
  gateway (``MCPClientCredentials`` is OAuth-only and cannot carry SigV4).
* ``build_gateway_mcp_client(url, region)`` — construct a Strands ``MCPClient``
  pointed at the gateway ``/mcp`` URL with the SigV4 auth provider, over the
  streamable-HTTP transport.

The agent (``agent/agent.py``) opens the client, calls ``list_tools_sync()`` to
discover the gateway-namespaced tools (``<target>___<tool>``), and passes those
MCP tools to the ``Agent`` in place of the local ``@tool`` shims. No tool names
are hardcoded — whatever the gateway advertises is what the agent uses.
"""

from __future__ import annotations

from typing import Generator

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# The gateway is an AgentCore resource; SigV4 is computed against this service
# name (matches infra/03_gateway.py and the runtime execution-role policy).
SIGV4_SERVICE = "bedrock-agentcore"


class SigV4HttpxAuth(httpx.Auth):
    """Sign each httpx request with AWS SigV4 for the AgentCore gateway.

    httpx calls ``auth_flow`` for every request; we recompute the SigV4 headers
    from the request's method/url/body/headers using the ambient AWS credentials
    (resolved once from the default boto3 session) and inject the signed headers
    before the request goes out. Credentials are resolved lazily so importing
    this module never touches the network.
    """

    # SigV4 signs over the body, so httpx must send the full request body in one
    # pass (no streaming request bodies). The gateway payloads are tiny JSON-RPC
    # messages, so this is fine.
    requires_request_body = True

    def __init__(self, region: str, service: str = SIGV4_SERVICE) -> None:
        self._region = region
        self._service = service
        self._session = boto3.Session()
        self._credentials = None  # resolved on first use

    def _creds(self):
        if self._credentials is None:
            creds = self._session.get_credentials()
            if creds is None:
                raise RuntimeError(
                    "No AWS credentials available to sign gateway MCP requests. "
                    "Configure credentials (env, profile, or role) before "
                    "running the agent in INFRA mode."
                )
            self._credentials = creds
        return self._credentials

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        body = request.content or b""
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=body,
            headers=dict(request.headers),
        )
        # Frozen credentials keep the signature stable for this request.
        SigV4Auth(self._creds(), self._service, self._region).add_auth(aws_request)
        # Copy the signed auth headers (Authorization, X-Amz-Date,
        # X-Amz-Security-Token, X-Amz-Content-SHA256) back onto the httpx request.
        for key, value in aws_request.headers.items():
            request.headers[key] = value
        yield request


def build_gateway_mcp_client(url: str, region: str):
    """Return a Strands ``MCPClient`` for the AWS_IAM gateway ``/mcp`` endpoint.

    Uses the streamable-HTTP transport with a SigV4 ``httpx.Auth`` provider so
    the client authenticates to the AWS_IAM gateway. The returned client is a
    context manager — the caller opens it, lists/loads tools, and runs the
    agent inside the ``with`` block.
    """
    # Imported here (not at module top) so importing this module stays cheap and
    # does not require the strands MCP extras unless INFRA mode is actually used.
    from strands.tools.mcp import MCPClient

    return MCPClient(
        url=url,
        auth_provider=SigV4HttpxAuth(region=region),
    )

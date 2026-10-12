#!/usr/bin/env python3
"""Mock Portfolio API -- synthetic client/portfolio data (design.md section 4.1).

The core-logic functions are plain Python and callable WITHOUT a Lambda event,
so they can be reused by the local tool adapters (mocks/local_tools.py). The
handler(event, context) wrapper makes the module Gateway-ready: it accepts both
a direct dict of params and an API-Gateway-style event with pathParameters /
queryStringParameters, and returns a {statusCode, body} response whose body is
JSON matching the section 4.1 data model.

Fixtures are resolved relative to this module file, so cwd does not matter.
No AWS calls.
"""
import json
import os

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "clients.json")

_CLIENTS_CACHE = None


def _load_clients():
    global _CLIENTS_CACHE
    if _CLIENTS_CACHE is None:
        with open(_FIXTURE, "r", encoding="utf-8") as fh:
            _CLIENTS_CACHE = json.load(fh)
    return _CLIENTS_CACHE


# --- core logic (callable without an event) ---------------------------------

def get_client(client_id):
    """Return the full client/portfolio record for client_id, or None."""
    for c in _load_clients():
        if c["client_id"] == client_id:
            return c
    return None


def get_positions(client_id):
    """Return the list of positions for client_id (empty list if unknown)."""
    client = get_client(client_id)
    return client["positions"] if client else []


def get_concentration_flags(client_id):
    """Return the concentration_flags list for client_id (empty if unknown)."""
    client = get_client(client_id)
    return client["concentration_flags"] if client else []


# --- Gateway-ready Lambda handler -------------------------------------------

def _extract_params(event):
    """Merge direct dict params with API-Gateway pathParameters / query params."""
    params = {}
    if isinstance(event, dict):
        for key in ("pathParameters", "queryStringParameters"):
            section = event.get(key)
            if isinstance(section, dict):
                params.update(section)
        # also accept top-level direct params
        for k, v in event.items():
            if k not in ("pathParameters", "queryStringParameters", "body",
                         "headers", "requestContext", "httpMethod", "resource"):
                params[k] = v
    return params


def _response(status, payload):
    return {"statusCode": status, "body": json.dumps(payload)}


def handler(event, context):
    """Lambda entry point: return a client portfolio by client_id."""
    params = _extract_params(event)
    client_id = params.get("client_id")
    if not client_id:
        return _response(400, {"error": "client_id is required"})

    client = get_client(client_id)
    if client is None:
        return _response(404, {"error": f"client {client_id} not found"})
    return _response(200, client)

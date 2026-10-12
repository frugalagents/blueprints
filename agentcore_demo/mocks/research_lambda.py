#!/usr/bin/env python3
"""Mock Research Notes API -- synthetic internal research (design.md section 4.3).

Core-logic functions are plain Python and callable WITHOUT a Lambda event; the
handler(event, context) wrapper is Gateway-ready (accepts direct dict params or
API-Gateway pathParameters / queryStringParameters) and returns {statusCode,
body} where body is JSON matching the section 4.3 shape ({note_id, ticker,
analyst, rating, price_target, published, summary, disclaimer_required}).

Fixtures are resolved relative to this module file. No AWS calls.
"""
import json
import os

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "research.json")

_RESEARCH_CACHE = None


def _load_research():
    global _RESEARCH_CACHE
    if _RESEARCH_CACHE is None:
        with open(_FIXTURE, "r", encoding="utf-8") as fh:
            _RESEARCH_CACHE = json.load(fh)
    return _RESEARCH_CACHE


# --- core logic (callable without an event) ---------------------------------

def list_research(ticker):
    """Return all research notes for a ticker, newest first (empty if none)."""
    key = ticker.upper()
    notes = [n for n in _load_research() if n["ticker"] == key]
    return sorted(notes, key=lambda n: n["published"], reverse=True)


def get_research(note_id):
    """Return a single research note by note_id, or None if unknown."""
    for n in _load_research():
        if n["note_id"] == note_id:
            return n
    return None


# --- Gateway-ready Lambda handler -------------------------------------------

def _extract_params(event):
    params = {}
    if isinstance(event, dict):
        for key in ("pathParameters", "queryStringParameters"):
            section = event.get(key)
            if isinstance(section, dict):
                params.update(section)
        for k, v in event.items():
            if k not in ("pathParameters", "queryStringParameters", "body",
                         "headers", "requestContext", "httpMethod", "resource"):
                params[k] = v
    return params


def _response(status, payload):
    return {"statusCode": status, "body": json.dumps(payload)}


def handler(event, context):
    """Lambda entry point: fetch a note by `note_id`, or list notes by `ticker`.

    `note_id` takes precedence and returns a single note object. Otherwise
    `ticker` returns a JSON array of notes for that ticker.
    """
    params = _extract_params(event)

    note_id = params.get("note_id")
    if note_id:
        note = get_research(note_id)
        if note is None:
            return _response(404, {"error": f"note {note_id} not found"})
        return _response(200, note)

    ticker = params.get("ticker")
    if not ticker:
        return _response(400, {"error": "ticker (or note_id) is required"})
    return _response(200, list_research(ticker))

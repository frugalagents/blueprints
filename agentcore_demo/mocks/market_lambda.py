#!/usr/bin/env python3
"""Mock Market Data API -- synthetic snapshot quotes (design.md section 4.2).

Core-logic functions are plain Python and callable WITHOUT a Lambda event; the
handler(event, context) wrapper is Gateway-ready (accepts direct dict params or
API-Gateway pathParameters / queryStringParameters) and returns {statusCode,
body} where body is JSON matching the section 4.2 shape
({ticker, last, change_pct, pe, as_of}).

Fixtures are resolved relative to this module file. No AWS calls.
"""
import json
import os

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "market.json")

_MARKET_CACHE = None


def _load_market():
    global _MARKET_CACHE
    if _MARKET_CACHE is None:
        with open(_FIXTURE, "r", encoding="utf-8") as fh:
            _MARKET_CACHE = json.load(fh)
    return _MARKET_CACHE


# --- core logic (callable without an event) ---------------------------------

def get_quote(ticker):
    """Return the quote record for a single ticker, or None if unknown."""
    key = ticker.upper()
    for q in _load_market():
        if q["ticker"] == key:
            return q
    return None


def get_quotes(tickers):
    """Return quote records for a list of tickers (unknown tickers skipped)."""
    wanted = {t.upper() for t in tickers}
    return [q for q in _load_market() if q["ticker"] in wanted]


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
    """Lambda entry point: return a quote for `ticker`, or quotes for `tickers`.

    `tickers` may be a comma-separated string or a list; when present it takes
    precedence and returns a JSON array. Otherwise `ticker` returns a single
    quote object.
    """
    params = _extract_params(event)

    tickers = params.get("tickers")
    if tickers:
        if isinstance(tickers, str):
            tickers = [t.strip() for t in tickers.split(",") if t.strip()]
        return _response(200, get_quotes(tickers))

    ticker = params.get("ticker")
    if not ticker:
        return _response(400, {"error": "ticker (or tickers) is required"})

    quote = get_quote(ticker)
    if quote is None:
        return _response(404, {"error": f"ticker {ticker} not found"})
    return _response(200, quote)

#!/usr/bin/env python3
"""Local-first tool adapters (design.md Phase 1 local-first).

Thin wrappers the Strands agent can call directly as local Python tools before
AgentCore Gateway is stood up. Each adapter IMPORTS and calls the SAME core-logic
functions from the three *_lambda.py modules -- no logic is duplicated, no event
is constructed, and no AWS is touched. Each return value is identical to the
parsed `body` of the corresponding Lambda handler response for the same input.
"""
try:  # imported as a package: `import mocks.local_tools`
    from . import portfolio_lambda, market_lambda, research_lambda
except ImportError:  # imported as a top-level module from inside mocks/
    import portfolio_lambda
    import market_lambda
    import research_lambda


def get_portfolio(client_id):
    """Return the client/portfolio record (matches portfolio_lambda handler body)."""
    return portfolio_lambda.get_client(client_id)


def get_positions(client_id):
    """Return the client's positions list."""
    return portfolio_lambda.get_positions(client_id)


def get_concentration_flags(client_id):
    """Return the client's concentration flags list."""
    return portfolio_lambda.get_concentration_flags(client_id)


def get_quote(ticker):
    """Return a single market quote (matches market_lambda handler body)."""
    return market_lambda.get_quote(ticker)


def get_quotes(tickers):
    """Return market quotes for a list of tickers."""
    return market_lambda.get_quotes(tickers)


def list_research(ticker):
    """Return research notes for a ticker (matches research_lambda handler body)."""
    return research_lambda.list_research(ticker)


def get_research(note_id):
    """Return a single research note by id."""
    return research_lambda.get_research(note_id)

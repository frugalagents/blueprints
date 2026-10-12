#!/usr/bin/env python3
"""Validate the synthetic mock-data fixtures against design.md section 4 shapes.

Loads clients.json, market.json and research.json, asserts every record has the
required keys with correct types, and asserts the demo invariants (Jane Doe
C-1001 with an NVDA concentration flag; NVDA and AGG present in market data; an
NVDA Overweight research note exists). Prints a pass line and exits 0 on success;
exits nonzero with a clear message on the first failure.

No AWS calls, no network. Deterministic, synthetic data only.
"""
import json
import os
import sys

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

NUMBER = (int, float)


class FixtureError(Exception):
    """Raised when a fixture record violates the expected shape or an invariant."""


def _load(name):
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not data:
        raise FixtureError(f"{name}: expected a non-empty JSON array")
    return data


def _require(cond, msg):
    if not cond:
        raise FixtureError(msg)


def validate_clients(clients):
    # design.md 4.1
    for i, c in enumerate(clients):
        where = f"clients[{i}] ({c.get('client_id', '?')})"
        _require(isinstance(c.get("client_id"), str), f"{where}: client_id must be str")
        _require(isinstance(c.get("name"), str), f"{where}: name must be str")
        _require(isinstance(c.get("risk_profile"), str), f"{where}: risk_profile must be str")
        _require(isinstance(c.get("advisor_id"), str), f"{where}: advisor_id must be str")
        _require(isinstance(c.get("positions"), list) and c["positions"],
                 f"{where}: positions must be a non-empty list")
        for j, p in enumerate(c["positions"]):
            pw = f"{where}.positions[{j}]"
            _require(isinstance(p.get("ticker"), str), f"{pw}: ticker must be str")
            _require(isinstance(p.get("shares"), NUMBER) and not isinstance(p.get("shares"), bool),
                     f"{pw}: shares must be a number")
            _require(isinstance(p.get("cost_basis"), NUMBER) and not isinstance(p.get("cost_basis"), bool),
                     f"{pw}: cost_basis must be a number")
            _require(isinstance(p.get("sector"), str), f"{pw}: sector must be str")
        _require(isinstance(c.get("concentration_flags"), list),
                 f"{where}: concentration_flags must be a list")
        for f in c["concentration_flags"]:
            _require(isinstance(f, str), f"{where}: concentration_flags entries must be str")

    # invariant: Jane Doe C-1001 with NVDA concentration
    jane = next((c for c in clients if c["client_id"] == "C-1001"), None)
    _require(jane is not None, "invariant: client C-1001 missing")
    _require(jane["name"] == "Jane Doe", "invariant: C-1001 name must be 'Jane Doe'")
    _require(jane["risk_profile"] == "moderate", "invariant: C-1001 risk_profile must be 'moderate'")
    _require(jane["advisor_id"] == "FA-77", "invariant: C-1001 advisor_id must be 'FA-77'")
    _require(any(p["ticker"] == "NVDA" for p in jane["positions"]),
             "invariant: C-1001 must hold an NVDA position")
    _require(any("NVDA" in f for f in jane["concentration_flags"]),
             "invariant: C-1001 must have an NVDA concentration_flags entry")


def validate_market(market):
    # design.md 4.2 -- pe may be null for instruments without a P/E (e.g. bond ETFs)
    for i, m in enumerate(market):
        where = f"market[{i}] ({m.get('ticker', '?')})"
        _require(isinstance(m.get("ticker"), str), f"{where}: ticker must be str")
        _require(isinstance(m.get("last"), NUMBER) and not isinstance(m.get("last"), bool),
                 f"{where}: last must be a number")
        _require(isinstance(m.get("change_pct"), NUMBER) and not isinstance(m.get("change_pct"), bool),
                 f"{where}: change_pct must be a number")
        _require("pe" in m and (m["pe"] is None or
                 (isinstance(m["pe"], NUMBER) and not isinstance(m["pe"], bool))),
                 f"{where}: pe must be a number or null")
        _require(isinstance(m.get("as_of"), str), f"{where}: as_of must be str")

    tickers = {m["ticker"] for m in market}
    _require("NVDA" in tickers, "invariant: market must include NVDA")
    _require("AGG" in tickers, "invariant: market must include AGG")


def validate_research(research):
    # design.md 4.3
    for i, n in enumerate(research):
        where = f"research[{i}] ({n.get('note_id', '?')})"
        _require(isinstance(n.get("note_id"), str), f"{where}: note_id must be str")
        _require(isinstance(n.get("ticker"), str), f"{where}: ticker must be str")
        _require(isinstance(n.get("analyst"), str), f"{where}: analyst must be str")
        _require(isinstance(n.get("rating"), str), f"{where}: rating must be str")
        _require(isinstance(n.get("price_target"), NUMBER) and not isinstance(n.get("price_target"), bool),
                 f"{where}: price_target must be a number")
        _require(isinstance(n.get("published"), str), f"{where}: published must be str")
        _require(isinstance(n.get("summary"), str), f"{where}: summary must be str")
        _require(isinstance(n.get("disclaimer_required"), bool),
                 f"{where}: disclaimer_required must be bool")

    _require(any(n["ticker"] == "NVDA" and n["rating"] == "Overweight" for n in research),
             "invariant: an NVDA 'Overweight' research note must exist")


def main():
    try:
        clients = _load("clients.json")
        market = _load("market.json")
        research = _load("research.json")
        validate_clients(clients)
        validate_market(market)
        validate_research(research)
    except (FixtureError, OSError, json.JSONDecodeError) as exc:
        print(f"FIXTURE VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        f"PASS: fixtures valid -- {len(clients)} clients, {len(market)} tickers, "
        f"{len(research)} research notes; invariants (Jane Doe C-1001 NVDA "
        f"concentration, NVDA & AGG market, NVDA Overweight note) hold."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

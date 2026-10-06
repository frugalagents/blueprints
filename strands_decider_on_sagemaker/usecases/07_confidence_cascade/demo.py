"""Confidence cascade: let Decider handle confident cases, escalate the rest to an expensive LLM.

The expensive LLM is SIMULATED here (assumed always right) so the demo shows the trade-off
without extra cost. Replace `expensive_llm` with a real call in production.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Tally, ask, choice

TEAMS = {
    "billing": "Charges, payouts, invoices, refunds",
    "technical": "Bugs, errors, outages, integrations",
    "sales": "Pricing, plans, upgrades, demos",
    "account": "Login, passwords, profile, data deletion",
}

MESSAGES = [
    ("I was charged twice this month", "billing"),
    ("The dashboard shows a blank page after login", "technical"),
    ("Can I get a quote for 50 licenses?", "sales"),
    ("I forgot my password", "account"),
    ("My invoice total doesn't match the quote", "billing"),
    ("The export button throws an error", "technical"),
    ("Do you offer annual discounts?", "sales"),
    ("Please remove my email from your systems", "account"),
    ("Payout failed again", "billing"),
    ("API returns 429 even at low volume", "technical"),
    ("We want to upgrade our plan", "sales"),
    ("Change the email on my profile", "account"),
    ("It's not working", "technical"),
    ("I need help with my plan", "sales"),
    ("Something is wrong with my account charges", "billing"),
    ("Can't sign in after the update, maybe a bug?", "account"),
    ("Need to talk about my subscription", "billing"),
    ("Is the integration included in my tier or do I need to pay extra?", "sales"),
    ("Why did my settings disappear?", "technical"),
    ("Update: still waiting", "technical"),
    ("Refund my last payment and close my account", "billing"),
    ("How do I add a teammate?", "account"),
    ("Your app crashed when I tried to pay", "technical"),
    ("Hello, I have a question", "sales"),
]


def expensive_llm(message, truth):
    return truth  # simulated: assume the big model is always right


if __name__ == "__main__":
    results = []
    for msg, truth in MESSAGES:
        a = ask(msg, {"team": choice("Which team should handle this message?", TEAMS)})["team"]
        results.append((a["choice"], a["confidence"], truth))

    print(f"{'threshold':>9} | {'handled by Decider':>18} | {'escalated to LLM':>16} | {'Decider accuracy on handled':>27}")
    chosen = None
    for thr in (0.0, 0.5, 0.7, 0.8, 0.9):
        handled = [(p, truth) for p, c, truth in results if c >= thr]
        ok = sum(p == truth for p, truth in handled)
        esc = len(results) - len(handled)
        acc = f"{ok}/{len(handled)} ({100 * ok // max(len(handled), 1)}%)"
        print(f"{thr:>9.1f} | {len(handled):>12}/{len(results)} | {esc:>13}/{len(results)} | {acc:>27}")
        if thr == 0.8:
            chosen = (ok, len(handled))

    t = Tally("07_confidence_cascade (accuracy of auto-handled at threshold 0.8)")
    t.correct, t.total = chosen
    t.summary()

"""Support-ticket triage: team, refund request, urgency and sentiment in ONE endpoint call."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Tally, ask, choice, level, score, yes_no

TEAMS = {
    "billing": "Charges, payouts, invoices, refunds, payment failures",
    "technical": "Bugs, errors, outages, integrations, how-to questions",
    "sales": "Pricing, plans, upgrades, demos, quotes",
    "account": "Login, passwords, profile, permissions, data deletion",
}
QUESTIONS = {
    "team": choice("Which team should handle this ticket?", TEAMS),
    "refund": yes_no("Is the customer asking for their money back?"),
    "urgency": score("How urgent is this ticket?", ["Low", "Medium", "High", "Critical"]),
    "sentiment": score("What is the customer's sentiment?", ["Very negative", "Negative", "Neutral", "Positive"]),
}

# (ticket, expected team, expected refund request)
TICKETS = [
    ("My payouts have been failing for 3 days and I can't pay my staff!", "billing", False),
    ("I was charged twice for the annual plan. Please refund the duplicate charge.", "billing", True),
    ("The API returns 500 errors on every call since this morning.", "technical", False),
    ("Webhook deliveries stopped after your update, nothing arrives at our endpoint.", "technical", False),
    ("How much would the enterprise plan cost for 200 seats?", "sales", False),
    ("We'd like a demo of the analytics add-on next week.", "sales", False),
    ("I can't log in, the password reset email never arrives.", "account", False),
    ("Please delete my account and all my personal data.", "account", False),
    ("This product doesn't do what the website promised. I want a full refund.", "billing", True),
    ("Where do I find the setting to export reports as CSV?", "technical", False),
]

if __name__ == "__main__":
    t = Tally("01_ticket_triage")
    for text, want_team, want_refund in TICKETS:
        a = ask(text, QUESTIONS)
        team, conf = a["team"]["choice"], a["team"]["confidence"]
        refund = a["refund"]["noul"] > 0.5
        print(
            f"[{t.add(team == want_team)}] team={team:<9} (conf {conf:.2f}) "
            f"[{t.add(refund == want_refund)}] refund={str(refund):<5} "
            f"urgency={level(a['urgency']):<8} sentiment={level(a['sentiment']):<13} | {text[:48]}"
        )
    t.summary()

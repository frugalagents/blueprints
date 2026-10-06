"""LLM router: send easy queries to a small model, hard ones to a large model, and flag retrieval needs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Tally, ask, choice, yes_no

QUESTIONS = {
    "tier": choice(
        "Which model tier should answer this request?",
        {
            "small": "Simple facts, rewriting, translation, chit-chat, short lookups",
            "large": "Multi-step reasoning, code, system design, analysis of several documents",
        },
    ),
    "needs_search": yes_no("Does answering require fresh or private information that must be looked up?"),
}

# (query, expected tier, expected needs_search)
QUERIES = [
    ("Rewrite this sentence to be more formal: 'hey can u send that over'", "small", False),
    ("What's the capital of Australia?", "small", False),
    ("Design a sharded database schema for a multi-tenant SaaS and explain the tradeoffs", "large", False),
    ("What were the top news headlines today?", "small", True),
    ("Write a Python function that detects cycles in a directed graph and prove its complexity", "large", False),
    ("Summarize our company's refund policy for a customer", "small", True),
    ("Compare the 2024 and 2025 EU AI Act obligations for high-risk systems and draft a compliance plan", "large", True),
    ("Thanks!", "small", False),
    ("What's the current price of AAPL?", "small", True),
    ("Debug this stack trace and propose a fix that spans three services", "large", False),
    ("Translate 'good morning' to Spanish", "small", False),
    ("Analyze these three quarterly reports and identify the biggest risks", "large", False),
]

if __name__ == "__main__":
    t = Tally("05_llm_router")
    large = 0
    for q, want_tier, want_search in QUERIES:
        a = ask(q, QUESTIONS)
        tier, search = a["tier"]["choice"], a["needs_search"]["noul"] > 0.5
        large += tier == "large"
        print(
            f"[{t.add(tier == want_tier)}] tier={tier:<5} [{t.add(search == want_search)}] "
            f"search={str(search):<5} (conf {a['tier']['confidence']:.2f}) | {q[:62]}"
        )
    print(f"\nrouted to large model: {large}/{len(QUERIES)} ({100 * large // len(QUERIES)}%)")
    t.summary()

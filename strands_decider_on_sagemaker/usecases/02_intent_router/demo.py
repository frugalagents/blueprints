"""Chat intent router: pick a handler for each message, falling back to a human when unsure."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Tally, ask, choice

INTENTS = {
    "order_status": "Where is my order, tracking, delivery problems",
    "cancel_order": "Cancel an order or subscription",
    "refund_request": "Money back, refunds, duplicate charges, broken items",
    "product_question": "Questions about product features, compatibility, care",
    "human_agent": "Wants a real person, manager, or escalation",
    "smalltalk": "Greetings, thanks, casual chat",
}
HANDLERS = {name: f"handle_{name}" for name in INTENTS}
MIN_CONFIDENCE = 0.6

MESSAGES = [
    ("where's my package? ordered last Tuesday", "order_status"),
    ("I want to cancel my order #4471 before it ships", "cancel_order"),
    ("this arrived broken, I want my money back", "refund_request"),
    ("does the X200 work with USB-C chargers?", "product_question"),
    ("let me talk to a real person", "human_agent"),
    ("hey there, how's it going?", "smalltalk"),
    ("tracking number says delivered but nothing is here", "order_status"),
    ("please refund the duplicate charge on my card", "refund_request"),
    ("is this jacket machine washable?", "product_question"),
    ("cancel my subscription please", "cancel_order"),
    ("this is ridiculous, get me a manager", "human_agent"),
    ("good morning!", "smalltalk"),
]


def route(message):
    a = ask(message, {"intent": choice("What does the customer want?", INTENTS)})["intent"]
    if a["confidence"] < MIN_CONFIDENCE:
        return "human_agent", a["choice"], a["confidence"], True
    return a["choice"], a["choice"], a["confidence"], False


if __name__ == "__main__":
    t = Tally("02_intent_router")
    fallbacks = 0
    for msg, want in MESSAGES:
        intent, raw, conf, fell_back = route(msg)
        fallbacks += fell_back
        note = f" (low confidence, raw={raw})" if fell_back else ""
        print(f"[{t.add(intent == want)}] -> {HANDLERS[intent]:<24} conf {conf:.2f}{note} | {msg}")
    print(f"\nfallbacks to human: {fallbacks}/{len(MESSAGES)}")
    t.summary()

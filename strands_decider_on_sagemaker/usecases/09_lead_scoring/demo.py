"""Lead scoring: rate inbound inquiries Cold / Warm / Hot and rank them."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Tally, ask, score, top_level_index

QUESTION = {
    "intent": score(
        "How ready is this lead to buy? Cold = just browsing, Warm = evaluating with no firm timeline, "
        "Hot = budget and urgency, ready to buy or migrate now.",
        ["Cold", "Warm", "Hot"],
    )
}
LEVELS = ["Cold", "Warm", "Hot"]

LEADS = [
    ("VP Eng at a 2,000-person retailer. Budget approved for Q4, vendor must be chosen by end of month. Can we schedule a demo this week?", "Hot"),
    ("Student working on a class project, just curious how it works.", "Cold"),
    ("Looking at options for next year, no fixed timeline. Can you send pricing?", "Warm"),
    ("CTO here. Our current vendor contract expires in 30 days and we need to migrate. Please call me today.", "Hot"),
    ("Saw your ad. What do you do?", "Cold"),
    ("Our team of 15 is evaluating three vendors, decision in roughly two months. We'd like a trial account.", "Warm"),
    ("Unsubscribe me.", "Cold"),
    ("Procurement here: please send the signed order form, we're ready to purchase 500 seats.", "Hot"),
    ("Just downloading the whitepaper for research.", "Cold"),
    ("We'll probably need something like this sometime next year, check back then.", "Cold"),
]

if __name__ == "__main__":
    t = Tally("09_lead_scoring")
    scored, within_one = [], 0
    for text, want in LEADS:
        a = ask(text, QUESTION)["intent"]
        idx = top_level_index(a)
        got = LEVELS[idx]
        within_one += abs(idx - LEVELS.index(want)) <= 1
        scored.append((a["score"], got, text))
        print(f"[{t.add(got == want)}] {got:<5} (want {want:<4}, score {a['score']:.2f}) | {text[:64]}")
    print(f"\nwithin one level: {within_one}/{len(LEADS)}")
    print("\nRanking (hottest first):")
    for s, got, text in sorted(scored, reverse=True)[:5]:
        print(f"  {s:.2f} {got:<5} {text[:70]}")
    t.summary()

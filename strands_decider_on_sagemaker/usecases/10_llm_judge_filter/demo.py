"""Cheap LLM-judge pre-filter: pass clear answers, fail clear misses, send borderline ones to a stronger judge."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Tally, ask, level, score, yes_no

PASS_MIN, FAIL_MAX = 0.7, 0.3  # between these -> borderline, escalate

QUESTIONS = {
    "helpful": yes_no("Does the answer directly and usefully address the question?"),
    "quality": score("Rate the answer quality.", ["Poor", "Fair", "Good", "Excellent"]),
}

# (question, answer, expected helpful)
PAIRS = [
    ("How do I reset my password?", "Click 'Forgot password' on the login page and follow the emailed link.", True),
    ("How do I reset my password?", "Our company was founded in 2010 and has offices in three countries.", False),
    ("What is 15% of 200?", "15% of 200 is 30.", True),
    ("What is 15% of 200?", "I'm not able to help with that.", False),
    ("Explain what an API is.", "An API is a set of rules that lets one program request data or actions from another, like a waiter carrying orders between you and the kitchen.", True),
    ("Explain what an API is.", "Bananas are rich in potassium.", False),
    ("How do I cancel my subscription?", "Go to Settings > Billing > Cancel subscription and confirm.", True),
    ("How do I cancel my subscription?", "Subscriptions are great value and we hope you'll stay!", False),
    ("Who wrote Pride and Prejudice?", "Jane Austen wrote Pride and Prejudice, published in 1813.", True),
    ("Who wrote Pride and Prejudice?", "It is a famous novel.", False),
]

if __name__ == "__main__":
    t = Tally("10_llm_judge_filter")
    border = 0
    for q, ans, want in PAIRS:
        a = ask(f"Question: {q}\nAnswer: {ans}", QUESTIONS)
        p = a["helpful"]["noul"]
        decision = "PASS" if p >= PASS_MIN else "FAIL" if p <= FAIL_MAX else "ESCALATE"
        border += decision == "ESCALATE"
        print(f"[{t.add((p > 0.5) == want)}] {decision:<8} p(helpful)={p:.2f} quality={level(a['quality']):<9} | {q[:30]} -> {ans[:40]}")
    print(f"\nescalated to a stronger judge: {border}/{len(PAIRS)}")
    t.summary()

"""Simulate an SQS batch (plain messages and S3-notification messages) hitting the Lambda handler locally."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Tally
from handler import handler

DOCS = {
    "inv-001": ("Invoice #8841. Amount due: $4,250.00. Payment terms: net 30, due Nov 15. Remit to Acme Supplies LLC.", "invoice", True),
    "cv-002": ("Jane Rivera. 7 years as a backend engineer at Stripe and Shopify. Skills: Go, Postgres, Kubernetes. B.S. Computer Science, MIT.", "resume", True),
    "ctr-003": ("This Services Agreement is entered into by and between Globex Corp and Initech LLC. The parties agree to the terms below, including confidentiality and termination. Signed: ______", "contract", False),
    "sup-004": ("Hi, the export feature keeps failing with an error and I can't finish my report. Can you help?", "support_request", False),
    "misc-005": ("Team lunch is moved to Thursday at noon. Pizza and salads will be provided.", "other", False),
}
S3_OBJECTS = {"incoming/ctr-006.txt": ("Mutual NDA between Hooli and Pied Piper. The parties agree not to disclose confidential information. Term: 3 years. Governing law: Delaware.", "contract", False)}


def fake_s3(bucket, key):
    return S3_OBJECTS[key][0]


if __name__ == "__main__":
    records = [{"body": json.dumps({"id": i, "text": txt})} for i, (txt, _, _) in DOCS.items()]
    records.append(
        {"body": json.dumps({"Records": [{"s3": {"bucket": {"name": "demo-bucket"}, "object": {"key": "incoming/ctr-006.txt"}}}]})}
    )
    expected = {**{i: v for i, v in DOCS.items()}, "incoming/ctr-006.txt": S3_OBJECTS["incoming/ctr-006.txt"]}

    result = handler({"Records": records}, fetch_s3=fake_s3)
    t = Tally("11_event_driven")
    for r in result["results"]:
        _, want_cat, want_sens = expected[r["id"]]
        print(
            f"[{t.add(r['category'] == want_cat)}] {r['id']:<22} -> {r['route']:<22} "
            f"sensitive={r['sensitive']!s:<5} [{t.add(r['sensitive'] == want_sens)}] conf {r['confidence']:.2f}"
        )
    t.summary()

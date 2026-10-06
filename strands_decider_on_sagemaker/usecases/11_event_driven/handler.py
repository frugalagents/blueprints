"""Lambda handler: classify documents arriving via SQS (optionally wrapping S3 events) with the Decider endpoint.

Deploy as a Lambda with an SQS trigger. The function's role needs sagemaker:InvokeEndpoint on the
endpoint, plus s3:GetObject if messages reference S3 objects. Env: DECIDER_ENDPOINT.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import ask, choice, yes_no

QUESTIONS = {
    "category": choice(
        "What kind of document is this?",
        {
            "invoice": "A bill requesting payment, with amounts and due dates",
            "resume": "A person's work history, skills and education",
            "contract": "A legal agreement with terms, parties and signatures",
            "support_request": "A customer asking for help with a problem",
            "other": "Anything else",
        },
    ),
    "sensitive": yes_no("Does the document contain sensitive personal or financial information?"),
}
ROUTES = {
    "invoice": "queue:accounts-payable",
    "resume": "queue:recruiting",
    "contract": "queue:legal-review",
    "support_request": "queue:support",
    "other": "queue:manual-review",
}


def _load_text(record_body, fetch_s3):
    body = json.loads(record_body)
    if "text" in body:
        return body.get("id", "?"), body["text"]
    s3 = body["Records"][0]["s3"]
    key = s3["object"]["key"]
    return key, fetch_s3(s3["bucket"]["name"], key)


def handler(event, context=None, fetch_s3=None):
    out = []
    for rec in event["Records"]:
        doc_id, text = _load_text(rec["body"], fetch_s3)
        a = ask(text[:6000], QUESTIONS)
        cat = a["category"]["choice"]
        if a["category"]["confidence"] < 0.5:
            cat = "other"
        out.append(
            {
                "id": doc_id,
                "category": cat,
                "route": ROUTES[cat],
                "sensitive": a["sensitive"]["noul"] > 0.5,
                "confidence": a["category"]["confidence"],
            }
        )
    return {"results": out}

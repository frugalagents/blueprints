"""Minimal example: call the Strands Decider SageMaker endpoint.

Usage: python try_endpoint.py
"""
import json

import boto3

ENDPOINT = "strands-decider"
runtime = boto3.client("sagemaker-runtime", region_name="us-east-1")

request = {
    "state": "Help! My payouts have been failing for 3 days!",
    "questions": {
        "team": {
            "type": "choice",
            "instructions": "Which team should handle this?",
            "criteria": {
                "billing": "Payments, payouts, invoices, refunds",
                "sales": "New purchases, pricing, upgrades",
                "retail": "In-store orders and returns",
            },
        },
        "urgent": {"type": "noul", "instructions": "Is this urgent?"},
    },
}

response = runtime.invoke_endpoint(
    EndpointName=ENDPOINT,
    ContentType="application/json",
    Body=json.dumps(request),
)
result = json.loads(response["Body"].read())

team = result["answers"]["team"]
print(f"Team: {team['choice']} (confidence {team['confidence']:.2f})")
for option, p in sorted(team["probabilities"].items(), key=lambda kv: -kv[1]):
    print(f"  {option:<8} {p:.3f}")
print(f"Urgent: P(true) = {result['answers']['urgent']['noul']:.3f}")
print(f"Server latency: {result['latency_ms']} ms")

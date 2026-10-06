"""Test the deployed Strands Decider endpoint.

Usage: python test_endpoint.py [--endpoint strands-decider] [--calls 50]
Checks: golden parity, latency (p50/p95), concurrency, and error handling.
"""
import argparse
import concurrent.futures as cf
import json
import os
import statistics
import sys
import time

import boto3
from botocore.exceptions import ClientError

ap = argparse.ArgumentParser()
ap.add_argument("--endpoint", default="strands-decider")
ap.add_argument("--calls", type=int, default=50)
args = ap.parse_args()

REGION = os.environ.get("AWS_REGION", "us-east-1")
rt = boto3.client("sagemaker-runtime", region_name=REGION)
REQS = json.load(open("tests/requests.json"))
GOLD = json.load(open("tests/golden.json"))
failures = []


def invoke(body):
    t = time.perf_counter()
    r = rt.invoke_endpoint(EndpointName=args.endpoint, ContentType="application/json", Body=json.dumps(body))
    return json.loads(r["Body"].read()), (time.perf_counter() - t) * 1000


def flat(resp):
    out = {}
    for q, a in resp["answers"].items():
        for k, v in a.items():
            if isinstance(v, float):
                out[f"{q}.{k}"] = v
            elif isinstance(v, dict) and v and all(isinstance(x, float) for x in v.values()):
                out.update({f"{q}.{k}.{kk}": vv for kk, vv in v.items()})
    return out


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


print(f"== 1. golden parity vs CPU reference (tolerance 0.02) [{args.endpoint}]")
for name, body in REQS.items():
    resp, ms = invoke(body)
    got, want = flat(resp), flat(GOLD[name])
    worst = max((abs(got[k] - want[k]) for k in want if k in got), default=99)
    choices_ok = all(
        resp["answers"][q]["choice"] == a["choice"] for q, a in GOLD[name]["answers"].items() if a["type"] == "choice"
    )
    check(name, got.keys() == want.keys() and worst < 0.02 and choices_ok, f"max diff {worst:.4f}, {ms:.0f} ms")

print(f"== 2. latency, {args.calls} serial calls")
body = REQS["route_billing"]
invoke(body)  # warm
client_ms, model_ms = [], []
for _ in range(args.calls):
    resp, ms = invoke(body)
    client_ms.append(ms)
    model_ms.append(resp["latency_ms"])
q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(len(xs) * p))]
print(f"  server-side: p50 {statistics.median(model_ms):.0f} ms, p95 {q(model_ms, .95):.0f} ms")
print(f"  client round trip: p50 {statistics.median(client_ms):.0f} ms, p95 {q(client_ms, .95):.0f} ms")
check("p50 server latency < 300 ms", statistics.median(model_ms) < 300)

print("== 3. concurrency, 4 clients x 10 calls")
names = list(REQS)
def worker(i):
    name = names[i % len(names)]
    resp, _ = invoke(REQS[name])
    want = GOLD[name]["answers"]
    return all(
        resp["answers"][k]["choice"] == a["choice"] for k, a in want.items() if a["type"] == "choice"
    )
with cf.ThreadPoolExecutor(4) as ex:
    results = list(ex.map(worker, range(40)))
check("40 concurrent-client calls succeeded and matched", all(results), f"({sum(results)}/40)")

print("== 4. error handling")
bad_cases = {
    "empty state": {"state": "", "questions": REQS["route_billing"]["questions"]},
    "single-option choice": {"state": "x", "questions": {"q": {"type": "choice", "instructions": "?", "criteria": {"a": "a"}}}},
    "wrong schema": {"nope": 1},
}
for label, payload in bad_cases.items():
    try:
        invoke(payload)
        check(f"rejects {label}", False, "(no error raised)")
    except ClientError as e:
        check(f"rejects {label}", True, f"({e.response['Error']['Code']})")

print()
if failures:
    print("FAILED:", failures)
    sys.exit(1)
print("ALL CHECKS PASSED")

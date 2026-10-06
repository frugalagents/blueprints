"""Local contract test for container/app.py (CPU). Run from repo root:
   DECIDER_DEVICE=cpu DECIDER_CHECKPOINT=model/decider HF_HUB_OFFLINE=1 pytest tests/test_app_local.py
"""
import json, sys
sys.path.insert(0, "container")
from fastapi.testclient import TestClient
import app as decider_app

client = TestClient(decider_app.app)
REQS = json.load(open("tests/requests.json"))
GOLD = json.load(open("tests/golden.json"))


def flat(answer):
    out = {}
    for q, a in answer["answers"].items():
        for k, v in a.items():
            if isinstance(v, float): out[f"{q}.{k}"] = v
            if isinstance(v, dict) and all(isinstance(x, float) for x in v.values()):
                out.update({f"{q}.{k}.{kk}": vv for kk, vv in v.items()})
    return out


def test_ping():
    assert client.get("/ping").status_code == 200


def test_matches_golden():
    for name, body in REQS.items():
        r = client.post("/invocations", json=body)
        assert r.status_code == 200, (name, r.text)
        got, want = flat(r.json()), flat(GOLD[name])
        assert got.keys() == want.keys()
        for k in want:
            assert abs(got[k] - want[k]) < 0.02, (name, k, got[k], want[k])
        for q, a in GOLD[name]["answers"].items():
            if a["type"] == "choice":
                assert r.json()["answers"][q]["choice"] == a["choice"]


def test_errors():
    assert client.post("/invocations", json={"state": "", "questions": REQS["route_billing"]["questions"]}).status_code == 422
    bad = {"state": "x", "questions": {"q": {"type": "choice", "instructions": "?", "criteria": {"only": "one"}}}}
    assert client.post("/invocations", json=bad).status_code == 422
    assert client.post("/invocations", json={"nope": 1}).status_code == 422

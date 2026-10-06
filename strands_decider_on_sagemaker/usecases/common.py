"""Shared helpers for the Strands Decider use-case demos."""
import json
import os

import boto3
from botocore.config import Config

ENDPOINT = os.environ.get("DECIDER_ENDPOINT", "strands-decider")
REGION = os.environ.get("AWS_REGION", "us-east-1")
_runtime = boto3.client(
    "sagemaker-runtime",
    region_name=REGION,
    config=Config(read_timeout=60, retries={"max_attempts": 3, "mode": "standard"}),
)


def ask(state, questions):
    """One endpoint call: a state plus named questions -> {name: answer}."""
    resp = _runtime.invoke_endpoint(
        EndpointName=ENDPOINT,
        ContentType="application/json",
        Body=json.dumps({"state": state, "questions": questions}),
    )
    return json.loads(resp["Body"].read())["answers"]


def choice(instructions, options):
    """Pick one. options: {name: description} or a list of names."""
    criteria = options if isinstance(options, dict) else {o: o for o in options}
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def yes_no(instructions):
    """Answer is a probability of 'yes' in answer['noul']."""
    return {"type": "noul", "instructions": instructions}


def score(levels_instructions, levels):
    """Rate against ordered levels, lowest first."""
    return {"type": "score", "instructions": levels_instructions, "criteria": levels}


def level_index(answer):
    n = len(answer["legend"])
    return min(max(round(answer["score"]), 0), n - 1)


def top_level_index(answer):
    probs = answer["probabilities"]
    return int(max(probs, key=probs.get))


def level(answer):
    return answer["legend"][str(top_level_index(answer))]


class Tally:
    def __init__(self, name):
        self.name, self.correct, self.total = name, 0, 0

    def add(self, ok):
        self.total += 1
        self.correct += bool(ok)
        return "ok  " if ok else "MISS"

    def summary(self):
        print(f"\nSUMMARY {self.name} {self.correct}/{self.total}")

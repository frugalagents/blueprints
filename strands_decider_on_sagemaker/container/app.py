"""SageMaker serving wrapper around strands-decider's System One engine.

SageMaker contract: GET /ping, POST /invocations on port 8080.
"""

import logging
import os
import threading
import time

import torch
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

from strands_decider import server
from strands_decider.schema import SystemOneRequest

log = logging.getLogger("decider")
logging.basicConfig(level=logging.INFO)

CHECKPOINT = os.environ.get("DECIDER_CHECKPOINT", "/opt/ml/model/decider")
DEVICE = os.environ.get("DECIDER_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
MODEL_NAME = os.environ.get("DECIDER_MODEL_NAME", "strands-decider-2B-hobson-v19")

log.info("loading %s on %s", CHECKPOINT, DEVICE)
_t = time.perf_counter()
app = server.create_app(CHECKPOINT, device=DEVICE, model_name=MODEL_NAME)
log.info("model loaded in %.1fs", time.perf_counter() - _t)

# The upstream server does not verify concurrent use; serialise engine calls.
_lock = threading.Lock()


def _evaluate(request: SystemOneRequest) -> dict:
    started = time.perf_counter()
    try:
        with _lock:
            response = server.get_engine().evaluate(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = response.model_dump()
    payload["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return payload


# Warm up (triton kernel compilation, CUDA init) before /ping can succeed.
_t = time.perf_counter()
_evaluate(
    SystemOneRequest(
        state="warmup",
        questions={
            "c": {"type": "choice", "instructions": "pick", "criteria": {"a": "a", "b": "b"}},
            "n": {"type": "noul", "instructions": "yes?"},
            "s": {"type": "score", "instructions": "rate", "criteria": ["low", "high"]},
        },
    )
)
log.info("warmup done in %.1fs", time.perf_counter() - _t)


@app.get("/ping")
def ping() -> Response:
    return Response(status_code=200)


@app.post("/invocations")
def invocations(request: SystemOneRequest) -> JSONResponse:
    return JSONResponse(content=_evaluate(request))

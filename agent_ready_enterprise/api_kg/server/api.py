from __future__ import annotations

import json
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api_kg.config import load_config
from api_kg.graph.graph_store import graph_stats, load_graph
from api_kg.retrieval.hybrid_retriever import retrieve
from api_kg.planning.plan_generator import generate_plan, _PLAN_CACHE
from api_kg.planning.plan_validator import validate_plan
from api_kg.planning.plan_repair import repair_plan
from api_kg.execution.dag_executor import execute_plan
from api_kg.reasoning.evidence_builder import build_evidence
from api_kg.reasoning.answer_synthesizer import synthesize_answer


app = FastAPI(title="Enterprise API KG Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

config = load_config("config.yaml")


class AskRequest(BaseModel):
    question: str


@app.get("/api/stats")
def get_stats():
    graph = load_graph(config["graph_file"])
    return graph_stats(graph)


@app.get("/api/graph")
def get_graph():
    graph = load_graph(config["graph_file"])
    nodes = []
    edges = []
    for node, data in graph.nodes(data=True):
        if data.get("node_type") in ("domain", "capability"):
            nodes.append({"id": node, **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list))}})
    for u, v, data in graph.edges(data=True):
        if data.get("edge_type") in ("capability_depends_on", "domain_has_capability"):
            edges.append({"source": u, "target": v, **{k: v2 for k, v2 in data.items() if isinstance(v2, (str, int, float, bool))}})
    return {"nodes": nodes, "edges": edges}


@app.post("/api/ask")
async def ask(req: AskRequest):
    import asyncio
    import queue
    import threading

    q: queue.Queue = queue.Queue()

    def run_pipeline():
        graph_file = config["graph_file"]
        start = time.time()

        q.put(("step", {"step": "retrieval", "status": "running", "label": "Searching semantic layer..."}))
        retrieval = retrieve(
            req.question, graph_file,
            max_capabilities=config.get("retrieval", {}).get("max_capabilities", 10),
            graph_hops=config.get("retrieval", {}).get("graph_hops", 2),
            config=config,
        )
        q.put(("retrieval", {
            "step": "retrieval", "status": "done",
            "domains": retrieval["domains"],
            "capabilities": [c.get("operation_id") for c in retrieval["matched_capabilities"]],
            "metadata": retrieval["metadata"],
            "edges": retrieval.get("edges", []),
        }))

        q.put(("step", {"step": "planning", "status": "running", "label": "Generating execution plan..."}))
        _PLAN_CACHE.clear()
        plan = generate_plan(req.question, retrieval, config, use_bedrock=True)
        validation = validate_plan(plan, graph_file, config)

        repair_attempts = 0
        while not validation.passed and repair_attempts < 2:
            q.put(("step", {"step": "planning", "status": "repairing", "label": f"Repairing plan (attempt {repair_attempts + 1})..."}))
            plan = repair_plan(plan, validation.violations, retrieval, config)
            validation = validate_plan(plan, graph_file, config)
            repair_attempts += 1

        q.put(("plan", {
            "step": "planning", "status": "done",
            "goal": plan.get("goal"),
            "steps": plan.get("steps", []),
            "validation": validation.__dict__,
        }))

        if not validation.passed:
            q.put(("error", {"message": "Plan validation failed", "violations": validation.violations}))
            q.put(("done", {"elapsed": time.time() - start}))
            q.put(None)
            return

        q.put(("step", {"step": "execution", "status": "running", "label": "Executing API calls..."}))
        execution = execute_plan(plan, graph_file, config.get("api_base_url", "http://localhost:8080"))
        q.put(("execution", {
            "step": "execution", "status": "done",
            "success": execution["success"],
            "trace": execution["trace"],
        }))

        q.put(("step", {"step": "evidence", "status": "running", "label": "Extracting evidence..."}))
        evidence = build_evidence(plan, execution)
        q.put(("evidence", {
            "step": "evidence", "status": "done",
            "claims": evidence.get("claims", []),
        }))

        q.put(("step", {"step": "synthesis", "status": "running", "label": "Generating answer..."}))
        answer = synthesize_answer(req.question, evidence, config, use_bedrock=True)
        q.put(("answer", {
            "step": "synthesis", "status": "done",
            "answer": answer,
        }))

        q.put(("done", {"elapsed": time.time() - start}))
        q.put(None)

    threading.Thread(target=run_pipeline, daemon=True).start()

    async def event_stream():
        while True:
            try:
                item = q.get(timeout=0.1)
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            if item is None:
                break
            event, data = item
            yield f"data: {json.dumps({'event': event, **data}, default=str)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

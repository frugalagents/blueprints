from __future__ import annotations

import argparse
import json
from typing import Any

from api_kg.config import load_config
from api_kg.graph.graph_builder import build_graph
from api_kg.graph.graph_store import graph_stats, load_graph, save_graph
from api_kg.ingestion.spec_normalizer import normalize_specs
from api_kg.planning.plan_generator import generate_plan
from api_kg.planning.plan_validator import validate_plan
from api_kg.planning.plan_repair import repair_plan
from api_kg.retrieval.hybrid_retriever import retrieve
from api_kg.execution.dag_executor import execute_plan
from api_kg.reasoning.evidence_builder import build_evidence
from api_kg.reasoning.answer_synthesizer import synthesize_answer


def cmd_build(args):
    config = load_config(args.config)
    capabilities, entities = normalize_specs(args.specs_dir)
    skip_llm = getattr(args, "skip_llm", False)
    graph = build_graph(capabilities, entities, config=config, skip_llm=skip_llm)
    graph_file = args.graph or config["graph_file"]
    save_graph(graph, graph_file)
    print(json.dumps(graph_stats(graph), indent=2))


def cmd_describe(args):
    from api_kg.semantic.descriptions import generate_descriptions

    config = load_config(args.config)
    capabilities, _ = normalize_specs(args.specs_dir or config.get("specs_dir", "specs/sample_hcm"))
    graph_file = args.graph or config["graph_file"]
    output_dir = args.output or config.get("semantic_dir", "semantic")
    use_bedrock = not getattr(args, "no_bedrock", False)
    generate_descriptions(capabilities, graph_file, config, output_dir=output_dir, use_bedrock=use_bedrock)


def cmd_index(args):
    from api_kg.semantic.embedder import build_semantic_index
    from api_kg.semantic.communities import generate_community_summaries
    from api_kg.planning.plan_cache import generate_plan_templates

    config = load_config(args.config)
    semantic_dir = config.get("semantic_dir", "semantic")
    graph_file = args.graph or config["graph_file"]
    build_semantic_index(semantic_dir, config)
    try:
        generate_community_summaries(graph_file, config, output_file=f"{semantic_dir}/communities.yaml")
    except Exception as e:
        print(f"Community detection skipped: {e}")
    try:
        generate_plan_templates(semantic_dir, graph_file, config)
    except Exception as e:
        print(f"Plan template generation skipped: {e}")


def cmd_stats(args):
    config = load_config(args.config)
    graph = load_graph(args.graph or config["graph_file"])
    print(json.dumps(graph_stats(graph), indent=2))


def cmd_inspect(args):
    config = load_config(args.config)
    graph = load_graph(args.graph or config["graph_file"])
    if not graph.has_node(args.node):
        raise SystemExit(f"Node not found: {args.node}")
    print(json.dumps(dict(graph.nodes[args.node]), indent=2, default=str))


def cmd_retrieve(args):
    config = load_config(args.config)
    result = retrieve(
        args.question,
        args.graph or config["graph_file"],
        max_capabilities=config.get("retrieval", {}).get("max_capabilities", 10),
        graph_hops=config.get("retrieval", {}).get("graph_hops", 2),
        config=config,
    )
    print(json.dumps(result, indent=2, default=str))


def cmd_plan(args):
    config = load_config(args.config)
    retrieval = retrieve(args.question, args.graph or config["graph_file"], config=config)
    plan = generate_plan(args.question, retrieval, config, use_bedrock=not args.no_bedrock)
    validation = validate_plan(plan, args.graph or config["graph_file"], config)
    print(json.dumps({"plan": plan, "validation": validation.__dict__}, indent=2, default=str))


def cmd_ask(args):
    config = load_config(args.config)
    result = run_ask(args.question, config, graph_file=args.graph, use_bedrock=not args.no_bedrock)
    print(json.dumps(result, indent=2, default=str))


def run_ask(question: str, config: dict[str, Any], graph_file: str | None = None, use_bedrock: bool = True) -> dict:
    graph_file = graph_file or config["graph_file"]
    retrieval = retrieve(
        question,
        graph_file,
        max_capabilities=config.get("retrieval", {}).get("max_capabilities", 10),
        graph_hops=config.get("retrieval", {}).get("graph_hops", 2),
        config=config,
    )
    plan = generate_plan(question, retrieval, config, use_bedrock=use_bedrock)
    validation = validate_plan(plan, graph_file, config)

    # Plan repair loop
    repair_attempts = 0
    while not validation.passed and use_bedrock and repair_attempts < 2:
        plan = repair_plan(plan, validation.violations, retrieval, config)
        validation = validate_plan(plan, graph_file, config)
        repair_attempts += 1

    if not validation.passed:
        return {
            "question": question,
            "answer": "Plan validation failed after repair attempts.",
            "retrieval": retrieval,
            "plan": plan,
            "validation": validation.__dict__,
            "execution": None,
            "evidence": None,
        }
    execution = execute_plan(plan, graph_file, config.get("api_base_url", "http://localhost:8080"))
    evidence = build_evidence(plan, execution)
    answer = synthesize_answer(question, evidence, config, use_bedrock=use_bedrock)
    return {
        "question": question,
        "answer": answer,
        "retrieval": retrieval,
        "plan": plan,
        "validation": validation.__dict__,
        "execution": execution,
        "evidence": evidence,
    }


def cmd_mock(args):
    import uvicorn
    from api_kg.execution.mock_server import create_mock_app

    app = create_mock_app(args.specs_dir, args.fixtures_dir)
    uvicorn.run(app, host=args.host, port=args.port)


def cmd_ui(args):
    import subprocess
    import sys

    subprocess.run([sys.executable, "-m", "streamlit", "run", "api_kg/ui/streamlit_app.py"], check=False)


def main():
    parser = argparse.ArgumentParser(description="Enterprise API Knowledge Graph Runtime")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build structural graph from OpenAPI specs")
    build.add_argument("--specs-dir", required=True)
    build.add_argument("--graph")
    build.add_argument("--skip-llm", action="store_true")
    build.set_defaults(func=cmd_build)

    describe = sub.add_parser("describe", help="Generate semantic descriptions for capabilities")
    describe.add_argument("--specs-dir")
    describe.add_argument("--graph")
    describe.add_argument("--output")
    describe.add_argument("--no-bedrock", action="store_true")
    describe.set_defaults(func=cmd_describe)

    index = sub.add_parser("index", help="Build vector index over semantic descriptions")
    index.add_argument("--graph")
    index.set_defaults(func=cmd_index)

    stats = sub.add_parser("stats", help="Show graph statistics")
    stats.add_argument("--graph")
    stats.set_defaults(func=cmd_stats)

    inspect = sub.add_parser("inspect", help="Inspect a graph node")
    inspect.add_argument("node")
    inspect.add_argument("--graph")
    inspect.set_defaults(func=cmd_inspect)

    ret = sub.add_parser("retrieve", help="Retrieve capabilities for a question")
    ret.add_argument("question")
    ret.add_argument("--graph")
    ret.set_defaults(func=cmd_retrieve)

    plan = sub.add_parser("plan", help="Generate execution plan for a question")
    plan.add_argument("question")
    plan.add_argument("--graph")
    plan.add_argument("--no-bedrock", action="store_true")
    plan.set_defaults(func=cmd_plan)

    ask = sub.add_parser("ask", help="Full pipeline: retrieve → plan → execute → answer")
    ask.add_argument("question")
    ask.add_argument("--graph")
    ask.add_argument("--no-bedrock", action="store_true")
    ask.set_defaults(func=cmd_ask)

    mock = sub.add_parser("mock", help="Start mock API server")
    mock.add_argument("--specs-dir", required=True)
    mock.add_argument("--fixtures-dir")
    mock.add_argument("--host", default="127.0.0.1")
    mock.add_argument("--port", type=int, default=8080)
    mock.set_defaults(func=cmd_mock)

    ui = sub.add_parser("ui", help="Launch Streamlit UI")
    ui.set_defaults(func=cmd_ui)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

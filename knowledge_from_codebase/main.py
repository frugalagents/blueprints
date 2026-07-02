#!/usr/bin/env python3
"""
Codebase Business Logic Extractor — CLI Entry Point
=====================================================
Orchestrates the full pipeline: parse → graph → classify → extract → output.

Usage:
    python main.py extract <repo_path>          # Full pipeline
    python main.py query  "How does billing work?"
    python main.py serve                        # Start MCP server
    python main.py dashboard                    # Regenerate HTML dashboard
    python main.py stats                        # Print graph statistics
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.tree import Tree

console = Console()

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load and validate the YAML config file."""
    p = Path(config_path)
    if not p.is_file():
        console.print(f"[red]Config file not found:[/red] {config_path}")
        console.print("[dim]Using built-in defaults.[/dim]")
        return _default_config()

    with open(p, "r") as fh:
        cfg = yaml.safe_load(fh)

    # Merge with defaults to fill any missing keys
    defaults = _default_config()
    for section in defaults:
        if section not in cfg:
            cfg[section] = defaults[section]
        elif isinstance(defaults[section], dict):
            for key in defaults[section]:
                cfg[section].setdefault(key, defaults[section][key])

    return cfg


def _default_config() -> dict:
    return {
        "llm": {
            "model_id": "anthropic.claude-sonnet-4-20250514",
            "region": "us-east-1",
            "max_tokens": 4096,
            "temperature": 0.0,
        },
        "graph": {"db_path": "output/code_graph.db"},
        "parser": {
            "languages": ["python"],
            "exclude_patterns": ["__pycache__", "node_modules", ".git", "venv", ".env"],
            "exclude_files": ["__init__.py", "setup.py", "conftest.py"],
            "min_function_lines": 3,
        },
        "classifier": {"batch_size": 5, "skip_technical": False},
        "output": {
            "dir": "output",
            "generate_dashboard": True,
            "generate_bdd": True,
            "export_json": True,
        },
    }


# ---------------------------------------------------------------------------
# Pipeline stages (stubs that import from sibling modules)
# ---------------------------------------------------------------------------

def _stage_parse(repo_path: str, cfg: dict, verbose: bool) -> dict:
    """Stage 1: Parse source files into function/class AST nodes."""
    from parser.code_parser import parse_repository  # type: ignore

    parser_cfg = cfg.get("parser", {})
    return parse_repository(
        repo_path,
        languages=parser_cfg.get("languages", ["python"]),
        exclude_patterns=parser_cfg.get("exclude_patterns", []),
        exclude_files=parser_cfg.get("exclude_files", []),
        min_lines=parser_cfg.get("min_function_lines", 3),
        verbose=verbose,
    )


def _stage_build_graph(parsed: dict, cfg: dict, verbose: bool) -> None:
    """Stage 2: Build the call graph and persist to SQLite."""
    from graph.builder import build_graph  # type: ignore

    db_path = cfg["graph"]["db_path"]
    build_graph(parsed, db_path=db_path, verbose=verbose)


def _stage_detect_communities(cfg: dict, verbose: bool) -> None:
    """Stage 3: Run Louvain community detection to discover domains."""
    from graph.communities import detect_communities  # type: ignore

    db_path = cfg["graph"]["db_path"]
    detect_communities(db_path=db_path, verbose=verbose)


def _stage_classify(cfg: dict, skip: bool, verbose: bool) -> None:
    """Stage 4: Classify functions as business / technical / glue via LLM."""
    if skip:
        console.print("[yellow]⏭  Skipping classification (--skip-classification)[/yellow]")
        return
    from classifier.llm_classifier import classify_functions  # type: ignore

    classify_functions(
        db_path=cfg["graph"]["db_path"],
        model_id=cfg["llm"]["model_id"],
        region=cfg["llm"]["region"],
        batch_size=cfg["classifier"]["batch_size"],
        skip_technical=cfg["classifier"].get("skip_technical", False),
        verbose=verbose,
    )


def _stage_extract_rules(cfg: dict, verbose: bool) -> None:
    """Stage 5a: Extract BDD business rules from business-classified functions."""
    from extractor.rule_extractor import extract_rules  # type: ignore

    extract_rules(
        db_path=cfg["graph"]["db_path"],
        model_id=cfg["llm"]["model_id"],
        region=cfg["llm"]["region"],
        verbose=verbose,
    )


def _stage_map_flows(cfg: dict, verbose: bool) -> None:
    """Stage 5b: Map end-to-end business flows from entry points."""
    from extractor.flow_mapper import map_flows  # type: ignore

    map_flows(db_path=cfg["graph"]["db_path"], verbose=verbose)


def _stage_generate_dashboard(cfg: dict, verbose: bool) -> None:
    """Stage 6: Generate the interactive HTML dashboard."""
    from output.dashboard import generate_dashboard  # type: ignore

    output_dir = cfg["output"]["dir"]
    generate_dashboard(db_path=cfg["graph"]["db_path"], output_dir=output_dir, verbose=verbose)


def _stage_export_json(cfg: dict, verbose: bool) -> None:
    """Stage 6b: Export the full knowledge graph as JSON."""
    from output.exporter import export_json  # type: ignore

    output_dir = cfg["output"]["dir"]
    export_json(db_path=cfg["graph"]["db_path"], output_dir=output_dir, verbose=verbose)


def _stage_summary(cfg: dict) -> None:
    """Print a summary of the extraction results."""
    db_path = cfg["graph"]["db_path"]
    if not Path(db_path).is_file():
        console.print("[red]No database found — nothing to summarise.[/red]")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total_funcs = conn.execute("SELECT COUNT(*) AS c FROM functions").fetchone()["c"]
    biz_funcs = conn.execute(
        "SELECT COUNT(*) AS c FROM functions WHERE classification = 'business'"
    ).fetchone()["c"]
    tech_funcs = conn.execute(
        "SELECT COUNT(*) AS c FROM functions WHERE classification = 'technical'"
    ).fetchone()["c"]
    total_calls = conn.execute("SELECT COUNT(*) AS c FROM calls").fetchone()["c"]

    rule_count = 0
    try:
        rule_count = conn.execute("SELECT COUNT(*) AS c FROM business_rules").fetchone()["c"]
    except sqlite3.OperationalError:
        pass

    domain_rows = conn.execute(
        "SELECT domain, COUNT(*) AS cnt FROM functions WHERE domain IS NOT NULL AND domain != '' GROUP BY domain ORDER BY cnt DESC"
    ).fetchall()

    conn.close()

    # Build summary panel
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold white")
    table.add_row("Total functions", str(total_funcs))
    table.add_row("Business functions", f"[green]{biz_funcs}[/green]")
    table.add_row("Technical functions", str(tech_funcs))
    table.add_row("Call edges", str(total_calls))
    table.add_row("Business rules", f"[green]{rule_count}[/green]")
    table.add_row("Domains discovered", str(len(domain_rows)))

    console.print()
    console.print(Panel(table, title="📊 Extraction Summary", border_style="green"))

    if domain_rows:
        tree = Tree("🏢 [bold]Business Domains[/bold]")
        for dr in domain_rows:
            tree.add(f"[cyan]{dr['domain']}[/cyan] — {dr['cnt']} functions")
        console.print(tree)
    console.print()


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.0", prog_name="codebase-extractor")
def cli():
    """🧠 Codebase Business Logic Extractor

    Extract business rules, processes, and domain knowledge from any codebase
    using AI agents and graph analysis.
    """
    pass


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--output-dir", default=None, help="Override output directory")
@click.option("--skip-classification", is_flag=True, help="Skip LLM classification (structural only)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def extract(repo_path: str, config_path: str, output_dir: str | None, skip_classification: bool, verbose: bool):
    """Run the full extraction pipeline on a repository.

    Stages: parse → graph → communities → classify → rules → flows → dashboard
    """
    cfg = load_config(config_path)

    if output_dir:
        cfg["output"]["dir"] = output_dir
        cfg["graph"]["db_path"] = os.path.join(output_dir, "code_graph.db")

    # Ensure output directory exists
    Path(cfg["output"]["dir"]).mkdir(parents=True, exist_ok=True)

    console.print()
    console.print(
        Panel(
            f"[bold]Extracting business logic from:[/bold] {repo_path}",
            title="🧠 Codebase Business Logic Extractor",
            border_style="blue",
        )
    )
    console.print()

    stages = [
        ("📂 Parsing source files", lambda: _stage_parse(repo_path, cfg, verbose)),
        ("🔗 Building call graph", lambda parsed=None: _stage_build_graph(parsed, cfg, verbose)),
        ("🏘  Detecting communities", lambda: _stage_detect_communities(cfg, verbose)),
        ("🏷  Classifying functions", lambda: _stage_classify(cfg, skip_classification, verbose)),
        ("📜 Extracting business rules", lambda: _stage_extract_rules(cfg, verbose)),
        ("🔀 Mapping business flows", lambda: _stage_map_flows(cfg, verbose)),
        ("📊 Generating dashboard", lambda: _stage_generate_dashboard(cfg, verbose)),
        ("💾 Exporting JSON", lambda: _stage_export_json(cfg, verbose)),
    ]

    # Filter stages based on config
    if not cfg["output"].get("generate_dashboard"):
        stages = [(n, f) for n, f in stages if "dashboard" not in n.lower()]
    if not cfg["output"].get("generate_bdd"):
        stages = [(n, f) for n, f in stages if "rules" not in n.lower()]
    if not cfg["output"].get("export_json"):
        stages = [(n, f) for n, f in stages if "json" not in n.lower()]

    parsed_data = None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Pipeline", total=len(stages))

        for i, (stage_name, stage_fn) in enumerate(stages):
            progress.update(task, description=stage_name)
            t0 = time.time()

            try:
                if i == 0:
                    # Parse stage returns data
                    parsed_data = stage_fn()
                elif i == 1:
                    # Build graph needs parsed data
                    stages[1] = (stage_name, lambda: _stage_build_graph(parsed_data, cfg, verbose))
                    stage_fn = stages[1][1]
                    stage_fn()
                else:
                    stage_fn()

                elapsed = time.time() - t0
                if verbose:
                    console.print(f"  [dim]✔ {stage_name} ({elapsed:.1f}s)[/dim]")

            except ImportError as e:
                console.print(f"  [yellow]⚠ {stage_name}: module not found — {e}[/yellow]")
            except Exception as e:
                console.print(f"  [red]✗ {stage_name}: {e}[/red]")
                if verbose:
                    console.print_exception()

            progress.advance(task)

    # Print summary
    _stage_summary(cfg)
    console.print("[bold green]✅ Extraction complete![/bold green]")
    console.print(f"[dim]Output: {cfg['output']['dir']}/[/dim]")


@cli.command()
@click.argument("question")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def query(question: str, config_path: str):
    """Ask a natural-language question about the extracted codebase."""
    cfg = load_config(config_path)

    console.print()
    console.print(f"[bold cyan]❓ Question:[/bold cyan] {question}")
    console.print()

    # Use the MCP server's ask_about_codebase internally
    os.environ["CBE_CONFIG"] = config_path
    from mcp_server.server import ask_about_codebase

    with console.status("[bold green]Thinking...[/bold green]"):
        try:
            answer = ask_about_codebase(question)
            console.print(Panel(answer, title="💡 Answer", border_style="green"))
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--host", default="0.0.0.0", help="Host to bind to")
@click.option("--port", default=8765, type=int, help="Port to bind to")
def serve(config_path: str, host: str, port: int):
    """Start the MCP server to expose the knowledge graph."""
    cfg = load_config(config_path)

    os.environ["CBE_CONFIG"] = config_path
    os.environ.setdefault("CBE_DB_PATH", cfg["graph"]["db_path"])

    console.print()
    console.print(
        Panel(
            f"[bold]Starting MCP server[/bold]\n"
            f"DB: {cfg['graph']['db_path']}\n"
            f"Transport: stdio",
            title="🌐 MCP Server",
            border_style="blue",
        )
    )
    console.print("[dim]Waiting for MCP client connections via stdio...[/dim]")
    console.print()

    from mcp_server.server import app
    app.run()


@cli.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def dashboard(config_path: str):
    """Regenerate the HTML dashboard from the existing graph database."""
    cfg = load_config(config_path)
    output_dir = cfg["output"]["dir"]

    console.print()
    console.print("[bold]📊 Regenerating dashboard...[/bold]")

    try:
        _stage_generate_dashboard(cfg, verbose=True)
        console.print(f"[bold green]✅ Dashboard written to {output_dir}/dashboard.html[/bold green]")
    except ImportError as e:
        console.print(f"[yellow]⚠ Module not found: {e}[/yellow]")
        console.print("[dim]Make sure the output module is available.[/dim]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def stats(config_path: str):
    """Print statistics about the extracted knowledge graph."""
    cfg = load_config(config_path)
    db_path = cfg["graph"]["db_path"]

    if not Path(db_path).is_file():
        console.print(f"[red]Database not found at {db_path}[/red]")
        console.print("[dim]Run 'python main.py extract <repo>' first.[/dim]")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    console.print()

    # ── Function statistics ──
    total = conn.execute("SELECT COUNT(*) AS c FROM functions").fetchone()["c"]
    by_class = conn.execute(
        "SELECT classification, COUNT(*) AS c FROM functions GROUP BY classification ORDER BY c DESC"
    ).fetchall()
    by_kind = conn.execute(
        "SELECT kind, COUNT(*) AS c FROM functions GROUP BY kind ORDER BY c DESC"
    ).fetchall()

    tbl = Table(title="📊 Function Statistics")
    tbl.add_column("Metric", style="cyan")
    tbl.add_column("Count", style="bold white", justify="right")
    tbl.add_row("Total functions", str(total))
    for row in by_class:
        label = row["classification"] or "unclassified"
        tbl.add_row(f"  └ {label}", str(row["c"]))
    tbl.add_row("", "")
    for row in by_kind:
        label = row["kind"] or "unknown"
        tbl.add_row(f"  {label}", str(row["c"]))
    console.print(tbl)

    # ── Graph statistics ──
    call_count = conn.execute("SELECT COUNT(*) AS c FROM calls").fetchone()["c"]
    tbl2 = Table(title="🔗 Graph Statistics")
    tbl2.add_column("Metric", style="cyan")
    tbl2.add_column("Count", style="bold white", justify="right")
    tbl2.add_row("Call edges", str(call_count))

    # Top callers
    top_callers = conn.execute(
        """
        SELECT f.name, COUNT(*) AS cnt
        FROM calls c JOIN functions f ON f.id = c.caller_id
        GROUP BY c.caller_id ORDER BY cnt DESC LIMIT 5
        """
    ).fetchall()
    for tc in top_callers:
        tbl2.add_row(f"  → {tc['name']}", f"{tc['cnt']} calls out")
    console.print(tbl2)

    # ── Domain statistics ──
    domains = conn.execute(
        "SELECT domain, COUNT(*) AS cnt FROM functions WHERE domain IS NOT NULL AND domain != '' GROUP BY domain ORDER BY cnt DESC"
    ).fetchall()

    if domains:
        tbl3 = Table(title="🏢 Domains")
        tbl3.add_column("Domain", style="cyan")
        tbl3.add_column("Functions", style="bold white", justify="right")
        for d in domains:
            tbl3.add_row(d["domain"], str(d["cnt"]))
        console.print(tbl3)

    # ── Business rules ──
    try:
        rule_count = conn.execute("SELECT COUNT(*) AS c FROM business_rules").fetchone()["c"]
        console.print(f"\n📜 [bold]Business rules extracted:[/bold] {rule_count}")
    except sqlite3.OperationalError:
        console.print("\n📜 [dim]No business rules table yet.[/dim]")

    conn.close()
    console.print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()

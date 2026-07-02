"""
step_runner.py — Execute a generated Playwright workflow with PII injection and failure repair.

PII flow:
  inputs/data.json  →  params dict  →  Playwright .fill() / .select_option()  →  browser
                                              ↑
                                   PII injected here — never in script source, never in prompts

Repair flow (when a step fails):
  1. Take screenshot BEFORE any re-fill (blank form — no PII visible)
  2. Strip input values from DOM
  3. Send step source + error + stripped DOM to repair_agent (no PII)
  4. Hot-swap fixed function, retry
  5. Write fix back to the workflow file for future runs

Screenshots:
  Saved to screenshots/<workcase_id>/ after every step (success and failure).
  Filenames: 01_step_name.png, 01_step_name_FAILED.png, 01_step_name_repaired.png

Usage:
  AWS_PROFILE=AdministratorAccess-424231649574 \\
    python step_runner.py workflows/al_sit.py inputs/al_data.json [--dry-run] [--headless]
"""

import argparse
import base64
import importlib.util
import inspect
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import repair_agent

MAX_REPAIR_ATTEMPTS = 1


# ---------------------------------------------------------------------------
# Workflow loading
# ---------------------------------------------------------------------------

def load_workflow(workflow_path: str) -> list:
    spec = importlib.util.spec_from_file_location("workflow", workflow_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "STEPS"):
        raise AttributeError(f"{workflow_path} has no STEPS list")
    return mod.STEPS


# ---------------------------------------------------------------------------
# Screenshot helpers
# ---------------------------------------------------------------------------

def screenshot_dir(workcase_id: str) -> Path:
    d = Path(__file__).parent / "screenshots" / workcase_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def take_screenshot(page, shot_dir: Path, step_index: int, fn_name: str, suffix: str = "") -> Path:
    label    = f"{fn_name}{suffix}"
    filename = f"{step_index:02d}_{label}.png"
    path     = shot_dir / filename
    page.screenshot(path=str(path), full_page=True)
    return path


# ---------------------------------------------------------------------------
# Step hot-swap
# ---------------------------------------------------------------------------

def compile_step(source: str, module_globals: dict | None = None) -> callable:
    """Compile a repaired step function, injecting the workflow module's globals so
    helper functions (e.g. _click_next, _wait_ready) are available to the hot-swapped code."""
    ns = {}
    globals_ctx = dict(module_globals) if module_globals else {}
    exec(compile(source, "<repair>", "exec"), globals_ctx, ns)
    for v in ns.values():
        if callable(v) and not isinstance(v, type):
            return v
    raise ValueError("No callable found in repaired source")


def save_fix(workflow_path: str, fn_name: str, fixed_source: str) -> None:
    text    = Path(workflow_path).read_text()
    pattern = rf"(def {re.escape(fn_name)}\(.*?)(?=\ndef |\Z)"
    updated = re.sub(pattern, fixed_source.rstrip(), text, count=1, flags=re.DOTALL)
    if updated == text:
        print(f"    [repair] Warning: could not locate {fn_name} in {workflow_path} to save fix")
        return
    Path(workflow_path).write_text(updated)
    print(f"    [repair] Fix written back to {workflow_path}")


# ---------------------------------------------------------------------------
# Step execution with repair loop
# ---------------------------------------------------------------------------

def run_step(page, step_fn: callable, params: dict, workflow_path: str,
             step_index: int, shot_dir: Path,
             module_globals: dict | None = None) -> callable:
    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        try:
            step_fn(page, params)

            # Success screenshot
            path = take_screenshot(page, shot_dir, step_index, step_fn.__name__)
            print(f" OK  →  {path.name}")
            return step_fn

        except Exception as exc:
            if attempt >= MAX_REPAIR_ATTEMPTS:
                # Final failure screenshot
                take_screenshot(page, shot_dir, step_index, step_fn.__name__, suffix="_FAILED")
                print(f"\n  [FAIL] {step_fn.__name__}: {exc}")
                raise

            print(f"\n  [step failed] {step_fn.__name__}: {exc}")

            # Screenshot BEFORE re-fill — form is in failed/empty state, no PII visible
            screenshot_b64 = base64.b64encode(
                page.screenshot(full_page=True)
            ).decode()
            take_screenshot(page, shot_dir, step_index, step_fn.__name__, suffix="_before_repair")

            print(f"  [repair] Calling Claude to fix the step ...")
            fixed_source = repair_agent.fix(
                step_source    = inspect.getsource(step_fn),
                error          = f"{type(exc).__name__}: {exc}",
                page           = page,
                screenshot_b64 = screenshot_b64,
            )

            print(f"  [repair] Hot-swapping and retrying ...")
            fn_name  = re.search(r"def (\w+)\(", fixed_source).group(1)
            step_fn  = compile_step(fixed_source, module_globals)
            save_fix(workflow_path, fn_name, fixed_source)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(workflow_path: str, input_path: str, headless: bool = False, dry_run: bool = False):
    with open(input_path) as f:
        params = json.load(f)

    if dry_run:
        params.setdefault("integrationDetails", {})["submitApplication"] = False

    workcase_id = params.get("integrationDetails", {}).get("workcaseId", "unknown")
    shot_dir    = screenshot_dir(workcase_id)

    print(f"Workflow    : {Path(workflow_path).name}")
    print(f"Workcase    : {workcase_id}")
    print(f"Screenshots : {shot_dir}")
    print(f"Headless    : {headless}  |  Dry-run: {dry_run}")
    print()

    spec = importlib.util.spec_from_file_location("workflow", workflow_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    steps = mod.STEPS
    workflow_module_globals = vars(mod)   # helpers (_click_next, _wait_ready, etc.) live here

    print(f"Steps : {len(steps)}")
    print("-" * 60)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page    = browser.new_page(viewport={"width": 1280, "height": 900})

        for i, step_fn in enumerate(steps, start=1):
            print(f"[{i:02d}/{len(steps)}] {step_fn.__name__} ...", end="", flush=True)
            step_fn = run_step(page, step_fn, params, workflow_path, i, shot_dir,
                               workflow_module_globals)

        browser.close()

    print("-" * 60)
    print(f"Done. Screenshots saved to: {shot_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run a generated Playwright workflow")
    parser.add_argument("workflow",   help="Path to workflow .py  (e.g. workflows/al_sit.py)")
    parser.add_argument("input",      help="Path to JSON input with PII  (e.g. inputs/al_data.json)")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--dry-run",  action="store_true", help="Skip final submit step")
    args = parser.parse_args()

    try:
        run(args.workflow, args.input, headless=args.headless, dry_run=args.dry_run)
    except Exception as e:
        print(f"\nWorkflow aborted: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
step_compiler.py — Compile a .bprelease into a parameterized Playwright workflow script.

Pipeline:
  1. bp_parser.py parses the .bprelease → IR JSON  (deterministic, no LLM)
  2. For each BP object, Claude (via Bedrock) generates one Playwright step function
  3. Functions are assembled into workflows/<process_name>.py

The generated script contains NO PII — all data values are referenced as params['key'].
PII is injected at runtime by step_runner.py directly into Playwright calls.

Usage:
  AWS_PROFILE=AdministratorAccess-424231649574 \\
    python step_compiler.py <file.bprelease> [--sample-input <input.json>] [--output <path>]
"""

import json
import os
import re
import sys
import argparse
from datetime import datetime
from pathlib import Path

import boto3

from bp_parser import parse_bprelease

BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
AWS_REGION       = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE      = os.environ.get("AWS_PROFILE", "AdministratorAccess-424231649574")

SKIP_STAGE_TYPES = {
    "Start", "End", "ProcessInfo", "SubSheetInfo", "Note",
    "Block", "Anchor", "WaitStart", "WaitEnd", "Recover", "Resume",
}
SKIP_ACTIONS = {
    "AttachApplication", "DetachApplication", "ActivateApp", "IsConnected", "Launch",
}


# ---------------------------------------------------------------------------
# Stage → English description  (feeds the prompt, no PII)
# ---------------------------------------------------------------------------

def stage_to_description(stage: dict) -> str | None:
    stype = stage.get("type", "")
    sname = stage.get("name", "")

    if stype in SKIP_STAGE_TYPES:
        return None

    if stype == "Write":
        w = stage.get("write", {})
        return f"[Write] '{sname}': type value of {w.get('expr','?')} into '{w.get('element','?')}'"

    elif stype == "Navigate":
        steps = stage.get("navigate_steps", [])
        parts = []
        for step in steps:
            action  = step.get("action", "")
            element = step.get("element", "")
            if action in SKIP_ACTIONS:
                continue
            args    = step.get("arguments", {})
            arg_str = f" with args {args}" if args else ""
            if action in ("UIAClickCentre", "UIAFocus", "WebClick", "Click"):
                parts.append(f"click '{element}'")
            elif action in ("SendKeys", "UIASendKeys", "WebType"):
                keys = list(args.values())[0] if args else "keys"
                parts.append(f"type '{keys}' into '{element}'")
            elif action in ("UIASelect", "WebSelect"):
                parts.append(f"select option in '{element}'{arg_str}")
            elif action:
                parts.append(f"{action} on '{element}'{arg_str}")
        if not parts:
            return None
        return f"[Navigate] '{sname}': " + "; then ".join(parts)

    elif stype == "Read":
        r = stage.get("read", {})
        return f"[Read] '{sname}': read from '{r.get('element','?')}' → {r.get('outputs', [])}"

    elif stype == "Decision":
        return f"[Decision] '{sname}': if {stage.get('condition','?')} → branch true/false"

    elif stype == "SubSheet":
        return f"[SubSheet] '{sname}': call '{stage.get('calls_subsheet','?')}'"

    elif stype == "Action":
        a = stage.get("action", {})
        return f"[Action] '{sname}': {a.get('object','')}.{a.get('action','')}({a.get('inputs',{})})"

    elif stype == "Calculation":
        c = stage.get("calculation", {})
        return f"[Calc] '{sname}': {c.get('result','')} = {c.get('expression','')}"

    elif stype == "Exception":
        ex = stage.get("exception", {})
        return f"[Exception] '{sname}': raise {ex.get('type','')} — {ex.get('detail','')}"

    elif stype == "ChoiceStart":
        choices = stage.get("choices", [])
        return f"[Choice] '{sname}': " + "; ".join(f"{c['name']} if {c['decision']}" for c in choices)

    return f"[{stype}] '{sname}'"


# ---------------------------------------------------------------------------
# Prompt builder  (no PII — only field names and element labels)
# ---------------------------------------------------------------------------

def build_prompt(obj: dict, step_number: int, sample_input: dict | None) -> str:
    lines = [
        "You are converting a Blue Prism RPA object into a Python Playwright step function.",
        "",
        f"STEP NUMBER: {step_number:02d}",
        f"OBJECT NAME: {obj['name']}",
        f"LAUNCH URL:  {obj.get('url') or 'already open — do NOT navigate, session is continuous'}",
        "",
    ]

    if sample_input:
        lines += [
            "INPUT SCHEMA — use these exact key paths to reference data (never inline values):",
            json.dumps(sample_input, indent=2),
            "",
            "Reference pattern:  params['companyInformation']['fein']",
            "                    params['stateInformation']['withholdingTaxInfo']['startDateOfEmployment']",
            "                    params['integrationDetails']['workcaseId']",
            "",
        ]
    else:
        lines += [
            "INPUT DATA is accessed via the params dict. Use descriptive key names.",
            "Example: params['entityType'], params['fein'], params['legalName']",
            "",
        ]

    lines += ["STAGES (what this BP object does, in execution order):"]
    for ss in obj["subsheets"]:
        if ss["type"] == "CleanUp":
            continue
        descs = [stage_to_description(s) for s in ss["stages"]]
        descs = [d for d in descs if d]
        if not descs:
            continue
        lines.append(f"\n  Subsheet: '{ss['name']}'")
        for d in descs:
            lines.append(f"    {d}")

    fn_name = f"step_{step_number:02d}_{obj_to_fn_suffix(obj['name'])}"

    lines += [
        "",
        f"TASK: Write exactly this Python function signature:",
        f"  def {fn_name}(page: Page, params: dict) -> None:",
        "",
        "RULES — follow precisely:",
        "1. Locator priority (use highest available for each element):",
        "     page.get_by_label('Field Label')              ← preferred for form inputs",
        "     page.get_by_role('button', name='Next')       ← preferred for buttons",
        "     page.get_by_role('combobox', name='...')      ← preferred for dropdowns",
        "     page.get_by_text('Link or heading text')      ← for links/headings",
        "     page.locator('#id') or page.locator('css')    ← last resort only",
        "2. After any page navigation or form submit, add: page.wait_for_load_state('networkidle')",
        "3. For optional/conditional elements: use page.locator(...).is_visible() before interacting",
        "4. For Decision branches: use Python if/else with params values",
        "5. For repeated items (e.g. multiple officers): use a Python for loop",
        "6. DO NOT navigate to any URL unless this is the first step (step 01)",
        "7. DO NOT re-attach, re-maximize, or re-launch the browser",
        "8. All data values MUST come from params — never hardcode names, IDs, dates, or codes",
        "9. Add a one-line comment above each logical group of interactions",
        "10. Output ONLY the function code — no imports, no explanation",
    ]

    return "\n".join(lines)


def obj_to_fn_suffix(obj_name: str) -> str:
    """'05.BusinessInfo_AL SIT' → 'business_info'"""
    name = re.sub(r"^\d+\.", "", obj_name)
    name = re.sub(r"_[A-Z]{2}\s+\w+$", "", name)
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    return name.strip("_").lower()


# ---------------------------------------------------------------------------
# Bedrock call
# ---------------------------------------------------------------------------

def get_bedrock_client():
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return session.client("bedrock-runtime")


def call_claude(client, prompt: str) -> str:
    response = client.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 4096},
    )
    raw = response["output"]["message"]["content"][0]["text"].strip()
    lines = raw.splitlines()
    return "\n".join(l for l in lines if not l.strip().startswith("```")).strip()


# ---------------------------------------------------------------------------
# Script assembly
# ---------------------------------------------------------------------------

WORKFLOW_HEADER = '''\
"""
Playwright workflow — auto-generated from Blue Prism release
Process : {process_name}
Source  : {release_name}
Generated: {timestamp}

DO NOT inline PII in this file.
All data values flow through the params dict at runtime (injected by step_runner.py).
"""

from playwright.sync_api import Page

'''

WORKFLOW_FOOTER = '''

# ---------------------------------------------------------------------------
# Execution order — consumed by step_runner.py
# ---------------------------------------------------------------------------
STEPS = [
{step_list}
]
'''


def assemble_workflow(ir: dict, functions: list[tuple[str, str]]) -> str:
    header = WORKFLOW_HEADER.format(
        process_name=ir["process_name"],
        release_name=ir["release_name"],
        timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )

    fn_bodies = "\n\n".join(code for _, code in functions)

    fn_names   = [extract_fn_name(code, fallback) for fallback, code in functions]
    step_list  = "\n".join(f"    {name}," for name in fn_names)

    footer = WORKFLOW_FOOTER.format(step_list=step_list)

    return header + fn_bodies + footer


def extract_fn_name(code: str, fallback: str) -> str:
    m = re.search(r"^def (\w+)\(", code, re.MULTILINE)
    return m.group(1) if m else fallback


def process_name_to_filename(process_name: str) -> str:
    """'AL SIT' → 'al_sit.py'"""
    return re.sub(r"[^a-z0-9]+", "_", process_name.lower()).strip("_") + ".py"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compile a .bprelease into a parameterized Playwright workflow"
    )
    parser.add_argument("input",          help=".bprelease file path")
    parser.add_argument("--sample-input", help="Optional sample input JSON — improves params key accuracy")
    parser.add_argument("--output",       help="Output .py path (default: workflows/<name>.py)")
    args = parser.parse_args()

    # --- Parse ---
    print(f"Parsing:  {Path(args.input).name}")
    ir = parse_bprelease(args.input)
    print(f"  Process: {ir['process_name']}  |  Objects: {len(ir['objects'])}")

    # --- Optional sample input ---
    sample_input = None
    if args.sample_input:
        with open(args.sample_input) as f:
            sample_input = json.load(f)
        print(f"  Sample input: {args.sample_input}")

    # --- Generate ---
    out_path = args.output or str(
        Path(__file__).parent / "workflows" / process_name_to_filename(ir["process_name"])
    )

    print(f"Generating Playwright workflow via Bedrock ({BEDROCK_MODEL_ID}) ...")
    client    = get_bedrock_client()
    functions = []

    for i, obj in enumerate(ir["objects"], start=1):
        fallback = f"step_{i:02d}_{obj_to_fn_suffix(obj['name'])}"
        print(f"  [{i:02d}/{len(ir['objects'])}] {obj['name']} ...", end="", flush=True)
        try:
            prompt = build_prompt(obj, i, sample_input)
            code   = call_claude(client, prompt)
            functions.append((fallback, code))
            print(f" → {extract_fn_name(code, fallback)}()")
        except Exception as e:
            print(f" ERROR: {e}")
            stub = f"def {fallback}(page: Page, params: dict) -> None:\n    pass  # TODO: generation failed: {e}"
            functions.append((fallback, stub))

    # --- Assemble ---
    script = assemble_workflow(ir, functions)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(script)

    print(f"\nWorkflow written to: {out_path}")
    print(f"Run with:")
    print(f"  python step_runner.py {out_path} inputs/your_data.json")


if __name__ == "__main__":
    main()

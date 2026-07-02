# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Converts Blue Prism RPA `.bprelease` XML exports into executable Playwright Python automation scripts. Claude (via AWS Bedrock) is used once at compile time to generate step functions, and again at runtime only when a step fails (self-healing).

## Pipeline

```
.bprelease XML → bp_parser.py → IR JSON → step_compiler.py (Claude) → workflows/*.py → step_runner.py → browser
```

- **bp_parser.py** — deterministic XML parser, no LLM calls
- **step_compiler.py** — calls Claude once per BP object to generate Playwright step functions
- **step_runner.py** — executes workflow, injects PII at runtime from `inputs/`
- **repair_agent.py** — called by step_runner on failure; Claude fixes selectors and hot-swaps the function

## Commands

### AWS Auth (required before running anything)
```bash
aws sso login --profile AdministratorAccess-424231649574
```

### Compile a Blue Prism release into a Playwright workflow
```bash
AWS_PROFILE=AdministratorAccess-424231649574 \
  python step_compiler.py MY_STATE.bprelease --sample-input inputs/my_data.json
# output: workflows/my_state.py
```

### Run a workflow
```bash
# Dry run (skips final submit)
AWS_PROFILE=AdministratorAccess-424231649574 \
  python step_runner.py workflows/my_state.py inputs/my_data.json --dry-run --headless

# Live run with visible browser
AWS_PROFILE=AdministratorAccess-424231649574 \
  python step_runner.py workflows/my_state.py inputs/my_data.json
```

## Architecture Constraints

**PII firewall**: `inputs/*.json` files are git-ignored and never referenced in source code. PII is loaded at runtime and passed directly into Playwright calls as `params['key']['subkey']`. Generated `workflows/*.py` files contain no PII and are safe to commit.

**DOM sanitization before Claude**: `repair_agent.py` strips `value=""`, textarea content, and `selected` attributes from the DOM before sending to Claude. Screenshots are taken in blank-form state. Claude never sees PII.

**Self-healing limit**: `MAX_REPAIR_ATTEMPTS = 1` in `step_runner.py`. One Claude repair attempt per failure; if it still fails, the workflow aborts.

**Stage skipping**: `step_compiler.py` defines `SKIP_STAGE_TYPES` — Blue Prism stages like Start, End, ProcessInfo, SubSheetInfo, Note, Block, Anchor are ignored during IR generation.

## AWS / Bedrock Configuration

- **AWS Profile**: `AdministratorAccess-424231649574`
- **Region**: `us-east-1` (hardcoded; override with `AWS_REGION`)
- **Model**: `us.anthropic.claude-sonnet-4-6`

## Input File Schema

`inputs/my_data.json` top-level keys: `integrationDetails`, `companyInformation`, `stateInformation`, `oneStopShop`. Generated step functions reference these as `params['companyInformation']['fein']`, etc.

## Generated Workflow Structure

Each `workflows/*.py` contains:
- Playwright imports
- Helper functions (`_wait_ready`, `_click_next`, `_select_by_text`, etc.)
- Step functions named `step_NN_<object_name>(page: Page, params: dict) -> None`
- A `STEPS = [...]` list at the bottom (ordered references)

## Locator Preference

When generating or fixing Playwright code, prefer semantic locators in this order: label → role → text → CSS → XPath.

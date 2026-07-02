# 02. BluePrism2PlayWright

Convert Blue Prism RPA automation files into Playwright test scripts — deterministically, with Claude as a safety net.

---

## What

`bp2playwright` takes a `.bprelease` file (a Blue Prism process export) and produces a ready-to-run Python Playwright script that drives a real browser through the same workflow. The conversion is a one-command operation:

```bash
python step_compiler.py MY_STATE.bprelease --sample-input inputs/my_data.json
# → workflows/my_state.py
```

At runtime, PII (names, SSNs, FEINs, dates) is loaded from a local JSON file and injected directly into the browser — it never appears in source code or prompts.

---

## Why

Blue Prism processes encode years of institutional knowledge about how to navigate state tax portals, fill multi-page forms, and handle edge cases. Rewriting that knowledge from scratch in Playwright is slow and error-prone.

`bp2playwright` automates the translation:
- Blue Prism XML stages map directly to Playwright interactions
- Field names, UI element labels, and navigation order are preserved
- The output is plain Python — readable, auditable, and version-controllable

When a portal changes its UI and a step breaks, Claude diagnoses the failure from the error and DOM structure and produces a targeted fix — without a human having to debug it manually.

---

## Benefits

**No LLM at runtime.** Playwright scripts run as plain Python. Fast, deterministic, and auditable. Claude is only involved at compile time (once per process) and on failure (once per broken step).

**PII never reaches the LLM.** All sensitive data flows from `inputs/` directly to Playwright `.fill()` calls. Code only ever contains `params['fein']`-style placeholders. The DOM is sanitized before any failure prompt is sent. Claude sees structure, never data.

**Self-healing.** When a step fails (selector changed, timing issue, new modal), the repair agent fixes the step and writes the fix back to the workflow file. The same failure never triggers Claude twice.

**One command per new state.** Drop in a `.bprelease` file, run the compiler, get a workflow. No manual coding required.

**Safe to commit.** Generated workflow files contain no PII and are deterministic enough to review in a pull request. Input files live in `inputs/` which is `.gitignore`d.

---

## Architecture

### Components

| File | Role |
|---|---|
| `bp_parser.py` | Parses `.bprelease` XML → Intermediate Representation (IR) JSON. Pure Python, no LLM. |
| `step_compiler.py` | IR JSON → `workflows/<name>.py`. Calls Claude once per BP object at compile time. |
| `step_runner.py` | Executes a workflow. Loads PII from `inputs/`, injects into Playwright, runs the repair loop on failure. |
| `repair_agent.py` | On step failure: sanitizes DOM, calls Claude with code + error + stripped DOM, hot-swaps the fixed function. |
| `workflows/` | Generated Playwright scripts. No PII. Safe to commit. |
| `inputs/` | PII input JSON files. `.gitignore`d. Never committed. |

### Happy path — no LLM at runtime

```
MY_STATE.bprelease
  ──► bp_parser.py       ──► IR JSON
  ──► step_compiler.py   ──► workflows/my_state.py   [Claude called once per object]

Runtime:
  inputs/data.json (PII)
       │
       ▼
  step_runner.py ──► params dict ──► Playwright ──► browser ──► done
                          ▲
                  PII injected here
                  never in script source
```

### Failure path — LLM involved, but no PII

```
Playwright step raises exception
  ──► step_runner catches it
  ──► repair_agent.fix() receives:
        · failed step source code    (params['fein'] — not "47-8291635")
        · error message              (TimeoutError, strict mode violation, etc.)
        · DOM skeleton               (all input values stripped before sending)
        · screenshot                 (taken before any re-fill — blank form)
  ──► Claude returns fixed step source
  ──► step_runner hot-swaps the function, retries
  ──► fix written back to workflows/my_state.py  ← next run skips Claude entirely
```

### PII firewall

| What Claude receives | Contains PII? | Why it's safe |
|---|---|---|
| IR JSON from bp_parser | No | Only field names and UI element labels |
| Step source code | No | Only `params['fein']` placeholders, never real values |
| Error messages | No | Timeouts, selector failures — no data values |
| DOM skeleton | No | `sanitize_dom()` strips all `value=` attributes and textarea content |
| Screenshot | No | Taken before re-fill, shows blank form state |

### Generated workflow structure

Every `workflows/*.py` follows this pattern:

```python
from playwright.sync_api import Page

def step_01_load_website(page: Page, params: dict) -> None:
    page.goto("https://example-state-tax-portal.gov/")
    page.wait_for_load_state("networkidle")

def step_02_business_id(page: Page, params: dict) -> None:
    page.get_by_label("Federal Employer ID").fill(params['companyInformation']['fein'])
    page.get_by_role("button", name="Next").click()
    page.wait_for_load_state("networkidle")

# ... one function per BP object ...

STEPS = [
    step_01_load_website,
    step_02_business_id,
    # ...
]
```

`STEPS` is the ordered execution list. The runner iterates it. The repair agent fixes individual functions within this file when they fail.

---

## Usage

### Compile a new state

```bash
# Authenticate (SSO — refresh as needed)
aws sso login --profile AdministratorAccess-424231649574

# Compile
AWS_PROFILE=AdministratorAccess-424231649574 \
  python step_compiler.py MY_STATE.bprelease --sample-input inputs/my_data.json
# → workflows/my_state.py
```

### Run a workflow

```bash
# Dry run (skips final submit)
AWS_PROFILE=AdministratorAccess-424231649574 \
  python step_runner.py workflows/my_state.py inputs/my_data.json --dry-run --headless

# Live run
AWS_PROFILE=AdministratorAccess-424231649574 \
  python step_runner.py workflows/my_state.py inputs/my_data.json
```

### Input file format

`inputs/my_data.json` is a nested JSON object. See `inputs/al_data.json` for a complete example. The top-level keys used across states are:

```json
{
  "integrationDetails": { "workcaseId": "...", "submitApplication": true },
  "companyInformation": { "fein": "...", "companyLegalName": "...", ... },
  "stateInformation":   { "withholdingTaxInfo": { ... }, ... },
  "oneStopShop":        { "securityQuestions": [ ... ] }
}
```

---

## AWS Setup

- **Profile**: `AdministratorAccess-424231649574` (SSO — run `aws sso login` to refresh)
- **Bedrock model**: `us.anthropic.claude-sonnet-4-6` (us-east-1 inference profile)
- **Region**: `us-east-1`

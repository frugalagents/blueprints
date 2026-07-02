"""
repair_agent.py — Fix a failed Playwright step using Claude.

PII firewall:
  - Input values are stripped from the DOM before sending to Claude
  - Step source code only contains params['key'] placeholders, never real values
  - Screenshots are taken before any re-fill attempt (blank form state)
  - Claude never sees actual names, SSNs, FEINs, addresses, or dates

Claude's job is narrow: look at the DOM structure and error, fix the selectors
or interaction pattern. The params references remain unchanged.
"""

import base64
import os
import re

import boto3

BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
AWS_REGION       = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE      = os.environ.get("AWS_PROFILE", "AdministratorAccess-424231649574")

MAX_DOM_CHARS = 8000   # keep prompt size reasonable


# ---------------------------------------------------------------------------
# DOM sanitization — strip all field values before sending to Claude
# ---------------------------------------------------------------------------

def sanitize_dom(html: str) -> str:
    """Remove all input/textarea values and selected states from HTML."""
    # Strip value="..." attributes from input fields
    html = re.sub(r'(<input\b[^>]*?)\bvalue=["\'][^"\']*["\']', r'\1', html, flags=re.IGNORECASE)
    # Strip textarea content
    html = re.sub(r'(<textarea\b[^>]*>)[^<]*(</textarea>)', r'\1\2', html, flags=re.IGNORECASE | re.DOTALL)
    # Remove selected="selected" / selected attributes from options
    html = re.sub(r'\bselected(?:=["\'][^"\']*["\'])?', '', html, flags=re.IGNORECASE)
    # Truncate to keep prompt size manageable
    if len(html) > MAX_DOM_CHARS:
        html = html[:MAX_DOM_CHARS] + "\n<!-- truncated -->"
    return html


# ---------------------------------------------------------------------------
# Prompt builder  (no PII)
# ---------------------------------------------------------------------------

def build_repair_prompt(step_source: str, error: str, dom_skeleton: str) -> str:
    return f"""A Playwright step failed. Fix the step function so it works with the current page.

FAILED STEP SOURCE (params['...'] are placeholders — do NOT change them):
{step_source}

ERROR:
{error}

CURRENT PAGE DOM (input values stripped — structure only):
{dom_skeleton}

RULES:
- Keep every params['...'] reference exactly as-is — these are PII placeholders
- Only fix: selectors, locator strategy, timing, interaction order
- Use Playwright semantic locators in priority order:
    page.get_by_label("...")
    page.get_by_role("button"/"combobox"/..., name="...")
    page.get_by_text("...")
    page.locator("css") — last resort
- If the issue is timing, add page.wait_for_load_state("networkidle") or page.wait_for_selector(...)
- If an element might be absent, guard with page.locator(...).is_visible()
- Output ONLY the corrected function code — same signature, no imports, no explanation
"""


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

def _get_client():
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return session.client("bedrock-runtime")


def fix(step_source: str, error: str, page, screenshot_b64: str | None = None) -> str:
    """
    Call Claude to repair a failed step.

    Args:
        step_source   : source code of the failed function (params placeholders, no PII)
        error         : exception message / traceback
        page          : live Playwright page (used to get DOM — values are stripped)
        screenshot_b64: optional base64 screenshot taken BEFORE any re-fill attempt

    Returns:
        Fixed function source code (still uses params placeholders)
    """
    dom_skeleton = sanitize_dom(page.content())

    prompt = build_repair_prompt(step_source, error, dom_skeleton)

    messages = [{"role": "user", "content": []}]

    # Attach screenshot if provided (visual context, taken before fill so no PII visible)
    if screenshot_b64:
        messages[0]["content"].append({
            "image": {
                "format": "png",
                "source": {"bytes": base64.b64decode(screenshot_b64)},
            }
        })

    messages[0]["content"].append({"text": prompt})

    client   = _get_client()
    response = client.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=messages,
        inferenceConfig={"maxTokens": 4096},
    )

    raw   = response["output"]["message"]["content"][0]["text"].strip()
    lines = raw.splitlines()
    return "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

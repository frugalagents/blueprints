from __future__ import annotations

import json

from api_kg.llm.bedrock_client import BedrockClient


def synthesize_answer(question: str, evidence: dict, config: dict, use_bedrock: bool = True) -> str:
    claims = evidence.get("claims", [])

    # Fast path: if claims are self-explanatory, format directly without LLM
    direct = _try_direct_answer(question, claims)
    if direct:
        return direct

    if use_bedrock:
        try:
            client = BedrockClient(config.get("bedrock", {}))
            return client.converse_text(
                _prompt(question, claims),
                model_key="synthesis_model",
                system="You answer enterprise questions using only the supplied evidence. Be concise: 2-4 sentences max. State the key facts. If data is missing or placeholder, say so in one sentence.",
            )
        except Exception:
            pass

    return _fallback_answer(claims)


def _try_direct_answer(question: str, claims: list[dict]) -> str | None:
    """If claims contain clear numeric deltas that answer the question, skip the LLM."""
    if not claims:
        return None

    # Check if we have real delta claims (not placeholder data)
    delta_claims = [c for c in claims if c.get("delta") is not None and c.get("previous") is not None]
    if not delta_claims:
        return None

    # Check for placeholder values
    for c in delta_claims:
        if any(str(v).startswith("sample_") for v in [c.get("previous"), c.get("current")] if v):
            return None

    # Format directly
    # Find the primary delta (largest absolute value, likely the net change)
    sorted_claims = sorted(delta_claims, key=lambda c: abs(c.get("delta", 0)), reverse=True)
    primary = sorted_claims[0]
    contributors = sorted_claims[1:]

    parts = []
    primary_field = primary.get("field", "")
    primary_delta = primary.get("delta", 0)
    sign = "increased" if primary_delta > 0 else "decreased"
    parts.append(f"**{primary_field}** {sign} by {abs(primary_delta)} (from {primary.get('previous')} to {primary.get('current')}).")

    if contributors:
        parts.append("\n\nContributing factors:")
        for c in contributors[:6]:
            field = c.get("field", "")
            delta = c.get("delta", 0)
            d_sign = "+" if delta > 0 else ""
            parts.append(f"- **{field}**: {c.get('previous')} → {c.get('current')} ({d_sign}{delta})")

    return "".join(parts)


def _prompt(question: str, claims: list[dict]) -> str:
    claims_text = json.dumps(claims[:15], indent=2, default=str)
    return f"""Question: {question}

Evidence claims:
{claims_text}

Answer in 2-4 sentences. State the key finding first, then the top contributors. If evidence contains placeholder/sample values, say the data is unavailable in one sentence."""


def _fallback_answer(claims: list[dict]) -> str:
    if not claims:
        return "No evidence was produced from the executed plan."
    delta_claims = [c for c in claims if c.get("delta") is not None]
    if delta_claims:
        top = sorted(delta_claims, key=lambda c: abs(c.get("delta", 0)), reverse=True)[:5]
        parts = []
        for c in top:
            parts.append(f"- {c.get('field')}: {c.get('previous')} → {c.get('current')} (delta {c.get('delta')})")
        return "Key changes:\n" + "\n".join(parts)
    # Non-delta claims
    top = claims[:5]
    return "\n".join(f"- {c.get('claim', '')}" for c in top)

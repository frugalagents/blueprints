"""
flow_mapper — Identify end-to-end business flows and generate codebase summaries.

Groups classified functions into coherent business processes and produces a
plain-language summary suitable for non-technical stakeholders.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Union

import boto3
from botocore.exceptions import ClientError

from .prompts import FLOW_MAPPING_PROMPT, CODEBASE_SUMMARY_PROMPT

logger = logging.getLogger(__name__)

# Retry configuration
_MAX_RETRIES: int = 3
_BASE_BACKOFF_SECS: float = 1.0


class FlowMapper:
    """Identify business flows and generate codebase summaries via LLM.

    Parameters
    ----------
    model_id : str
        Bedrock model identifier.
    region : str
        AWS region for the Bedrock Runtime client.
    """

    def __init__(
        self,
        model_id: str = "anthropic.claude-sonnet-4-20250514",
        region: str = "us-east-1",
    ) -> None:
        self.model_id = model_id
        self.region = region
        self._client = boto3.client("bedrock-runtime", region_name=self.region)
        logger.info(
            "FlowMapper initialised  model=%s  region=%s",
            self.model_id,
            self.region,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def identify_flows(
        self,
        graph: Any,
        domain_functions: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Identify end-to-end business flows, one LLM call per domain.

        Parameters
        ----------
        graph : object
            The code-relationship graph (used for ordering hints).  May expose
            ``get_callers(name)``, ``get_callees(name)``.
        domain_functions : dict[str, list[dict]]
            Mapping of ``domain_name → list of classified function dicts``.
            Each function dict should include ``function_name``,
            ``classification``, ``business_summary``, ``file_path``.

        Returns
        -------
        list[dict]
            Each dict has keys: ``flow_name``, ``description``, ``steps``,
            ``functions_involved``, ``domain``.
        """
        all_flows: List[Dict[str, Any]] = []

        for domain_name, functions in domain_functions.items():
            if not functions:
                continue

            logger.info(
                "Mapping flows for domain '%s' (%d functions)",
                domain_name,
                len(functions),
            )

            functions_text = self._format_classified_functions(functions, graph)
            prompt = FLOW_MAPPING_PROMPT.format(
                domain_name=domain_name,
                functions_with_classifications=functions_text,
            )

            raw = self._invoke_llm(prompt)

            try:
                flows = self._parse_json_response(raw)
            except ValueError:
                logger.warning(
                    "Failed to parse flows for domain '%s' — skipping",
                    domain_name,
                )
                continue

            if not isinstance(flows, list):
                flows = [flows] if isinstance(flows, dict) else []

            # Tag each flow with its domain
            for flow in flows:
                flow["domain"] = domain_name
                # Ensure required keys exist
                flow.setdefault("flow_name", "Unnamed Flow")
                flow.setdefault("description", "")
                flow.setdefault("steps", [])
                flow.setdefault("functions_involved", [])

            all_flows.extend(flows)
            logger.debug(
                "Found %d flows in domain '%s'", len(flows), domain_name
            )

        logger.info("Total business flows identified: %d", len(all_flows))
        return all_flows

    def generate_codebase_summary(
        self,
        domains: List[str],
        stats: Dict[str, Any],
    ) -> str:
        """Generate a plain-language business summary of the codebase.

        Parameters
        ----------
        domains : list[str]
            Names of the discovered business domains.
        stats : dict
            Must contain ``function_count`` (int) and ``rule_count`` (int).

        Returns
        -------
        str
            Multi-paragraph business summary text.
        """
        prompt = CODEBASE_SUMMARY_PROMPT.format(
            domains=", ".join(domains) if domains else "none identified",
            function_count=stats.get("function_count", 0),
            rule_count=stats.get("rule_count", 0),
        )

        raw = self._invoke_llm(prompt)
        # The summary prompt asks for plain text — return as-is
        return raw.strip()

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _invoke_llm(self, prompt: str) -> str:
        """Call Bedrock ``converse()`` with retry + exponential backoff."""
        messages = [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ]

        last_error: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._client.converse(
                    modelId=self.model_id,
                    messages=messages,
                    inferenceConfig={
                        "maxTokens": 4096,
                        "temperature": 0.2,
                    },
                )
                output_message = response.get("output", {}).get("message", {})
                content_blocks = output_message.get("content", [])
                texts = [
                    block["text"]
                    for block in content_blocks
                    if "text" in block
                ]
                return "\n".join(texts)

            except ClientError as exc:
                last_error = exc
                error_code = exc.response.get("Error", {}).get("Code", "")
                if error_code in (
                    "ThrottlingException",
                    "ModelTimeoutException",
                    "ServiceUnavailableException",
                ):
                    wait = _BASE_BACKOFF_SECS * (2 ** (attempt - 1))
                    logger.warning(
                        "Bedrock %s on attempt %d/%d — retrying in %.1fs",
                        error_code,
                        attempt,
                        _MAX_RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                wait = _BASE_BACKOFF_SECS * (2 ** (attempt - 1))
                logger.warning(
                    "Unexpected error on attempt %d/%d: %s — retrying in %.1fs",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"Bedrock converse() failed after {_MAX_RETRIES} attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_response(text: str) -> Union[Dict[str, Any], List[Any]]:
        """Extract JSON from LLM output, handling markdown fencing."""
        if not text:
            raise ValueError("Empty LLM response")

        fenced = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except json.JSONDecodeError:
                pass

        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        for start_char, end_char in [("[", "]"), ("{", "}")]:
            s = text.find(start_char)
            e = text.rfind(end_char)
            if s != -1 and e != -1 and e > s:
                try:
                    return json.loads(text[s : e + 1])
                except json.JSONDecodeError:
                    continue

        raise ValueError(f"Could not extract JSON from LLM response: {text[:200]}…")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_classified_functions(
        functions: List[Dict[str, Any]],
        graph: Any,
    ) -> str:
        """Format a list of classified functions for the flow-mapping prompt.

        Includes call relationships when the graph is available.
        """
        lines: List[str] = []
        for fn in functions:
            name = fn.get("function_name", "unknown")
            classification = fn.get("classification", "unclassified")
            summary = fn.get("business_summary", "")
            file_path = fn.get("file_path", "")

            # Gather call edges from graph
            callers_str = ""
            callees_str = ""
            if graph is not None:
                if hasattr(graph, "get_callers"):
                    callers = list(graph.get_callers(name))
                    if callers:
                        callers_str = f"  Called by: {', '.join(callers)}"
                if hasattr(graph, "get_callees"):
                    callees = list(graph.get_callees(name))
                    if callees:
                        callees_str = f"  Calls: {', '.join(callees)}"

            line = (
                f"- **{name}** [{classification}] ({file_path})\n"
                f"  Summary: {summary}"
            )
            if callers_str:
                line += f"\n{callers_str}"
            if callees_str:
                line += f"\n{callees_str}"

            lines.append(line)

        return "\n".join(lines)

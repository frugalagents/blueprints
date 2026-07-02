"""
rule_extractor — Extract BDD-style business rules from classified functions.

Operates on functions already classified as ``BUSINESS_RULE`` or
``BUSINESS_PROCESS`` and produces structured Given / When / Then rules.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Union

import boto3
from botocore.exceptions import ClientError

from .prompts import RULE_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

# Retry configuration
_MAX_RETRIES: int = 3
_BASE_BACKOFF_SECS: float = 1.0

# Only these classifications are eligible for rule extraction
_RULE_ELIGIBLE = frozenset({"BUSINESS_RULE", "BUSINESS_PROCESS"})


class RuleExtractor:
    """Extract BDD business rules from source code using an LLM.

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
            "RuleExtractor initialised  model=%s  region=%s",
            self.model_id,
            self.region,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_rules(
        self,
        func_metadata: Dict[str, Any],
        source_code: str,
        related_functions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Extract BDD rules from a single function.

        Parameters
        ----------
        func_metadata : dict
            Must contain ``function_name``, ``business_domain``,
            ``classification``, and ``business_summary``.
        source_code : str
            Raw source code of the function.
        related_functions : list[dict]
            Summaries of callers, callees, or community siblings for context.
            Each dict should have at least ``function_name`` and
            ``business_summary``.

        Returns
        -------
        list[dict]
            Each dict has keys: ``rule_name``, ``given``, ``when``, ``then``,
            ``source_lines``, ``source_snippet``, ``confidence``,
            ``business_impact``.  Returns ``[]`` when no rules are found.
        """
        related_text = self._format_related_functions(related_functions)

        prompt = RULE_EXTRACTION_PROMPT.format(
            function_name=func_metadata.get("function_name", "unknown"),
            business_domain=func_metadata.get("business_domain", "Unknown"),
            classification=func_metadata.get("classification", "BUSINESS_RULE"),
            summary=func_metadata.get("business_summary", ""),
            source_code=source_code,
            related_functions=related_text,
        )

        raw = self._invoke_llm(prompt)

        try:
            rules = self._parse_json_response(raw)
        except ValueError:
            logger.warning(
                "Failed to parse rules for %s — returning empty list",
                func_metadata.get("function_name"),
            )
            return []

        if not isinstance(rules, list):
            logger.warning(
                "Expected JSON array from LLM but got %s — wrapping",
                type(rules).__name__,
            )
            rules = [rules] if isinstance(rules, dict) else []

        # Tag each rule with its source function
        for rule in rules:
            rule["source_function"] = func_metadata.get("function_name")
            rule["business_domain"] = func_metadata.get("business_domain", "Unknown")
            # Clamp confidence
            try:
                rule["confidence"] = max(0.0, min(1.0, float(rule.get("confidence", 0.5))))
            except (TypeError, ValueError):
                rule["confidence"] = 0.5

        return rules

    def extract_all_rules(
        self,
        graph: Any,
        codebase: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Extract rules from every eligible function in the codebase.

        Parameters
        ----------
        graph : object
            The code-relationship graph.  Expected helpers:
            ``get_callers(name)``, ``get_callees(name)``,
            ``get_community_members(name)``, ``get_node(name)``.
        codebase : dict
            Mapping of ``function_name → dict`` with at least
            ``source_code``, ``classification``, ``business_domain``,
            ``business_summary``.

        Returns
        -------
        list[dict]
            All extracted rules across the codebase.
        """
        all_rules: List[Dict[str, Any]] = []
        eligible = [
            (name, meta)
            for name, meta in codebase.items()
            if meta.get("classification") in _RULE_ELIGIBLE
        ]
        logger.info(
            "Extracting rules from %d eligible functions (out of %d total)",
            len(eligible),
            len(codebase),
        )

        for name, meta in eligible:
            source_code = meta.get("source_code", "")
            related = self._gather_related(name, graph, codebase)

            rules = self.extract_rules(
                func_metadata={
                    "function_name": name,
                    "business_domain": meta.get("business_domain", "Unknown"),
                    "classification": meta.get("classification"),
                    "business_summary": meta.get("business_summary", ""),
                },
                source_code=source_code,
                related_functions=related,
            )
            all_rules.extend(rules)
            logger.debug("Extracted %d rules from %s", len(rules), name)

        logger.info("Total rules extracted: %d", len(all_rules))
        return all_rules

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
                        "temperature": 0.1,
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

        # Strip markdown code fences
        fenced = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try raw text
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Locate first [ or {
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
    def _format_related_functions(related: List[Dict[str, Any]]) -> str:
        """Format related functions into a readable block for the prompt."""
        if not related:
            return "(none)"
        lines: List[str] = []
        for fn in related:
            name = fn.get("function_name", "unknown")
            summary = fn.get("business_summary", "no summary")
            classification = fn.get("classification", "unclassified")
            lines.append(f"- {name} [{classification}]: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _gather_related(
        function_name: str,
        graph: Any,
        codebase: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Pull callers, callees, and community siblings from the graph.

        Returns a deduplicated list of metadata dicts for each related
        function.
        """
        related_names: set[str] = set()

        if graph is not None:
            if hasattr(graph, "get_callers"):
                related_names.update(graph.get_callers(function_name))
            if hasattr(graph, "get_callees"):
                related_names.update(graph.get_callees(function_name))
            if hasattr(graph, "get_community_members"):
                siblings = graph.get_community_members(function_name)
                related_names.update(siblings)

        # Remove self
        related_names.discard(function_name)

        related: List[Dict[str, Any]] = []
        for name in sorted(related_names):
            meta = codebase.get(name, {})
            related.append(
                {
                    "function_name": name,
                    "classification": meta.get("classification", "unclassified"),
                    "business_summary": meta.get("business_summary", ""),
                    "business_domain": meta.get("business_domain", "Unknown"),
                }
            )
        return related

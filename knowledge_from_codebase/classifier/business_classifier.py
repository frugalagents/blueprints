"""
business_classifier — LLM-powered function classification via Amazon Bedrock.

Classifies each function/method into one of:
  BUSINESS_RULE | BUSINESS_PROCESS | DATA_ACCESS |
  TECHNICAL_INFRASTRUCTURE | INTEGRATION
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Union

import boto3
from botocore.exceptions import ClientError

from .prompts import CLASSIFICATION_PROMPT

logger = logging.getLogger(__name__)

# Categories emitted by the classifier
VALID_CLASSIFICATIONS = frozenset(
    {
        "BUSINESS_RULE",
        "BUSINESS_PROCESS",
        "DATA_ACCESS",
        "TECHNICAL_INFRASTRUCTURE",
        "INTEGRATION",
    }
)

# Retry configuration
_MAX_RETRIES: int = 3
_BASE_BACKOFF_SECS: float = 1.0


class BusinessClassifier:
    """Classify source-code functions into business-domain categories.

    Uses Amazon Bedrock's ``converse()`` API with an Anthropic Claude model.

    Parameters
    ----------
    model_id : str
        Bedrock model identifier (default ``anthropic.claude-sonnet-4-20250514``).
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
            "BusinessClassifier initialised  model=%s  region=%s",
            self.model_id,
            self.region,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_function(
        self,
        func_metadata: Dict[str, Any],
        source_code: str,
        graph_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Classify a single function.

        Parameters
        ----------
        func_metadata : dict
            Must contain at minimum ``function_name``, ``file_path``, ``kind``,
            ``language``.  May also include ``docstring``.
        source_code : str
            The raw source code of the function.
        graph_context : dict
            Neighbourhood info: ``callers`` (list[str]), ``callees`` (list[str]),
            ``community_id`` (int | str).

        Returns
        -------
        dict
            Keys: ``classification``, ``confidence``, ``business_summary``,
            ``business_domain``, ``reasoning``.  On failure an extra
            ``error`` key is present and ``classification`` falls back to
            ``TECHNICAL_INFRASTRUCTURE``.
        """
        prompt = CLASSIFICATION_PROMPT.format(
            function_name=func_metadata.get("function_name", "unknown"),
            file_path=func_metadata.get("file_path", "unknown"),
            kind=func_metadata.get("kind", "function"),
            language=func_metadata.get("language", "unknown"),
            callers=", ".join(graph_context.get("callers", [])) or "none",
            callees=", ".join(graph_context.get("callees", [])) or "none",
            community_id=graph_context.get("community_id", "N/A"),
            docstring=func_metadata.get("docstring", "(no docstring)"),
            source_code=source_code,
        )

        raw = self._invoke_llm(prompt)
        result = self._parse_json_response(raw)

        if isinstance(result, dict):
            # Normalise / validate the classification label
            result["classification"] = self._normalise_classification(
                result.get("classification", "")
            )
            # Clamp confidence
            try:
                result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
            except (TypeError, ValueError):
                result["confidence"] = 0.5
            return result

        # Fallback when parsing fails
        logger.warning(
            "Failed to parse classification for %s – falling back to TECHNICAL_INFRASTRUCTURE",
            func_metadata.get("function_name"),
        )
        return {
            "classification": "TECHNICAL_INFRASTRUCTURE",
            "confidence": 0.0,
            "business_summary": "Classification failed – defaulting to infrastructure.",
            "business_domain": "Unknown",
            "reasoning": "LLM response could not be parsed.",
            "error": raw,
        }

    def classify_batch(
        self,
        functions: List[Dict[str, Any]],
        graph: Any,
        batch_size: int = 5,
    ) -> List[Dict[str, Any]]:
        """Classify multiple functions, batching ``batch_size`` per LLM call.

        Parameters
        ----------
        functions : list[dict]
            Each dict must contain ``function_name``, ``file_path``, ``kind``,
            ``language``, ``source_code``, and optionally ``docstring``.
        graph : object
            A graph object that exposes helpers for neighbourhood lookup:
            ``get_callers(name) -> list[str]``,
            ``get_callees(name) -> list[str]``,
            ``get_community(name) -> str|int``.
        batch_size : int
            Number of functions per batched prompt (default 5).

        Returns
        -------
        list[dict]
            One classification dict per input function, in the same order.
        """
        results: List[Dict[str, Any]] = []

        for i in range(0, len(functions), batch_size):
            batch = functions[i : i + batch_size]

            if len(batch) == 1:
                # Single function — use the dedicated single-classify path
                func = batch[0]
                ctx = self._graph_context_for(func, graph)
                result = self.classify_function(
                    func_metadata=func,
                    source_code=func.get("source_code", ""),
                    graph_context=ctx,
                )
                result["function_name"] = func.get("function_name")
                results.append(result)
                continue

            # Build a multi-function prompt
            prompt = self._build_batch_prompt(batch, graph)
            raw = self._invoke_llm(prompt)
            parsed = self._parse_json_response(raw)

            if isinstance(parsed, list) and len(parsed) == len(batch):
                for func, res in zip(batch, parsed):
                    if isinstance(res, dict):
                        res["classification"] = self._normalise_classification(
                            res.get("classification", "")
                        )
                        try:
                            res["confidence"] = max(
                                0.0, min(1.0, float(res.get("confidence", 0.5)))
                            )
                        except (TypeError, ValueError):
                            res["confidence"] = 0.5
                    else:
                        res = self._fallback_result()
                    res["function_name"] = func.get("function_name")
                    results.append(res)
            else:
                # Batch parse failed — fall back to one-by-one
                logger.warning(
                    "Batch classification parse failed (got %s items, expected %d). "
                    "Falling back to individual classification.",
                    len(parsed) if isinstance(parsed, list) else type(parsed).__name__,
                    len(batch),
                )
                for func in batch:
                    ctx = self._graph_context_for(func, graph)
                    result = self.classify_function(
                        func_metadata=func,
                        source_code=func.get("source_code", ""),
                        graph_context=ctx,
                    )
                    result["function_name"] = func.get("function_name")
                    results.append(result)

        return results

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _invoke_llm(self, prompt: str) -> str:
        """Call Bedrock ``converse()`` API with retry and exponential backoff.

        Parameters
        ----------
        prompt : str
            The user-turn message to send.

        Returns
        -------
        str
            The assistant's text reply.

        Raises
        ------
        RuntimeError
            If all retries are exhausted.
        """
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
                # Extract assistant text
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
        """Parse a JSON object or array from LLM output.

        Handles common LLM quirks:
        * Markdown code fences (````json ... ``` ``)
        * Leading / trailing prose around the JSON payload

        Parameters
        ----------
        text : str
            Raw LLM response text.

        Returns
        -------
        dict | list
            Parsed JSON structure.

        Raises
        ------
        ValueError
            If no valid JSON can be extracted.
        """
        if not text:
            raise ValueError("Empty LLM response")

        # 1. Try stripping markdown code fences
        fenced = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 2. Try the raw text directly
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # 3. Find the first { or [ and try from there
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start_idx = text.find(start_char)
            end_idx = text.rfind(end_char)
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                try:
                    return json.loads(text[start_idx : end_idx + 1])
                except json.JSONDecodeError:
                    continue

        raise ValueError(f"Could not extract JSON from LLM response: {text[:200]}…")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_classification(raw: str) -> str:
        """Return a valid classification label or ``TECHNICAL_INFRASTRUCTURE``."""
        normalised = raw.strip().upper().replace(" ", "_").replace("-", "_")
        if normalised in VALID_CLASSIFICATIONS:
            return normalised
        logger.warning(
            "Unknown classification '%s' — mapping to TECHNICAL_INFRASTRUCTURE", raw
        )
        return "TECHNICAL_INFRASTRUCTURE"

    @staticmethod
    def _fallback_result() -> Dict[str, Any]:
        return {
            "classification": "TECHNICAL_INFRASTRUCTURE",
            "confidence": 0.0,
            "business_summary": "Classification failed.",
            "business_domain": "Unknown",
            "reasoning": "Parse error on batch item.",
        }

    @staticmethod
    def _graph_context_for(func: Dict[str, Any], graph: Any) -> Dict[str, Any]:
        """Build a ``graph_context`` dict from a graph object."""
        name = func.get("function_name", "")
        callers: List[str] = []
        callees: List[str] = []
        community_id: Any = "N/A"

        if graph is not None:
            if hasattr(graph, "get_callers"):
                callers = list(graph.get_callers(name))
            if hasattr(graph, "get_callees"):
                callees = list(graph.get_callees(name))
            if hasattr(graph, "get_community"):
                community_id = graph.get_community(name)

        return {
            "callers": callers,
            "callees": callees,
            "community_id": community_id,
        }

    def _build_batch_prompt(
        self,
        batch: List[Dict[str, Any]],
        graph: Any,
    ) -> str:
        """Create a single prompt that asks the LLM to classify N functions.

        Returns a prompt instructing the model to return a JSON **array** of
        classification objects, one per function, in the same order.
        """
        sections: List[str] = []
        for idx, func in enumerate(batch, start=1):
            ctx = self._graph_context_for(func, graph)
            section = (
                f"--- Function {idx} ---\n"
                f"Name: {func.get('function_name', 'unknown')}\n"
                f"File: {func.get('file_path', 'unknown')}\n"
                f"Kind: {func.get('kind', 'function')}\n"
                f"Language: {func.get('language', 'unknown')}\n"
                f"Callers: {', '.join(ctx['callers']) or 'none'}\n"
                f"Callees: {', '.join(ctx['callees']) or 'none'}\n"
                f"Community ID: {ctx['community_id']}\n"
                f"Docstring: {func.get('docstring', '(none)')}\n"
                f"Source:\n```\n{func.get('source_code', '')}\n```\n"
            )
            sections.append(section)

        functions_block = "\n".join(sections)

        return (
            "You are a senior software architect specialising in business-domain "
            "analysis.\n\n"
            "Classify EACH of the following functions into exactly ONE category:\n"
            "BUSINESS_RULE | BUSINESS_PROCESS | DATA_ACCESS | "
            "TECHNICAL_INFRASTRUCTURE | INTEGRATION\n\n"
            f"{functions_block}\n"
            "Return a JSON **array** with one object per function, in the same "
            "order.  Each object must have keys: classification, confidence, "
            "business_summary, business_domain, reasoning.\n\n"
            "Return ONLY the JSON array — no other text."
        )

"""
graph.schema — Node, Edge, and classification type definitions for the code graph.

All graph entities are defined as dataclasses with full type annotations.
Constants enumerate the valid node kinds, edge types, and business classifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enumerated constants
# ---------------------------------------------------------------------------

NODE_TYPES: list[str] = [
    "Module",
    "Class",
    "Function",
    "Method",
    "Endpoint",
    "DataModel",
    "BusinessRule",
    "Community",
]

EDGE_TYPES: list[str] = [
    "CALLS",
    "IMPORTS",
    "IMPLEMENTS",
    "CONTAINS",
    "USES_TYPE",
    "HANDLES",
    "READS",
    "WRITES",
    "MEMBER_OF",
    "GOVERNED_BY",
]

CLASSIFICATIONS: list[str] = [
    "BUSINESS_RULE",
    "BUSINESS_PROCESS",
    "DATA_ACCESS",
    "TECHNICAL_INFRASTRUCTURE",
    "INTEGRATION",
]


# ---------------------------------------------------------------------------
# Dataclass definitions
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """A single node in the code graph.

    Parameters
    ----------
    qualified_name : str
        Fully-qualified Python name (e.g. ``mypackage.module.ClassName.method``).
    name : str
        Short display name.
    kind : str
        One of :pydata:`NODE_TYPES`.
    file : str
        Relative path to the source file.
    start_line : int
        First line of the definition in *file*.
    end_line : int
        Last line of the definition in *file*.
    docstring : str
        Extracted docstring (may be empty).
    source_snippet : str
        First *N* lines of source for quick preview.
    domain : str | None
        Business domain label (e.g. ``"billing"``, ``"auth"``).
    classification : str | None
        One of :pydata:`CLASSIFICATIONS`, or ``None`` if not yet classified.
    business_summary : str | None
        LLM-generated plain-English summary of what this node does from a
        business perspective.
    confidence : float | None
        Classification confidence in ``[0.0, 1.0]``.
    community_id : int | None
        Community cluster ID assigned by community detection.
    metadata : dict[str, Any]
        Arbitrary extra key-value data.
    """

    qualified_name: str
    name: str
    kind: str
    file: str = ""
    start_line: int = 0
    end_line: int = 0
    docstring: str = ""
    source_snippet: str = ""
    domain: Optional[str] = None
    classification: Optional[str] = None
    business_summary: Optional[str] = None
    confidence: Optional[float] = None
    community_id: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        if self.kind not in NODE_TYPES:
            raise ValueError(
                f"Invalid node kind {self.kind!r}. Must be one of {NODE_TYPES}"
            )
        if self.classification is not None and self.classification not in CLASSIFICATIONS:
            raise ValueError(
                f"Invalid classification {self.classification!r}. "
                f"Must be one of {CLASSIFICATIONS}"
            )
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"Confidence must be in [0.0, 1.0], got {self.confidence}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)


@dataclass
class Edge:
    """A directed edge between two nodes.

    Parameters
    ----------
    from_name : str
        ``qualified_name`` of the source node.
    to_name : str
        ``qualified_name`` of the target node.
    edge_type : str
        One of :pydata:`EDGE_TYPES`.
    metadata : dict[str, Any]
        Arbitrary extra context (call-site line number, import alias, …).
    """

    from_name: str
    to_name: str
    edge_type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.edge_type not in EDGE_TYPES:
            raise ValueError(
                f"Invalid edge type {self.edge_type!r}. Must be one of {EDGE_TYPES}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)


@dataclass
class Community:
    """A detected community / cluster of related nodes.

    Parameters
    ----------
    community_id : int
        Unique cluster identifier.
    label : str
        Human-readable label (may be auto-generated).
    members : list[str]
        ``qualified_name`` values of member nodes.
    domain : str | None
        Dominant domain in the cluster, if determined.
    summary : str | None
        LLM-generated summary of what this community represents.
    """

    community_id: int
    label: str = ""
    members: list[str] = field(default_factory=list)
    domain: Optional[str] = None
    summary: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

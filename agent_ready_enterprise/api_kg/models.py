from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class FieldRef:
    name: str
    field_type: str = "string"
    location: str = "response"
    required: bool = False
    is_identifier: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Entity:
    name: str
    domain: str
    fields: list[str] = field(default_factory=list)
    key_field: str = ""
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Capability:
    operation_id: str
    domain: str
    method: str
    path: str
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    input_fields: list[FieldRef] = field(default_factory=list)
    output_fields: list[FieldRef] = field(default_factory=list)
    consumes_entities: list[str] = field(default_factory=list)
    produces_entities: list[str] = field(default_factory=list)
    explicit_depends_on: list[str] = field(default_factory=list)
    side_effect: str = "read"
    pii_level: str = "unknown"

    @property
    def required_inputs(self) -> list[str]:
        return [f.name for f in self.input_fields if f.required]

    @property
    def path_params(self) -> list[str]:
        return [f.name for f in self.input_fields if f.location == "path"]

    def search_text(self) -> str:
        names = [f.name for f in self.input_fields + self.output_fields]
        return " ".join(
            [
                self.operation_id,
                self.domain,
                self.method,
                self.path,
                self.summary,
                self.description,
                " ".join(self.tags),
                " ".join(names),
                " ".join(self.consumes_entities),
                " ".join(self.produces_entities),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_inputs"] = self.required_inputs
        data["path_params"] = self.path_params
        return data


@dataclass
class DependencyEdge:
    source: str
    target: str
    confidence: float
    dependency_type: str = "data_flow"
    methods: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    is_explicit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_identifier(name: str) -> bool:
    lower = name.lower()
    return lower == "id" or lower.endswith(("_id", "_code", "_number", "_ref")) or lower in {"mrn"}

from __future__ import annotations

from collections import defaultdict

from api_kg.models import Capability, DependencyEdge


GENERIC_FIELDS = {"q", "limit", "offset", "page", "status", "type", "sort", "since"}


def infer_dependencies(capabilities: list[Capability]) -> list[DependencyEdge]:
    edges: dict[tuple[str, str], DependencyEdge] = {}

    for cap in capabilities:
        for dep in cap.explicit_depends_on:
            merge_edge(
                edges,
                DependencyEdge(
                    source=dep,
                    target=cap.operation_id,
                    confidence=1.0,
                    methods=["explicit"],
                    reasons=["Explicit x-depends-on annotation"],
                    is_explicit=True,
                ),
            )

    output_index: dict[str, list[Capability]] = defaultdict(list)
    for cap in capabilities:
        for field in cap.output_fields:
            if field.name not in GENERIC_FIELDS:
                output_index[field.name].append(cap)

    for consumer in capabilities:
        for input_field in consumer.input_fields:
            if input_field.name in GENERIC_FIELDS:
                continue
            # Identifiers such as employee_id/patient_id are usually provided by
            # the user/session resolver. Treat them as graph join keys, not
            # operation dependencies, unless an explicit edge says otherwise.
            if input_field.is_identifier:
                continue
            for producer in output_index.get(input_field.name, []):
                if producer.operation_id == consumer.operation_id:
                    continue
                confidence = 0.45
                if input_field.location == "path":
                    confidence += 0.2
                if producer.domain != consumer.domain:
                    confidence += 0.1
                if input_field.is_identifier:
                    confidence += 0.1
                merge_edge(
                    edges,
                    DependencyEdge(
                        source=producer.operation_id,
                        target=consumer.operation_id,
                        confidence=min(confidence, 0.9),
                        methods=["field_match"],
                        reasons=[f"Output field '{input_field.name}' feeds input of {consumer.operation_id}"],
                    ),
                )

    entity_producers: dict[str, list[Capability]] = defaultdict(list)
    entity_consumers: dict[str, list[Capability]] = defaultdict(list)
    for cap in capabilities:
        for entity in cap.produces_entities:
            entity_producers[entity].append(cap)
        for entity in cap.consumes_entities:
            entity_consumers[entity].append(cap)

    for entity, consumers in entity_consumers.items():
        for consumer in consumers:
            for producer in entity_producers.get(entity, []):
                if producer.operation_id != consumer.operation_id:
                    merge_edge(
                        edges,
                        DependencyEdge(
                            source=producer.operation_id,
                            target=consumer.operation_id,
                            confidence=0.65,
                            methods=["entity_flow"],
                            reasons=[f"{producer.operation_id} produces entity {entity} consumed by {consumer.operation_id}"],
                        ),
                    )

    return sorted(edges.values(), key=lambda e: e.confidence, reverse=True)


def merge_edge(edges: dict[tuple[str, str], DependencyEdge], edge: DependencyEdge) -> None:
    key = (edge.source, edge.target)
    existing = edges.get(key)
    if existing is None:
        edges[key] = edge
        return
    existing.confidence = min(1.0, max(existing.confidence, edge.confidence) + 0.1)
    existing.methods = sorted(set(existing.methods + edge.methods))
    existing.reasons = sorted(set(existing.reasons + edge.reasons))
    existing.is_explicit = existing.is_explicit or edge.is_explicit

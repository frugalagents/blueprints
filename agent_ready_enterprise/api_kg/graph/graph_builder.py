from __future__ import annotations

from typing import Any

import networkx as nx

from api_kg.graph.dependency_inference import infer_dependencies, merge_edge
from api_kg.models import Capability, DependencyEdge, Entity


DOMAIN_COLORS = [
    "#4A90D9", "#50C878", "#F5A623", "#E74C3C", "#9B59B6", "#1ABC9C",
    "#F39C12", "#3498DB", "#E67E22", "#2ECC71", "#E91E63", "#00BCD4",
]


def build_graph(capabilities: list[Capability], entities: list[Entity], config: dict[str, Any] | None = None, skip_llm: bool = False) -> nx.DiGraph:
    graph = nx.DiGraph()
    domains = sorted({c.domain for c in capabilities} | {e.domain for e in entities})
    colors = {domain: DOMAIN_COLORS[i % len(DOMAIN_COLORS)] for i, domain in enumerate(domains)}

    for domain in domains:
        graph.add_node(
            f"domain:{domain}",
            node_type="domain",
            label=domain,
            domain=domain,
            color=colors[domain],
        )

    for entity in entities:
        graph.add_node(
            f"entity:{entity.name}",
            node_type="entity",
            label=entity.name,
            domain=entity.domain,
            fields=entity.fields,
            key_field=entity.key_field,
            aliases=entity.aliases,
            color=colors.get(entity.domain, "#888"),
        )
        graph.add_edge(f"domain:{entity.domain}", f"entity:{entity.name}", edge_type="domain_has_entity")
        for field in entity.fields:
            field_node = f"field:{entity.name}.{field}"
            graph.add_node(field_node, node_type="field", label=field, entity=entity.name, domain=entity.domain)
            graph.add_edge(f"entity:{entity.name}", field_node, edge_type="entity_has_field")

    for cap in capabilities:
        graph.add_node(
            cap.operation_id,
            node_type="capability",
            **cap.to_dict(),
            color=colors.get(cap.domain, "#888"),
        )
        graph.add_edge(f"domain:{cap.domain}", cap.operation_id, edge_type="domain_has_capability")
        for entity in cap.produces_entities:
            if graph.has_node(f"entity:{entity}"):
                graph.add_edge(cap.operation_id, f"entity:{entity}", edge_type="capability_produces_entity")
        for entity in cap.consumes_entities:
            if graph.has_node(f"entity:{entity}"):
                graph.add_edge(f"entity:{entity}", cap.operation_id, edge_type="capability_consumes_entity")
        for field in cap.input_fields:
            graph.add_node(f"param:{field.name}", node_type="parameter", label=field.name, is_identifier=field.is_identifier)
            graph.add_edge(f"param:{field.name}", cap.operation_id, edge_type="capability_requires_field", location=field.location)
        for field in cap.output_fields:
            graph.add_node(f"param:{field.name}", node_type="parameter", label=field.name, is_identifier=field.is_identifier)
            graph.add_edge(cap.operation_id, f"param:{field.name}", edge_type="capability_returns_field", location="response")

    rule_edges = infer_dependencies(capabilities)

    # Merge LLM-inferred edges if enabled
    all_edges: dict[tuple[str, str], DependencyEdge] = {}
    for edge in rule_edges:
        merge_edge(all_edges, edge)

    if not skip_llm and config:
        from api_kg.graph.llm_inference import infer_dependencies_llm

        llm_edges = infer_dependencies_llm(capabilities, config)
        for edge in llm_edges:
            merge_edge(all_edges, edge)

    for edge in all_edges.values():
        if graph.has_node(edge.source) and graph.has_node(edge.target):
            source_domain = graph.nodes[edge.source].get("domain")
            target_domain = graph.nodes[edge.target].get("domain")
            graph.add_edge(
                edge.source,
                edge.target,
                edge_type="capability_depends_on",
                relation="depends_on",
                confidence=edge.confidence,
                dependency_type=edge.dependency_type,
                methods=edge.methods,
                reasons=edge.reasons,
                is_explicit=edge.is_explicit,
                is_cross_domain=source_domain != target_domain,
            )

    return graph

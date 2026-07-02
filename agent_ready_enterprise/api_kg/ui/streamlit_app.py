from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st
from pyvis.network import Network

from api_kg.config import load_config
from api_kg.cli import run_ask
from api_kg.graph.graph_store import graph_stats, load_graph


def main():
    st.set_page_config(page_title="Enterprise API Knowledge Graph", layout="wide")
    st.title("Enterprise API Knowledge Graph")

    config_path = st.sidebar.text_input("Config", "config.yaml")
    config = load_config(config_path)
    graph_file = config.get("graph_file", "out/api_graph.json")

    if not Path(graph_file).exists():
        st.error(f"Graph not found at `{graph_file}`. Run: `python -m api_kg.cli build --specs-dir ./specs/sample_hcm`")
        return

    graph = load_graph(graph_file)
    stats = graph_stats(graph)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Graph Stats")
    st.sidebar.metric("APIs", stats["nodes_by_type"].get("capability", 0))
    st.sidebar.metric("Domains", stats["nodes_by_type"].get("domain", 0))
    st.sidebar.metric("Dependencies", stats["dependency_edges"])
    st.sidebar.metric("Cross-Domain", stats["cross_domain_dependencies"])
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Domains")
    for domain, count in sorted(stats.get("capabilities_per_domain", {}).items()):
        st.sidebar.markdown(f"- **{domain}**: {count} APIs")

    view = st.radio("", ["Knowledge Graph", "Ask a Question"], horizontal=True, label_visibility="collapsed")

    if view == "Knowledge Graph":
        _render_graph_view(graph, stats)
    else:
        _render_chat(graph, config)


def _render_graph_view(graph, stats):
    st.markdown("### API Landscape")
    st.markdown(
        f"**{stats['nodes_by_type'].get('capability', 0)} APIs** across "
        f"**{stats['nodes_by_type'].get('domain', 0)} domains** with "
        f"**{stats['cross_domain_dependencies']} cross-domain connections**"
    )

    # Domain filter
    all_domains = sorted(stats.get("capabilities_per_domain", {}).keys())
    col1, col2 = st.columns([3, 1])
    with col1:
        selected_domains = st.multiselect(
            "Filter domains",
            all_domains,
            default=all_domains,
            help="Select which domains to display"
        )
    with col2:
        show_mode = st.selectbox("Show", ["Cross-domain only", "All dependencies", "Domain clusters"])

    if not selected_domains:
        st.info("Select at least one domain.")
        return

    net = _build_graph_viz(graph, selected_domains, show_mode)
    _display_pyvis(net, height=650)

    # Stats table below the graph
    with st.expander("Dependency details"):
        deps = []
        for u, v, data in graph.edges(data=True):
            if data.get("edge_type") != "capability_depends_on":
                continue
            u_domain = graph.nodes[u].get("domain", "")
            v_domain = graph.nodes[v].get("domain", "")
            if u_domain not in selected_domains or v_domain not in selected_domains:
                continue
            if show_mode == "Cross-domain only" and u_domain == v_domain:
                continue
            deps.append({
                "From": u,
                "From Domain": u_domain,
                "To": v,
                "To Domain": v_domain,
                "Confidence": f"{data.get('confidence', 0):.2f}",
                "Methods": ", ".join(data.get("methods", [])),
            })
        if deps:
            import pandas as pd
            st.dataframe(pd.DataFrame(deps), use_container_width=True, hide_index=True)
        else:
            st.info("No dependencies match the current filter.")


def _build_graph_viz(graph, selected_domains, show_mode):
    net = Network(height="650px", width="100%", directed=True, bgcolor="#0e1117", font_color="#fafafa")

    # Physics settings optimized for readability at scale
    net.set_options("""{
        "physics": {
            "forceAtlas2Based": {
                "gravitationalConstant": -80,
                "centralGravity": 0.015,
                "springLength": 200,
                "springConstant": 0.02,
                "damping": 0.4,
                "avoidOverlap": 0.8
            },
            "solver": "forceAtlas2Based",
            "stabilization": {"iterations": 150}
        },
        "nodes": {
            "font": {"size": 12, "face": "arial"}
        },
        "edges": {
            "smooth": {"type": "curvedCW", "roundness": 0.2},
            "arrows": {"to": {"scaleFactor": 0.5}}
        },
        "interaction": {
            "hover": true,
            "tooltipDelay": 100,
            "zoomView": true,
            "dragView": true
        }
    }""")

    DOMAIN_COLORS = {
        "payroll": "#4A90D9", "benefits": "#50C878", "tax": "#F5A623",
        "employee": "#E74C3C", "time": "#9B59B6", "compliance": "#1ABC9C",
        "patient": "#FF6B6B", "labs": "#4ECDC4", "pharmacy": "#45B7D1",
        "imaging": "#96CEB4", "billing": "#FFEAA7", "scheduling": "#DDA0DD",
        "accounts": "#FF8C00", "lending": "#7B68EE", "cards": "#FF69B4",
        "compliance_fin": "#20B2AA", "market_data": "#DAA520", "payments": "#87CEEB",
        "recruiting": "#FF7F50", "learning": "#6B8E23", "performance": "#BA55D3",
        "compensation": "#CD853F",
    }

    # Collect nodes to show
    cap_nodes = []
    for node, data in graph.nodes(data=True):
        if data.get("node_type") != "capability":
            continue
        if data.get("domain") not in selected_domains:
            continue
        cap_nodes.append((node, data))

    # Group by domain for cluster layout
    domain_groups = {}
    for node, data in cap_nodes:
        domain = data.get("domain", "unknown")
        domain_groups.setdefault(domain, []).append((node, data))

    # Add domain hub nodes (larger, central to cluster)
    for domain in selected_domains:
        color = DOMAIN_COLORS.get(domain, "#888")
        count = len(domain_groups.get(domain, []))
        net.add_node(
            f"domain:{domain}",
            label=f"{domain.upper()}\n({count})",
            color=color,
            size=40,
            shape="diamond",
            font={"size": 14, "bold": True, "color": "#ffffff"},
            title=f"Domain: {domain}\n{count} APIs",
            mass=3,
        )

    # Add capability nodes
    for node, data in cap_nodes:
        domain = data.get("domain", "unknown")
        color = DOMAIN_COLORS.get(domain, "#888")
        label = node.replace("get_", "").replace("list_", "").replace("check_", "").replace("_", " ").title()
        if len(label) > 20:
            label = label[:18] + "..."
        title = (
            f"{node}\n"
            f"Domain: {domain}\n"
            f"{data.get('method', '')} {data.get('path', '')}\n"
            f"{data.get('summary', '')}"
        )
        net.add_node(
            node,
            label=label,
            color=color,
            size=14,
            shape="dot",
            title=title,
            font={"size": 10},
        )
        # Connect to domain hub
        net.add_edge(f"domain:{domain}", node, color="#333333", width=0.5, dashes=True, physics=True)

    # Add dependency edges
    node_set = {n for n, _ in cap_nodes}
    for u, v, data in graph.edges(data=True):
        if data.get("edge_type") != "capability_depends_on":
            continue
        if u not in node_set or v not in node_set:
            continue
        is_cross = data.get("is_cross_domain", False)

        if show_mode == "Cross-domain only" and not is_cross:
            continue
        if show_mode == "Domain clusters":
            continue

        color = "#ff6b6b" if is_cross else "#4ecdc4"
        width = 2.5 if is_cross else 1.5
        confidence = data.get("confidence", 0)
        reasons = data.get("reasons", [])
        title = f"{'Cross-domain' if is_cross else 'Same-domain'}\nConfidence: {confidence:.2f}\n{chr(10).join(reasons[:3])}"
        net.add_edge(u, v, color=color, width=width, title=title, arrows="to")

    return net


def _render_chat(graph, config):
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                _render_assistant_message(msg, graph)
            else:
                st.markdown(msg["content"])

    st.markdown("**Try:** _Why did Sarah's net pay drop $340 this month?_ | _What is Sarah's total compensation?_ | _What training is Sarah overdue on?_")

    if question := st.chat_input("Ask a question..."):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Semantic search → Graph expansion → LLM planning → Execution..."):
                result = run_ask(question, config, use_bedrock=True)

            msg_data = {"role": "assistant", "content": result.get("answer", ""), "result": result}
            st.session_state.messages.append(msg_data)
            _render_assistant_message(msg_data, graph)


def _render_assistant_message(msg, graph):
    result = msg.get("result")
    if not result:
        st.markdown(msg["content"])
        return

    retrieval = result.get("retrieval", {})
    call_sequence = retrieval.get("call_sequence", [])
    domains = retrieval.get("domains", [])
    plan = result.get("plan", {})
    execution = result.get("execution", {})
    evidence = result.get("evidence", {})
    metadata = retrieval.get("metadata", {})

    # === ANSWER FIRST ===
    st.markdown(result.get("answer", "No answer generated."))

    st.markdown("---")
    st.markdown("##### How I got this answer")

    # Step 1: Semantic Search + Graph Expansion
    with st.expander(f"**1. Retrieval** — {len(call_sequence)} APIs from {len(domains)} domains", expanded=False):
        st.caption(
            "Searched semantic descriptions (vector similarity) for capabilities matching your question, "
            "then expanded along dependency edges in the structural graph."
        )
        col1, col2, col3 = st.columns(3)
        col1.metric("Vector hits", metadata.get("vector_hits", "?"))
        col2.metric("Graph expanded", metadata.get("expanded_from_graph", "?"))
        col3.metric("Domains", len(domains))

        st.markdown(f"**Domains:** {', '.join(domains)}")
        st.markdown("**Candidate APIs** (ranked by semantic similarity + graph proximity):")
        for i, cap_id in enumerate(call_sequence[:10], 1):
            if graph.has_node(cap_id):
                domain = graph.nodes[cap_id].get("domain", "")
                summary = graph.nodes[cap_id].get("summary", "")
                st.markdown(f"{i}. `{domain}` / **{cap_id}** — _{summary}_")

        if call_sequence:
            net = _build_traversal_viz(graph, call_sequence, retrieval.get("edges", []))
            _display_pyvis(net, height=300)

    # Step 2: LLM Plan Generation
    with st.expander(f"**2. Planning** — {len(plan.get('steps', []))} steps", expanded=False):
        st.caption(
            "LLM generated a constrained execution plan from the retrieved capability descriptions. "
            "No templates — the model reasons about which APIs to call and in what order."
        )
        if plan.get("goal"):
            st.markdown(f"**Goal:** _{plan['goal']}_")
        if plan.get("steps"):
            for i, step in enumerate(plan["steps"], 1):
                if step.get("type") == "api_call":
                    cap = step.get("capability", "")
                    args = step.get("args", {})
                    args_display = ", ".join(f"{k}={v}" for k, v in args.items())
                    st.markdown(f"{i}. **CALL** `{cap}`({args_display})")
                elif step.get("type") == "operator":
                    op = step.get("operator", "")
                    st.markdown(f"{i}. **{op.upper()}** on previous results")

    # Step 3: Deterministic Execution
    with st.expander(f"**3. Execution** — {'Success' if execution and execution.get('success') else 'Failed'}", expanded=False):
        st.caption("Each plan step executed deterministically against the API layer. No LLM in the loop.")
        if execution:
            trace = execution.get("trace", {})
            for step_id, item in trace.items():
                status = item.get("status", "")
                icon = "✅" if status == "success" else "❌"
                step = item.get("step", {})
                cap = step.get("capability", step.get("operator", ""))
                st.markdown(f"{icon} **{step_id}** → `{cap}`")
                output = item.get("output", {})
                data = output.get("data", output)
                st.json(data)

    # Step 4: Evidence
    claims = evidence.get("claims", []) if evidence else []
    with st.expander(f"**4. Evidence** — {len(claims)} claims", expanded=False):
        st.caption("Structured claims extracted from execution results. The LLM synthesizes its answer only from these.")
        if claims:
            import pandas as pd
            rows = []
            for claim in claims[:15]:
                row = {"Claim": claim.get("claim", "")}
                if "delta" in claim and claim["delta"] is not None:
                    row["Previous"] = claim.get("previous", "")
                    row["Current"] = claim.get("current", "")
                    row["Delta"] = claim.get("delta", "")
                elif "value" in claim:
                    row["Value"] = claim.get("value", "")
                rows.append(row)
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No structured claims — answer synthesized directly from API responses.")


def _build_traversal_viz(graph, call_sequence, edges):
    net = Network(height="350px", width="100%", directed=True, bgcolor="#0e1117", font_color="#fafafa")
    net.set_options("""{
        "physics": {
            "forceAtlas2Based": {
                "gravitationalConstant": -50,
                "centralGravity": 0.02,
                "springLength": 120,
                "springConstant": 0.04
            },
            "solver": "forceAtlas2Based",
            "stabilization": {"iterations": 100}
        },
        "edges": {
            "smooth": {"type": "curvedCW", "roundness": 0.15},
            "arrows": {"to": {"scaleFactor": 0.6}}
        }
    }""")

    DOMAIN_COLORS = {
        "payroll": "#4A90D9", "benefits": "#50C878", "tax": "#F5A623",
        "employee": "#E74C3C", "time": "#9B59B6", "compliance": "#1ABC9C",
        "patient": "#FF6B6B", "labs": "#4ECDC4", "pharmacy": "#45B7D1",
        "imaging": "#96CEB4", "billing": "#FFEAA7", "scheduling": "#DDA0DD",
        "accounts": "#FF8C00", "lending": "#7B68EE", "cards": "#FF69B4",
        "compliance_fin": "#20B2AA", "market_data": "#DAA520", "payments": "#87CEEB",
        "recruiting": "#FF7F50", "learning": "#6B8E23", "performance": "#BA55D3",
        "compensation": "#CD853F",
    }

    node_set = set(call_sequence)
    for node in call_sequence:
        if not graph.has_node(node):
            continue
        data = graph.nodes[node]
        domain = data.get("domain", "")
        color = DOMAIN_COLORS.get(domain, "#888")
        label = node.replace("get_", "").replace("_", " ").title()
        title = f"{domain} / {node}\n{data.get('summary', '')}"
        net.add_node(node, label=label, color=color, size=20, title=title,
                    font={"size": 11, "color": "#ffffff"}, borderWidth=2)

    for edge in edges:
        src = edge.get("from", "")
        dst = edge.get("to", "")
        if src in node_set and dst in node_set:
            is_cross = edge.get("is_cross_domain", False)
            color = "#ff6b6b" if is_cross else "#4ecdc4"
            net.add_edge(src, dst, color=color, width=2.5, arrows="to")

    return net


def _display_pyvis(net, height=600):
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        net.save_graph(f.name)
        html_path = f.name
    with open(html_path) as f:
        html = f.read()
    st.components.v1.html(html, height=height + 20, scrolling=False)


if __name__ == "__main__":
    main()

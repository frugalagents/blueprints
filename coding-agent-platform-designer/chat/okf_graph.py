"""OKF graph walker — loads knowledge/ into memory and gives 1-hop retrieval.

Per ARCHITECTURE.md §2: relationships between components are plain markdown
links in the '## Connects to' section. There is no separate edges file — the
directory of links *is* the graph. This module parses that structure once and
exposes lookup-by-id plus 1-hop neighbor traversal, matching the "RAG + 1-hop
graph walk" retrieval shape described in ARCHITECTURE.md §4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
FIELD_RE = re.compile(r"^([a-zA-Z]+):\s*(.*)$")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
CONNECTS_SECTION_RE = re.compile(r"^## Connects to\n(.*?)(?=^## |\Z)", re.DOTALL | re.MULTILINE)


@dataclass
class Component:
    id: str
    title: str
    description: str
    group: str
    status: str
    path: Path
    body: str
    neighbor_ids: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return f"# {self.title}\n\n{self.body}"


class OKFGraph:
    """In-memory index over knowledge/. Rebuilds are cheap (small corpus)."""

    def __init__(self, knowledge_root: Path):
        self.root = knowledge_root
        self.components: dict[str, Component] = {}
        self._load()

    def _load(self) -> None:
        for md_path in sorted(self.root.rglob("*.md")):
            if md_path.name == "index.md":
                continue
            text = md_path.read_text(encoding="utf-8")
            match = FRONTMATTER_RE.match(text)
            if not match:
                continue
            yaml_block, body = match.groups()
            fields: dict[str, str] = {}
            for line in yaml_block.splitlines():
                fm = FIELD_RE.match(line)
                if fm:
                    fields[fm.group(1)] = fm.group(2).strip()
            if fields.get("type") != "platform-component":
                continue

            comp_id = md_path.stem
            neighbor_ids = self._extract_neighbor_ids(body, md_path)

            self.components[comp_id] = Component(
                id=comp_id,
                title=fields.get("title", comp_id),
                description=fields.get("description", ""),
                group=fields.get("group", md_path.parent.name),
                status=fields.get("status", "unknown"),
                path=md_path,
                body=body.strip(),
                neighbor_ids=neighbor_ids,
            )

    def _extract_neighbor_ids(self, body: str, md_path: Path) -> list[str]:
        section_match = CONNECTS_SECTION_RE.search(body)
        if not section_match:
            return []
        section = section_match.group(1)
        neighbor_ids = []
        for _label, target in LINK_RE.findall(section):
            if target.startswith(("http://", "https://")):
                continue
            resolved = (md_path.parent / target).resolve()
            neighbor_id = resolved.stem
            if neighbor_id not in neighbor_ids:
                neighbor_ids.append(neighbor_id)
        return neighbor_ids

    def get(self, component_id: str) -> Component | None:
        return self.components.get(component_id)

    def neighbors(self, component_id: str) -> list[Component]:
        comp = self.get(component_id)
        if not comp:
            return []
        return [self.components[n] for n in comp.neighbor_ids if n in self.components]

    def retrieve(self, component_id: str) -> dict:
        """1-hop retrieval: the component itself + its resolved neighbors.

        Returns None-safe dict; caller checks 'found'. This is the exact
        mechanic manually simulated in the by-hand probes before Phase 3 —
        this function is what replaces the human doing the graph walk.
        """
        comp = self.get(component_id)
        if not comp:
            return {"found": False, "component_id": component_id}
        return {
            "found": True,
            "component": comp,
            "neighbors": self.neighbors(component_id),
        }

    def all_ids(self) -> list[str]:
        return list(self.components.keys())

"""Layer 2 evaluator — resolves org-profile answers into an ordered, filtered
component list (ARCHITECTURE.md §3), and checks the running decisions log
against data/decision-matrix.yaml's conflicts: rules (§3.4).

Probe 3 (logged in IMPLEMENTATION_PLAN.md after Phase 1) found a real
conversation can produce contradictory advice across two components with
nothing to catch it. check_conflicts() is what closes that gap — it must be
called after every decision is recorded, not just at the end, per §3.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ComponentRelevance:
    component_id: str
    relevance: str  # "required" | "optional"
    priority: int


@dataclass
class Decision:
    component_id: str
    option: str


@dataclass
class ConflictHit:
    id: str
    warning: str
    decisions_involved: list[Decision] = field(default_factory=list)


class DecisionMatrix:
    def __init__(self, matrix_path: Path):
        self._data = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
        self.axes: dict[str, list[str]] = self._data.get("axes", {})
        self.components: dict[str, dict] = self._data.get("components", {})
        self.conflict_rules: list[dict] = self._data.get("conflicts", [])

    def resolve(self, profile: dict[str, str]) -> list[ComponentRelevance]:
        """profile: {axis_name: axis_value}. Returns components sorted
        required-first, then by ascending priority (lower = more urgent),
        per the resolution rule documented in decision-matrix.yaml.
        """
        results = []
        for comp_id, axes_data in self.components.items():
            matched = []
            for axis, value in profile.items():
                entry = axes_data.get(axis, {}).get(value)
                if entry:
                    matched.append(entry)
            if not matched:
                relevance, priority = "optional", 99
            else:
                relevance = "required" if any(m["relevance"] == "required" for m in matched) else "optional"
                priority = min(m["priority"] for m in matched)
            results.append(ComponentRelevance(comp_id, relevance, priority))

        results.sort(key=lambda r: (0 if r.relevance == "required" else 1, r.priority))
        return results

    def check_conflicts(self, decisions_log: list[Decision]) -> list[ConflictHit]:
        """Evaluate every conflicts: rule against the full decisions log so
        far. Called after each new decision is appended (§3.4) — cheap at
        this scale (a handful of rules, a handful of decisions), so no need
        to optimize for incremental-only checking.
        """
        made = {(d.component_id, d.option) for d in decisions_log}
        hits = []
        for rule in self.conflict_rules:
            if_clause = rule["if"]
            and_clause = rule["and"]
            if_match = (if_clause["component"], if_clause["option"]) in made
            and_match = (and_clause["component"], and_clause["option"]) in made
            if if_match and and_match:
                involved = [
                    d for d in decisions_log
                    if (d.component_id, d.option) in {
                        (if_clause["component"], if_clause["option"]),
                        (and_clause["component"], and_clause["option"]),
                    }
                ]
                hits.append(ConflictHit(id=rule["id"], warning=rule["warn"].strip(), decisions_involved=involved))
        return hits

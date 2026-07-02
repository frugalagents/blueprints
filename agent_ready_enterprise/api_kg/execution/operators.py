from __future__ import annotations

from typing import Any


def run_operator(operator: str, args: dict[str, Any]) -> Any:
    if operator == "diff":
        return diff(args.get("left"), args.get("right"))
    if operator == "attribute_delta":
        return attribute_delta(args.get("delta"))
    if operator == "reconcile_total":
        return reconcile_total(args)
    if operator == "extract_field":
        return extract_field(args.get("source"), args.get("field"))
    if operator == "sum":
        values = args.get("values") or []
        return sum(v for v in values if isinstance(v, (int, float)))
    raise ValueError(f"Unsupported operator: {operator}")


def diff(left: Any, right: Any) -> Any:
    left_data = _unwrap(left)
    right_data = _unwrap(right)
    if isinstance(left_data, dict) and isinstance(right_data, dict):
        return _diff_dict(left_data, right_data)
    if isinstance(left_data, (int, float)) and isinstance(right_data, (int, float)):
        return right_data - left_data
    return {"left": left_data, "right": right_data, "changed": left_data != right_data}


def _diff_dict(left: dict, right: dict) -> dict:
    out = {"fields": {}, "left": left, "right": right}
    for key in sorted(set(left) | set(right)):
        lval = left.get(key)
        rval = right.get(key)
        if isinstance(lval, (int, float)) and isinstance(rval, (int, float)):
            delta = rval - lval
            if delta != 0:
                out["fields"][key] = {"previous": lval, "current": rval, "delta": delta}
        elif isinstance(lval, list) and isinstance(rval, list):
            nested = _diff_lists(lval, rval)
            if nested:
                out["fields"][key] = nested
        elif lval != rval:
            out["fields"][key] = {"previous": lval, "current": rval, "changed": True}
    return out


def _diff_lists(left: list, right: list) -> dict:
    if all(isinstance(x, dict) and "type" in x and "amount" in x for x in left + right):
        left_map = {x["type"]: x.get("amount", 0) for x in left}
        right_map = {x["type"]: x.get("amount", 0) for x in right}
        changes = {}
        for key in sorted(set(left_map) | set(right_map)):
            delta = right_map.get(key, 0) - left_map.get(key, 0)
            if delta:
                changes[key] = {"previous": left_map.get(key, 0), "current": right_map.get(key, 0), "delta": delta}
        return changes
    return {"previous_count": len(left), "current_count": len(right)} if left != right else {}


def attribute_delta(delta: Any) -> dict:
    delta_data = _unwrap(delta)
    fields = delta_data.get("fields", {}) if isinstance(delta_data, dict) else {}
    contributors = []
    for field, change in fields.items():
        if isinstance(change, dict) and "delta" in change:
            contributors.append({"field": field, **change})
        elif isinstance(change, dict):
            for item, item_change in change.items():
                if isinstance(item_change, dict) and "delta" in item_change:
                    contributors.append({"field": f"{field}.{item}", **item_change})
    contributors.sort(key=lambda x: abs(x.get("delta", 0)), reverse=True)
    return {"contributors": contributors, "delta": delta_data}


def reconcile_total(args: dict[str, Any]) -> dict:
    total = args.get("total", 0)
    parts = args.get("parts", [])
    explained = sum(p for p in parts if isinstance(p, (int, float)))
    return {"total": total, "explained": explained, "unexplained": total - explained}


def extract_field(source: Any, field: str | None) -> Any:
    value = _unwrap(source)
    if not field:
        return value
    for part in field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value and "request" in value:
        return _unwrap(value["data"])
    if isinstance(value, dict) and set(value.keys()) == {"data"}:
        return _unwrap(value["data"])
    return value

from __future__ import annotations

from pathlib import Path
from typing import Any

from api_kg.ingestion.openapi_loader import iter_openapi_specs
from api_kg.models import Capability, Entity, FieldRef, is_identifier


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def normalize_specs(specs_dir: str | Path) -> tuple[list[Capability], list[Entity]]:
    capabilities: list[Capability] = []
    entities: dict[str, Entity] = {}

    for spec_path, spec in iter_openapi_specs(specs_dir):
        domain = (
            spec.get("info", {}).get("x-domain")
            or spec.get("info", {}).get("x_domain")
            or spec_path.parent.name
            or spec_path.stem.replace("_api", "")
        )
        components = spec.get("components", {})

        for schema_name, schema in components.get("schemas", {}).items():
            resolved = _resolve_schema(schema, components)
            fields = list(resolved.get("properties", {}).keys())
            key_field = next((f for f in fields if is_identifier(f)), "")
            entities.setdefault(
                schema_name,
                Entity(name=schema_name, domain=domain, fields=fields, key_field=key_field),
            )

        for path, path_item in spec.get("paths", {}).items():
            for method, op in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                cap = _normalize_operation(domain, path, method.upper(), op, components)
                capabilities.append(cap)

    return capabilities, list(entities.values())


def _normalize_operation(domain: str, path: str, method: str, op: dict[str, Any], components: dict[str, Any]) -> Capability:
    operation_id = op.get("operationId") or f"{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
    inputs: list[FieldRef] = []
    outputs: list[FieldRef] = []
    consumes: list[str] = []
    produces: list[str] = []

    for param in op.get("parameters", []):
        name = param.get("name", "")
        inputs.append(
            FieldRef(
                name=name,
                field_type=param.get("schema", {}).get("type", "string"),
                location=param.get("in", "query"),
                required=param.get("required", False),
                is_identifier=is_identifier(name),
            )
        )

    request_body = op.get("requestBody", {})
    if request_body:
        schema, ref_name = _content_schema(request_body, components)
        if ref_name:
            consumes.append(ref_name)
        inputs.extend(_fields_from_schema(schema, "body"))

    response_schema, response_ref = _success_response_schema(op, components)
    if response_ref:
        produces.append(response_ref)
    outputs.extend(_fields_from_schema(response_schema, "response"))

    side_effect = "read" if method == "GET" else "write"
    if method == "POST" and any(word in operation_id.lower() for word in ["calculate", "check", "search", "validate", "estimate", "explain"]):
        side_effect = "compute"

    return Capability(
        operation_id=operation_id,
        domain=domain,
        method=method,
        path=path,
        summary=op.get("summary", ""),
        description=op.get("description", ""),
        tags=op.get("tags", []),
        input_fields=_dedupe_fields(inputs),
        output_fields=_dedupe_fields(outputs),
        consumes_entities=consumes,
        produces_entities=produces,
        explicit_depends_on=op.get("x-depends-on", []),
        side_effect=side_effect,
        pii_level=op.get("x-pii-level", "unknown"),
    )


def _content_schema(container: dict[str, Any], components: dict[str, Any]) -> tuple[dict[str, Any], str]:
    content = container.get("content", {})
    app_json = content.get("application/json", {})
    return _resolve_schema_with_name(app_json.get("schema", {}), components)


def _success_response_schema(op: dict[str, Any], components: dict[str, Any]) -> tuple[dict[str, Any], str]:
    responses = op.get("responses", {})
    response = responses.get("200") or responses.get("201") or responses.get("default") or {}
    schema, ref_name = _content_schema(response, components)
    if schema.get("type") == "array":
        item_schema, item_ref = _resolve_schema_with_name(schema.get("items", {}), components)
        return item_schema, item_ref or ref_name
    return schema, ref_name


def _resolve_schema_with_name(schema: dict[str, Any], components: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not schema:
        return {}, ""
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        return _resolve_schema(schema, components), ref_name
    return schema, ""


def _resolve_schema(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    if "$ref" not in schema:
        return schema
    current: Any = {"components": components}
    for part in schema["$ref"].lstrip("#/").split("/"):
        current = current.get(part, {})
    return current if isinstance(current, dict) else {}


def _fields_from_schema(schema: dict[str, Any], location: str) -> list[FieldRef]:
    if not schema:
        return []
    if schema.get("type") == "array":
        schema = schema.get("items", {})
    required = set(schema.get("required", []))
    fields: list[FieldRef] = []
    for name, field_schema in schema.get("properties", {}).items():
        field_type = field_schema.get("type", "object" if "properties" in field_schema else "string")
        fields.append(
            FieldRef(
                name=name,
                field_type=field_type,
                location=location,
                required=name in required,
                is_identifier=is_identifier(name),
            )
        )
        if field_type == "object":
            fields.extend(_fields_from_schema(field_schema, location))
        if field_type == "array" and isinstance(field_schema.get("items"), dict):
            fields.extend(_fields_from_schema(field_schema["items"], location))
    return fields


def _dedupe_fields(fields: list[FieldRef]) -> list[FieldRef]:
    seen: set[tuple[str, str]] = set()
    out: list[FieldRef] = []
    for field in fields:
        key = (field.name, field.location)
        if key not in seen and field.name:
            out.append(field)
            seen.add(key)
    return out

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api_kg.ingestion.spec_normalizer import normalize_specs


def create_mock_app(specs_dir: str | Path, fixtures_dir: str | Path | None = None) -> FastAPI:
    capabilities, _ = normalize_specs(specs_dir)
    fixtures = Path(fixtures_dir) if fixtures_dir else None
    app = FastAPI(title="Enterprise API KG Mock Server")

    for cap in sorted(capabilities, key=lambda c: c.path.count("{")):
        _register_route(app, cap.to_dict(), fixtures)

    @app.get("/health")
    def health():
        return {"status": "healthy", "capabilities": len(capabilities)}

    return app


def _register_route(app: FastAPI, cap: dict[str, Any], fixtures: Path | None) -> None:
    async def handler(request: Request):
        fixture = _load_fixture(fixtures, cap["operation_id"], request)
        if fixture is not None:
            return JSONResponse(content=fixture)
        return JSONResponse(content=_schema_mock(cap, request))

    handler.__name__ = cap["operation_id"]
    method = cap["method"].lower()
    getattr(app, method)(cap["path"], name=cap["operation_id"])(handler)


def _load_fixture(fixtures: Path | None, operation_id: str, request: Request) -> Any:
    if not fixtures:
        return None
    path = fixtures / f"{operation_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "by_query" in data:
        query = dict(request.query_params)
        for item in data["by_query"]:
            if all(str(query.get(k)) == str(v) for k, v in item.get("match", {}).items()):
                return item.get("response")
        return data.get("default")
    return data


def _schema_mock(cap: dict[str, Any], request: Request) -> dict[str, Any]:
    random.seed(cap["operation_id"])
    out = {"operation_id": cap["operation_id"]}
    for field in cap.get("output_fields", []):
        name = field.get("name")
        if not name:
            continue
        if name in request.path_params:
            out[name] = request.path_params[name]
        elif field.get("field_type") in {"integer"}:
            out[name] = random.randint(1, 100)
        elif field.get("field_type") in {"number"}:
            out[name] = round(random.uniform(10, 1000), 2)
        elif field.get("field_type") == "boolean":
            out[name] = random.choice([True, False])
        elif field.get("field_type") == "array":
            out[name] = []
        else:
            out[name] = f"sample_{name}"
    return out

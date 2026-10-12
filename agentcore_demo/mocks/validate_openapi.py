#!/usr/bin/env python3
"""Validate the three OpenAPI 3.1 schemas for the mock data APIs.

If openapi_spec_validator is importable it is used for full spec validation.
Otherwise (the default on this machine) the script falls back to parsing each
file with yaml.safe_load and asserting the structural requirements this POC
cares about:
  * openapi version starts with "3.1"
  * info present
  * paths non-empty
  * every operation has a non-empty description
  * operationIds are unique across all three files

No pip installs are required for the fallback path. Exits 0 on success, nonzero
with a clear message on failure.
"""
import os
import sys

import yaml

OPENAPI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openapi")
FILES = ["portfolio.yaml", "market.yaml", "research.yaml"]
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}


def _try_import_validator():
    try:
        from openapi_spec_validator import validate as _validate  # type: ignore
        return _validate
    except Exception:
        try:
            from openapi_spec_validator import validate_spec as _validate  # type: ignore
            return _validate
        except Exception:
            return None


def _fallback_checks(name, spec, seen_operation_ids):
    errors = []

    version = spec.get("openapi")
    if not (isinstance(version, str) and version.startswith("3.1")):
        errors.append(f"{name}: openapi version must start with '3.1' (got {version!r})")

    if not spec.get("info"):
        errors.append(f"{name}: 'info' is missing")

    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        errors.append(f"{name}: 'paths' must be a non-empty object")
        return errors

    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        ops = {m: op for m, op in item.items() if m.lower() in HTTP_METHODS}
        if not ops:
            errors.append(f"{name}: path {path} has no operations")
        for method, op in ops.items():
            where = f"{name} {method.upper()} {path}"
            desc = op.get("description")
            if not (isinstance(desc, str) and desc.strip()):
                errors.append(f"{where}: operation needs a non-empty description")
            op_id = op.get("operationId")
            if not (isinstance(op_id, str) and op_id.strip()):
                errors.append(f"{where}: operation needs an operationId")
            elif op_id in seen_operation_ids:
                errors.append(
                    f"{where}: duplicate operationId '{op_id}' "
                    f"(already defined in {seen_operation_ids[op_id]})"
                )
            else:
                seen_operation_ids[op_id] = name
    return errors


def main():
    validator = _try_import_validator()
    mode = "openapi_spec_validator" if validator else "YAML parse + required-key fallback"
    print(f"OpenAPI validation mode: {mode}")

    seen_operation_ids = {}
    all_errors = []

    for name in FILES:
        path = os.path.join(OPENAPI_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                spec = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as exc:
            all_errors.append(f"{name}: could not parse YAML: {exc}")
            continue

        if validator is not None:
            try:
                validator(spec)
            except Exception as exc:  # validation error type varies by version
                all_errors.append(f"{name}: openapi_spec_validator failed: {exc}")
        # Always run the structural checks so operationId uniqueness is enforced
        # across all three files regardless of which validator path ran.
        all_errors.extend(_fallback_checks(name, spec, seen_operation_ids))

    if all_errors:
        print("OPENAPI VALIDATION FAILED:", file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(
        f"PASS: {len(FILES)} schemas valid (OpenAPI 3.1); "
        f"{len(seen_operation_ids)} unique operationIds "
        f"({', '.join(sorted(seen_operation_ids))}); "
        f"every operation has a non-empty description."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

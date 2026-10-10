"""Generate standalone JSON Schema files from contracts/openapi.yaml.

The OpenAPI document is the single source of truth for contract v1.0. This script
derives self-contained JSON Schema (draft 2020-12) files under contracts/schemas/ so
that Het and Neil can validate payloads with any standard validator, without an
OpenAPI toolchain and without a second hand-maintained copy of the contract.

Run after any contract change:

    python contracts/build_schemas.py

Then commit both openapi.yaml and the regenerated schemas in the same commit.
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
OPENAPI = ROOT / "openapi.yaml"
OUT_DIR = ROOT / "schemas"

# Root schema name -> output filename.
ROOTS = {
    "HealthResponse": "health_response.schema.json",
    "ParticipantsResponse": "participants_response.schema.json",
    "HistoryResponse": "history_response.schema.json",
    "PredictRequest": "predict_request.schema.json",
    "ForecastResponse": "predict_response.schema.json",
    "CompareRequest": "compare_request.schema.json",
    "CompareResponse": "compare_response.schema.json",
    "MetricsResponse": "metrics_response.schema.json",
    "ModelInfoResponse": "model_info_response.schema.json",
    "ErrorResponse": "error_response.schema.json",
}

# OpenAPI 3.0 keywords with no JSON Schema draft 2020-12 meaning.
_DROP = {"example", "examples", "discriminator", "xml", "externalDocs", "deprecated"}


def convert(node: object) -> object:
    """Translate an OpenAPI 3.0 Schema Object into JSON Schema draft 2020-12."""
    if isinstance(node, list):
        return [convert(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, object] = {}
    nullable = bool(node.get("nullable", False))

    for key, value in node.items():
        if key in _DROP or key == "nullable":
            continue
        if key == "$ref":
            # "#/components/schemas/Foo" -> "#/$defs/Foo"
            out["$ref"] = value.replace("#/components/schemas/", "#/$defs/")
            continue
        out[key] = convert(value)

    if nullable:
        if "$ref" in out:
            # A nullable $ref cannot carry sibling type keywords; wrap it.
            ref = out.pop("$ref")
            rest = dict(out)
            out = {"anyOf": [{"$ref": ref}, {"type": "null"}]}
            out.update(rest)
        elif "type" in out:
            declared = out["type"]
            types = declared if isinstance(declared, list) else [declared]
            if "null" not in types:
                out["type"] = [*types, "null"]
        else:
            out = {"anyOf": [out, {"type": "null"}]} if out else {}

    return out


def collect_refs(node: object, found: set[str]) -> None:
    """Record every #/$defs/<Name> reachable from node."""
    if isinstance(node, list):
        for item in node:
            collect_refs(item, found)
        return
    if not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        found.add(ref.removeprefix("#/$defs/"))
    for value in node.values():
        collect_refs(value, found)


def closure(root: str, converted: dict[str, object]) -> dict[str, object]:
    """Transitively gather the $defs a root schema needs (including itself, for self-refs)."""
    needed: set[str] = set()
    collect_refs(converted[root], needed)
    frontier = set(needed)
    while frontier:
        nxt: set[str] = set()
        for name in frontier:
            discovered: set[str] = set()
            collect_refs(converted[name], discovered)
            nxt |= discovered - needed
        needed |= nxt
        frontier = nxt
    return {name: converted[name] for name in sorted(needed)}


def main() -> int:
    spec = yaml.safe_load(OPENAPI.read_text())
    version = spec["info"]["version"]
    components = spec["components"]["schemas"]
    converted = {name: convert(schema) for name, schema in components.items()}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for root, filename in ROOTS.items():
        if root not in converted:
            print(f"ERROR: {root} missing from openapi.yaml components", file=sys.stderr)
            return 1
        body = dict(converted[root])
        document = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://glucotwin.local/contracts/v1/{filename}",
            "title": root,
            "description": (
                f"GlucoTwin contract v{version} — generated from contracts/openapi.yaml "
                "by contracts/build_schemas.py. Do not edit by hand."
            ),
            **body,
        }
        defs = closure(root, converted)
        if defs:
            document["$defs"] = defs
        path = OUT_DIR / filename
        path.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n")
        written.append(f"{filename} ({len(defs)} $defs)")

    print(f"contract v{version}: wrote {len(written)} schema files to {OUT_DIR}")
    for line in written:
        print("  ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

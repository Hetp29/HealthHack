"""The published v2 contract files match the running code."""

from __future__ import annotations

import json
import pathlib

from contracts.v2 import build_contract

EXAMPLES = pathlib.Path("contracts/v2/examples")


def test_published_openapi_document_is_current():
    assert build_contract.main(["--check"]) == 0, (
        "contracts/v2/openapi.json is stale; run `python -m contracts.v2.build_contract`")


def test_openapi_declares_every_route_group_and_no_frozen_route():
    spec = json.loads(pathlib.Path("contracts/v2/openapi.json").read_text())
    paths = set(spec["paths"])
    for required in ("/auth/register", "/onboarding", "/consents/{consent_type}", "/meals/images",
                     "/meals/{meal_id}/analyze", "/meals/{meal_id}/confirm", "/meals/{meal_id}/predict",
                     "/meals/{meal_id}/outcome", "/predictions/{prediction_id}", "/cgm/import",
                     "/cgm/readings", "/cgm/status", "/devices", "/devices/{provider}/connect",
                     "/wearables/observations", "/medications", "/medication-doses",
                     "/alert-settings", "/alerts"):
        assert required in paths, required
    assert not any(p.startswith("/api/") for p in paths)


def test_example_payloads_are_labelled_demo_and_carry_no_credentials():
    meta = json.loads((EXAMPLES / "_meta.json").read_text())
    assert meta["data_class"] == "demo"
    files = sorted(EXAMPLES.glob("*.json"))
    assert len(files) >= 35
    for path in files:
        text = path.read_text()
        json.loads(text)
        for forbidden in ("scrypt$", "password_hash", "token_enc", "sk-", "/Users/"):
            assert forbidden not in text, f"{forbidden!r} in {path.name}"


def test_example_forecast_is_the_frozen_contract_shape():
    from jsonschema import Draft202012Validator

    schema = json.loads(pathlib.Path("contracts/schemas/predict_response.schema.json").read_text())
    for name in ("prediction_created", "prediction_history_4_below_retrieval_gate",
                 "prediction_history_5_retrieval_active"):
        body = json.loads((EXAMPLES / f"{name}.json").read_text())["body"]
        assert list(Draft202012Validator(schema).iter_errors(body["forecast"])) == []
    four = json.loads((EXAMPLES / "prediction_history_4_below_retrieval_gate.json").read_text())["body"]
    five = json.loads((EXAMPLES / "prediction_history_5_retrieval_active.json").read_text())["body"]
    assert four["personalization"]["retrieval_active"] is False
    assert five["personalization"]["retrieval_active"] is True


def test_every_operation_is_recorded_in_the_interface_registry():
    registry = pathlib.Path("docs/project/API_INTERFACE_REGISTRY.md").read_text()
    spec = json.loads(pathlib.Path("contracts/v2/openapi.json").read_text())
    missing = [f"{method.upper()} {path}" for path, operations in spec["paths"].items()
               for method in operations if f"`{path}`" not in registry]
    assert missing == [], f"not in the registry: {missing}"


def test_every_example_the_registry_cites_exists():
    registry = pathlib.Path("docs/project/API_INTERFACE_REGISTRY.md").read_text()
    stems = {path.stem for path in EXAMPLES.glob("*.json")}
    cited = set()
    for line in registry.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        # v2 rows end with an example column; the two-column v1 table has none.
        if len(cells) >= 7 and cells[1] in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            cited.update(name.strip("` ") for name in cells[-2].split(",") if name.strip("` —"))
    assert cited, "no example citations were parsed from the registry tables"
    assert cited <= stems, f"cited but missing: {sorted(cited - stems)}"

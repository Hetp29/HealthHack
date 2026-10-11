"""The freeze record and the frozen files must agree, and the record must not move.

On 2026-10-11 a settings rename edited five frozen files and then re-recorded their
checksums in the freeze manifest and in the freeze test, so every existing check kept
passing. These tests close that gap: the manifest itself is pinned to the bytes
committed at the freeze (e362e88), and every file it lists is compared with it. Changing
a frozen file now needs a new freeze, not a new checksum.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "governance" / "fs13_integrity_manifest.json"

#: SHA-256 of the manifest exactly as committed at the formal freeze (commit e362e88).
MANIFEST_SHA256_AT_FREEZE = "37afa9c66d4caf9c9893a1e9e536dc3e1bb6019efb1f1cb44f318f53f01d07a7"
CONTRACT_SHA256_AT_FREEZE = "be836dddaac5ed4cd78af7adac02bf9b7a1b77133143b84df34621f1be9200f4"
MODEL_SHA256_AT_FREEZE = "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe"


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def test_the_freeze_manifest_is_the_one_committed_at_the_freeze():
    assert sha256(MANIFEST) == MANIFEST_SHA256_AT_FREEZE, (
        "docs/governance/fs13_integrity_manifest.json was edited. It is the freeze record: "
        "it is replaced only by a new, explicitly authorized freeze.")


def test_the_manifest_records_the_original_contract_and_model():
    record = manifest()
    assert record["contract_sha256"] == CONTRACT_SHA256_AT_FREEZE
    assert record["artifact_sha256"]["model.joblib"] == MODEL_SHA256_AT_FREEZE
    assert "rehash_log" not in record


def test_contract_v1_matches_the_freeze_record():
    assert sha256(ROOT / "contracts" / "openapi.yaml") == CONTRACT_SHA256_AT_FREEZE


@pytest.mark.parametrize("relative", sorted(json.loads(MANIFEST.read_text())["inference_source_sha256"]))
def test_every_frozen_source_file_matches_the_freeze_record(relative):
    assert sha256(ROOT / relative) == manifest()["inference_source_sha256"][relative], (
        f"{relative} is a frozen file and no longer matches the freeze record")


def test_the_model_artifact_matches_the_freeze_record_when_present():
    artifact = ROOT / manifest()["artifact_dir"] / "model.joblib"
    if not artifact.exists():
        pytest.skip("the model artifact is not in git and is not on this machine")
    assert sha256(artifact) == MODEL_SHA256_AT_FREEZE

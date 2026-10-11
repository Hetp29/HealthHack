"""The deployment path can only ever serve the frozen artifact."""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys

import pytest

from backend.app.v2.verify_artifact import verify

FROZEN_SHA256 = "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe"
FROZEN_ARTIFACT = pathlib.Path("ml/artifacts_cgmacros_fs13/model.joblib")


def _instructions(path: str) -> str:
    """File content with comment lines removed, so prose about training is ignored."""
    return "\n".join(line for line in pathlib.Path(path).read_text().splitlines()
                     if not line.lstrip().startswith("#"))


def test_dockerfile_never_trains_and_never_points_at_a_synthetic_model():
    text = _instructions("Dockerfile")
    for forbidden in ("ml.train", "ml.evaluate", "--source synthetic_dev", "ml/artifacts/model.joblib"):
        assert forbidden not in text, f"Dockerfile must not contain {forbidden!r}"
    assert "GLIMPSE_MODEL_ARTIFACT=ml/artifacts_cgmacros_fs13/model.joblib" in text
    assert f"GLIMPSE_EXPECTED_MODEL_SHA256={FROZEN_SHA256}" in text
    # Verified at build time and again before serving.
    assert len(re.findall(r"python -m backend\.app\.v2\.verify_artifact", text)) == 2
    assert "backend.app.v2.asgi:app" in text
    assert "backend.app.main:app" not in text


def test_compose_uses_the_frozen_artifact_and_enforces_its_checksum():
    text = _instructions("docker-compose.yml")
    assert "ml/artifacts/model.joblib" not in text
    assert "ml/artifacts_cgmacros_fs13/model.joblib" in text and FROZEN_SHA256 in text


def test_the_frozen_artifact_is_not_excluded_from_the_build_context():
    ignored = [line.strip() for line in pathlib.Path(".dockerignore").read_text().splitlines()
               if line.strip() and not line.startswith("#")]
    assert "ml/artifacts_cgmacros_fs13" not in ignored and "ml" not in ignored
    assert "ml/artifacts" in ignored          # the synthetic development artifact IS excluded


def test_verification_fails_closed(tmp_path):
    other = tmp_path / "model.joblib"
    other.write_bytes(b"not the model")
    digest = hashlib.sha256(b"not the model").hexdigest()
    assert verify(str(other), digest) == (True, f"model artifact verified: {digest}")
    assert verify(str(other), FROZEN_SHA256)[0] is False            # wrong artifact
    assert verify(str(tmp_path / "missing.joblib"), FROZEN_SHA256)[0] is False
    assert verify(str(other), None)[0] is False                     # no expectation = no service
    assert verify(str(other), "")[0] is False
    assert verify(None, FROZEN_SHA256)[0] is False


def test_the_synthetic_development_artifact_is_rejected_by_the_gate():
    synthetic = pathlib.Path("ml/artifacts/model.joblib")
    assert synthetic.exists()
    ok, message = verify(str(synthetic), FROZEN_SHA256)
    assert ok is False and "mismatch" in message


@pytest.mark.skipif(not FROZEN_ARTIFACT.exists(), reason="frozen fs-1.3 artifact not on this machine")
def test_the_gate_command_passes_for_the_frozen_artifact_and_blocks_otherwise():
    def run(artifact: str, expected: str | None) -> int:
        env = {"PATH": "", "GLIMPSE_MODEL_ARTIFACT": artifact}
        if expected is not None:
            env["GLIMPSE_EXPECTED_MODEL_SHA256"] = expected
        return subprocess.run([sys.executable, "-m", "backend.app.v2.verify_artifact"],
                              env=env, capture_output=True).returncode

    assert run(str(FROZEN_ARTIFACT), FROZEN_SHA256) == 0
    assert run("ml/artifacts/model.joblib", FROZEN_SHA256) == 1
    assert run(str(FROZEN_ARTIFACT), None) == 1

"""Shared pytest configuration.

Two jobs:

1. Put the repository root on `sys.path`, so tests can import `ml.*`, `backend.*` and
   `contracts.*` without an editable install. A clean clone then needs only
   `pip install -r requirements-dev.txt`.

2. Make the suite **self-sufficient on a clean clone**. `ml/artifacts/` is gitignored
   (the artifact is reproducible, so committing a binary would be churn), but
   `tests/test_api.py` exercises the service with a model loaded -- the real
   integration surface. Without provisioning, 20 API tests fail on a fresh clone for
   the uninteresting reason that nobody had run `ml.train` yet, which makes
   `python -m pytest` an unreliable signal for anyone following the README verbatim.

   The fixture below trains and evaluates once per session **only if the artifacts are
   absent**, so the common case (artifacts already present from local work) costs
   nothing. Tests that need the *untrained* behaviour -- `TestModelNotTrained` -- are
   unaffected: they point their own settings at a temporary empty directory rather than
   relying on the repo being in an untrained state.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ARTIFACT_PATH = REPO_ROOT / "ml" / "artifacts" / "model.joblib"
METRICS_PATH = REPO_ROOT / "ml" / "artifacts" / "metrics.json"


def _run_module(module: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`python -m {module} {' '.join(args)}` failed during test setup:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def _artifact_is_current() -> bool:
    """True when the on-disk artifact matches the running feature contract.

    A feature-set bump (fs-1.0 -> fs-1.1) makes an existing artifact unloadable:
    `load_artifact` refuses it rather than serving coefficients that correspond to a
    different column layout. Without this check the suite would fail on every machine
    that had trained before the bump, for a reason that has nothing to do with the
    code under test.
    """
    if not ARTIFACT_PATH.exists():
        return False
    try:
        from ml.train import load_artifact

        load_artifact(ARTIFACT_PATH)
        return True
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def ensure_trained_artifacts() -> None:
    """Provision `model.joblib` and `metrics.json` when absent or stale.

    Only regenerates the synthetic-fixture artifact under `ml/artifacts/`. Real-data
    artifacts live in `ml/artifacts_<dataset>/` and are never touched by the tests.
    """
    if not _artifact_is_current():
        _run_module("ml.train", "--source", "synthetic_dev")
        _run_module("ml.evaluate", "--protocol", "all")
    elif not METRICS_PATH.exists():
        _run_module("ml.evaluate", "--protocol", "all")

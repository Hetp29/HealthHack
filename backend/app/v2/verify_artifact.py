"""Refuse to start unless the model artifact is exactly the approved one.

    python -m backend.app.v2.verify_artifact

Run at image build time and again as the first step of the container command. It
exits non-zero -- so the build fails, or the container never starts serving -- when:

  * `GLIMPSE_EXPECTED_MODEL_SHA256` is not set;
  * the file at `GLIMPSE_MODEL_ARTIFACT` is missing;
  * its SHA-256 differs from the expected value.

This is the deployment-level guard for the frozen v1 endpoints, which cannot carry a
check of their own without modifying frozen code. It reads the file and nothing else:
it does not load, train, download or write a model.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import sys


def verify(artifact: str | None, expected: str | None) -> tuple[bool, str]:
    if not expected:
        return False, "GLIMPSE_EXPECTED_MODEL_SHA256 is not set; refusing to serve an unverified model"
    if not artifact:
        return False, "GLIMPSE_MODEL_ARTIFACT is not set"
    path = pathlib.Path(artifact)
    if not path.is_file():
        return False, f"model artifact not found at {path}; it must be supplied, it is never trained here"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected.strip().lower():
        return False, f"model artifact checksum mismatch: expected {expected}, found {actual}"
    return True, f"model artifact verified: {actual}"


def main() -> int:
    # GLIMPSE_* with the legacy GLUCOTWIN_* name as fallback (renamed 2026-10-11).
    def env(name: str) -> str | None:
        return os.environ.get("GLIMPSE_" + name) or os.environ.get("GLUCOTWIN_" + name)

    ok, message = verify(env("MODEL_ARTIFACT"), env("EXPECTED_MODEL_SHA256"))
    print(message, file=sys.stdout if ok else sys.stderr, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

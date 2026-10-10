"""Shared pytest configuration: put the repository root on sys.path.

Lets tests import `ml.*`, `backend.*` and `contracts.*` without an editable install,
so a clean clone needs only `pip install -r requirements-dev.txt`.
"""

from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

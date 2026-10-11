"""`GLIMPSE_*` settings, delivered to code that only knows `GLUCOTWIN_*`.

The project was renamed GLIMPSE, and the application's own settings may be given under
either prefix. The frozen fs-1.3 engine (`backend/app/config.py` and the files frozen
with it) reads `GLUCOTWIN_*` and is not edited: its bytes are part of the freeze record
(`docs/governance/fs13_integrity_manifest.json`). So instead of teaching frozen code a
new name, the new name is copied onto the old one before that code looks.

`GLUCOTWIN_X` keeps working exactly as before. `GLIMPSE_X`, when set and not blank,
wins over it, which is the same rule the v2 settings reader applies.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping

NEW_PREFIX, LEGACY_PREFIX = "GLIMPSE_", "GLUCOTWIN_"


def legacy_aliases(environ: Mapping[str, str]) -> dict[str, str]:
    """The `GLUCOTWIN_*` values implied by the `GLIMPSE_*` ones. Changes nothing."""
    return {
        LEGACY_PREFIX + name[len(NEW_PREFIX):]: value
        for name, value in environ.items()
        if name.startswith(NEW_PREFIX) and value.strip() != ""
        and environ.get(LEGACY_PREFIX + name[len(NEW_PREFIX):]) != value
    }


def apply_env_aliases(environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Copy every `GLIMPSE_X` onto `GLUCOTWIN_X`. Idempotent. Returns what it set."""
    target = os.environ if environ is None else environ
    changes = legacy_aliases(target)
    target.update(changes)
    return changes

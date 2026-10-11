"""Which meals in a demonstration account were really eaten.

A demonstration account replays one research participant's recorded glucose. Two kinds
of meal can exist in it:

* an **authentic recorded meal**: one the dataset records the participant eating,
  logged by the governed replay;
* a **hypothetical demonstration meal**: one typed in or photographed by whoever is
  exploring the demonstration.

A hypothetical meal may still be forecast: the frozen model answers "what would this
meal do, given this glucose history?". But the participant never ate it, so the
recording that follows is NOT its observed response. It therefore has no outcome, is
never personal history, and is invisible to every authentic meal's forecast and score.

The distinction is the meal's note. The replay writes a reserved prefix, and the public
API refuses that prefix from any other caller, so the prefix cannot be forged. Accounts
outside demonstration mode are unaffected: every meal there is the user's own.
See DECISIONS D-019.
"""

from __future__ import annotations

import hmac
import secrets

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement

from backend.app.v2.db import tables as t

#: Note prefixes only the replay may write (research bundle, and the test fixture).
RESERVED_NOTE_PREFIXES = ("Research dataset meal record", "Generated test-fixture meal record")

#: Carried by the replay's in-process API calls. Never leaves this process.
REPLAY_HEADER = "x-glimpse-replay-token"
_REPLAY_TOKEN = secrets.token_hex(32)

OUTCOME_NOT_APPLICABLE = "not_applicable"
REASON_HYPOTHETICAL = "hypothetical_demo_meal"


def replay_headers() -> dict[str, str]:
    return {REPLAY_HEADER: _REPLAY_TOKEN}


def is_replay_request(headers) -> bool:
    return hmac.compare_digest(headers.get(REPLAY_HEADER) or "", _REPLAY_TOKEN)


def has_reserved_prefix(note: str | None) -> bool:
    return bool(note) and note.startswith(RESERVED_NOTE_PREFIXES)


def is_demo_account(conn: Connection, user_id: str) -> bool:
    return conn.execute(
        select(t.demo_replay_state.c.user_id).where(t.demo_replay_state.c.user_id == user_id)
    ).first() is not None


def is_hypothetical(conn: Connection, user_id: str, meal: dict) -> bool:
    """True for a meal entered by hand or by photo inside a demonstration account."""
    return is_demo_account(conn, user_id) and not has_reserved_prefix(meal.get("note"))


def authentic_meals_only(conn: Connection, user_id: str) -> ColumnElement[bool] | None:
    """A filter on `meals` that keeps only really-eaten meals, or `None` when every
    meal qualifies (any account that is not a demonstration)."""
    if not is_demo_account(conn, user_id):
        return None
    return and_(t.meals.c.note.is_not(None),
                or_(*(t.meals.c.note.like(f"{prefix}%") for prefix in RESERVED_NOTE_PREFIXES)))
